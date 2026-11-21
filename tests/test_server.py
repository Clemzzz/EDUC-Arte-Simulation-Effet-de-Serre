import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from simulator.physics import GreenhouseSimulator
from simulator.server import create_server


class TestSimulatorServer(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = GreenhouseSimulator(seed=123)
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            simulator=self.sim,
            sample_interval_s=0.0,  # désactive le tick automatique pour des tests déterministes
            quiet=True,
        )
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def _get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers.items()), exc.read()
            finally:
                exc.close()

    def _post_raw(self, path: str, raw: bytes) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers.items()), exc.read()
            finally:
                exc.close()

    def _post_json(self, path: str, payload: object) -> tuple[int, dict[str, str], bytes]:
        return self._post_raw(path, json.dumps(payload).encode("utf-8"))

    def test_get_api_data_returns_expected_json(self):
        status, headers, body = self._get("/api/data")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["mode"], "simulation")
        self.assertIn("temperature_c", data)
        self.assertIn("humidity_pct", data)
        self.assertEqual(data["sample_count"], 1)

    def test_get_api_history_and_export_csv(self):
        self.sim.step(5.0)
        self.sim.step(5.0)

        status, _, body = self._get("/api/history")
        self.assertEqual(status, 200)
        history = json.loads(body.decode("utf-8"))
        self.assertEqual(history["mode"], "simulation")
        self.assertEqual(len(history["samples"]), 3)

        csv_status, csv_headers, csv_body = self._get("/api/export.csv")
        self.assertEqual(csv_status, 200)
        self.assertIn("text/csv", csv_headers.get("Content-Type", ""))
        self.assertIn(
            "educarte_effet_de_serre.csv",
            csv_headers.get("Content-Disposition", ""),
        )
        lines = csv_body.decode("utf-8").strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(
            lines[0],
            "timestamp,elapsed_s,temperature_c,humidity_pct,lamp_on,wet_paper",
        )

    def test_post_sim_config_updates_state_and_validates_input(self):
        status, _, body = self._post_json(
            "/api/sim/config",
            {"lamp_on": False, "wet_paper": False, "speed": 60},
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["lamp_on"])
        self.assertFalse(data["wet_paper"])
        self.assertEqual(data["speed"], 60)

        for bad_payload in [
            {"speed": 999},
            {"speed": True},
            {"lamp_on": "yes"},
            {"wet_paper": 1},
        ]:
            bad_status, _, _ = self._post_json("/api/sim/config", bad_payload)
            self.assertEqual(bad_status, 400, f"Attendu 400 pour {bad_payload}")

        invalid_json_status, _, _ = self._post_raw("/api/sim/config", b"{not-json")
        self.assertEqual(invalid_json_status, 400)

    def test_post_sim_reset_clears_history(self):
        self.sim.step(10.0)
        self.sim.step(10.0)
        self.assertEqual(len(self.sim.get_history()), 3)

        status, _, body = self._post_json("/api/sim/reset", {})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["elapsed_s"], 0.0)
        self.assertEqual(data["sample_count"], 1)

    def test_serves_web_assets_and_blocks_unknown_paths(self):
        for path, expected_ct in [
            ("/", "text/html"),
            ("/style.css", "text/css"),
            ("/app.js", "application/javascript"),
        ]:
            status, headers, body = self._get(path)
            self.assertEqual(status, 200, f"Échec sur {path}")
            self.assertIn(expected_ct, headers.get("Content-Type", ""))
            self.assertGreater(len(body), 0)

        status404, _, _ = self._get("/../simulator/server.py")
        self.assertEqual(status404, 404)

    def test_background_sampler_records_samples_automatically(self):
        sim = GreenhouseSimulator(seed=7, speed=10)
        srv = create_server(
            host="127.0.0.1",
            port=0,
            simulator=sim,
            sample_interval_s=0.05,
            quiet=True,
        )
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            time.sleep(0.2)
            self.assertGreaterEqual(sim.get_latest()["sample_count"], 3)
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
