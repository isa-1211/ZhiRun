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

PALETTE = {"navy": "#173F5F", "blue": "#20639B", "teal": "#3CAEA3", "gold": "#F6C85F", "red": "#ED553B", "gray": "#5B6770", "light": "#EAF2F8"}
TARGET_LABELS = {"water_m3_mu": "Water (m³ mu⁻¹)", "n_kg_mu": "N (kg mu⁻¹)", "p2o5_kg_mu": "P₂O₅ (kg mu⁻¹)", "k2o_kg_mu": "K₂O (kg mu⁻¹)"}


def style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 10,
        "axes.labelsize": 9, "axes.linewidth": 0.8, "xtick.labelsize": 8,
        "ytick.labelsize": 8, "legend.fontsize": 8, "figure.dpi": 180,
        "savefig.dpi": 600, "savefig.bbox": "tight", "axes.spines.top": False,
        "axes.spines.right": False, "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def save(fig, stem):
    fig.savefig(OUT / f"{stem}.png", dpi=600)
    fig.savefig(OUT / f"{stem}.pdf")
    fig.savefig(OUT / f"{stem}.svg")
    plt.close(fig)


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
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.1), constrained_layout=True)
    crops = ["向日葵", "玉米", "甜菜", "马铃薯"]
    crop_en = ["Sunflower", "Maize", "Sugar beet", "Potato"]
    counts = [int((df.crop == c).sum()) for c in crops]
    axes[0, 0].bar(crop_en, counts, color=[PALETTE["teal"], PALETTE["blue"], PALETTE["gold"], PALETTE["red"]])
    axes[0, 0].set_title("A  Crop representation"); axes[0, 0].set_ylabel("Samples (n)"); axes[0, 0].tick_params(axis="x", rotation=20)
    for i, v in enumerate(counts): axes[0, 0].text(i, v + 120, f"{v:,}", ha="center", fontsize=7)
    nonzero = [float((df[t] > 0).mean() * 100) for t in TARGETS]
    axes[0, 1].bar(["Water", "N", "P₂O₅", "K₂O"], nonzero, color=[PALETTE["blue"], PALETTE["teal"], PALETTE["gold"], PALETTE["red"]])
    axes[0, 1].set_title("B  Positive target rate"); axes[0, 1].set_ylabel("Rows with target > 0 (%)"); axes[0, 1].set_ylim(0, 40)
    for i, v in enumerate(nonzero): axes[0, 1].text(i, v + 1, f"{v:.2f}", ha="center", fontsize=7)
    annual = df.groupby("year").size()
    axes[1, 0].plot(annual.index, annual.values, marker="o", color=PALETTE["navy"], linewidth=1.8)
    axes[1, 0].axvspan(2015, 2022, color=PALETTE["light"], alpha=0.8, label="Training")
    axes[1, 0].axvline(2022.5, color=PALETTE["gray"], linestyle="--", linewidth=0.9)
    axes[1, 0].set_title("C  Chronological split"); axes[1, 0].set_xlabel("Year"); axes[1, 0].set_ylabel("Samples (n)"); axes[1, 0].legend(frameon=False, loc="lower right")
    positive_values = [df.loc[df[t] > 0, t].to_numpy() for t in TARGETS]
    axes[1, 1].boxplot(positive_values, labels=["Water", "N", "P₂O₅", "K₂O"], showfliers=False, patch_artist=True,
                       boxprops={"facecolor": "#DDEBF7", "edgecolor": PALETTE["blue"]}, medianprops={"color": PALETTE["red"], "linewidth": 1.4})
    axes[1, 1].set_title("D  Non-zero target magnitudes"); axes[1, 1].set_ylabel("Native target units"); axes[1, 1].set_yscale("log")
    fig.suptitle("Dataset composition and target sparsity (n = 39,600)", fontsize=12, fontweight="bold", color=PALETTE["navy"])
    save(fig, "fig1_dataset_overview")


