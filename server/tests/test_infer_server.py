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
        self.assertIn("soil_moisture_pct", quality["soil_critical_missing"])

    def test_stale_soil_frame_is_not_accepted_for_automation(self):
        quality = infer_server._input_quality({
            "soilMoist": 42, "soilTemp": 24, "soilPH": 6.8,
            "n": 20, "p": 15, "k": 18, "soilStale": True,
        }, {})
        self.assertIn("soil_moisture_pct", quality["soil_critical_missing"])

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
        for key in ("soil_moisture_pct", "soil_ph",
                    "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg"):
            self.assertNotIn(key, provider.sensor_data)
        self.assertEqual(provider.sensor_data["source"]["soil_sensor"],
                         "invalid_zero_frame; automation blocked")


class FarmAssessmentTests(unittest.TestCase):
    def test_assessment_reports_readiness_for_critical_soil_input(self):
        original_decide = infer_server.decide
        try:
            infer_server.decide = lambda _body: ({
                "crop": "玉米", "stage": "灌浆", "input_quality": {
                    "soil_critical_missing": ["soil_moisture_pct"], "missing": [], "invalid": [],
                }, "alerts": [], "execution_status": "safety_blocked",
            }, {"automatic_inputs": {"source": {}}})
            assessment = infer_server.assess_farm_condition({})
        finally:
            infer_server.decide = original_decide
        self.assertEqual(assessment["assessment_type"], "model_readiness_v1")
        self.assertIn("安全拦截", assessment["summary"])
        self.assertNotIn("score", assessment)
        self.assertNotIn("rating", assessment)
        self.assertNotIn("components", assessment)

    def test_assessment_has_no_obsolete_score_fields(self):
        original_decide = infer_server.decide
        try:
            infer_server.decide = lambda _body: ({
                "crop": "玉米", "stage": "灌浆", "dynamic_trigger_moisture_pct": 30,
                "dynamic_target_moisture_pct": 55,
                "input_quality": {"soil_critical_missing": [], "missing": [], "invalid": []},
                "alerts": [], "execution_status": "not_needed",
            }, {"automatic_inputs": {"soil_moisture_pct": 45, "source": {}}})
            assessment = infer_server.assess_farm_condition({})
        finally:
            infer_server.decide = original_decide
        for key in ("score", "rating", "components"):
            self.assertNotIn(key, assessment)


if __name__ == "__main__":
    unittest.main()
