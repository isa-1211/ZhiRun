import unittest

from scripts.build_job import HARDWARE, build_job
from scripts.controller import FlowController, SensorFrame, State
from scripts.recommend import recommend


def dry_potato_decision():
    return recommend("马铃薯", "块茎膨大", 18, 0, 5.5, 8, 1.0, 0, 0, 0, "low", "low", "low")


class JobTests(unittest.TestCase):
    def test_npk_job_uses_three_independent_meters(self):
        job = build_job(1.0, dry_potato_decision())
        self.assertEqual([dose["flow_meter"] for dose in job["doses"]], ["N_FLOW", "P_FLOW", "K_FLOW"])
        self.assertEqual(job["phases"][0]["state"], "DOSE_PARALLEL")
        self.assertEqual(job["phases"][1]["state"], "OUTLET_TRANSFER")

    def test_each_pump_stops_at_its_own_target_then_outlet_starts(self):
        job = build_job(0.01, dry_potato_decision())
        controller = FlowController(job, HARDWARE)
        frame = SensorFrame(dose_total_l={"A": 0, "B": 0, "C": 0},
                            dose_flow_l_min={"A": 1, "B": 1, "C": 1})
        outputs = controller.start(frame)
        self.assertTrue(outputs.n_pump and outputs.p_pump and outputs.k_pump)
        targets = job["targets_l"]
        outputs = controller.update(SensorFrame(
            dose_total_l={"A": targets["N"], "B": 0, "C": 0},
            dose_flow_l_min={"A": 0, "B": 1, "C": 1},
        ))
        self.assertFalse(outputs.n_pump)
        self.assertTrue(outputs.p_pump and outputs.k_pump)
        outputs = controller.update(SensorFrame(
            dose_total_l={"A": targets["N"], "B": targets["P"], "C": targets["K"]},
            dose_flow_l_min={"A": 0, "B": 0, "C": 0},
        ))
        self.assertEqual(controller.state, State.OUTLET)
        self.assertTrue(outputs.outlet_pump)
        self.assertFalse(outputs.n_pump or outputs.p_pump or outputs.k_pump)

    def test_flow_fault_turns_every_output_off(self):
        job = build_job(0.01, dry_potato_decision())
        controller = FlowController(job, HARDWARE)
        outputs = controller.start(SensorFrame(
            dose_total_l={"A": 0, "B": 0, "C": 0},
            dose_flow_l_min={"A": 0, "B": 1, "C": 1},
        ))
        self.assertEqual(controller.state, State.FAULT)
        self.assertFalse(any(outputs.as_dict().values()))


if __name__ == "__main__":
    unittest.main()
