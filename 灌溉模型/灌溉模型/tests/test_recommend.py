import unittest

from scripts.recommend import recommend


class RecommendTests(unittest.TestCase):
    def test_dry_potato_triggers_irrigation(self):
        out = recommend("马铃薯", "块茎膨大", 18, 0, 5.5, 8, 1.0, 0)
        self.assertTrue(out["irrigate"])
        self.assertGreater(out["irrigation_m3_mu"], 0)
        self.assertLessEqual(out["nitrogen_kg_mu"], out["stage_n_remaining_before_this_job_kg_mu"])

    def test_rain_blocks_irrigation(self):
        out = recommend("玉米", "抽雄吐丝", 16, 20, 5.5, 8)
        self.assertFalse(out["irrigate"])

    def test_ec_blocks_fertilizer(self):
        out = recommend("马铃薯", "块茎膨大", 18, 0, 5.5, 8, 2.1)
        self.assertTrue(out["irrigate"])
        self.assertFalse(out["fertigate"])

    def test_stage_budget_cannot_be_exceeded(self):
        out = recommend("向日葵", "开花", 16, 0, 5.5, 8, 1.0, 0.75)
        self.assertLessEqual(out["nitrogen_kg_mu"], 0.05 + 1e-9)

    def test_high_soil_p_and_k_blocks_those_tanks(self):
        out = recommend("马铃薯", "块茎膨大", 18, 0, 5.5, 8, 1.0, 0, 0, 0, "medium", "high", "high")
        self.assertEqual(out["p2o5_kg_mu"], 0)
        self.assertEqual(out["k2o_kg_mu"], 0)


if __name__ == "__main__":
    unittest.main()
