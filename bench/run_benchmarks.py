"""Measure the transport over impaired loopback links and write CSVs to results/.

    .venv/bin/python bench/run_benchmarks.py

Experiments:
  loss_sweep.csv        goodput / retransmissions / timeouts vs. random loss (both directions),
                        with RFC 7323-style timestamps on and off (Karn's rule only)
  trace_bottleneck.csv  cwnd/ssthresh/RTO trace through an 8 Mbit/s bottleneck with a small queue
  trace_loss.csv        the same trace on an unconstrained link with 2% random loss
"""

from __future__ import annotations

import csv
import hashlib
import platform
import random
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rudp.config import TransportConfig  # noqa: E402
from rudp.harness import run_transfer, write_events_csv  # noqa: E402
from rudp.proxy import FORWARD, LinkProfile  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "results"
ONE_WAY_DELAY = 0.010
SWEEP_BYTES = 512 * 1024
SWEEP_LOSSES = [0.0, 0.01, 0.02, 0.05, 0.10]
SEEDS = [1, 2, 3]


def loss_sweep() -> list[dict]:
    rows = []
    for timestamps in (True, False):
        config = TransportConfig(timestamps=timestamps)
        for loss in SWEEP_LOSSES:
            for seed in SEEDS:
                data = random.Random(seed).randbytes(SWEEP_BYTES)
                profile = LinkProfile(loss=loss, delay=ONE_WAY_DELAY)
                wall = time.monotonic()
                result = run_transfer(data, profile, config=config, seed=seed)
                s = result.sender
                rows.append({
                    "timestamps": timestamps, "loss": loss, "seed": seed, "bytes": len(data),
                    "sha256_match": hashlib.sha256(result.received).digest() == hashlib.sha256(data).digest(),
                    "duration_s": round(s.duration, 4), "goodput_mbps": round(s.goodput_mbps, 4),
                    "segments_sent": s.segments_sent, "retransmissions": s.retransmissions,
                    "timeouts": s.timeouts, "fast_retransmits": s.fast_retransmits,
                    "max_rto_s": max(e.rto for e in result.events),
                    "link_lost_fwd": result.link[FORWARD].lost,
                })
                print(f"ts={timestamps!s:5} loss={loss:.2f} seed={seed} {s.duration:6.2f}s "
                      f"{s.goodput_mbps:6.2f} Mbit/s rtx={s.retransmissions} to={s.timeouts} "
                      f"(wall {time.monotonic() - wall:.1f}s)", flush=True)
    return rows


def write_rows(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarise(rows: list[dict]) -> str:
    lines = [
        f"Measured {time.strftime('%Y-%m-%d')} on {platform.system()} {platform.machine()}, Python {platform.python_version()}.",
        f"{SWEEP_BYTES // 1024} KiB per transfer, {ONE_WAY_DELAY * 1000:.0f} ms one-way delay (20 ms RTT), "
        f"loss applied independently to both directions, {len(SEEDS)} seeds per cell. Values are mean (min-max).",
        "",
        "| RTT source | loss | goodput Mbit/s | retransmissions | timeouts | fast retransmits | max RTO s | intact |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def cell(values, fmt):
        return f"{fmt.format(statistics.mean(values))} ({fmt.format(min(values))}-{fmt.format(max(values))})"

    for timestamps in (True, False):
        for loss in SWEEP_LOSSES:
            group = [r for r in rows if r["timestamps"] == timestamps and r["loss"] == loss]
            lines.append("| {} | {:.0%} | {} | {} | {} | {} | {} | {}/{} |".format(
                "timestamps" if timestamps else "Karn only", loss,
                cell([r["goodput_mbps"] for r in group], "{:.2f}"),
                cell([r["retransmissions"] for r in group], "{:.0f}"),
                cell([r["timeouts"] for r in group], "{:.0f}"),
                cell([r["fast_retransmits"] for r in group], "{:.0f}"),
                cell([r["max_rto_s"] for r in group], "{:.2f}"),
                sum(r["sha256_match"] for r in group), len(group)))
    return "\n".join(lines) + "\n"


def trace(name: str, size: int, forward: LinkProfile, reverse: LinkProfile) -> str:
    data = random.Random(0).randbytes(size)
    result = run_transfer(data, forward, reverse, config=replace(TransportConfig()), seed=0)
    write_events_csv(result.events, RESULTS / f"trace_{name}.csv")
    s, link = result.sender, result.link[FORWARD]
    return (f"- `{name}`: {size // 1024} KiB, intact={result.intact}, {s.duration:.2f}s, "
            f"{s.goodput_mbps:.2f} Mbit/s, retransmissions={s.retransmissions}, timeouts={s.timeouts}, "
            f"fast retransmits={s.fast_retransmits}, queue drops={link.queue_drops}, random losses={link.lost}\n")


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    rows = loss_sweep()
    write_rows(rows, RESULTS / "loss_sweep.csv")
    summary = summarise(rows)
    summary += "\nTraces:\n"
    summary += trace("bottleneck", 2 * 1024 * 1024,
                     LinkProfile(delay=ONE_WAY_DELAY, rate_bps=8e6, queue_bytes=16 * 1024),
                     LinkProfile(delay=ONE_WAY_DELAY))
    summary += trace("loss", 1024 * 1024, LinkProfile(loss=0.02, delay=ONE_WAY_DELAY), LinkProfile(delay=ONE_WAY_DELAY))
    (RESULTS / "summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
