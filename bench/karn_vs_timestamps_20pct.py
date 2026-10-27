import random, threading, time, sys
sys.path.insert(0, ".")
from rudp.harness import run_transfer
from rudp.proxy import LinkProfile
from rudp.config import TransportConfig
for ts in (True, False):
    out = {}
    def go():
        t = time.monotonic()
        r = run_transfer(random.Random(7).randbytes(100_000), LinkProfile(loss=0.2, delay=0.002),
                         config=TransportConfig(initial_rto=0.2, min_rto=0.2, time_wait=1.0, timestamps=ts), seed=3)
        out.update(ok=r.intact, dur=r.sender.duration, rto=max(e.rto for e in r.events), to=r.sender.timeouts, wall=time.monotonic()-t)
    th = threading.Thread(target=go, daemon=True); th.start(); th.join(400)
    print(f"timestamps={ts}:", out or "did not finish within 400 s", flush=True)
