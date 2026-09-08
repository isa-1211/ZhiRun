"""Shared publication figure style and deterministic export helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from PIL import Image


MM_PER_INCH = 25.4
FULL_WIDTH_IN = 170 / MM_PER_INCH

# Okabe-Ito-derived, color-vision-deficiency-friendly colors. Repeated meanings
# keep the same color throughout the manuscript and also use shape/hatch cues.
COLORS = {
    "water": "#0072B2",
    "nitrogen": "#D55E00",
    "phosphorus": "#009E73",
    "potassium": "#CC79A7",
    "outlet": "#4D4D4D",
    "reference": "#5B5B5B",
    "accent": "#E69F00",
    "light": "#E8EEF2",
    "grid": "#D7DCE0",
    "text": "#202124",
}
TARGET_COLORS = [COLORS["water"], COLORS["nitrogen"], COLORS["phosphorus"], COLORS["potassium"]]
TARGET_MARKERS = ["o", "s", "^", "D"]
TARGET_LINESTYLES = ["-", "--", "-.", ":"]
HATCHES = ["", "///", "xx", "..", "\\\\"]


def publication_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.titlesize": 8.5,
        "axes.titleweight": "semibold",
        "axes.labelsize": 8,
        "axes.linewidth": 0.7,
        "axes.edgecolor": COLORS["text"],
        "axes.labelcolor": COLORS["text"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "xtick.color": COLORS["text"],
        "ytick.color": COLORS["text"],
        "legend.fontsize": 7,
        "legend.frameon": False,
        "figure.dpi": 180,
        "figure.facecolor": "white",
        "savefig.dpi": 600,
        "savefig.facecolor": "white",
        "savefig.transparent": False,
        "savefig.bbox": None,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "hatch.linewidth": 0.7,
    })


def panel_label(ax, label: str) -> None:
    ax.text(
        -0.12,
        1.04,
        label.lower(),
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=9,
        fontweight="bold",
        color=COLORS["text"],
    )


def apply_hatches(bars, hatches=HATCHES) -> None:
    for index, bar in enumerate(bars):
        bar.set_hatch(hatches[index % len(hatches)])
        bar.set_edgecolor(COLORS["text"])
        bar.set_linewidth(0.45)


def export_figure(fig, out_dir: Path, stem: str, provenance: dict | None = None, dpi: int = 600) -> None:
    """Export exact-size publication files plus a compact reproducibility manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()
    width, height = fig.get_size_inches()
    common = {"facecolor": "white", "transparent": False, "bbox_inches": None}
    png_path = out_dir / f"{stem}.png"
    tiff_path = out_dir / f"{stem}.tiff"
    fig.savefig(png_path, dpi=dpi, **common)
    fig.savefig(out_dir / f"{stem}.pdf", **common)
    svg_path = out_dir / f"{stem}.svg"
    fig.savefig(svg_path, **common)
    fig.savefig(
        tiff_path,
        dpi=dpi,
        format="tiff",
        pil_kwargs={"compression": "tiff_lzw"},
        **common,
    )
    # Matplotlib can retain an unused alpha channel even with a white opaque
    # canvas. Remove it explicitly so publisher preflight sees true RGB files.
    with Image.open(png_path) as image:
        image.convert("RGB").save(png_path, dpi=(dpi, dpi), optimize=True)
    with Image.open(tiff_path) as image:
        image.convert("RGB").save(tiff_path, dpi=(dpi, dpi), compression="tiff_lzw")
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text("\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n", encoding="utf-8")
    manifest = {
        "figure": stem,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "physical_size_in": [round(float(width), 4), round(float(height), 4)],
        "raster_dpi": dpi,
        "pixel_size": [round(float(width) * dpi), round(float(height) * dpi)],
        "formats": ["png", "pdf", "svg", "tiff"],
        "background": "opaque white",
        "color_encoding": "color plus marker, line style, or hatch where categories are compared",
        **(provenance or {}),
    }
    (out_dir / f"{stem}.export.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plt.close(fig)
