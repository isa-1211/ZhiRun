import unittest

from server import infer_server


class CaptureProvider:
    def __init__(self):
        self.sensor_data = None

    def fetch(self, _latitude, _longitude, sensor_data=None, offline=False):
        self.sensor_data = sensor_data
        return sensor_data


class SoilFrameValidationTests(unittest.TestCase):
    def test_quality_gate_keeps_missing_wind_direction_noncritical(self):
        quality = infer_server._input_quality({
            "n_concentration_g_l": 100, "p_concentration_g_l": 80, "k_concentration_g_l": 120,
            "soilMoist": 18, "soilTemp": 22, "soilPH": 7.8,
            "soilN": 1200, "soilP": 20, "soilK": 160,
        }, {})
        self.assertEqual(quality["soil_critical_missing"], [])
        self.assertNotIn("wind_direction", quality["missing"])

    def test_quality_gate_marks_missing_soil_as_irrigation_blocker(self):
        quality = infer_server._input_quality({
            "n_concentration_g_l": 100, "p_concentration_g_l": 80, "k_concentration_g_l": 120,
            "soilTemp": 22, "soilPH": 7.8,
            "soilN": 1200, "soilP": 20, "soilK": 160,
        }, {})
        self.assertIn("soil_moisture_20_pct", quality["soil_critical_missing"])

    def test_stale_soil_frame_is_not_accepted_for_automation(self):
        quality = infer_server._input_quality({
            "soilMoist": 42, "soilTemp": 24, "soilPH": 6.8,
            "n": 20, "p": 15, "k": 18, "soilStale": True,
        }, {})
        self.assertIn("soil_moisture_20_pct", quality["soil_critical_missing"])

    def test_complete_zero_soil_frame_is_invalid(self):
        self.assertTrue(infer_server.invalid_zero_soil_frame({
            "soilMoist": 0, "n": 0, "p": 0, "k": 0,
        }))

    def test_partial_or_nonzero_frame_is_not_invalid(self):
        self.assertFalse(infer_server.invalid_zero_soil_frame({"soilMoist": 0}))
        self.assertFalse(infer_server.invalid_zero_soil_frame({
            "soilMoist": 12.5, "n": 0, "p": 0, "k": 0,
        }))

    def test_invalid_frame_keeps_temperature_and_omits_soil_measurements(self):
        original_provider, original_defaults = infer_server._provider, infer_server._defaults
        provider = CaptureProvider()
        infer_server._provider, infer_server._defaults = provider, (40.84, 111.75)
        try:
            infer_server.environment_from_request({
                "soilMoist": 0, "soilTemp": 22.9, "soilPH": 9,
                "n": 0, "p": 0, "k": 0,
            }, "玉米")
        finally:
            infer_server._provider, infer_server._defaults = original_provider, original_defaults
        self.assertEqual(provider.sensor_data["soil_temperature_c"], 22.9)
        for key in ("soil_moisture_20_pct", "soil_ph",
                    "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg"):
            self.assertNotIn(key, provider.sensor_data)
        self.assertEqual(provider.sensor_data["source"]["soil_sensor"],
                         "invalid_zero_frame; regional prior applied")


class FarmAssessmentTests(unittest.TestCase):
    def test_band_score_is_continuous_and_uses_hard_bounds(self):
        self.assertEqual(infer_server._band_score(0.30, 0.66, 0.80, 0.30, 1.25), 0.0)
        self.assertEqual(infer_server._band_score(0.70, 0.66, 0.80, 0.30, 1.25), 100.0)
        self.assertEqual(infer_server._band_score(1.25, 0.66, 0.80, 0.30, 1.25), 0.0)

    def test_assessment_pauses_score_for_critical_soil_input(self):
        original_decide = infer_server.decide
        try:
            infer_server.decide = lambda _body: ({
                "crop": "玉米", "stage": "灌浆", "input_quality": {
                    "soil_critical_missing": ["soil_moisture_20_pct"], "missing": [], "invalid": [],
                }, "alerts": [], "execution_status": "safety_blocked",
            }, {"automatic_inputs": {"source": {}}})
            assessment = infer_server.assess_farm_condition({})
        finally:
            infer_server.decide = original_decide
        self.assertIsNone(assessment["score"])
        self.assertEqual(assessment["rating"], "数据不足")

    def test_assessment_uses_weighted_model_components(self):
        original_decide = infer_server.decide
        try:
            infer_server.decide = lambda _body: ({
                "crop": "玉米", "stage": "灌浆", "relative_field_capacity": 0.72,
                "dynamic_trigger_relative_fc": 0.66, "dynamic_target_relative_fc": 0.80,
                "soil_n_level": "medium", "soil_p_level": "medium", "soil_k_level": "medium",
                "predicted_environment": {"wind_max_m_s": 3, "temperature_mean_c": 24},
                "input_quality": {"soil_critical_missing": [], "missing": [], "invalid": []},
                "alerts": [], "execution_status": "not_needed",
            }, {"automatic_inputs": {"soil_moisture_20_pct": 45, "soil_ph": 6.8, "source": {}}})
            assessment = infer_server.assess_farm_condition({})
        finally:
            infer_server.decide = original_decide
        self.assertEqual(assessment["score"], 100)
        self.assertEqual(assessment["rating"], "良好")

    def test_extreme_sensor_moisture_cannot_hide_behind_other_good_inputs(self):
        original_decide = infer_server.decide
        try:
            def fake_decide(body):
                moisture = body.get("soilMoist")
                return ({"crop": "玉米", "stage": "灌浆", "relative_field_capacity": 1.0,
                         "dynamic_trigger_relative_fc": 0.66, "dynamic_target_relative_fc": 0.80,
                         "soil_n_level": "medium", "soil_p_level": "medium", "soil_k_level": "medium",
                         "predicted_environment": {"wind_max_m_s": 3, "temperature_mean_c": 24},
                         "input_quality": {"soil_critical_missing": [], "missing": [], "invalid": []},
                         "alerts": [], "execution_status": "not_needed"},
                        {"automatic_inputs": {"soil_moisture_20_pct": moisture, "soil_ph": 6.8,
                                               "source": {}}})
            infer_server.decide = fake_decide
            dry = infer_server.assess_farm_condition({"soilMoist": 0})
            saturated = infer_server.assess_farm_condition({"soilMoist": 100})
        finally:
            infer_server.decide = original_decide
        self.assertLessEqual(dry["score"], 35)
        self.assertLessEqual(saturated["score"], 35)


if __name__ == "__main__":
    unittest.main()
