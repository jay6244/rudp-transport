"""RTT sampling and RTO-floor usage at 10% loss, timestamps vs. Karn-only.

    .venv/bin/python bench/rto_floor_10pct.py
"""
import random, sys
sys.path.insert(0, ".")
from rudp.harness import run_transfer
from rudp.proxy import LinkProfile
from rudp.config import TransportConfig
for ts in (True, False):
    for seed in (1, 2, 3):
        r = run_transfer(random.Random(seed).randbytes(512*1024), LinkProfile(loss=0.10, delay=0.010),
                         config=TransportConfig(timestamps=ts), seed=seed)
        acks = [e for e in r.events if e.kind == "ack"]
        at_floor = sum(abs(e.rto - 0.2) < 1e-9 for e in acks) / len(acks)
        srtts = [e.srtt for e in acks if e.srtt]
        print(f"ts={ts!s:5} seed={seed} samples={r.sender.rtt_samples:4d} acks={len(acks):4d} "
              f"rto_at_200ms_floor={at_floor:.0%} srtt_ms min/max={min(srtts)*1e3:.1f}/{max(srtts)*1e3:.1f} "
              f"max_rto={max(e.rto for e in r.events):.2f} timeouts={r.sender.timeouts}", flush=True)
