"""Convert a model decision into the four-relay fertigation work order."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .recommend import recommend, CONFIG
except ImportError:
    from recommend import recommend, CONFIG


ROOT = Path(__file__).resolve().parents[1]
HARDWARE = json.loads((ROOT / "configs" / "hardware.json").read_text(encoding="utf-8"))


def build_job(area_mu: float, decision: dict, hardware: dict = HARDWARE) -> dict:
    if area_mu <= 0:
        raise ValueError("area_mu must be greater than zero")
    water_l = max(0.0, float(decision["irrigation_m3_mu"]) * area_mu * 1000)
    nutrient_keys = {"N": "nitrogen_kg_mu", "P2O5": "p2o5_kg_mu", "K2O": "k2o_kg_mu"}
    line_names = {"A": "N", "B": "P", "C": "K"}
    doses = []
    targets = {"N": 0.0, "P": 0.0, "K": 0.0}
    for line_id, line in hardware["fertilizer_lines"].items():
        concentration = float(line["concentration_g_l"])
        if concentration <= 0:
            raise ValueError("fertilizer concentration must be positive")
        nutrient_kg = float(decision[nutrient_keys[line["nutrient"]]]) * area_mu
        target_l = nutrient_kg * 1000 / concentration if water_l > 0 and nutrient_kg > 0 else 0.0
        element = line_names[line_id]
        targets[element] = round(target_l, 3)
        if target_l > 0:
            doses.append({
                "line": line_id,
                "valve": line_id,
                "element": element,
                "pump": line["pump"],
                "flow_meter": line["flow_meter"],
                "nutrient": line["nutrient"],
                "target_nutrient_kg": round(nutrient_kg, 3),
                "target_solution_l": round(target_l, 3),
                "close_at_meter_l": round(target_l, 3),
                "concentration_g_l": concentration,
                "pulses_per_liter": float(line["pulses_per_liter"]),
            })

    main_flow = max(0.01, float(hardware.get("main", {}).get("default_flow_l_min", 60.0)))
    outlet_run_s = round(water_l / main_flow * 60) if water_l > 0 else 0
    outlet_cfg = hardware.get("outlet_pump", {})
    if outlet_run_s:
        outlet_run_s = int(max(
            float(outlet_cfg.get("minimum_duration_s", 1)),
            min(outlet_run_s, float(outlet_cfg.get("maximum_duration_s", 7200))),
        ))

    phases = []
    if doses:
        phases.append({
            "state": "DOSE_PARALLEL",
            "targets_l": targets,
            "open_outputs": [dose["pump"] for dose in doses],
            "close_condition": "each flow meter reaches its own target",
        })
    if outlet_run_s:
        phases.append({
            "state": "OUTLET_TRANSFER",
            "outlet_run_s": outlet_run_s,
            "open_outputs": ["OUTLET_PUMP"],
            "start_condition": "all N/P/K dosing pumps are stopped",
        })
    phases.append({"state": "COMPLETE", "open_outputs": []})
    return {
        "schema": "four_relay_independent_flow_v1",
        "area_mu": area_mu,
        "crop": decision["crop"],
        "stage": decision["stage"],
        "target_main_water_l": round(water_l, 1),
        "targets_l": targets,
        "doses": doses,
        "outlet_run_s": outlet_run_s,
        "estimated_main_seconds": outlet_run_s,
        "phases": phases,
        "interlocks": [
            "N/P/K pumps stop independently when their own meter reaches target",
            "a running fertilizer pump faults if its meter reports no flow",
            "the outlet pump starts only after every fertilizer pump is off",
            "stop, timeout, or fault turns all four relays off",
        ],
        "hardware_warning": hardware["warning"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crop", required=True, choices=CONFIG["crops"].keys())
    parser.add_argument("--stage", required=True)
    parser.add_argument("--area-mu", type=float, required=True)
    parser.add_argument("--soil-moisture", type=float, required=True)
    parser.add_argument("--rain-forecast", type=float, default=0)
    parser.add_argument("--eto", type=float, required=True)
    args = parser.parse_args()
    decision = recommend(args.crop, args.stage, args.soil_moisture,
                         args.rain_forecast, args.eto, 8)
    print(json.dumps(build_job(args.area_mu, decision), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
