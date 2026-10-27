from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TransportConfig:
    mss: int = 1000                       # payload bytes per segment
    initial_cwnd: float = 2.0             # segments (RFC 5681 allows up to 4 at this MSS)
    initial_ssthresh: float = math.inf
    rcv_buffer_segments: int = 64         # receiver's out-of-order buffer
    initial_rto: float = 1.0              # seconds (RFC 6298)
    min_rto: float = 0.2                  # Linux's floor; RFC 6298 suggests 1s
    max_rto: float = 60.0
    max_retries: int = 12                 # consecutive timeouts before giving up
    last_ack_retries: int = 4             # receiver's FIN-ACK retransmissions before closing anyway
    time_wait: float = 2.0                # sender lingers this long to re-ACK a lost FIN-ACK
    idle_timeout: float = 30.0            # receiver gives up on a silent peer
    timestamps: bool = True               # RTT samples from echoed timestamps; False = Karn's rule only
