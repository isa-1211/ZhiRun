"""训练环境驱动的四作物水肥策略蒸馏模型（不是产量最优模型）。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

try:
    from .recommend import CONFIG, recommend
except ImportError:
    from recommend import CONFIG, recommend


ROOT = Path(__file__).resolve().parents[1]
WEATHER = ROOT / "data" / "weather" / "hohhot_nasa_power_daily.csv"
SOILS = json.loads((ROOT / "configs" / "soil_profiles.json").read_text(encoding="utf-8"))["profiles"]
OUT = ROOT / "data" / "processed" / "policy_v2_samples.csv.gz"
MODEL = ROOT / "models" / "hohhot_fertigation_policy_v2.joblib"
METRICS = ROOT / "models" / "policy_v2_metrics.json"

CATEGORICAL = ["crop", "stage", "soil_n_level", "soil_p_level", "soil_k_level"]
NUMERIC = [
    "doy", "t_mean", "t_max", "t_min", "rh", "wind", "radiation", "rain_today",
    "rain_next_2d", "eto", "gdd10_14d", "rain_7d", "eto_7d", "dry_days",
    "soil_moisture_pct", "moisture_trigger_pct", "moisture_target_pct", "soil_ec", "soil_ph",
    "days_since_fertigation", "n_applied_stage", "p_applied_stage", "k_applied_stage",
    "kc",
    "n_remaining", "p_remaining", "k_remaining", "n_level_factor", "p_level_factor", "k_level_factor",
    "fertilizer_interval_ready", "ec_block", "latitude", "longitude", "co2_ppm",
    "soil_temperature_c", "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg", "light_lux", "rain_24h_mm",
]
TARGETS = ["water_m3_mu", "n_kg_mu", "p2o5_kg_mu", "k2o_kg_mu"]

CALENDAR = {
    "马铃薯": [(121, 161, "苗期"), (162, 186, "块茎形成"), (187, 232, "块茎膨大"), (233, 258, "成熟")],
    "甜菜": [(115, 156, "苗期"), (157, 186, "叶丛快速生长"), (187, 237, "块根膨大"), (238, 274, "糖分积累")],
    "玉米": [(121, 166, "苗期"), (167, 196, "拔节大喇叭口"), (197, 217, "抽雄吐丝"), (218, 253, "灌浆"), (254, 274, "成熟")],
    "向日葵": [(121, 172, "苗期"), (173, 196, "现蕾"), (197, 217, "开花"), (218, 248, "灌浆"), (249, 268, "成熟")],
}


def stage_for(crop: str, doy: int) -> str | None:
    for start, end, stage in CALENDAR[crop]:
        if start <= doy <= end:
            return stage
    return None


def fao56_eto(row: pd.Series, latitude: float = 40.72, elevation: float = 1070.0) -> float:
    """由NASA POWER逐日变量计算FAO-56 Penman-Monteith ET0。"""
    doy = row.name.dayofyear
    t, tmax, tmin = row.T2M, row.T2M_MAX, row.T2M_MIN
    rh, u2, rs = row.RH2M, row.WS2M, row.ALLSKY_SFC_SW_DWN
    es_max = 0.6108 * math.exp(17.27 * tmax / (tmax + 237.3))
    es_min = 0.6108 * math.exp(17.27 * tmin / (tmin + 237.3))
    es = (es_max + es_min) / 2
    ea = es * rh / 100
    delta = 4098 * (0.6108 * math.exp(17.27 * t / (t + 237.3))) / ((t + 237.3) ** 2)
    pressure = 101.3 * ((293 - 0.0065 * elevation) / 293) ** 5.26
    gamma = 0.000665 * pressure
    phi = math.radians(latitude)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    decl = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(decl))))
    ra = 24 * 60 / math.pi * 0.0820 * dr * (ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws))
    rso = (0.75 + 2e-5 * elevation) * ra
    rns = 0.77 * rs
    sigma = 4.903e-9
    rnl = sigma * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * math.sqrt(max(ea, 0))) * (1.35 * min(rs / max(rso, 0.1), 1.0) - 0.35)
    rn = rns - rnl
    value = (0.408 * delta * rn + gamma * (900 / (t + 273)) * u2 * (es - ea)) / (delta + gamma * (1 + 0.34 * u2))
    return max(0.0, value)


def weather_features() -> pd.DataFrame:
    df = pd.read_csv(WEATHER, parse_dates=["date"]).set_index("date").sort_index()
    df = df.replace(-999, np.nan).interpolate(limit=3).dropna()
    df["eto"] = df.apply(fao56_eto, axis=1)
    df["rain_next_2d"] = df.PRECTOTCORR.shift(-1).fillna(0) + df.PRECTOTCORR.shift(-2).fillna(0)
    df["rain_7d"] = df.PRECTOTCORR.rolling(7, min_periods=1).sum()
    df["eto_7d"] = df.eto.rolling(7, min_periods=1).sum()
    df["gdd10_14d"] = ((df.T2M_MAX + df.T2M_MIN) / 2 - 10).clip(lower=0).rolling(14, min_periods=1).sum()
    dry = (df.PRECTOTCORR < 1).astype(int)
    groups = (dry == 0).cumsum()
    df["dry_days"] = dry.groupby(groups).cumsum()
    return df


def build_samples(seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    weather = weather_features()
    records = []
    levels = ["low", "medium", "high"]
    for date, w in weather.iterrows():
        doy = date.dayofyear
        for crop in CONFIG["crops"]:
            stage = stage_for(crop, doy)
            if stage is None:
                continue
            for profile in SOILS:
                # 每个真实天气×作物×土壤点抽取两个可复现单探针/土检情景。
                for scenario in range(2):
                    moisture = float(np.clip(18 + 48 * ((doy + scenario * 3 + len(crop)) % 11) / 10 + rng.normal(0, 1.2), 2, 95))
                    n_level = levels[(doy + scenario) % 3]
                    p_level = levels[(doy // 3 + scenario) % 3]
                    k_level = levels[(doy // 5 + scenario) % 3]
                    days = int((doy + scenario * 4) % 15)
                    stage_cfg = CONFIG["crops"][crop]["stages"][stage]
                    applied_fraction = ((doy + scenario) % 5) / 5
                    n_applied = CONFIG["crops"][crop]["season_n_kg_mu"] * stage_cfg["n_share"] * applied_fraction
                    p_applied = CONFIG["crops"][crop]["season_p2o5_kg_mu"] * stage_cfg["p_share"] * applied_fraction
                    k_applied = CONFIG["crops"][crop]["season_k2o_kg_mu"] * stage_cfg["k_share"] * applied_fraction
                    soil_ec = float(np.clip(0.45 + 0.12 * (profile["ph"] - 7) + rng.normal(0, 0.35), 0.2, 2.8))
                    d = recommend(crop, stage, moisture, float(w.rain_next_2d), float(w.eto), days,
                                  soil_ec, n_applied, p_applied, k_applied, n_level, p_level, k_level)
                    factors = {"low": 1.0, "medium": 0.65, "high": 0.0}
                    records.append({
                        "date": date, "year": date.year, "crop": crop, "stage": stage,
                        "doy": doy, "t_mean": w.T2M, "t_max": w.T2M_MAX, "t_min": w.T2M_MIN, "rh": w.RH2M,
                        "wind": w.WS2M, "radiation": w.ALLSKY_SFC_SW_DWN, "rain_today": w.PRECTOTCORR,
                        "rain_next_2d": w.rain_next_2d, "eto": w.eto, "gdd10_14d": w.gdd10_14d,
                        "rain_7d": w.rain_7d, "eto_7d": w.eto_7d, "dry_days": w.dry_days,
                        "soil_moisture_pct": moisture, "moisture_trigger_pct": stage_cfg["trigger_moisture_pct"],
                        "moisture_target_pct": stage_cfg["target_moisture_pct"], "soil_ec": soil_ec,
                        "soil_ph": profile["ph"], "soil_n_level": n_level, "soil_p_level": p_level,
                        "soil_k_level": k_level, "days_since_fertigation": days, "n_applied_stage": n_applied,
                        "p_applied_stage": p_applied, "k_applied_stage": k_applied,
                        "kc": stage_cfg["kc"],
                        "n_remaining": d["stage_n_remaining_before_this_job_kg_mu"],
                        "p_remaining": d["stage_p2o5_remaining_before_this_job_kg_mu"],
                        "k_remaining": d["stage_k2o_remaining_before_this_job_kg_mu"],
                        "n_level_factor": factors[n_level], "p_level_factor": factors[p_level], "k_level_factor": factors[k_level],
                        "fertilizer_interval_ready": int(days >= 7), "ec_block": int(soil_ec >= 2.0),
                        "latitude": profile["latitude"], "longitude": profile["longitude"],
                        "co2_ppm": float(np.clip(420 + 12 * math.sin(doy / 365 * 2 * math.pi) + rng.normal(0, 8), 350, 800)),
                        "soil_temperature_c": float(np.clip(w.T2M + 2.0 + rng.normal(0, 1.0), -5, 45)),
                        "soil_n_mg_kg": profile["nitrogen_g_kg"] * 1000,
                        "soil_p_mg_kg": profile.get("phosphorus_mg_kg", 20.0),
                        "soil_k_mg_kg": profile.get("potassium_mg_kg", 160.0),
                        "light_lux": max(0.0, float(w.ALLSKY_SFC_SW_DWN) * 120.0),
                        "rain_24h_mm": float(w.PRECTOTCORR),
                        "water_m3_mu": d["irrigation_m3_mu"], "n_kg_mu": d["nitrogen_kg_mu"],
                        "p2o5_kg_mu": d["p2o5_kg_mu"], "k2o_kg_mu": d["k2o_kg_mu"],
                    })
    return pd.DataFrame(records)


def score_split(model: Pipeline, frame: pd.DataFrame) -> dict:
    truth = frame[TARGETS].to_numpy()
    pred = np.maximum(0, model.predict(frame[CATEGORICAL + NUMERIC]))
    result = {"rows": len(frame)}
    for i, target in enumerate(TARGETS):
        result[target] = {
            "mae": round(float(mean_absolute_error(truth[:, i], pred[:, i])), 4),
            "rmse": round(float(mean_squared_error(truth[:, i], pred[:, i]) ** 0.5), 4),
            "r2": round(float(r2_score(truth[:, i], pred[:, i])), 4),
        }
    irrig_true, irrig_pred = truth[:, 0] >= 0.5, pred[:, 0] >= 0.5
    result["irrigation_decision_accuracy"] = round(float(np.mean(irrig_true == irrig_pred)), 4)
    return result


def main() -> None:
    data = build_samples()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT, index=False, compression="gzip", encoding="utf-8")
    train = data[data.year <= 2022]
    validation = data[data.year == 2023]
    test = data[data.year >= 2024]
    preprocessing = ColumnTransformer([
        ("categories", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
        ("numbers", "passthrough", NUMERIC),
    ])
    estimator = ExtraTreesRegressor(n_estimators=350, min_samples_leaf=2, max_features=0.85,
                                    n_jobs=-1, random_state=42)
    model = Pipeline([("preprocess", preprocessing), ("model", estimator)])
    model.fit(train[CATEGORICAL + NUMERIC], train[TARGETS])
    metrics = {
        "model_kind": "ExtraTrees multi-output policy distillation",
        "label_origin": "单探针土壤水分直接阈值 + crop-stage N/P/K budgets + safety interlocks",
        "not_a_yield_model": True,
        "data_rows": len(data), "train_years": [2015, 2022], "validation_years": [2023], "test_years": [2024, 2025],
        "validation": score_split(model, validation), "test": score_split(model, test),
        "features": CATEGORICAL + NUMERIC, "targets": TARGETS,
        "sources": ["NASA POWER daily weather", "four-crop published agronomic priors", "single-probe moisture setpoints"],
        "warning": "高分只表示模型能复现规则教师；没有本地产量标签，不能解释为增产效果或最优处方精度。",
    }
    joblib.dump({"pipeline": model, "features": CATEGORICAL + NUMERIC, "targets": TARGETS, "metrics": metrics}, MODEL, compress=3)
    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
