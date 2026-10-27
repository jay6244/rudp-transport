"""TCP Reno congestion control (RFC 5681), with the window measured in segments."""

from __future__ import annotations

import math

DUP_ACK_THRESHOLD = 3


class RenoCongestionControl:
    def __init__(self, initial_cwnd: float = 2.0, initial_ssthresh: float = math.inf):
        self.cwnd = float(initial_cwnd)
        self.ssthresh = float(initial_ssthresh)
        self.dup_acks = 0
        self.in_recovery = False

    @property
    def state(self) -> str:
        if self.in_recovery:
            return "fast_recovery"
        return "slow_start" if self.cwnd < self.ssthresh else "congestion_avoidance"

    def on_new_ack(self, acked_segments: int, cwnd_limited: bool = True) -> None:
        """An ACK advanced snd_una by `acked_segments`.

        `cwnd_limited` is False when the sender was not using its full window; growing
        cwnd then would only measure idle capacity (the RFC 7661 concern), so it is held.
        """
        self.dup_acks = 0
        if self.in_recovery:
            # Reno leaves fast recovery on the first new ACK and deflates the window.
            self.in_recovery = False
            self.cwnd = self.ssthresh
            return
        if not cwnd_limited:
            return
        if self.cwnd < self.ssthresh:
            # Appropriate Byte Counting with L = 2 (RFC 3465).
            self.cwnd += min(acked_segments, 2)
        else:
            self.cwnd += acked_segments / self.cwnd

    def on_dup_ack(self, flight_segments: int) -> bool:
        """Count a duplicate ACK. Returns True when the caller should fast-retransmit."""
        self.dup_acks += 1
        if self.in_recovery:
            # Each further duplicate means another segment left the network.
            self.cwnd += 1
            return False
        if self.dup_acks == DUP_ACK_THRESHOLD:
            self.ssthresh = max(flight_segments / 2, 2.0)
            self.cwnd = self.ssthresh + DUP_ACK_THRESHOLD
            self.in_recovery = True
            return True
        return False

    def on_timeout(self, flight_segments: int) -> None:
        self.ssthresh = max(flight_segments / 2, 2.0)
        self.cwnd = 1.0
        self.dup_acks = 0
        self.in_recovery = False
