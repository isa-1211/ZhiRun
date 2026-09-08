"""Reproducible baselines and feature-ablation study for the manuscript.

The labels remain the generated teacher-policy targets.  This script therefore
tests policy reproduction sensitivity, not agronomic treatment effects.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, mean_absolute_error, precision_score, recall_score, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from figure_style import COLORS, FULL_WIDTH_IN, apply_hatches, export_figure, publication_style


PAPER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA = PROJECT_ROOT / "灌溉模型" / "灌溉模型" / "data" / "processed" / "policy_v2_samples.csv.gz"
MODEL_PATH = PROJECT_ROOT / "灌溉模型" / "灌溉模型" / "models" / "hohhot_fertigation_policy_v2.joblib"
OUT = PAPER_ROOT / "figures"
OUT.mkdir(exist_ok=True)

TARGETS = ["water_m3_mu", "n_kg_mu", "p2o5_kg_mu", "k2o_kg_mu"]
CATEGORICAL = ["crop", "stage", "soil_n_level", "soil_p_level", "soil_k_level"]
bundle = joblib.load(MODEL_PATH)
NUMERIC = bundle["features"][5:]
ALL_FEATURES = CATEGORICAL + NUMERIC

WEATHER = ["doy", "t_mean", "t_max", "t_min", "rh", "wind", "radiation", "rain_today", "rain_next_2d", "eto", "gdd10_14d", "rain_7d", "eto_7d", "dry_days", "rain_24h_mm"]
SOIL = ["soil_moisture_pct", "moisture_trigger_pct", "moisture_target_pct", "soil_ec", "soil_ph", "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg", "soil_temperature_c"]
CROP_STAGE = ["crop", "stage", "kc", "n_remaining", "p_remaining", "k_remaining", "n_applied_stage", "p_applied_stage", "k_applied_stage", "days_since_fertigation", "fertilizer_interval_ready"]


def score(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "water_mae": round(float(mean_absolute_error(y[:, 0], p[:, 0])), 4),
        "water_r2": round(float(r2_score(y[:, 0], p[:, 0])), 4),
        "n_r2": round(float(r2_score(y[:, 1], p[:, 1])), 4),
        "p2o5_r2": round(float(r2_score(y[:, 2], p[:, 2])), 4),
        "k2o_r2": round(float(r2_score(y[:, 3], p[:, 3])), 4),
        "decision_accuracy": round(float(accuracy_score(y[:, 0] >= 0.5, p[:, 0] >= 0.5)), 4),
    }


def fit_ablation(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> dict:
    cats = [c for c in CATEGORICAL if c in features]
    nums = [c for c in NUMERIC if c in features]
    pre = ColumnTransformer([
        ("categories", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cats),
        ("numbers", "passthrough", nums),
    ])
    estimator = ExtraTreesRegressor(n_estimators=250, min_samples_leaf=2, max_features=0.85, n_jobs=-1, random_state=42)
    pipe = Pipeline([("preprocess", pre), ("model", estimator)])
    pipe.fit(train[features], train[TARGETS])
    pred = np.maximum(0, pipe.predict(test[features]))
    return score(test[TARGETS].to_numpy(), pred)


def main() -> None:
    publication_style()
    df = pd.read_csv(DATA)
    train, test = df[df.year <= 2022].copy(), df[df.year >= 2024].copy()
    full_prediction = np.maximum(0, bundle["pipeline"].predict(test[ALL_FEATURES]))
    result = {
        "label_origin": "generated teacher-policy targets; no yield labels",
        "test_rows": int(len(test)),
        "full_model": score(test[TARGETS].to_numpy(), full_prediction),
        "ablations": {},
    }
    for name, removed in [("No weather features", WEATHER), ("No soil features", SOIL), ("No crop-stage features", CROP_STAGE)]:
        keep = [f for f in ALL_FEATURES if f not in removed]
        result["ablations"][name] = {"removed": removed, "features": len(keep), "metrics": fit_ablation(train, test, keep)}

    # A transparent agronomic reference: irrigate below the measured stage trigger,
    # use the mean positive water event, and never predict fertilizer.
    positive_mean = float(train.loc[train.water_m3_mu > 0, "water_m3_mu"].mean())
    threshold_pred = np.zeros((len(test), 4), dtype=float)
    threshold_pred[:, 0] = (test.soil_moisture_pct < test.moisture_trigger_pct) * positive_mean
    result["ablations"]["Soil-moisture threshold + mean dose"] = {
        "positive_water_mean_m3_mu": round(positive_mean, 4),
        "metrics": score(test[TARGETS].to_numpy(), threshold_pred),
    }
    teacher_event = test.water_m3_mu.to_numpy() >= 0.5
    result["decision_threshold_sensitivity"] = {}
    for threshold in (0.1, 0.5, 1.0, 2.0):
        predicted_event = full_prediction[:, 0] >= threshold
        tn, fp, fn, tp = confusion_matrix(teacher_event, predicted_event, labels=[False, True]).ravel()
        result["decision_threshold_sensitivity"][str(threshold)] = {
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
            "accuracy": round(float(accuracy_score(teacher_event, predicted_event)), 4),
            "precision": round(float(precision_score(teacher_event, predicted_event, zero_division=0)), 4),
            "recall": round(float(recall_score(teacher_event, predicted_event, zero_division=0)), 4),
            "f1": round(float(f1_score(teacher_event, predicted_event, zero_division=0)), 4),
            "specificity": round(float(tn / (tn + fp)), 4),
        }
    (OUT / "ablation_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    names = ["Full model", "No weather", "No soil", "No crop-stage", "Moisture threshold\n+mean dose"]
    vals = [result["full_model"]["decision_accuracy"]] + [result["ablations"][n]["metrics"]["decision_accuracy"] for n in result["ablations"]]
    fig, ax = plt.subplots(figsize=(FULL_WIDTH_IN, 3.25), layout="constrained")
    bars = ax.bar(names, np.array(vals) * 100, color=COLORS["water"], width=0.65)
    apply_hatches(bars)
    ax.set_ylim(0, 100); ax.set_ylabel("Irrigation decision accuracy (%)")
    ax.grid(axis="y", color=COLORS["grid"], linewidth=.6)
    for bar, val in zip(bars, vals): ax.text(bar.get_x() + bar.get_width() / 2, val * 100 + 1, f"{val * 100:.1f}%", ha="center", va="bottom", fontsize=7)
    export_figure(
        fig,
        OUT,
        "fig8_ablation_comparison",
        provenance={
            "source_script": "paper/scripts/run_ablation_experiment.py",
            "source_data": str(DATA.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "model": str(MODEL_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "test_split": "2024-2025, n=7200",
            "uncertainty": "point estimates; no interval shown",
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