def fig_parity(test):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.3), constrained_layout=True)
    rng = np.random.default_rng(42); idx = rng.choice(len(test), size=min(5000, len(test)), replace=False)
    for ax, target in zip(axes.ravel(), TARGETS):
        y = test[target].to_numpy(); p = test[f"pred_{target}"].to_numpy(); lim = max(float(y.max()), float(p.max())) * 1.05
        hb = ax.hexbin(y[idx], p[idx], gridsize=35, mincnt=1, bins="log", cmap="Blues", linewidths=0.15)
        ax.plot([0, lim], [0, lim], color=PALETTE["red"], linestyle="--", linewidth=1.0, label="1:1")
        ax.set_xlim(0, lim); ax.set_ylim(0, lim); ax.set_xlabel("Teacher target"); ax.set_ylabel("Model prediction"); ax.set_title(TARGET_LABELS[target])
        mae = mean_absolute_error(y, p); r2 = r2_score(y, p)
        ax.text(0.05, 0.90, f"MAE = {mae:.4f}\nR² = {r2:.4f}\nn = {len(y):,}", transform=ax.transAxes, fontsize=8, bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"})
    fig.suptitle("Independent test parity plots (2024–2025; n = 7,200)", fontsize=12, fontweight="bold", color=PALETTE["navy"])
    save(fig, "fig2_test_parity")


def fig_generalization(df, test, model):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.6), constrained_layout=True)
    years = [2023, 2024, 2025]; colors = [PALETTE["blue"], PALETTE["teal"], PALETTE["red" ]]
    for target in TARGETS:
        vals = []
        for year in years:
            g = df[df.year == year]; p = np.maximum(0, model.predict(g[CATEGORICAL + NUMERIC]))[:, TARGETS.index(target)]
            vals.append(r2_score(g[target], p))
        axes[0, 0].plot(years, vals, marker="o", linewidth=1.7, label=TARGET_LABELS[target])
    axes[0, 0].set_title("A  R² across validation and test years"); axes[0, 0].set_xlabel("Year"); axes[0, 0].set_ylabel("R²"); axes[0, 0].set_ylim(0.75, 1.01); axes[0, 0].legend(frameon=False, fontsize=7)
    crop_order = ["向日葵", "玉米", "甜菜", "马铃薯"]; crop_en = ["Sunflower", "Maize", "Sugar beet", "Potato"]
    vals = []
    for c in crop_order:
        g = test[test.crop == c]; vals.append(accuracy_score(g.water_m3_mu > 0.5, g.pred_water_m3_mu > 0.5))
    axes[0, 1].bar(crop_en, vals, color=[PALETTE["teal"], PALETTE["blue"], PALETTE["gold"], PALETTE["red"]]); axes[0, 1].set_title("B  Water decision accuracy by crop"); axes[0, 1].set_ylabel("Accuracy"); axes[0, 1].set_ylim(0.85, 1.0); axes[0, 1].tick_params(axis="x", rotation=20)
    for i, v in enumerate(vals): axes[0, 1].text(i, v + .004, f"{v:.3f}", ha="center", fontsize=7)
    # Test positive-event F1 scores
    labels = ["Water", "N", "P₂O₅", "K₂O"]; f1s = []
    for target in TARGETS:
        y = test[target].to_numpy() > 0; p = test[f"pred_{target}"].to_numpy() > (0.5 if target == "water_m3_mu" else 0.01); f1s.append(f1_score(y, p, zero_division=0))
    axes[1, 0].bar(labels, f1s, color=[PALETTE["blue"], PALETTE["teal"], PALETTE["gold"], PALETTE["red"]]); axes[1, 0].set_title("C  Positive-event F1 score"); axes[1, 0].set_ylabel("F1"); axes[1, 0].set_ylim(0, 1)
    for i, v in enumerate(f1s): axes[1, 0].text(i, v + .03, f"{v:.3f}", ha="center", fontsize=7)
    g = test.groupby("year").apply(lambda x: accuracy_score(x.water_m3_mu > .5, x.pred_water_m3_mu > .5), include_groups=False)
    axes[1, 1].plot(g.index, g.values, marker="o", color=PALETTE["navy"], linewidth=2); axes[1, 1].set_title("D  Irrigation decision accuracy"); axes[1, 1].set_xlabel("Test year"); axes[1, 1].set_ylabel("Accuracy"); axes[1, 1].set_ylim(.85, 1.0)
    for x, v in zip(g.index, g.values): axes[1, 1].text(x, v + .006, f"{v:.3f}", ha="center", fontsize=7)
    fig.suptitle("Temporal, crop-level and positive-event robustness", fontsize=12, fontweight="bold", color=PALETTE["navy"])
    save(fig, "fig3_generalization")


