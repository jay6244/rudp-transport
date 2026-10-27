"""Render the benchmark CSVs in results/ as PNGs.

    .venv/bin/python bench/plot_results.py
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "results"

# Validated categorical slots (light surface) plus recessive ink for text, grid and reference lines.
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, VIOLET = "#2a78d6", "#eb6834", "#6250d6"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK, "axes.titlesize": 12,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": INK_2, "ytick.color": INK_2, "font.size": 10, "legend.frameon": False,
    "legend.labelcolor": INK, "lines.linewidth": 2,
})


def read(name: str) -> list[dict]:
    with open(RESULTS / name) as f:
        return list(csv.DictReader(f))


def plot_trace(name: str, title: str) -> None:
    events = read(f"trace_{name}.csv")
    t = [float(e["t"]) for e in events]
    cwnd = [float(e["cwnd"]) for e in events]
    ssthresh = [float(e["ssthresh"]) if math.isfinite(float(e["ssthresh"])) else math.nan for e in events]

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(t, cwnd, color=BLUE, label="cwnd", drawstyle="steps-post")
    ax.plot(t, ssthresh, color=INK_2, linewidth=1.2, linestyle="--", label="ssthresh", drawstyle="steps-post")
    for kind, color, marker, label in (("fast_retransmit", ORANGE, "v", "fast retransmit"),
                                       ("timeout", VIOLET, "x", "timeout")):
        pts = [(float(e["t"]), float(e["cwnd"])) for e in events if e["kind"] == kind]
        if pts:
            ax.scatter(*zip(*pts), color=color, marker=marker, s=40, zorder=3, linewidths=1.8,
                       label=f"{label} ({len(pts)})", edgecolors=SURFACE if marker == "v" else None)
    ax.set_title(title)
    ax.set_xlabel("time since connection start (s)")
    ax.set_ylabel("segments")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", ncols=4)
    fig.tight_layout()
    fig.savefig(RESULTS / f"cwnd_{name}.png", dpi=150)
    plt.close(fig)


def plot_rtt(name: str, title: str) -> None:
    events = [e for e in read(f"trace_{name}.csv") if e["srtt"]]
    t = [float(e["t"]) for e in events]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(t, [float(e["srtt"]) * 1000 for e in events], color=BLUE, label="smoothed RTT")
    ax.plot(t, [float(e["rto"]) * 1000 for e in events], color=ORANGE, label="RTO", drawstyle="steps-post")
    ax.set_title(title)
    ax.set_xlabel("time since connection start (s)")
    ax.set_ylabel("milliseconds")
    ax.set_ylim(bottom=0)
    ax.legend(loc="center right", ncols=2)
    fig.tight_layout()
    fig.savefig(RESULTS / f"rtt_{name}.png", dpi=150)
    plt.close(fig)


def plot_sweep() -> None:
    groups: dict[tuple[str, float], list[float]] = defaultdict(list)
    for row in read("loss_sweep.csv"):
        groups[(row["timestamps"], float(row["loss"]))].append(float(row["goodput_mbps"]))

    # Karn-only runs measured within noise of these at <= 10% loss, so only the default mode is drawn.
    fig, ax = plt.subplots(figsize=(7, 4))
    losses = sorted(loss for ts, loss in groups if ts == "True")
    x = [loss * 100 for loss in losses]
    values = [groups[("True", loss)] for loss in losses]
    mean = [sum(v) / len(v) for v in values]
    ax.fill_between(x, [min(v) for v in values], [max(v) for v in values], color=BLUE, alpha=0.15, linewidth=0)
    ax.plot(x, mean, color=BLUE, marker="o", markersize=7, markeredgecolor=SURFACE)
    for xi, m in zip(x, mean):
        ax.annotate(f"{m:.2f}", (xi, m), xytext=(6, 6), textcoords="offset points", color=INK, fontsize=9)
    ax.set_yscale("log")
    ax.set_title("Goodput vs. random loss (20 ms RTT, loss both directions)")
    ax.set_xlabel("packet loss per direction (%)")
    ax.set_ylabel("goodput, Mbit/s (log scale)")
    ax.text(0, -0.2, "Line = mean of 3 seeds; band = min-max. 512 KiB per transfer.", transform=ax.transAxes,
            color=INK_2, fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULTS / "goodput_vs_loss.png", dpi=150)
    plt.close(fig)


def main() -> None:
    plot_trace("bottleneck", "Reno cwnd through an 8 Mbit/s bottleneck with a 16 KiB drop-tail queue")
    plot_trace("loss", "Reno cwnd with 2% random loss on the data path")
    plot_rtt("bottleneck", "Smoothed RTT and RTO through the 8 Mbit/s bottleneck")
    plot_sweep()
    print("wrote", ", ".join(p.name for p in sorted(RESULTS.glob("*.png"))))


if __name__ == "__main__":
    main()
