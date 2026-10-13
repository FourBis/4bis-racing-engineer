"""Servidor loopback y CLI. Ningún endpoint acepta rutas ni controla el simulador."""
import argparse
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .analysis import compare_laps, report_markdown, summarize
from .corners import corner_guidance
from .demo import demo_samples
from .navigation import Navigator
from .review import review_vertical, validate_marker
from .race_results import race_results
from .storage import Recorder, load_events, load_replay
from .voice import VoiceCoach


class App:
    def __init__(self, mode, storage, retention_days=None, annotations=None, review_path=None):
        self.mode = mode
        self.storage = storage
        self.retention_days = retention_days
        self.samples = deque(maxlen=50000)
        self.total = 0
        self.status = "Esperando muestras"
        self.channels = {}
        self.connected = False
        self.last_received_at = None
        self.recorder = None
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.cache_time = -float("inf")
        self.cache = None
        self.record_error = None
        self.running = True
        self.annotations = annotations or {}
        self.comparison = None
        self.voice = VoiceCoach(mode)
        self.navigator = Navigator()
        self.voice_events = deque(maxlen=5000)
        self.voice_event_count = 0
        self.voice_log_error = None
        self.marker_path = Path(review_path or storage).with_suffix(".markers.json")
        self.markers = []
        self.marker_error = None
        try:
            if self.marker_path.exists():
                if self.marker_path.stat().st_size > 1000000:
                    raise ValueError("Archivo de marcas demasiado grande")
                rows = json.loads(self.marker_path.read_text(encoding="utf-8"))
                if not isinstance(rows, list) or len(rows) > 300:
                    raise ValueError("Se requieren hasta 300 marcas")
                self.markers = [validate_marker(row) for row in rows]
        except (OSError, ValueError, TypeError) as error:
            self.marker_error = f"No se pudieron leer las marcas: {error}"

    def fresh(self, max_age_s=2):
        return self.connected and self.last_received_at is not None and time.monotonic() - self.last_received_at <= max_age_s

    def record(self, enabled):
        with self.lock:
            self.flush_voice_events()
            if enabled and self.recorder is None:
                self.recorder = Recorder(self.storage, self.mode, self.retention_days)
                self.voice.recording_id = self.recorder.recording_id
                self.record_error = None
            elif not enabled and self.recorder is not None:
                recorder, self.recorder = self.recorder, None
                self.voice.recording_id = None
                recorder.close()

    def flush_voice_events(self):
        # All DB writes stay on App's lock; SAPI only queues lifecycle observations.
        with self.lock:
            for event in self.voice.drain_events():
                event = dict(event, persisted=False)
                if self.recorder and event.get("recording_id") == self.recorder.recording_id:
                    try:
                        self.recorder.write_event({**event, "persisted": True})
                        event["persisted"] = True
                    except (OSError, sqlite3.Error, ValueError) as error:
                        self.voice_log_error = f"No se guardó un aviso: {error}"
                self.voice_events.append(event)
                self.voice_event_count += 1

    def voice_history(self, limit=None):
        self.flush_voice_events()
        events = list(self.voice_events)
        return {"events": events[-limit:] if limit else events,
                "error": self.voice_log_error, "total_events": self.voice_event_count,
                "memory_window_truncated": self.voice_event_count > len(events),
                "persisted_only_while_recording": True,
                "note": "Se guarda durante la grabación. Finalizado por SAPI no prueba que el piloto lo escuchó; una locución puede terminar fuera del intervalo grabado."}

    def mark(self, body):
        marker = validate_marker(body)
        with self.lock:
            if self.marker_error:
                raise ValueError(self.marker_error)
            if not self.samples or marker["session_id"] != self.samples[-1].session_id:
                raise ValueError("La marca debe corresponder a la sesión visible actual")
            rows = [m for m in self.markers if (m["session_id"], m["name"]) != (marker["session_id"], marker["name"])]
            rows.append(marker)
            if len(rows) > 300:
                raise ValueError("Límite local de 300 marcas alcanzado")
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.marker_path.parent,
                                                 prefix=self.marker_path.stem, suffix=".tmp", delete=False) as file:
                    name = file.name
                    json.dump(rows, file, ensure_ascii=False, allow_nan=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(name, self.marker_path)
            finally:
                if name and Path(name).exists():
                    Path(name).unlink()
            self.markers = rows
            self.cache_time = -float("inf")
            return marker

    def add(self, sample):
        with self.lock:
            self.navigator.update(sample)
            self.samples.append(sample)
            self.total += 1
            self.last_received_at = time.monotonic()
            if self.recorder:
                try:
                    self.recorder.write(sample)
                except (OSError, sqlite3.Error, ValueError) as error:
                    self.record_error = f"Grabación detenida: {error}"
                    recorder, self.recorder = self.recorder, None
                    self.voice.recording_id = None
                    try:
                        recorder.close()
                    except (OSError, sqlite3.Error):
                        pass

    def summary(self):
        with self.lock:
            if self.cache is None or time.monotonic() - self.cache_time >= 3:
                self.cache = summarize(list(self.samples))
                self.cache["source_mode"] = self.mode
                self.cache["operator_annotations"] = self.annotations
                self.cache["synthetic"] = bool(self.samples) and all(s.source == "demo" for s in self.samples)
                self.cache["samples_analyzed"] = len(self.samples)
                self.cache["samples_received"] = self.total
                self.cache["memory_window_truncated"] = self.total > len(self.samples)
                self.cache["review"] = review_vertical(list(self.samples), self.markers)
                self.cache["race_results"] = race_results(list(self.samples))
                if self.marker_error:
                    self.cache["review"]["note"] += " " + self.marker_error
                self.cache_time = time.monotonic()
            self.cache["comparison"] = self.comparison
            self.cache["voice_history"] = self.voice_history()
            self.cache["navigation"] = self.navigation()
            return self.cache

    def navigation(self):
        with self.lock:
            result = corner_guidance(self.navigator.snapshot())
            if self.mode == "live" and not self.fresh(0.5):
                result = {**result, "next": None, "delta_s": None, "voice_ready": False,
                          "status": "Esperando telemetría en vivo reciente"}
            return result

    def snapshot(self):
        with self.lock:
            summary = self.summary()
            return {"mode": self.mode, "status": self.status, "recording": self.recorder is not None, "running": self.running,
                    "connected": self.connected,
                    "fresh": self.fresh(), "voice": self.voice.snapshot(),
                    "record_error": self.record_error, "csrf": self.csrf, "channels": self.channels,
                    "samples": [s.to_dict() for s in list(self.samples)[-1200:]],
                    "summary": {key: value for key, value in summary.items() if key not in ("review", "voice_history", "navigation")},
                    "navigation": self.navigation(),
                    "voice_history": self.voice_history(80),
                    "review": {**summary["review"], "events": summary["review"]["events"][-100:]}}


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def allowed(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            return self.headers.get("Host") == expected and self.headers.get("Origin", f"http://{expected}") == f"http://{expected}"

        def send(self, body, mime="application/json; charset=utf-8", status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return self.send({"error": "Origen local requerido"}, status=403)
            url = urlsplit(self.path)
            if url.path == "/":
                return self.send(Path(__file__).with_name("dashboard.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self.send(app.snapshot())
            if url.path == "/api/compare":
                query = parse_qs(url.query)
                with app.lock:
                    result = compare_laps(list(app.samples), query.get("first", [""])[0], query.get("second", [""])[0])
                    app.comparison = result
                return self.send(result)
            if url.path == "/api/report.json":
                return self.send(app.summary())
            if url.path == "/api/race-results.json":
                query = parse_qs(url.query)
                lap_id = query.get("lap", [None])[0]
                if lap_id is None:
                    return self.send(app.summary()["race_results"])
                try:
                    with app.lock:
                        result = race_results(list(app.samples), review_lap_id=lap_id)
                    return self.send(result)
                except ValueError as error:
                    return self.send({"error": str(error)}, status=400)
            if url.path == "/api/report.md":
                return self.send(report_markdown(app.summary()).encode("utf-8"), "text/markdown; charset=utf-8")
            if url.path == "/api/voice-history.json":
                with app.lock:
                    return self.send(app.voice_history())
            if url.path == "/api/review.json":
                return self.send(app.summary()["review"])
            if url.path == "/api/navigation.json":
                return self.send(app.navigation())
            return self.send({"error": "Ruta inexistente"}, status=404)

        def do_POST(self):
            if not self.allowed():
                return self.send({"error": "Origen local requerido"}, status=403)
            if self.path not in ("/api/record", "/api/voice", "/api/marker"):
                return self.send({"error": "Ruta inexistente"}, status=404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Se requiere JSON de hasta 1024 bytes")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Se requiere objeto JSON")
                if not isinstance(body.get("csrf"), str) or not hmac.compare_digest(body["csrf"], app.csrf):
                    return self.send({"error": "Token local inválido"}, status=403)
                if self.path == "/api/marker":
                    return self.send(app.mark({key: value for key, value in body.items() if key != "csrf"}))
                if self.path == "/api/voice":
                    with app.lock:
                        if set(body) == {"csrf", "enabled"} and type(body["enabled"]) is bool:
                            app.voice.set_enabled(body["enabled"])
                        elif set(body) == {"csrf", "action"} and body["action"] == "test":
                            app.voice.test()
                        else:
                            raise ValueError("Indica enabled booleano o action=test; no se acepta texto libre")
                        app.flush_voice_events()
                        return self.send(app.voice.snapshot())
                if type(body.get("enabled")) is not bool:
                    raise ValueError("enabled debe ser booleano")
                if body["enabled"] and not app.running:
                    return self.send({"error": "La fuente terminó; inicia otra ejecución para grabar"}, status=409)
                app.record(body["enabled"])
            except (ValueError, UnicodeError, TypeError) as error:
                return self.send({"error": str(error)}, status=400)
            except (OSError, sqlite3.Error) as error:
                return self.send({"error": f"No se pudo actualizar grabación: {error}"}, status=500)
            return self.send({"recording": app.recorder is not None})

    return Handler


def collect(app, source, stop, speed=1, seconds=0, realtime=True):
    started = time.monotonic()
    previous = None
    try:
        while not stop.is_set() and (not seconds or time.monotonic() - started < seconds):
            if app.mode == "live":
                sample = source.read()
                with app.lock:
                    app.status = source.status
                    app.channels = source.channels
                    app.connected = source.connected
                if sample is None:
                    with app.lock:
                        if not app.fresh(0.5):
                            app.voice.update(None, fresh=False)
                        app.flush_voice_events()
                    stop.wait(0.1)
                    continue
            else:
                sample = next(source, None)
                if sample is None:
                    app.status = "Demo SINTÉTICA terminada" if app.mode == "demo" else "REPLAY terminado"
                    break
                if realtime and previous is not None:
                    delta = sample.time_s - previous.time_s if sample.session_id == previous.session_id else 0.1
                    if stop.wait(min(max(delta, 0), 2) / speed):
                        break
                app.status = f"Demo SINTÉTICA · x{speed:g}" if app.mode == "demo" else f"REPLAY · x{speed:g}"
            if app.annotations:
                sample = replace(sample, **app.annotations)
            app.add(sample)
            with app.lock:
                app.voice.update(sample, fresh=app.mode == "live" and app.fresh(),
                                 navigation=app.navigation())
                app.flush_voice_events()
            previous = sample
            if app.mode == "live":
                stop.wait(0.1)
    except (OSError, sqlite3.Error, ValueError) as error:
        app.status = f"Fuente detenida: {error}"
    finally:
        app.cache_time = -float("inf")
        app.running = False
        app.connected = False
        app.voice.update(None, fresh=False)
        try:
            source.close()
        except Exception as error:
            app.status = f"Fuente detenida: no se pudo cerrar el lector: {error}"
        finally:
            try:
                app.record(False)
            except (OSError, sqlite3.Error) as error:
                app.record_error = f"No se pudo cerrar la grabación: {error}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="4bis Racing Engineer — telemetría local, solo lectura")
    parser.add_argument("mode", choices=("demo", "replay", "live"))
    parser.add_argument("file", nargs="?", help="SQLite de entrada para replay")
    parser.add_argument("--recording-id")
    parser.add_argument("--record", metavar="SQLITE", help="Consentimiento explícito para grabar al iniciar")
    parser.add_argument("--storage", default="recordings/local.sqlite", help="Destino del botón Iniciar grabación")
    parser.add_argument("--retention-days", type=int, help="Retención optativa; por defecto conserva indefinidamente")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--speed", type=float, default=5)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--report", metavar="PREFIX", help="Exportar PREFIX.json y PREFIX.md al terminar")
    parser.add_argument("--voice", action="store_true", help="Activar avisos locales en voz española de Windows")
    parser.add_argument("--damage", choices=("healthy", "damaged", "unknown"), help="Anotación explícita del piloto; no es medición SDK")
    parser.add_argument("--traffic", choices=("clear", "traffic", "unknown"), help="Anotación explícita del piloto; no verifica rebufo")
    args = parser.parse_args(argv)
    if not 0 < args.speed <= 100 or not 0 <= args.seconds < 86400 or not 1 <= args.port <= 65535:
        parser.error("speed, seconds o port fuera de rango")
    if args.retention_days is not None and args.retention_days < 1:
        parser.error("retention-days debe ser positivo")
    if args.mode == "replay" and not args.file:
        parser.error("replay requiere archivo SQLite")
    if args.mode != "replay" and args.file:
        parser.error("El archivo de entrada es solo para replay")
    if args.headless and args.mode == "live" and not args.seconds:
        parser.error("live --headless requiere --seconds para una prueba acotada")
    if args.mode == "replay" and Path(args.file).resolve() == Path(args.record or args.storage).resolve():
        parser.error("Entrada replay y destino de grabación deben ser diferentes")
    annotations = {key: getattr(args, key) for key in ("damage", "traffic") if getattr(args, key) is not None}
    app = App(args.mode, args.record or args.storage, args.retention_days, annotations,
              review_path=args.file if args.mode == "replay" else None)
    if args.mode == "replay":
        try:
            events = load_events(args.file, args.recording_id)
            app.voice_events.extend(events)
            app.voice_event_count = len(events)
        except (OSError, sqlite3.Error, ValueError) as error:
            print(f"No se pudo abrir historial del replay: {error}")
            return 1
    if args.mode == "live":
        from .iracing import IRacingSource
        source = IRacingSource()
    elif args.mode == "demo":
        source = demo_samples()
    else:
        source = load_replay(args.file, args.recording_id)
    stop = threading.Event()
    server = None
    thread = None
    try:
        if not args.headless:
            server = HTTPServer(("127.0.0.1", args.port), handler_for(app))
        if args.record:
            app.record(True)
        if args.voice:
            app.voice.set_enabled(True)
        if args.headless:
            collect(app, source, stop, seconds=args.seconds, realtime=False)
        else:
            thread = threading.Thread(target=collect, args=(app, source, stop, args.speed, args.seconds), daemon=True)
            thread.start()
            print(f"4bis Racing Engineer: http://127.0.0.1:{args.port} · {args.mode.upper()} · Ctrl+C para cerrar", flush=True)
            server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"No se pudo iniciar: {error}")
        return 1
    finally:
        stop.set()
        if thread:
            thread.join(timeout=5)
            if thread.is_alive():
                app.status = "Fuente detenida: el lector no confirmó cierre dentro de 5 s"
        # El cierre del archivo no depende de que el SDK responda a tiempo.
        try:
            app.record(False)
        except (OSError, sqlite3.Error) as error:
            app.record_error = f"No se pudo cerrar la grabación: {error}"
        if server:
            server.server_close()
        app.voice.close()
        app.flush_voice_events()
    report = app.summary()
    if args.report:
        prefix = Path(args.report)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        prefix.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    print(json.dumps({"mode": app.mode, "status": app.status, "samples": app.total,
                      "record_error": app.record_error, "channels": app.channels}, ensure_ascii=False))
    return 1 if app.record_error or app.status.startswith("Fuente detenida:") else 0
