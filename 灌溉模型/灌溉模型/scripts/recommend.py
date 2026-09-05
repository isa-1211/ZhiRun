"""呼和浩特四种露地作物的可解释水肥建议器。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "configs" / "crops.json").read_text(encoding="utf-8"))


def recommend(
    crop: str,
    stage: str,
    soil_moisture_pct: float,
    rain_forecast_mm: float,
    eto_mm: float,
    days_since_fertigation: int,
    soil_ec_ds_m: float | None = None,
    nitrogen_applied_in_stage_kg_mu: float = 0.0,
    p2o5_applied_in_stage_kg_mu: float = 0.0,
    k2o_applied_in_stage_kg_mu: float = 0.0,
    soil_test_n_level: str = "medium",
    soil_test_p_level: str = "medium",
    soil_test_k_level: str = "medium",
    trigger_moisture_override: float | None = None,
    target_moisture_override: float | None = None,
) -> dict:
    crop_cfg = CONFIG["crops"][crop]
    stage_cfg = crop_cfg["stages"][stage]
    soil_moisture_pct = float(soil_moisture_pct)
    if not 0 <= soil_moisture_pct <= 100:
        raise ValueError("soil_moisture_pct必须在0到100之间")
    trigger = stage_cfg["trigger_moisture_pct"] if trigger_moisture_override is None else float(trigger_moisture_override)
    target = stage_cfg["target_moisture_pct"] if target_moisture_override is None else float(target_moisture_override)
    if not 0 <= trigger <= 100:
        raise ValueError("trigger_moisture_pct必须在0到100之间")
    if not 0 <= target <= 100 or target < trigger:
        raise ValueError("target_moisture_pct必须不小于trigger_moisture_pct且在0到100之间")
    # The single installed probe is the sole soil-water input. The crop stage
    # depth is only used to convert a measured percentage deficit to volume.
    effective_depth_m = {
        "苗期": 0.30, "拔节大喇叭口": 0.60, "抽雄吐丝": 0.80, "灌浆": 0.90,
        "成熟": 0.90, "块茎形成": 0.35, "块茎膨大": 0.45, "叶丛快速生长": 0.50,
        "块根膨大": 0.60, "糖分积累": 0.60, "现蕾": 0.65, "开花": 0.90,
    }.get(stage, 0.50)
    moisture_deficit_mm = max(0.0, (target - soil_moisture_pct) / 100 * effective_depth_m * 1000)
    et_need_mm = max(0.0, stage_cfg["kc"] * eto_mm - rain_forecast_mm * CONFIG["defaults"]["effective_rain_fraction"])
    should_irrigate = soil_moisture_pct <= trigger and rain_forecast_mm < max(5.0, et_need_mm)
    gross_mm = max(moisture_deficit_mm, et_need_mm) / CONFIG["defaults"]["application_efficiency"] if should_irrigate else 0.0
    irrigation = min(gross_mm * 0.667, CONFIG["defaults"]["max_single_irrigation_m3_mu"])

    stage_n_budget = crop_cfg["season_n_kg_mu"] * stage_cfg["n_share"]
    stage_p_budget = crop_cfg["season_p2o5_kg_mu"] * stage_cfg["p_share"]
    stage_k_budget = crop_cfg["season_k2o_kg_mu"] * stage_cfg["k_share"]
    stage_n_remaining = max(0.0, stage_n_budget - nitrogen_applied_in_stage_kg_mu)
    stage_p_remaining = max(0.0, stage_p_budget - p2o5_applied_in_stage_kg_mu)
    stage_k_remaining = max(0.0, stage_k_budget - k2o_applied_in_stage_kg_mu)
    ec_block = soil_ec_ds_m is not None and soil_ec_ds_m >= 2.0
    level_factor = {"low": 1.0, "medium": 0.65, "high": 0.0}
    fertilizer_event_due = irrigation > 0 and days_since_fertigation >= 7 and not ec_block
    n_rate = min(2.5, stage_n_remaining * level_factor[soil_test_n_level]) if fertilizer_event_due else 0.0
    p_rate = min(1.5, stage_p_remaining * level_factor[soil_test_p_level]) if fertilizer_event_due else 0.0
    k_rate = min(3.0, stage_k_remaining * level_factor[soil_test_k_level]) if fertilizer_event_due else 0.0
    fertigation_due = any(rate > 0 for rate in (n_rate, p_rate, k_rate))
    alerts = []
    if ec_block:
        alerts.append("土壤EC达到2.0 dS/m或以上：本次禁止注肥，先核查盐分和水质")
    if irrigation >= CONFIG["defaults"]["max_single_irrigation_m3_mu"]:
        alerts.append("缺水量超过单次上限：建议分两次灌，间隔后复测湿润锋")
    if crop == "甜菜" and stage in {"块根膨大", "糖分积累"}:
        alerts.append("甜菜后期控氮，优先保护含糖率")
    if crop in {"马铃薯", "向日葵"} and stage == "成熟":
        alerts.append("成熟期避免过灌，结合计划收获日人工确认")
    return {
        "crop": crop,
        "stage": stage,
        "soil_moisture_pct": round(soil_moisture_pct, 2),
        "moisture_trigger_pct": round(trigger, 2),
        "moisture_target_pct": round(target, 2),
        "irrigate": should_irrigate,
        "irrigation_m3_mu": round(irrigation, 1),
        "fertigate": fertigation_due,
        "nitrogen_kg_mu": round(n_rate, 2),
        "p2o5_kg_mu": round(p_rate, 2),
        "k2o_kg_mu": round(k_rate, 2),
        "stage_n_budget_kg_mu": round(stage_n_budget, 2),
        "stage_n_remaining_before_this_job_kg_mu": round(stage_n_remaining, 2),
        "stage_p2o5_remaining_before_this_job_kg_mu": round(stage_p_remaining, 2),
        "stage_k2o_remaining_before_this_job_kg_mu": round(stage_k_remaining, 2),
        "alerts": alerts,
        "confidence": "低（公共数据迁移基线，尚未用本地田间数据校准）",
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="呼和浩特水肥一体化基线建议")
    p.add_argument("--crop", choices=CONFIG["crops"].keys())
    p.add_argument("--stage")
    p.add_argument("--soil-moisture", type=float)
    p.add_argument("--rain-forecast", type=float, default=0.0)
    p.add_argument("--eto", type=float, default=5.0)
    p.add_argument("--days-since-fertigation", type=int, default=8)
    p.add_argument("--soil-ec", type=float)
    p.add_argument("--nitrogen-applied-in-stage", type=float, default=0.0,
                   help="当前生育阶段此前已经施入的纯氮，kg/亩")
    p.add_argument("--p2o5-applied-in-stage", type=float, default=0.0)
    p.add_argument("--k2o-applied-in-stage", type=float, default=0.0)
    p.add_argument("--soil-test-n-level", choices=["low", "medium", "high"], default="medium")
    p.add_argument("--soil-test-p-level", choices=["low", "medium", "high"], default="medium")
    p.add_argument("--soil-test-k-level", choices=["low", "medium", "high"], default="medium")
    p.add_argument("--demo", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.demo:
        examples = [("马铃薯", "块茎膨大", [18, 21]), ("甜菜", "叶丛快速生长", [19, 22]),
                    ("玉米", "抽雄吐丝", [19, 21, 23]), ("向日葵", "开花", [19, 21, 23])]
        for crop, stage, moisture in examples:
            print(json.dumps(recommend(crop, stage, moisture[0], 0, 5.5, 8), ensure_ascii=False))
        return
    if not (args.crop and args.stage and args.soil_moisture is not None):
        raise SystemExit("非 demo 模式必须提供 --crop、--stage 和 --soil-moisture")
    if args.stage not in CONFIG["crops"][args.crop]["stages"]:
        valid = "、".join(CONFIG["crops"][args.crop]["stages"])
        raise SystemExit(f"{args.crop} 的 stage 应为：{valid}")
    result = recommend(args.crop, args.stage, args.soil_moisture, args.rain_forecast,
                       args.eto, args.days_since_fertigation, args.soil_ec, args.nitrogen_applied_in_stage,
                       args.p2o5_applied_in_stage, args.k2o_applied_in_stage,
                       args.soil_test_n_level, args.soil_test_p_level, args.soil_test_k_level)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
