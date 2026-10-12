import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from racing_engineer.app import App, collect, handler_for
from racing_engineer.demo import demo_samples
from racing_engineer.model import Sample
from racing_engineer.storage import Recorder, load_replay


class ModelAndStorageTests(unittest.TestCase):
    def test_input_boundary_rejects_nan_booleans_wrong_units_and_unknown_versions(self):
        good = Sample("demo", "fixture", 0).to_dict()
        for key, value in (("time_s", None), ("time_s", float("nan")), ("speed_mps", float("inf")),
                           ("brake", True), ("brake", 50), ("on_track", "false"),
                           ("lap", 1.5), ("source", "invented"), ("schema_version", True),
                           ("schema_version", 2)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                Sample.from_dict({**good, key: value})
        self.assertIsNone(Sample.from_dict(good).brake)

    def test_deterministic_demo_round_trips_every_value_and_provenance(self):
        fixture = list(demo_samples())
        self.assertEqual(fixture, list(demo_samples()))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.sqlite"
            recorder = Recorder(path, "demo")
            for sample in fixture:
                recorder.write(sample)
            recorder.close()
            original = path.read_bytes()
            replay = list(load_replay(path))
            self.assertEqual(fixture, replay)
            self.assertEqual(original, path.read_bytes())
            self.assertTrue(all(s.source == "demo" for s in replay))

    def test_optional_voice_fields_keep_old_v1_recordings_readable(self):
        old = {"schema_version": 1, "source": "iracing", "session_id": "old", "time_s": 1}
        sample = Sample.from_dict(old)
        self.assertIsNone(sample.abs_active)
        self.assertIsNone(sample.throttle_raw)
        self.assertIsNone(sample.vert_accel_mps2)
        self.assertIsNone(sample.on_pit_road)
        for field, invalid in (("on_pit_road", 1), ("abs_active", 1), ("throttle_raw", -0.1), ("vert_accel_mps2", float("nan"))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                Sample.from_dict({**old, field: invalid})

    def test_missing_replay_does_not_create_file_and_multiple_runs_need_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.sqlite"
            with self.assertRaises(FileNotFoundError):
                list(load_replay(path))
            self.assertFalse(path.exists())
            first = Recorder(path, "demo")
            first.write(Sample("demo", "a", 1))
            first.close()
            second = Recorder(path, "demo")
            second.write(Sample("demo", "b", 2))
            second.close()
            with self.assertRaisesRegex(ValueError, "recording-id"):
                list(load_replay(path))
            self.assertEqual("a", list(load_replay(path, first.recording_id))[0].session_id)

    def test_other_database_is_never_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE precious (text)")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                Recorder(path, "demo")
            self.assertEqual(before, path.read_bytes())

    def test_opt_in_retention_only_expires_its_own_completed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.sqlite"
            expires = Recorder(path, "demo", retention_days=1)
            expires.write(Sample("demo", "expires", 1))
            expires.close()
            keeps = Recorder(path, "demo")
            keeps.write(Sample("demo", "keeps", 1))
            keeps.close()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE recordings SET started_utc='2000-01-01T00:00:00+00:00'")
            latest = Recorder(path, "demo")
            latest.close()
            with closing(sqlite3.connect(path)) as db, db:
                ids = [r[0] for r in db.execute("SELECT id FROM recordings")]
            self.assertNotIn(expires.recording_id, ids)
            self.assertIn(keeps.recording_id, ids)
            self.assertEqual(1, len(list(load_replay(path, keeps.recording_id))))

    def test_bad_stored_sample_reports_sequence_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.sqlite"
            recorder = Recorder(path, "demo")
            recorder.write(Sample("demo", "a", 0))
            recorder.close()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE samples SET payload='{}'")
            with self.assertRaisesRegex(ValueError, "Muestra persistida 1"):
                list(load_replay(path))


class LocalAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "capture.sqlite"
        self.app = App("demo", self.path)
        self.server = HTTPServer(("127.0.0.1", 0), handler_for(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.record(False)
        self.app.voice.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        content = response.read()
        status = response.status
        connection.close()
        return status, content

    def test_no_recording_by_default_then_explicit_start_stop(self):
        sample = next(demo_samples())
        self.app.add(sample)
        self.assertFalse(self.path.exists())
        status, state = self.request("GET", "/api/state")
        self.assertEqual(200, status)
        csrf = json.loads(state)["csrf"]
        self.assertEqual(403, self.request("POST", "/api/record", {"enabled": True, "csrf": "wrong"})[0])
        self.assertFalse(self.path.exists())
        self.assertEqual(200, self.request("POST", "/api/record", {"enabled": True, "csrf": csrf})[0])
        self.app.add(replace(sample, time_s=1))
        self.assertEqual(200, self.request("POST", "/api/record", {"enabled": False, "csrf": csrf})[0])
        self.app.add(replace(sample, time_s=2))
        replay = list(load_replay(self.path))
        self.assertEqual([1], [s.time_s for s in replay])

    def test_browser_trust_boundary_rejects_remote_origin_host_and_wrong_types(self):
        for headers in ({"Origin": "https://other.example"}, {"Host": "evil.example"}):
            self.assertEqual(403, self.request("GET", "/api/state", headers=headers)[0])
        self.assertEqual(400, self.request("POST", "/api/record", {"enabled": "false", "csrf": self.app.csrf})[0])
        self.assertFalse(self.path.exists())
        self.assertEqual(404, self.request("GET", "/../../evidence/private.jpg")[0])

    def test_marker_api_rejects_untrusted_input_and_exports_current_review(self):
        self.app.add(Sample("demo", "fixture", 1, lap=1, lap_dist_pct=.37,
                            speed_mps=30, vert_accel_mps2=25, on_track=True, is_replay=False))
        body = {"csrf": self.app.csrf, "name": "5", "session_id": "fixture", "progress_pct": .37}
        self.assertEqual(403, self.request("POST", "/api/marker", {**body, "csrf": "bad"})[0])
        self.assertEqual(403, self.request("POST", "/api/marker", body,
                                          {"Origin": "https://other.example"})[0])
        for changes in ({"progress_pct": True}, {"progress_pct": float("nan")},
                        {"progress_pct": 50}, {"name": "<script>"}, {"session_id": "another"},
                        {"path": "other.json"}):
            self.assertEqual(400, self.request("POST", "/api/marker", {**body, **changes})[0])
        self.assertEqual(200, self.request("POST", "/api/marker", body)[0])
        status, raw = self.request("GET", "/api/review.json")
        self.assertEqual(200, status)
        self.assertEqual("5", json.loads(raw)["events"][0]["corner"])
        self.assertEqual([], json.loads(self.request("GET", "/api/voice-history.json")[1])["events"])
        self.assertFalse(self.path.exists())

    def test_voice_api_is_local_token_protected_and_independent_from_recording(self):
        token = self.app.csrf
        with patch.object(self.app.voice, "set_enabled") as enable, patch.object(self.app.voice, "test") as test:
            self.assertEqual(403, self.request("POST", "/api/voice", {"enabled": True, "csrf": "bad"})[0])
            self.assertEqual(403, self.request("POST", "/api/voice", {"enabled": True, "csrf": token},
                                              {"Origin": "https://other.example"})[0])
            self.assertEqual(400, self.request("POST", "/api/voice", {"enabled": "true", "csrf": token})[0])
            self.assertEqual(400, self.request("POST", "/api/voice", {"action": "test", "text": "untrusted", "csrf": token})[0])
            enable.assert_not_called()
            test.assert_not_called()
            self.assertEqual(200, self.request("POST", "/api/voice", {"enabled": True, "csrf": token})[0])
            enable.assert_called_once_with(True)
            self.assertEqual(200, self.request("POST", "/api/voice", {"action": "test", "csrf": token})[0])
            test.assert_called_once_with()
            self.assertFalse(self.path.exists())
            self.assertIsNone(self.app.recorder)

    def test_headless_demo_is_usable_and_replay_has_identical_observations(self):
        self.app.record(True)
        collect(self.app, demo_samples(), threading.Event(), realtime=False)
        self.assertFalse(self.app.recorder)
        self.assertEqual(901, self.app.total)
        replay_app = App("replay", Path(self.tmp.name) / "unused.sqlite")
        collect(replay_app, load_replay(self.path), threading.Event(), realtime=False)
        self.assertEqual(self.app.summary()["findings"], replay_app.summary()["findings"])
        self.assertTrue(replay_app.summary()["synthetic"])
        self.assertEqual("replay", replay_app.summary()["source_mode"])
        self.assertEqual(409, self.request("POST", "/api/record", {"enabled": True, "csrf": self.app.csrf})[0])
        self.assertEqual(200, self.request("GET", "/api/report.md")[0])
        laps = self.app.summary()["laps"]
        query = urlencode({"first": laps[0]["id"], "second": laps[1]["id"]})
        status, compared = self.request("GET", "/api/compare?" + query)
        self.assertEqual(200, status)
        self.assertEqual("ok", json.loads(compared)["status"])
        exported = json.loads(self.request("GET", "/api/report.json")[1])
        self.assertEqual(json.loads(compared), exported["comparison"])
        self.assertIn("Comparación seleccionada", self.request("GET", "/api/report.md")[1].decode())

    def test_recording_failure_is_visible_and_preserves_memory(self):
        self.app.record(True)
        with patch.object(self.app.recorder, "write", side_effect=sqlite3.OperationalError("disk full")):
            self.app.add(next(demo_samples()))
        self.assertIsNone(self.app.recorder)
        self.assertIn("disk full", self.app.snapshot()["record_error"])
        self.assertEqual(1, self.app.total)

    def test_race_results_export_and_manual_selection_are_local_read_only(self):
        collect(self.app, demo_samples(), threading.Event(), realtime=False)
        status, raw = self.request("GET", "/api/race-results.json")
        self.assertEqual(200, status)
        balance = json.loads(raw)
        self.assertEqual("ok", balance["status"])
        self.assertEqual(balance, json.loads(self.request("GET", "/api/state")[1])["summary"]["race_results"])
        query = urlencode({"lap": balance["worst"]["id"]})
        selected = json.loads(self.request("GET", "/api/race-results.json?" + query)[1])
        self.assertEqual("manual", selected["selection"])
        self.assertEqual(balance["comparison"], selected["comparison"])
        self.assertEqual(400, self.request("GET", "/api/race-results.json?lap=missing")[0])
        self.assertEqual(403, self.request("GET", "/api/race-results.json",
                                          headers={"Origin": "https://other.example"})[0])
        report = self.request("GET", "/api/report.md")[1].decode()
        self.assertIn("Balance de vueltas", report)
        self.assertIn("Referencia: vuelta", report)
        self.assertFalse(self.path.exists())

    def test_source_close_failure_still_closes_recording(self):
        class BrokenClose:
            def __next__(self):
                raise StopIteration

            def close(self):
                raise RuntimeError("close failed")

        self.app.record(True)
        self.app.add(next(demo_samples()))
        collect(self.app, BrokenClose(), threading.Event(), realtime=False)
        self.assertIsNone(self.app.recorder)
        self.assertIn("close failed", self.app.status)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNotNone(db.execute("SELECT stopped_utc FROM recordings").fetchone()[0])

    def test_connected_but_stale_samples_are_not_current(self):
        self.app.connected = True
        self.app.add(next(demo_samples()))
        self.assertTrue(self.app.snapshot()["fresh"])
        with patch("racing_engineer.app.time.monotonic", return_value=self.app.last_received_at + 3):
            self.assertTrue(self.app.snapshot()["connected"])
            self.assertFalse(self.app.snapshot()["fresh"])


if __name__ == "__main__":
    unittest.main()
