import unittest

from server.zhirun_server import compact_board_prediction


class BoardPredictionTests(unittest.TestCase):
    def test_ready_job_is_flat_and_executable(self):
        compact = compact_board_prediction({
            "ok": True,
            "decision": {
                "execution_status": "ready",
                "irrigate": True,
                "fertigate": True,
                "irrigation_m3_mu": 3.25,
                "nitrogen_kg_mu": 0.8,
                "p2o5_kg_mu": 0.4,
                "k2o_kg_mu": 0.6,
                "dynamic_trigger_moisture_pct": 40.7,
            },
            "result": {
                "automatic_inputs": {"soil_moisture_pct": 38.2, "rain_next_2d_mm": 0.4},
                "job": {"targets_l": {"N": 8, "P": 5, "K": 6}, "outlet_run_s": 120},
            },
        })
        self.assertTrue(compact["can_execute"])
        self.assertEqual(compact["reason_code"], "ready")
        self.assertEqual(compact["n_target_l"], 8.0)
        self.assertEqual(compact["outlet_run_s"], 120.0)
        self.assertNotIn("result", compact)

    def test_safety_block_has_no_executable_job(self):
        compact = compact_board_prediction({
            "ok": True,
            "decision": {"execution_status": "safety_blocked", "irrigate": False},
            "result": {"job": {"targets_l": {}, "outlet_run_s": 0}},
        })
        self.assertFalse(compact["can_execute"])
        self.assertEqual(compact["reason_code"], "safety_blocked")

    def test_rain_hold_has_specific_reason(self):
        compact = compact_board_prediction({
            "ok": True,
            "decision": {
                "execution_status": "not_needed",
                "irrigate": False,
                "dynamic_trigger_moisture_pct": 40.7,
            },
            "result": {
                "automatic_inputs": {"soil_moisture_pct": 38.2, "rain_next_2d_mm": 5.5},
                "job": {"targets_l": {"N": 0, "P": 0, "K": 0}, "outlet_run_s": 0},
            },
        })
        self.assertFalse(compact["can_execute"])
        self.assertEqual(compact["reason_code"], "forecast_rain")


if __name__ == "__main__":
    unittest.main()
