import unittest

from simulator.physics import GreenhouseSimulator


class TestGreenhouseSimulator(unittest.TestCase):
    def test_initial_state_matches_ambient_and_records_initial_sample(self):
        sim = GreenhouseSimulator(seed=42)
        latest = sim.get_latest()

        self.assertEqual(latest["elapsed_s"], 0.0)
        self.assertAlmostEqual(latest["temperature_c"], 21.0, delta=0.5)
        self.assertAlmostEqual(latest["humidity_pct"], 48.0, delta=1.0)
        self.assertTrue(latest["lamp_on"])
        self.assertTrue(latest["wet_paper"])
        self.assertEqual(latest["speed"], 1)
        self.assertEqual(latest["sample_count"], 1)
        self.assertEqual(len(sim.get_history()), 1)

    def test_lamp_on_with_wet_paper_heats_and_raises_humidity(self):
        sim = GreenhouseSimulator(seed=42)
        initial = sim.get_latest()

        # Simulate 10 minutes (600s) of lamp + wet paper
        for _ in range(60):
            sim.step(10.0)

        after = sim.get_latest()
        self.assertGreater(after["temperature_c"], initial["temperature_c"] + 10.0)
        self.assertGreater(after["humidity_pct"], initial["humidity_pct"] + 20.0)
        self.assertLessEqual(after["humidity_pct"], 98.0)
        self.assertEqual(after["elapsed_s"], 600.0)
        self.assertEqual(after["sample_count"], 61)

    def test_lamp_on_without_wet_paper_heats_and_lowers_relative_humidity(self):
        sim = GreenhouseSimulator(seed=42, lamp_on=True, wet_paper=False)
        initial = sim.get_latest()

        # Simulate 10 minutes without damp paper: warmer air holds more water -> RH drops
        for _ in range(60):
            sim.step(10.0)

        after = sim.get_latest()
        self.assertGreater(after["temperature_c"], initial["temperature_c"] + 10.0)
        self.assertLess(after["humidity_pct"], initial["humidity_pct"] - 10.0)
        self.assertGreaterEqual(after["humidity_pct"], 10.0)

    def test_lamp_off_cools_down_toward_ambient(self):
        sim = GreenhouseSimulator(seed=42, lamp_on=True, wet_paper=True)
        for _ in range(60):
            sim.step(10.0)
        hot_temp = sim.get_latest()["temperature_c"]

        sim.configure(lamp_on=False)
        for _ in range(90):
            sim.step(10.0)
        cooled_temp = sim.get_latest()["temperature_c"]

        self.assertLess(cooled_temp, hot_temp - 8.0)
        self.assertGreaterEqual(cooled_temp, 20.0)

    def test_wet_paper_gradually_dries_over_long_heated_run(self):
        sim = GreenhouseSimulator(seed=42, lamp_on=True, wet_paper=True)
        initial_moisture = sim.get_latest()["paper_moisture_pct"]

        for _ in range(180):
            sim.step(10.0)  # 30 minutes simulated

        later_moisture = sim.get_latest()["paper_moisture_pct"]
        self.assertLess(later_moisture, initial_moisture - 20.0)
        self.assertGreaterEqual(later_moisture, 0.0)

    def test_speed_multiplier_scales_simulated_time(self):
        sim = GreenhouseSimulator(seed=42)
        sim.configure(speed=60)
        sim.step(1.0)  # 1 real second at x60 -> 60 simulated seconds

        self.assertEqual(sim.get_latest()["elapsed_s"], 60.0)

    def test_configure_rejects_invalid_speed(self):
        sim = GreenhouseSimulator(seed=42)
        with self.assertRaises(ValueError):
            sim.configure(speed=5)

    def test_reset_restores_initial_state_and_clears_history(self):
        sim = GreenhouseSimulator(seed=42)
        for _ in range(20):
            sim.step(10.0)
        self.assertGreater(len(sim.get_history()), 1)

        sim.reset()
        latest = sim.get_latest()
        self.assertEqual(latest["elapsed_s"], 0.0)
        self.assertEqual(latest["sample_count"], 1)
        self.assertEqual(len(sim.get_history()), 1)
        self.assertAlmostEqual(latest["temperature_c"], 21.0, delta=0.5)

    def test_export_csv_format_and_rows(self):
        sim = GreenhouseSimulator(seed=42)
        sim.step(2.0)
        sim.step(2.0)

        csv_text = sim.export_csv()
        lines = [line for line in csv_text.strip().splitlines() if line]
        self.assertEqual(
            lines[0],
            "timestamp,elapsed_s,temperature_c,humidity_pct,lamp_on,wet_paper",
        )
        self.assertEqual(len(lines), 4)  # header + 3 samples (t=0, 2, 4)
        cols = lines[1].split(",")
        self.assertEqual(len(cols), 6)
        self.assertEqual(cols[1], "0.0")
        self.assertEqual(cols[4], "1")
        self.assertEqual(cols[5], "1")


if __name__ == "__main__":
    unittest.main()
