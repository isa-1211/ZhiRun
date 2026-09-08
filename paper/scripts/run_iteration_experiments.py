"""Run repeated learning-curve and ExtraTrees sensitivity experiments.

All iteration outcomes are measured against generated teacher-policy labels on
the 2023 validation split. The 2024-2025 test split remains reserved for the
single final-model evaluation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import accuracy_score, mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


PAPER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = PROJECT_ROOT / "灌溉模型" / "灌溉模型"
DATA = MODEL_DIR / "data" / "processed" / "policy_v2_samples.csv.gz"
MODEL_PATH = MODEL_DIR / "models" / "hohhot_fertigation_policy_v2.joblib"
OUT = PAPER_ROOT / "figures"
OUT.mkdir(exist_ok=True)

BUNDLE = joblib.load(MODEL_PATH)
FEATURES = list(BUNDLE["features"])
TARGETS = list(BUNDLE["targets"])
CATEGORICAL = ["crop", "stage", "soil_n_level", "soil_p_level", "soil_k_level"]
NUMERIC = [name for name in FEATURES if name not in CATEGORICAL]
SEEDS = [11, 23, 37, 53, 71]
FRACTIONS = [0.10, 0.25, 0.50, 0.75, 1.00]


def pipeline(n_estimators: int, min_samples_leaf: int, seed: int) -> Pipeline:
    preprocessing = ColumnTransformer([
        ("categories", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
        ("numbers", "passthrough", NUMERIC),
    ])
    estimator = ExtraTreesRegressor(
        n_estimators=n_estimators,
        min_samples_leaf=min_samples_leaf,
        max_features=0.85,
        n_jobs=-1,
        random_state=seed,
    )
    return Pipeline([("preprocess", preprocessing), ("model", estimator)])


def metrics(truth: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    nutrient_r2 = [r2_score(truth[:, index], prediction[:, index]) for index in range(1, 4)]
    return {
        "water_mae": float(mean_absolute_error(truth[:, 0], prediction[:, 0])),
        "water_r2": float(r2_score(truth[:, 0], prediction[:, 0])),
        "nutrient_macro_r2": float(np.mean(nutrient_r2)),
        "decision_accuracy": float(accuracy_score(truth[:, 0] >= 0.5, prediction[:, 0] >= 0.5)),
    }


def fit_and_score(train: pd.DataFrame, evaluation: pd.DataFrame, n_estimators: int, min_leaf: int, seed: int) -> dict:
    model = pipeline(n_estimators, min_leaf, seed)
    started = time.perf_counter()
    model.fit(train[FEATURES], train[TARGETS])
    elapsed = time.perf_counter() - started
    prediction = np.maximum(0, model.predict(evaluation[FEATURES]))
    return {**metrics(evaluation[TARGETS].to_numpy(), prediction), "fit_seconds": elapsed}


def learning_curve(train: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fraction in FRACTIONS:
        for seed in SEEDS:
            subset = train.sample(frac=fraction, random_state=seed).sort_index()
            row = fit_and_score(subset, validation, n_estimators=350, min_leaf=2, seed=seed)
            rows.append({"fraction": fraction, "seed": seed, "train_rows": len(subset), **row})
            print(f"learning fraction={fraction:.2f} seed={seed} rows={len(subset)}")
    return pd.DataFrame(rows)


def parameter_sensitivity(train: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for trees in [100, 200, 350, 500]:
        for leaf in [1, 2, 4]:
            row = fit_and_score(train, validation, n_estimators=trees, min_leaf=leaf, seed=42)
            rows.append({"n_estimators": trees, "min_samples_leaf": leaf, "train_rows": len(train), **row})
            print(f"parameters trees={trees} leaf={leaf}")
    return pd.DataFrame(rows)


def make_figure(curve: pd.DataFrame, sensitivity: pd.DataFrame) -> None:
    summary = curve.groupby("fraction").agg(
        decision_mean=("decision_accuracy", "mean"), decision_sd=("decision_accuracy", "std"),
        mae_mean=("water_mae", "mean"), mae_sd=("water_mae", "std"),
        nutrient_mean=("nutrient_macro_r2", "mean"), nutrient_sd=("nutrient_macro_r2", "std"),
    ).reset_index()
    fig, axes = plt.subplots(1, 3, figsize=(8.2, 3.25), constrained_layout=True)
    x = summary.fraction.to_numpy() * 100
    axes[0].errorbar(x, summary.decision_mean * 100, yerr=summary.decision_sd * 100, marker="o", capsize=3, color="#20639B")
    axes[0].set_ylabel("Decision accuracy (%)")
    axes[1].errorbar(x, summary.mae_mean, yerr=summary.mae_sd, marker="o", capsize=3, color="#ED553B")
    axes[1].set_ylabel("Water MAE (m3 mu-1)")
    axes[2].errorbar(x, summary.nutrient_mean, yerr=summary.nutrient_sd, marker="o", capsize=3, color="#3CAEA3")
    axes[2].set_ylabel("Nutrient macro R2")
    for index, ax in enumerate(axes):
        ax.set_xlabel("Training data used (%)")
        ax.set_title(chr(65 + index))
        ax.grid(color="#D9E1E8", linewidth=.6)
    fig.suptitle("Repeated learning curve (2023 validation; five seeds per size)", fontsize=11, fontweight="bold", color="#173F5F")
    for ext in ("png", "pdf", "svg"):
        kwargs = {"dpi": 600} if ext == "png" else {}
        fig.savefig(OUT / f"fig9_iteration_learning_curve.{ext}", bbox_inches="tight", **kwargs)
    plt.close(fig)


def main() -> None:
    data = pd.read_csv(DATA)
    train = data[data.year <= 2022].copy()
    validation = data[data.year == 2023].copy()
    curve = learning_curve(train, validation)
    sensitivity = parameter_sensitivity(train, validation)
    curve.to_csv(OUT / "iteration_learning_curve_raw.csv", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(OUT / "iteration_parameter_sensitivity.csv", index=False, encoding="utf-8-sig")
    curve_summary = curve.groupby("fraction").agg(
        runs=("seed", "count"), train_rows=("train_rows", "first"),
        decision_mean=("decision_accuracy", "mean"), decision_sd=("decision_accuracy", "std"),
        water_mae_mean=("water_mae", "mean"), water_mae_sd=("water_mae", "std"),
        water_r2_mean=("water_r2", "mean"), water_r2_sd=("water_r2", "std"),
        nutrient_r2_mean=("nutrient_macro_r2", "mean"), nutrient_r2_sd=("nutrient_macro_r2", "std"),
    ).reset_index()
    payload = {
        "evidence_scope": "teacher-policy reproduction on fixed 2023 validation rows",
        "validation_rows": len(validation), "seeds": SEEDS,
        "learning_curve_summary": curve_summary.to_dict(orient="records"),
        "parameter_sensitivity": sensitivity.to_dict(orient="records"),
    }
    (OUT / "iteration_experiment_metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    make_figure(curve, sensitivity)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
