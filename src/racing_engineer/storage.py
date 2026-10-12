"""SQLite de propiedad de esta app. Abrir un replay nunca crea ni altera el archivo."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .model import Sample

APP_ID = 0x34424953


class Recorder:
    def __init__(self, path, mode, retention_days=None):
        if retention_days is not None and (type(retention_days) is not int or retention_days < 1):
            raise ValueError("retention_days debe ser un entero positivo")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # App serializa todas las operaciones con un lock; CLI usa un solo hilo.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.recording_id = uuid.uuid4().hex
        try:
            app_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
            tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app_id != APP_ID and tables:
                raise ValueError("El archivo SQLite no pertenece a Racing Engineer")
            self.connection.execute(f"PRAGMA application_id={APP_ID}")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, started_utc TEXT NOT NULL,
                    stopped_utc TEXT, retention_days INTEGER);
                CREATE TABLE IF NOT EXISTS samples (
                    seq INTEGER PRIMARY KEY, recording_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, recording_id TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            # ponytail: retención por grabación al iniciar otra; no borra archivos ni otras bases.
            expired = self.connection.execute("""
                SELECT id FROM recordings WHERE stopped_utc IS NOT NULL AND retention_days IS NOT NULL
                AND julianday('now') - julianday(started_utc) > retention_days
            """).fetchall()
            for (recording_id,) in expired:
                self.connection.execute("DELETE FROM events WHERE recording_id=?", (recording_id,))
                self.connection.execute("DELETE FROM samples WHERE recording_id=?", (recording_id,))
                self.connection.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
            self.connection.execute("INSERT INTO recordings VALUES (?,?,?,NULL,?)",
                                    (self.recording_id, mode, datetime.now(timezone.utc).isoformat(), retention_days))
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def write(self, sample):
        self.connection.execute("INSERT INTO samples(recording_id,payload) VALUES (?,?)",
                                (self.recording_id, json.dumps(sample.to_dict(), allow_nan=False)))
        # Cada muestra queda confirmada: cierre abrupto no pierde toda la sesión.
        self.connection.commit()

    def close(self):
        try:
            self.connection.execute("UPDATE recordings SET stopped_utc=? WHERE id=?",
                                    (datetime.now(timezone.utc).isoformat(), self.recording_id))
            self.connection.commit()
        finally:
            self.connection.close()

    def write_event(self, event):
        validate_voice_event(event)
        if event.get("recording_id") != self.recording_id:
            raise ValueError("El aviso pertenece a otra grabación")
        self.connection.execute("INSERT INTO events(recording_id,payload) VALUES (?,?)",
                                (self.recording_id, json.dumps(event, ensure_ascii=False, allow_nan=False)))
        self.connection.commit()


def validate_voice_event(event):
    if not isinstance(event, dict) or event.get("kind") != "voice":
        raise ValueError("Evento de voz inválido")
    if event.get("stage") not in ("accepted", "suppressed", "error", "started", "finished", "cancelled"):
        raise ValueError("Estado de voz inválido")
    for key in ("event_id", "cue", "message", "utc", "mode"):
        if not isinstance(event.get(key), str) or not 1 <= len(event[key]) <= 2000:
            raise ValueError(f"Campo de voz inválido: {key}")
    if event["mode"] not in ("live", "demo", "replay"):
        raise ValueError("Modo de voz inválido")
    # Also rejects non-finite nested numbers before export or persistence.
    if len(json.dumps(event, allow_nan=False)) > 16000:
        raise ValueError("Evento de voz demasiado grande")


def load_events(path, recording_id=None):
    """Leer avisos existentes no migra ni altera grabaciones antiguas."""
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID:
            raise ValueError("La grabación no es una base Racing Engineer")
        ids = [r[0] for r in connection.execute("SELECT id FROM recordings ORDER BY rowid")]
        if recording_id is None:
            if len(ids) != 1:
                raise ValueError("Elige --recording-id para leer el historial")
            recording_id = ids[0]
        if recording_id not in ids:
            raise ValueError("No existe esa grabación")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return []
        events = []
        for seq, payload in connection.execute("SELECT seq,payload FROM events WHERE recording_id=? ORDER BY seq", (recording_id,)):
            try:
                if len(payload) > 16000:
                    raise ValueError("Evento demasiado grande")
                event = json.loads(payload)
                validate_voice_event(event)
                if event.get("recording_id") != recording_id:
                    raise ValueError("Grabación del evento no coincide")
                events.append(event)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Evento persistido {seq}: {error}") from error
        return events
    finally:
        connection.close()


def load_replay(path, recording_id=None):
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID:
            raise ValueError("La grabación no es una base Racing Engineer")
        ids = [row[0] for row in connection.execute("SELECT id FROM recordings ORDER BY rowid")]
        if recording_id is None:
            if len(ids) != 1:
                raise ValueError(f"Elige --recording-id entre: {', '.join(ids) or '(sin grabaciones)'}")
            recording_id = ids[0]
        if recording_id not in ids:
            raise ValueError("No existe esa grabación")
        for seq, payload in connection.execute("SELECT seq,payload FROM samples WHERE recording_id=? ORDER BY seq", (recording_id,)):
            try:
                yield Sample.from_dict(json.loads(payload))
            except (ValueError, TypeError) as error:
                raise ValueError(f"Muestra persistida {seq}: {error}") from error
    finally:
        connection.close()
