Measured 2026-10-24 on Darwin arm64, Python 3.13.3.
512 KiB per transfer, 10 ms one-way delay (20 ms RTT), loss applied independently to both directions, 3 seeds per cell. Values are mean (min-max).

| RTT source | loss | goodput Mbit/s | retransmissions | timeouts | fast retransmits | max RTO s | intact |
|---|---|---|---|---|---|---|---|
| timestamps | 0% | 10.65 (10.09-11.61) | 0 (0-0) | 0 (0-0) | 0 (0-0) | 0.20 (0.20-0.20) | 3/3 |
| timestamps | 1% | 3.13 (2.96-3.22) | 10 (8-12) | 1 (1-1) | 5 (3-7) | 0.40 (0.40-0.40) | 3/3 |
| timestamps | 2% | 1.87 (1.74-2.11) | 13 (12-14) | 2 (1-3) | 12 (11-13) | 0.40 (0.40-0.40) | 3/3 |
| timestamps | 5% | 0.86 (0.76-0.99) | 28 (23-31) | 9 (6-11) | 16 (15-18) | 0.67 (0.40-0.80) | 3/3 |
| timestamps | 10% | 0.38 (0.30-0.52) | 64 (47-82) | 28 (17-39) | 20 (18-22) | 1.33 (0.80-1.60) | 3/3 |
| Karn only | 0% | 10.46 (10.12-11.15) | 0 (0-0) | 0 (0-0) | 0 (0-0) | 0.20 (0.20-0.20) | 3/3 |
| Karn only | 1% | 3.05 (2.89-3.19) | 10 (8-12) | 1 (1-1) | 5 (3-7) | 0.40 (0.40-0.40) | 3/3 |
| Karn only | 2% | 1.90 (1.76-2.17) | 13 (12-14) | 2 (1-3) | 12 (11-13) | 0.40 (0.40-0.40) | 3/3 |
| Karn only | 5% | 0.84 (0.76-0.94) | 28 (23-31) | 9 (6-11) | 16 (15-18) | 0.67 (0.40-0.80) | 3/3 |
| Karn only | 10% | 0.36 (0.28-0.51) | 64 (47-82) | 28 (17-39) | 20 (18-22) | 1.33 (0.80-1.60) | 3/3 |

Traces:
- `bottleneck`: 2048 KiB, intact=True, 3.68s, 4.56 Mbit/s, retransmissions=47, timeouts=2, fast retransmits=12, queue drops=29, random losses=0
- `loss`: 1024 KiB, intact=True, 4.81s, 1.74 Mbit/s, retransmissions=27, timeouts=3, fast retransmits=24, queue drops=0, random losses=27
