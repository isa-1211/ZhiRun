"""Run large, reproducible controller simulations for manuscript evidence."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "灌溉模型" / "灌溉模型"
sys.path.insert(0, str(MODEL_ROOT))

from scripts.build_job import HARDWARE  # noqa: E402
from scripts.controller import FlowController, SensorFrame, State  # noqa: E402

OUT = ROOT / "paper_figures" / "controller_monte_carlo_metrics.json"


def job_from_targets(targets: dict[str, float], outlet_s: int) -> dict:
    return {"targets_l": targets, "outlet_run_s": outlet_s}


def run_normal(rng: np.random.Generator) -> dict:
    targets = {k: float(rng.uniform(0.1, 4.0)) for k in "NPK"}
    flows = {k: float(rng.uniform(0.08, 4.5)) for k in "NPK"}
    job = job_from_targets(targets, int(rng.integers(3, 31)))
    controller = FlowController(job, copy.deepcopy(HARDWARE))
    totals = {k: 0.0 for k in "NPK"}
    frame = SensorFrame(dose_total_l=totals.copy(), dose_flow_l_min=flows.copy(), timestamp_s=0.0)
    outputs = controller.start(frame)
    overlap = False; premature = False; steps = 0
    while controller.state not in {State.COMPLETE, State.FAULT} and steps < 5000:
        steps += 1
        active = {"N": outputs.n_pump, "P": outputs.p_pump, "K": outputs.k_pump}
        overlap |= outputs.outlet_pump and any(active.values())
        for k, on in active.items():
            if on: totals[k] += flows[k] / 60.0
            if not on and totals[k] + 1e-12 < targets[k]: premature = True
        outputs = controller.update(SensorFrame(
            dose_total_l=totals.copy(),
            dose_flow_l_min={k: flows[k] if active[k] else 0.0 for k in "NPK"},
            timestamp_s=float(steps),
        ))
    tolerance = max((flows[k] / 60.0 for k in "NPK"))
    target_error = max(max(0.0, totals[k] - targets[k]) for k in "NPK")
    all_off = not any(outputs.as_dict().values())
    return {"complete": controller.state == State.COMPLETE, "overlap": overlap,
            "premature": premature, "all_off": all_off, "max_overshoot_l": target_error,
            "overshoot_bound_ok": target_error <= tolerance + 1e-9}


def run_fault(rng: np.random.Generator, emergency=False) -> dict:
    targets = {k: float(rng.uniform(0.2, 2.0)) for k in "NPK"}
    controller = FlowController(job_from_targets(targets, 10), copy.deepcopy(HARDWARE))
    frame = SensorFrame(dose_total_l={k: 0 for k in "NPK"},
                        dose_flow_l_min={"N": 1.0, "P": 1.0, "K": 1.0}, timestamp_s=0.0)
    outputs = controller.start(frame)
    if emergency:
        outputs = controller.update(SensorFrame(dose_total_l={k: 0 for k in "NPK"},
                                                dose_flow_l_min={"N": 1.0, "P": 1.0, "K": 1.0},
                                                timestamp_s=1.0, emergency_stop=True))
    else:
        bad = str(rng.choice(list("NPK")))
        flow = {"N": 1.0, "P": 1.0, "K": 1.0}; flow[bad] = 0.0
        outputs = controller.update(SensorFrame(dose_total_l={k: 0 for k in "NPK"},
                                                dose_flow_l_min=flow, timestamp_s=1.0))
    return {"fault": controller.state == State.FAULT, "all_off": not any(outputs.as_dict().values())}


def main():
    rng = np.random.default_rng(20260908)
    n_normal, n_no_flow, n_emergency = 10_000, 2_000, 2_000
    normal = [run_normal(rng) for _ in range(n_normal)]
    no_flow = [run_fault(rng, False) for _ in range(n_no_flow)]
    emergency = [run_fault(rng, True) for _ in range(n_emergency)]
    metrics = {
        "seed": 20260908,
        "total_scenarios": n_normal + n_no_flow + n_emergency,
        "normal": {"n": n_normal,
                   "completion_rate": float(np.mean([x["complete"] for x in normal])),
                   "safety_timeout_rate": float(np.mean([not x["complete"] for x in normal])),
                   "safety_timeout_count": int(sum(not x["complete"] for x in normal)),
                   "outlet_overlap_rate": float(np.mean([x["overlap"] for x in normal])),
                   "premature_stop_rate": float(np.mean([x["premature"] for x in normal])),
                   "all_outputs_off_at_end_rate": float(np.mean([x["all_off"] for x in normal])),
                   "overshoot_bound_success_rate": float(np.mean([x["overshoot_bound_ok"] for x in normal])),
                   "max_observed_overshoot_l": float(max(x["max_overshoot_l"] for x in normal))},
        "no_flow_fault": {"n": n_no_flow,
                          "fault_detection_rate": float(np.mean([x["fault"] for x in no_flow])),
                          "all_outputs_off_rate": float(np.mean([x["all_off"] for x in no_flow]))},
        "emergency_stop": {"n": n_emergency,
                           "fault_detection_rate": float(np.mean([x["fault"] for x in emergency])),
                           "all_outputs_off_rate": float(np.mean([x["all_off"] for x in emergency]))},
    }
    OUT.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
