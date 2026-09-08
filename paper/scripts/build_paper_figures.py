"""Create reproducible, data-derived figures and extended metrics for the manuscript.

All evidence panels are rendered from the repository data/model with matplotlib;
no generative image model is used for scientific evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from sklearn.metrics import (
    accuracy_score, balanced_accuracy_score, f1_score, mean_absolute_error,
    mean_squared_error, precision_score, recall_score, r2_score,
)

from figure_style import (
    COLORS,
    FULL_WIDTH_IN,
    HATCHES,
    TARGET_COLORS,
    TARGET_LINESTYLES,
    TARGET_MARKERS,
    apply_hatches,
    export_figure,
    panel_label,
    publication_style,
)


PAPER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA = PROJECT_ROOT / "灌溉模型" / "灌溉模型" / "data" / "processed" / "policy_v2_samples.csv.gz"
MODEL_PATH = PROJECT_ROOT / "灌溉模型" / "灌溉模型" / "models" / "hohhot_fertigation_policy_v2.joblib"
OUT = PAPER_ROOT / "figures"
OUT.mkdir(exist_ok=True)

TARGETS = ["water_m3_mu", "n_kg_mu", "p2o5_kg_mu", "k2o_kg_mu"]
CATEGORICAL = ["crop", "stage", "soil_n_level", "soil_p_level", "soil_k_level"]
NUMERIC = [
    "doy", "t_mean", "t_max", "t_min", "rh", "wind", "radiation", "rain_today",
    "rain_next_2d", "eto", "gdd10_14d", "rain_7d", "eto_7d", "dry_days",
    "soil_moisture_pct", "moisture_trigger_pct", "moisture_target_pct", "soil_ec", "soil_ph",
    "days_since_fertigation", "n_applied_stage", "p_applied_stage", "k_applied_stage", "kc",
    "n_remaining", "p_remaining", "k_remaining", "n_level_factor", "p_level_factor", "k_level_factor",
    "fertilizer_interval_ready", "ec_block", "latitude", "longitude", "co2_ppm",
    "soil_temperature_c", "soil_n_mg_kg", "soil_p_mg_kg", "soil_k_mg_kg", "light_lux", "rain_24h_mm",
]

PALETTE = {
    "navy": COLORS["text"], "blue": COLORS["water"], "teal": COLORS["phosphorus"],
    "gold": COLORS["accent"], "red": COLORS["nitrogen"], "gray": COLORS["reference"],
    "light": COLORS["light"],
}
TARGET_LABELS = {"water_m3_mu": "Water (m³ mu⁻¹)", "n_kg_mu": "N (kg mu⁻¹)", "p2o5_kg_mu": "P₂O₅ (kg mu⁻¹)", "k2o_kg_mu": "K₂O (kg mu⁻¹)"}


def style():
    publication_style()


def save(fig, stem):
    export_figure(
        fig,
        OUT,
        stem,
        provenance={
            "source_script": "paper/scripts/build_paper_figures.py",
            "source_data": str(DATA.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "model": str(MODEL_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "seed_policy": "fixed seeds documented in the source script",
            "missing_data": "not imputed for plots; source rows and model outputs are used as stored",
        },
    )


def load():
    df = pd.read_csv(DATA)
    bundle = joblib.load(MODEL_PATH)
    model = bundle["pipeline"]
    test = df[df.year >= 2024].copy()
    pred = np.maximum(0, model.predict(test[CATEGORICAL + NUMERIC]))
    for i, t in enumerate(TARGETS): test[f"pred_{t}"] = pred[:, i]
    return df, test, model


def bootstrap_metrics(y, p, n=1000, seed=42):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(y), size=(n, len(y)))
    out = {}
    for i, target in enumerate(TARGETS):
        vals_mae = np.empty(n); vals_rmse = np.empty(n); vals_r2 = np.empty(n)
        for j in range(n):
            yy, pp = y[idx[j], i], p[idx[j], i]
            vals_mae[j] = mean_absolute_error(yy, pp)
            vals_rmse[j] = mean_squared_error(yy, pp) ** 0.5
            vals_r2[j] = r2_score(yy, pp)
        out[target] = {"mae_ci95": [round(float(x), 4) for x in np.quantile(vals_mae, [0.025, 0.975])],
                       "rmse_ci95": [round(float(x), 4) for x in np.quantile(vals_rmse, [0.025, 0.975])],
                       "r2_ci95": [round(float(x), 4) for x in np.quantile(vals_r2, [0.025, 0.975])]}
    return out


def classification_metrics(y, p):
    result = {}
    for i, target in enumerate(TARGETS):
        yt = y[:, i] > 0
        yp = p[:, i] > 0.5 if i == 0 else p[:, i] > 0.01
        result[target] = {"precision": round(float(precision_score(yt, yp, zero_division=0)), 4),
                          "recall": round(float(recall_score(yt, yp, zero_division=0)), 4),
                          "f1": round(float(f1_score(yt, yp, zero_division=0)), 4),
                          "balanced_accuracy": round(float(balanced_accuracy_score(yt, yp)), 4),
                          "positive_n": int(yt.sum())}
    return result


def make_extended_metrics(df, test, model):
    y = test[TARGETS].to_numpy(); p = test[[f"pred_{t}" for t in TARGETS]].to_numpy()
    metrics = {"test_rows": len(test), "bootstrap_replicates": 1000,
               "bootstrap_ci95": bootstrap_metrics(y, p),
               "positive_event_metrics": classification_metrics(y, p)}
    # Weather-context masking is a robustness stress test, not a claim of imputation accuracy.
    masked = test.copy()
    train = df[df.year <= 2022]
    for col in ["wind", "rain_next_2d", "rain_today"]: masked[col] = train[col].median()
    pm = np.maximum(0, model.predict(masked[CATEGORICAL + NUMERIC]))
    original_dec = p[:, 0] > 0.5; masked_dec = pm[:, 0] > 0.5
    metrics["optional_weather_masking"] = {
        "fields": ["wind", "rain_next_2d", "rain_today"],
        "decision_agreement": round(float(accuracy_score(original_dec, masked_dec)), 4),
        "changed_decisions": int(np.sum(original_dec != masked_dec)),
        "masked_test_decision_accuracy_vs_teacher": round(float(accuracy_score(y[:, 0] > 0.5, masked_dec)), 4),
    }
    metrics["dataset"] = {"rows": int(len(df)), "years": sorted(int(x) for x in df.year.unique()),
                           "crop_counts": {str(k): int(v) for k, v in df.crop.value_counts().sort_index().items()},
                           "stage_counts": {str(k): int(v) for k, v in df.stage.value_counts().sort_index().items()}}
    (OUT / "extended_experiment_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def fig_dataset(df):
    fig, axes = plt.subplots(2, 2, figsize=(FULL_WIDTH_IN, 4.55), layout="constrained")
    crops = ["向日葵", "玉米", "甜菜", "马铃薯"]
    crop_en = ["Sunflower", "Maize", "Sugar\nbeet", "Potato"]
    counts = [int((df.crop == c).sum()) for c in crops]
    bars = axes[0, 0].bar(crop_en, counts, color=COLORS["light"])
    apply_hatches(bars)
    axes[0, 0].set_title("Crop representation"); axes[0, 0].set_ylabel("Samples (n)")
    for i, v in enumerate(counts): axes[0, 0].text(i, v + 120, f"{v:,}", ha="center", fontsize=7)
    nonzero = [float((df[t] > 0).mean() * 100) for t in TARGETS]
    bars = axes[0, 1].bar(["Water", "N", "P₂O₅", "K₂O"], nonzero, color=TARGET_COLORS)
    apply_hatches(bars)
    axes[0, 1].set_title("Positive target rate"); axes[0, 1].set_ylabel("Rows with target > 0 (%)"); axes[0, 1].set_ylim(0, 40)
    for i, v in enumerate(nonzero): axes[0, 1].text(i, v + 1, f"{v:.2f}", ha="center", fontsize=7)
    annual = df.groupby("year").size()
    axes[1, 0].plot(annual.index, annual.values, marker="o", color=PALETTE["navy"], linewidth=1.8)
    axes[1, 0].axvspan(2015, 2022, color=PALETTE["light"], alpha=0.8, label="Training")
    axes[1, 0].axvline(2022.5, color=PALETTE["gray"], linestyle="--", linewidth=0.9)
    axes[1, 0].set_title("Chronological split"); axes[1, 0].set_xlabel("Year"); axes[1, 0].set_ylabel("Samples (n)"); axes[1, 0].legend(loc="lower right")
    axes[1, 0].set_xticks(range(2015, 2026, 2))
    axes[1, 0].set_ylim(0, 4000)
    positive_values = [df.loc[df[t] > 0, t].to_numpy() for t in TARGETS]
    bp = axes[1, 1].boxplot(positive_values, tick_labels=["Water", "N", "P₂O₅", "K₂O"], showfliers=False, patch_artist=True,
                       medianprops={"color": COLORS["text"], "linewidth": 1.2})
    for patch, color, hatch in zip(bp["boxes"], TARGET_COLORS, HATCHES):
        patch.set_facecolor(color); patch.set_alpha(0.65); patch.set_hatch(hatch)
    axes[1, 1].set_title("Non-zero target magnitudes"); axes[1, 1].set_ylabel("Native target units"); axes[1, 1].set_yscale("log")
    for label, ax in zip("abcd", axes.ravel()): panel_label(ax, label)
    save(fig, "fig1_dataset_overview")


def fig_parity(test):
    fig, axes = plt.subplots(2, 2, figsize=(FULL_WIDTH_IN, 5.6), layout="constrained")
    rng = np.random.default_rng(42); idx = rng.choice(len(test), size=min(5000, len(test)), replace=False)
    for ax, target in zip(axes.ravel(), TARGETS):
        y = test[target].to_numpy(); p = test[f"pred_{target}"].to_numpy(); lim = max(float(y.max()), float(p.max())) * 1.05
        ax.hexbin(y[idx], p[idx], gridsize=35, mincnt=1, bins="log", cmap="cividis", linewidths=0.1)
        ax.plot([0, lim], [0, lim], color=COLORS["reference"], linestyle="--", linewidth=1.0, label="1:1")
        ax.set_xlim(0, lim); ax.set_ylim(0, lim); ax.set_xlabel("Teacher-policy target"); ax.set_ylabel("Model prediction"); ax.set_title(TARGET_LABELS[target])
        mae = mean_absolute_error(y, p); r2 = r2_score(y, p)
        ax.text(0.96, 0.05, f"MAE = {mae:.4f}\nR² = {r2:.4f}\nn = {len(y):,}", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=7, bbox={"facecolor": "white", "alpha": 0.88, "edgecolor": "none", "pad": 1.5})
    for label, ax in zip("abcd", axes.ravel()): panel_label(ax, label)
    save(fig, "fig2_test_parity")


def fig_generalization(df, test, model):
    fig, axes = plt.subplots(2, 2, figsize=(FULL_WIDTH_IN, 4.75), layout="constrained")
    years = [2023, 2024, 2025]
    for target_index, target in enumerate(TARGETS):
        vals = []
        for year in years:
            g = df[df.year == year]; p = np.maximum(0, model.predict(g[CATEGORICAL + NUMERIC]))[:, TARGETS.index(target)]
            vals.append(r2_score(g[target], p))
        axes[0, 0].plot(years, vals, marker=TARGET_MARKERS[target_index], linestyle=TARGET_LINESTYLES[target_index],
                        color=TARGET_COLORS[target_index], linewidth=1.4, label=TARGET_LABELS[target])
    axes[0, 0].set_title("R² across validation and test years"); axes[0, 0].set_xlabel("Year"); axes[0, 0].set_ylabel("R²"); axes[0, 0].set_ylim(0.75, 1.01); axes[0, 0].legend(fontsize=6.5, ncol=2)
    axes[0, 0].set_xticks(years)
    crop_order = ["向日葵", "玉米", "甜菜", "马铃薯"]; crop_en = ["Sunflower", "Maize", "Sugar beet", "Potato"]
    vals = []
    for c in crop_order:
        g = test[test.crop == c]; vals.append(accuracy_score(g.water_m3_mu > 0.5, g.pred_water_m3_mu > 0.5))
    bars = axes[0, 1].bar(["Sunflower", "Maize", "Sugar\nbeet", "Potato"], vals, color=COLORS["water"])
    apply_hatches(bars)
    axes[0, 1].set_title("Water decision accuracy by crop"); axes[0, 1].set_ylabel("Accuracy"); axes[0, 1].set_ylim(0.85, 1.0)
    for i, v in enumerate(vals): axes[0, 1].text(i, v + .004, f"{v:.3f}", ha="center", fontsize=7)
    # Test positive-event F1 scores
    labels = ["Water", "N", "P₂O₅", "K₂O"]; f1s = []
    for target in TARGETS:
        y = test[target].to_numpy() > 0; p = test[f"pred_{target}"].to_numpy() > (0.5 if target == "water_m3_mu" else 0.01); f1s.append(f1_score(y, p, zero_division=0))
    bars = axes[1, 0].bar(labels, f1s, color=TARGET_COLORS); apply_hatches(bars)
    axes[1, 0].set_title("Positive-event F1 score"); axes[1, 0].set_ylabel("F1"); axes[1, 0].set_ylim(0, 1)
    for i, v in enumerate(f1s): axes[1, 0].text(i, v + .03, f"{v:.3f}", ha="center", fontsize=7)
    g = test.groupby("year").apply(lambda x: accuracy_score(x.water_m3_mu > .5, x.pred_water_m3_mu > .5), include_groups=False)
    axes[1, 1].plot(g.index, g.values, marker="o", color=COLORS["water"], linewidth=1.6); axes[1, 1].set_title("Irrigation decision accuracy"); axes[1, 1].set_xlabel("Test year"); axes[1, 1].set_ylabel("Accuracy"); axes[1, 1].set_ylim(.85, 1.0); axes[1, 1].set_xticks([2024, 2025])
    for index, (x, v) in enumerate(zip(g.index, g.values)):
        axes[1, 1].text(x, v + .006, f"{v:.3f}", ha="left" if index == 0 else "right", fontsize=7)
    for label, ax in zip("abcd", axes.ravel()): panel_label(ax, label)
    save(fig, "fig3_generalization")


def fig_architecture():
    fig, ax = plt.subplots(figsize=(FULL_WIDTH_IN, 3.15), layout="constrained"); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    boxes = [
        (0.02, 0.48, 0.20, 0.28, "Field sensors", "RS485 soil and weather\nSingle probe and rain gauge\nThree flow meters", "#E8F2F8", COLORS["water"]),
        (0.28, 0.48, 0.18, 0.28, "RK3506B edge", "Modbus acquisition\nLocal LVGL interface\nWi-Fi or Ethernet", "#F4F4F4", COLORS["reference"]),
        (0.52, 0.43, 0.21, 0.38, "Public server", "Validation and storage\nExtraTrees policy inference\nWeather fallback\nWork-order API", "#E9F4EF", COLORS["phosphorus"]),
        (0.80, 0.48, 0.18, 0.28, "ESP32-S3", "Relay state machine\nPulse counting\nFail-safe stop", "#F8ECE7", COLORS["nitrogen"]),
        (0.25, 0.08, 0.50, 0.18, "Closed-loop actuation", "N/P/K stop at their own flow targets; outlet starts only after dosing stops", "#F4F4F4", COLORS["reference"]),
    ]
    for x, y, w, h, title, body, fc, ec in boxes:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.010,rounding_size=0.012", facecolor=fc, edgecolor=ec, linewidth=1.2))
        ax.text(x + .014, y + h - .05, title, fontsize=8.4, fontweight="bold", color=COLORS["text"], va="top")
        ax.text(x + .014, y + h - .11, body, fontsize=6.8, color=COLORS["text"], va="top", linespacing=1.3)
    def arr(a, b, label=None, dy=0):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=13, linewidth=1.4, color=PALETTE["gray"]))
        if label: ax.text((a[0]+b[0])/2, (a[1]+b[1])/2 + dy, label, ha="center", fontsize=7.5, color=PALETTE["gray"])
    arr((.22,.62),(.28,.62)); arr((.46,.62),(.52,.62)); arr((.73,.62),(.80,.62))
    ax.text(.25, .70, "Modbus", ha="center", fontsize=6.5, color=PALETTE["gray"])
    ax.text(.49, .70, "HTTPS/JSON", ha="center", fontsize=6.5, color=PALETTE["gray"])
    ax.text(.765, .70, "USB/CH341", ha="center", fontsize=6.5, color=PALETTE["gray"])
    arr((.63,.43),(.60,.26)); arr((.40,.26),(.40,.48))
    ax.text(.5, .015, "Synchronized sensor, work-order and relay states are visible on the browser dashboard and local interface.", ha="center", fontsize=6.6, color=PALETTE["gray"])
    save(fig, "fig4_architecture")


def fig_controller():
    fig, ax = plt.subplots(figsize=(FULL_WIDTH_IN, 2.8), layout="constrained")
    starts = {"N pump": 0, "P pump": 0, "K pump": 0, "Outlet pump": 360}; ends = {"N pump": 160, "P pump": 160, "K pump": 360, "Outlet pump": 610}; colors = TARGET_COLORS[:3] + [COLORS["outlet"]]
    labels = list(starts)
    for i, label in enumerate(labels):
        bars = ax.barh(i, ends[label]-starts[label], left=starts[label], height=.52, color=colors[i], edgecolor=COLORS["text"], linewidth=.45)
        bars[0].set_hatch(HATCHES[i])
        ax.text(starts[label] + (ends[label]-starts[label])/2, i, f"{starts[label]}–{ends[label]} s", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    ax.axvline(360, color=PALETTE["gray"], linestyle="--", linewidth=1)
    ax.text(365, 3.35, "dosing complete\noutlet released", fontsize=7, color=PALETTE["gray"], va="top")
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels); ax.invert_yaxis(); ax.set_xlabel("Elapsed time (s)"); ax.set_xlim(0, 650); ax.set_ylim(3.8, -0.8); ax.grid(axis="x", color=COLORS["grid"], linewidth=.6)
    save(fig, "fig5_controller_trace")


def fig_uncertainty(test):
    y = test[TARGETS].to_numpy(); p = test[[f"pred_{t}" for t in TARGETS]].to_numpy()
    rng = np.random.default_rng(42); idx = rng.integers(0, len(y), size=(1000, len(y)))
    mae = np.zeros((1000, 4))
    for j in range(1000): mae[j] = np.mean(np.abs(y[idx[j]] - p[idx[j]]), axis=0)
    means = mae.mean(axis=0); lo, hi = np.quantile(mae, [0.025, 0.975], axis=0)
    fig, axes = plt.subplots(2, 2, figsize=(FULL_WIDTH_IN, 4.15), layout="constrained")
    labels = ["Water", "N", "P₂O₅", "K₂O"]
    for i, ax in enumerate(axes.ravel()):
        ax.errorbar([0], [means[i]], yerr=[[means[i] - lo[i]], [hi[i] - means[i]]],
                    fmt=TARGET_MARKERS[i], color=TARGET_COLORS[i], ecolor=COLORS["reference"],
                    markersize=5, elinewidth=1.2, capsize=4)
        ax.set_xlim(-0.6, 0.6); ax.set_ylim(0, hi[i] * 1.48)
        ax.set_xticks([]); ax.set_title(labels[i]); ax.grid(axis="y", color=COLORS["grid"], linewidth=.6)
        ax.set_ylabel("MAE (m³ mu⁻¹)" if i == 0 else "MAE (kg mu⁻¹)")
        ax.text(0, hi[i] * 1.10, f"{means[i]:.4f}\n95% CI [{lo[i]:.4f}, {hi[i]:.4f}]",
                ha="center", va="bottom", fontsize=6.3)
        panel_label(ax, "abcd"[i])
    save(fig, "fig6_bootstrap_uncertainty")


def fig_controller_mc():
    metrics = json.loads((OUT / "controller_monte_carlo_metrics.json").read_text(encoding="utf-8"))
    labels = ["Normal\ncompleted", "Outlet\noverlap avoided", "Premature\nstop avoided", "No-flow\nfault detected", "Emergency\nstop safe"]
    vals = [metrics["normal"]["completion_rate"], 1-metrics["normal"]["outlet_overlap_rate"],
            1-metrics["normal"]["premature_stop_rate"], metrics["no_flow_fault"]["fault_detection_rate"],
            metrics["emergency_stop"]["all_outputs_off_rate"]]
    ns = [metrics["normal"]["n"], metrics["normal"]["n"], metrics["normal"]["n"], metrics["no_flow_fault"]["n"], metrics["emergency_stop"]["n"]]
    fig, ax = plt.subplots(figsize=(FULL_WIDTH_IN, 3.2), layout="constrained")
    bars = ax.bar(labels, np.array(vals)*100, color=COLORS["water"], width=.67)
    apply_hatches(bars)
    ax.set_ylim(0, 105); ax.set_ylabel("Scenario success rate (%)")
    ax.grid(axis="y", color=COLORS["grid"], linewidth=.6)
    for bar, val, n in zip(bars, vals, ns): ax.text(bar.get_x()+bar.get_width()/2, val*100+1.2, f"{val*100:.1f}%\nn = {n:,}", ha="center", va="bottom", fontsize=6.8)
    save(fig, "fig7_controller_monte_carlo")


def main():
    style(); df, test, model = load(); make_extended_metrics(df, test, model); fig_dataset(df); fig_parity(test); fig_generalization(df, test, model); fig_architecture(); fig_controller(); fig_uncertainty(test); fig_controller_mc(); print(f"Wrote figures and metrics to {OUT}")


if __name__ == "__main__":
    main()
