import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from racing_engineer.app import App
from racing_engineer.model import Sample
from racing_engineer.storage import Recorder, load_events


def event(recording_id, stage="accepted"):
    return {"event_id": "test-1", "kind": "voice", "cue": "test", "message": "Prueba local",
            "stage": stage, "reason": None, "utc": "2026-10-12T00:00:00+00:00", "mode": "live",
            "recording_id": recording_id, "session_id": "session-a", "time_s": 1, "lap": 2,
            "lap_dist_pct": .36}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "capture.sqlite"
        self.app = App("live", self.path)

    def tearDown(self):
        self.app.record(False)
        self.app.voice.close()
        self.tmp.cleanup()

    def deliver(self, row):
        with patch.object(self.app.voice, "drain_events", return_value=[row]):
            self.app.flush_voice_events()

    def test_opt_in_history_persists_and_late_completion_never_moves_to_new_recording(self):
        self.deliver(event(None))
        self.assertFalse(self.path.exists())
        self.app.record(True)
        first = self.app.recorder.recording_id
        self.deliver(event(first))
        self.app.record(False)
        self.app.record(True)
        second = self.app.recorder.recording_id
        self.deliver(event(first, "finished"))
        self.deliver(event(second, "started"))
        self.app.record(False)
        self.assertEqual(["accepted"], [e["stage"] for e in load_events(self.path, first)])
        self.assertEqual(["started"], [e["stage"] for e in load_events(self.path, second)])
        self.assertFalse(self.app.voice_history()["events"][-2]["persisted"])

    def test_old_recording_without_events_is_read_only_and_unsupported_events_are_rejected(self):
        recorder = Recorder(self.path, "live")
        rid = recorder.recording_id
        with self.assertRaises(ValueError):
            recorder.write_event(event("wrong"))
        recorder.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE events")
        before = self.path.read_bytes()
        self.assertEqual([], load_events(self.path, rid))
        self.assertEqual(before, self.path.read_bytes())

    def test_event_disk_error_is_visible_and_does_not_stop_sample_recording(self):
        self.app.record(True)
        with patch.object(self.app.recorder, "write_event", side_effect=sqlite3.OperationalError("disk full")):
            self.deliver(event(self.app.recorder.recording_id))
        self.assertIn("disk full", self.app.voice_history()["error"])
        self.assertFalse(self.app.voice_history()["events"][-1]["persisted"])
        self.assertIsNotNone(self.app.recorder)

    def test_markers_are_session_scoped_atomic_and_reloaded_without_changing_telemetry(self):
        self.app.add(Sample("iracing", "session-a", 1, on_track=True, is_replay=False))
        marker = {"name": "5", "session_id": "session-a", "progress_pct": .37}
        self.app.mark(marker)
        self.assertEqual([marker], json.loads(self.app.marker_path.read_text(encoding="utf-8")))
        self.assertFalse(self.path.exists())
        with patch("racing_engineer.app.os.replace", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                self.app.mark({**marker, "progress_pct": .38})
        self.assertEqual([marker], self.app.markers)
        self.assertEqual([marker], App("replay", self.path).markers)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        with self.assertRaises(ValueError):
            self.app.mark({**marker, "session_id": "another-track-session"})

    def test_malformed_marker_file_is_not_overwritten_silently(self):
        self.app.marker_path.write_text('{"bad":true}', encoding="utf-8")
        restored = App("replay", self.path)
        self.assertIn("No se pudieron leer", restored.marker_error)
        restored.add(Sample("iracing", "session-a", 1))
        with self.assertRaises(ValueError):
            restored.mark({"name": "5", "session_id": "session-a", "progress_pct": .37})
        self.assertEqual('{"bad":true}', self.app.marker_path.read_text(encoding="utf-8"))

    def test_retention_expires_events_with_their_recording(self):
        recorder = Recorder(self.path, "live", retention_days=1)
        rid = recorder.recording_id
        recorder.write_event(event(rid))
        recorder.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE recordings SET started_utc='2000-01-01T00:00:00+00:00'")
        latest = Recorder(self.path, "live")
        latest.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM events WHERE recording_id=?", (rid,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
