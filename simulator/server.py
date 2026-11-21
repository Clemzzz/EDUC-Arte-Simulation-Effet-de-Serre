"""Serveur HTTP local émulant l'ESP32 EDUC'Arte (zéro dépendance externe).

Lancement :
    python simulator/server.py
    python simulator/server.py --port 8080 --speed 10
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simulator.physics import VALID_SPEEDS, GreenhouseSimulator  # noqa: E402

WEB_DIR = PROJECT_ROOT / "web"

STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


class GreenhouseHTTPServer(ThreadingHTTPServer):
    """Serveur HTTP multi-thread avec boucle d'échantillonnage de la simulation."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        simulator: GreenhouseSimulator,
        web_dir: Path = WEB_DIR,
        sample_interval_s: float = 1.0,
        quiet: bool = False,
    ) -> None:
        self.simulator = simulator
        self.web_dir = web_dir
        self.sample_interval_s = sample_interval_s
        self.quiet = quiet
        self._stop_event = threading.Event()
        self._sampler_thread: threading.Thread | None = None

        super().__init__(server_address, GreenhouseRequestHandler)

        if self.sample_interval_s > 0:
            self._sampler_thread = threading.Thread(
                target=self._run_sampler,
                name="greenhouse-sampler",
                daemon=True,
            )
            self._sampler_thread.start()

    def _run_sampler(self) -> None:
        while not self._stop_event.wait(self.sample_interval_s):
            self.simulator.step(self.sample_interval_s)

    def shutdown(self) -> None:
        self._stop_event.set()
        super().shutdown()
        if self._sampler_thread is not None:
            self._sampler_thread.join(timeout=2.0)


class GreenhouseRequestHandler(BaseHTTPRequestHandler):
    """Gestionnaire HTTP exposant la même API que le firmware ESP32."""

    server: GreenhouseHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        if not self.server.quiet:
            super().log_message(format, *args)

    def _send_bytes(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _read_json_body(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("En-tête Content-Length invalide") from exc

        if length < 0 or length > 16_384:
            raise ValueError("Taille du corps JSON invalide")

        if length == 0:
            return {}

        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON invalide") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Un objet JSON est attendu")
        return parsed

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/data":
            self._send_json(HTTPStatus.OK, self.server.simulator.get_latest())
            return

        if path == "/api/history":
            self._send_json(
                HTTPStatus.OK,
                {
                    "mode": "simulation",
                    "samples": self.server.simulator.get_history(),
                },
            )
            return

        if path == "/api/export.csv":
            csv_body = self.server.simulator.export_csv().encode("utf-8")
            self._send_bytes(
                HTTPStatus.OK,
                "text/csv; charset=utf-8",
                csv_body,
                extra_headers={
                    "Content-Disposition": 'attachment; filename="educarte_effet_de_serre.csv"'
                },
            )
            return

        if path in STATIC_ROUTES:
            filename, content_type = STATIC_ROUTES[path]
            file_path = self.server.web_dir / filename
            if not file_path.is_file():
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"Fichier introuvable: {filename}"},
                )
                return
            self._send_bytes(HTTPStatus.OK, content_type, file_path.read_bytes())
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route introuvable"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/sim/config":
            try:
                body = self._read_json_body()
                lamp_on = body.get("lamp_on")
                wet_paper = body.get("wet_paper")
                speed = body.get("speed")

                if lamp_on is not None and not isinstance(lamp_on, bool):
                    raise ValueError("'lamp_on' doit être un booléen")
                if wet_paper is not None and not isinstance(wet_paper, bool):
                    raise ValueError("'wet_paper' doit être un booléen")
                if speed is not None and (
                    isinstance(speed, bool) or not isinstance(speed, int)
                ):
                    raise ValueError("'speed' doit être un entier (1, 10 ou 60)")

                updated = self.server.simulator.configure(
                    lamp_on=lamp_on,
                    wet_paper=wet_paper,
                    speed=speed,
                )
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.OK, updated)
            return

        if path == "/api/sim/reset":
            updated = self.server.simulator.reset()
            self._send_json(HTTPStatus.OK, updated)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route introuvable"})


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    simulator: GreenhouseSimulator | None = None,
    web_dir: Path = WEB_DIR,
    sample_interval_s: float = 1.0,
    quiet: bool = False,
) -> GreenhouseHTTPServer:
    sim = simulator if simulator is not None else GreenhouseSimulator()
    return GreenhouseHTTPServer(
        (host, port),
        simulator=sim,
        web_dir=web_dir,
        sample_interval_s=sample_interval_s,
        quiet=quiet,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serveur local simulant l'ESP32 + capteur DHT22 du projet EDUC'Arte."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute (défaut: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port HTTP (défaut: 8000)")
    parser.add_argument(
        "--speed",
        type=int,
        choices=VALID_SPEEDS,
        default=1,
        help="Vitesse initiale de simulation (1, 10 ou 60 ; défaut: 1)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Intervalle d'échantillonnage réel en secondes (défaut: 1.0)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    sim = GreenhouseSimulator(speed=args.speed)
    server = create_server(
        host=args.host,
        port=args.port,
        simulator=sim,
        sample_interval_s=args.interval,
    )
    url = f"http://{args.host}:{args.port}"
    print("============================================================")
    print("  EDUC'Arte — Simulateur ESP32 (Effet de serre sous cloche)")
    print("============================================================")
    print(f"  Interface web : {url}")
    print(f"  Export CSV    : {url}/api/export.csv")
    print("  Appuyez sur Ctrl+C pour arrêter le serveur.")
    print("============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du simulateur EDUC'Arte...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