def fig_architecture():
    fig, ax = plt.subplots(figsize=(8.2, 4.2)); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    boxes = [
        (0.03, 0.43, 0.18, 0.22, "Field sensors", "RS485 soil/weather\nsingle probe + rain\n3 flow meters", "#E8F5E9", "#3A7D44"),
        (0.28, 0.43, 0.18, 0.22, "RK3506B edge", "Modbus acquisition\nlocal LVGL HMI\nWi-Fi / Ethernet", "#FFF3D6", "#A66A00"),
        (0.53, 0.38, 0.20, 0.32, "Public server", "validation + storage\nExtraTrees policy\nweather fallback\nwork-order API", "#EEE8FF", "#6543A5"),
        (0.80, 0.43, 0.17, 0.22, "ESP32-S3", "relay state machine\npulse counting\nfail-safe stop", "#FDE7E7", "#A83A3A"),
        (0.28, 0.08, 0.45, 0.17, "Closed-loop actuation", "N/P/K pumps stop at own flow target → outlet pump starts after all dosing stops", "#F2F4F5", "#586A78"),
    ]
    for x, y, w, h, title, body, fc, ec in boxes:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.015", facecolor=fc, edgecolor=ec, linewidth=1.6))
        ax.text(x + .015, y + h - .045, title, fontsize=10, fontweight="bold", color=PALETTE["navy"], va="top")
        ax.text(x + .015, y + h - .09, body, fontsize=8.1, color="#263238", va="top", linespacing=1.35)
    def arr(a, b, label=None, dy=0):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=13, linewidth=1.4, color=PALETTE["gray"]))
        if label: ax.text((a[0]+b[0])/2, (a[1]+b[1])/2 + dy, label, ha="center", fontsize=7.5, color=PALETTE["gray"])
    arr((.21,.54),(.28,.54))
    arr((.46,.54),(.53,.54))
    arr((.73,.54),(.80,.54))
    ax.text(.245, .69, "Modbus", ha="center", fontsize=7.5, color=PALETTE["gray"])
    ax.text(.495, .69, "HTTPS / JSON", ha="center", fontsize=7.5, color=PALETTE["gray"])
    ax.text(.765, .69, "USB/CH341", ha="center", fontsize=7.5, color=PALETTE["gray"])
    arr((.63,.38),(.56,.25))
    arr((.40,.25),(.40,.43))
    ax.text(.5, .93, "ZhiRun edge-cloud fertigation architecture", ha="center", fontsize=13, fontweight="bold", color=PALETTE["navy"])
    ax.text(.5, .01, "Browser dashboard and local HMI expose synchronized sensor, work-order and relay states", ha="center", fontsize=8, color=PALETTE["gray"])
    save(fig, "fig4_architecture")


def fig_controller():
    fig, ax = plt.subplots(figsize=(8.0, 3.4))
    starts = {"N pump": 0, "P pump": 0, "K pump": 0, "Outlet pump": 360}; ends = {"N pump": 160, "P pump": 160, "K pump": 360, "Outlet pump": 610}; colors = [PALETTE["blue"], PALETTE["teal"], PALETTE["gold"], PALETTE["red"]]
    labels = list(starts)
    for i, label in enumerate(labels):
        ax.barh(i, ends[label]-starts[label], left=starts[label], height=.52, color=colors[i], edgecolor="white", linewidth=.8)
        ax.text(starts[label] + (ends[label]-starts[label])/2, i, f"{starts[label]}–{ends[label]} s", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    ax.axvline(360, color=PALETTE["gray"], linestyle="--", linewidth=1); ax.text(365, 3.35, "all fertilizer pumps OFF\noutlet interlock released", fontsize=8, color=PALETTE["gray"], va="top")
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels); ax.invert_yaxis(); ax.set_xlabel("Elapsed time (s)"); ax.set_title("Controller timing trace with independent flow-meter closure", fontsize=11, fontweight="bold", color=PALETTE["navy"]); ax.set_xlim(0, 650); ax.set_ylim(3.8, -0.8); ax.grid(axis="x", color="#D9E1E8", linewidth=.6)
    ax.text(0.0, -0.58, "Illustrative deterministic trace: N=4.0 L at 1.5 L min⁻¹; P=2.0 L at 0.75 L min⁻¹; K=6.0 L at 1.0 L min⁻¹.", transform=ax.transAxes, fontsize=7.5, color=PALETTE["gray"], va="top")
    save(fig, "fig5_controller_trace")


