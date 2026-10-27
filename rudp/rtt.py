"""Retransmission timeout estimation (Jacobson/Karels, as specified in RFC 6298)."""

from __future__ import annotations


class RTTEstimator:
    ALPHA = 1 / 8
    BETA = 1 / 4
    K = 4

    def __init__(self, initial_rto: float = 1.0, min_rto: float = 0.2, max_rto: float = 60.0, granularity: float = 0.001):
        self.min_rto = min_rto
        self.max_rto = max_rto
        self.granularity = granularity
        self.srtt: float | None = None
        self.rttvar: float | None = None
        self.rto = initial_rto

    def sample(self, rtt: float) -> None:
        """Fold in one RTT measurement. Callers must apply Karn's rule (no samples from retransmitted segments)."""
        if self.srtt is None:
            self.srtt = rtt
            self.rttvar = rtt / 2
        else:
            # RTTVAR is updated with the previous SRTT, per RFC 6298 section 2.3.
            self.rttvar = (1 - self.BETA) * self.rttvar + self.BETA * abs(self.srtt - rtt)
            self.srtt = (1 - self.ALPHA) * self.srtt + self.ALPHA * rtt
        self.rto = self._clamp(self.srtt + max(self.granularity, self.K * self.rttvar))

    def backoff(self) -> None:
        """Exponential backoff after a retransmission timeout (RFC 6298 section 5.5)."""
        self.rto = self._clamp(self.rto * 2)

    def _clamp(self, value: float) -> float:
        return min(max(value, self.min_rto), self.max_rto)