def fig_uncertainty(test):
    y = test[TARGETS].to_numpy(); p = test[[f"pred_{t}" for t in TARGETS]].to_numpy()
    rng = np.random.default_rng(42); idx = rng.integers(0, len(y), size=(1000, len(y)))
    mae = np.zeros((1000, 4))
    for j in range(1000): mae[j] = np.mean(np.abs(y[idx[j]] - p[idx[j]]), axis=0)
    means = mae.mean(axis=0); lo, hi = np.quantile(mae, [0.025, 0.975], axis=0)
    fig, ax = plt.subplots(figsize=(7.0, 3.6), constrained_layout=True)
    x = np.arange(4); colors = [PALETTE["blue"], PALETTE["teal"], PALETTE["gold"], PALETTE["red"]]
    ax.errorbar(x, means, yerr=[means-lo, hi-means], fmt="none", ecolor=PALETTE["gray"], elinewidth=1.4, capsize=4, zorder=2)
    ax.scatter(x, means, s=85, c=colors, edgecolor="white", linewidth=0.8, zorder=3)
    ax.set_xticks(x); ax.set_xticklabels(["Water", "N", "P₂O₅", "K₂O"]); ax.set_ylabel("MAE (native units)"); ax.set_title("Bootstrap uncertainty of independent-test MAE\n(1,000 replicates)", fontsize=11, fontweight="bold", color=PALETTE["navy"])
    for i, (m, l, h) in enumerate(zip(means, lo, hi)):
        yoff = 0.018 if i == 0 else 0.004
        ax.text(i, h + yoff, f"{m:.4f}\n[{l:.4f}, {h:.4f}]", ha="center", va="bottom", fontsize=7.5)
    ax.grid(axis="y", color="#D9E1E8", linewidth=.6); save(fig, "fig6_bootstrap_uncertainty")


def fig_controller_mc():
    metrics = json.loads((OUT / "controller_monte_carlo_metrics.json").read_text(encoding="utf-8"))
    labels = ["Normal\ncompleted", "Outlet\noverlap avoided", "Premature\nstop avoided", "No-flow\nfault detected", "Emergency\nstop safe"]
    vals = [metrics["normal"]["completion_rate"], 1-metrics["normal"]["outlet_overlap_rate"],
            1-metrics["normal"]["premature_stop_rate"], metrics["no_flow_fault"]["fault_detection_rate"],
            metrics["emergency_stop"]["all_outputs_off_rate"]]
    ns = [metrics["normal"]["n"], metrics["normal"]["n"], metrics["normal"]["n"], metrics["no_flow_fault"]["n"], metrics["emergency_stop"]["n"]]
    fig, ax = plt.subplots(figsize=(7.1, 3.8), constrained_layout=True)
    colors = [PALETTE["blue"], PALETTE["teal"], PALETTE["gold"], PALETTE["red"], PALETTE["navy"]]
    bars = ax.bar(labels, np.array(vals)*100, color=colors, width=.67)
    ax.set_ylim(95, 100.35); ax.set_ylabel("Scenario success rate (%)"); ax.set_title("Controller Monte Carlo safety evaluation (14,000 scenarios)", fontsize=11, fontweight="bold", color=PALETTE["navy"])
    ax.grid(axis="y", color="#D9E1E8", linewidth=.6)
    for bar, val, n in zip(bars, vals, ns): ax.text(bar.get_x()+bar.get_width()/2, val*100+.07, f"{val*100:.1f}%\nn={n:,}", ha="center", va="bottom", fontsize=7.5)
    save(fig, "fig7_controller_monte_carlo")


def main():
    style(); df, test, model = load(); make_extended_metrics(df, test, model); fig_dataset(df); fig_parity(test); fig_generalization(df, test, model); fig_architecture(); fig_controller(); fig_uncertainty(test); fig_controller_mc(); print(f"Wrote figures and metrics to {OUT}")


if __name__ == "__main__":
    main()
