"""End-to-end transfers through the impairment proxy on loopback."""

import random

import pytest

from rudp.config import TransportConfig
from rudp.harness import run_transfer
from rudp.packet import SYN, decode
from rudp.proxy import FORWARD, LinkProfile

CONFIG = TransportConfig(initial_rto=0.2, min_rto=0.2, time_wait=1.0)
MSS = CONFIG.mss


def payload(size, seed=0):
    return random.Random(seed).randbytes(size)


class DropSegment:
    """Drops the first `times` transmissions of the data segment at byte `offset`."""

    def __init__(self, offset, times=1):
        self.offset, self.remaining, self.isn = offset, times, None

    def __call__(self, direction, index, datagram):
        if direction != FORWARD:
            return False
        pkt = decode(datagram)
        if pkt.has(SYN):
            self.isn = pkt.seq
            return False
        if pkt.payload and self.remaining and (pkt.seq - self.isn - 1) % 2**32 == self.offset:
            self.remaining -= 1
            return True
        return False


def test_clean_link_delivers_without_retransmission():
    data = payload(300_000)
    result = run_transfer(data, config=CONFIG)
    assert result.intact
    assert result.sender.retransmissions == 0
    assert result.sender.segments_sent == len(data) // MSS


def test_empty_transfer_completes_handshake_and_teardown():
    result = run_transfer(b"", config=CONFIG)
    assert result.intact and result.received == b""


def test_lost_syn_is_retransmitted():
    result = run_transfer(payload(5_000), config=CONFIG, drop_filter=lambda d, i, _: d == FORWARD and i == 0)
    assert result.intact
    assert result.sender.retransmissions >= 1


def test_single_loss_is_repaired_by_fast_retransmit():
    result = run_transfer(payload(60 * MSS), config=CONFIG, drop_filter=DropSegment(20 * MSS))
    assert result.intact
    assert result.sender.fast_retransmits == 1
    assert result.sender.timeouts == 0
    assert result.receiver.out_of_order >= 3
    kinds = [e.kind for e in result.events]
    halved = result.events[kinds.index("fast_retransmit")]
    assert halved.ssthresh == pytest.approx(max(halved.flight / MSS / 2, 2), abs=1)


def test_tail_loss_needs_timeouts_and_backs_off_rto():
    # The last segment has nothing behind it to generate duplicate ACKs.
    data = payload(30 * MSS)
    result = run_transfer(data, config=CONFIG, drop_filter=DropSegment(29 * MSS, times=2))
    assert result.intact
    assert result.sender.timeouts == 2
    timeouts = [e for e in result.events if e.kind == "timeout"]
    assert timeouts[1].rto == pytest.approx(2 * timeouts[0].rto)
    assert all(e.cwnd == 1 for e in timeouts)


@pytest.mark.parametrize("profile", [
    LinkProfile(loss=0.05, delay=0.005),
    LinkProfile(loss=0.10, delay=0.005, jitter=0.004, duplicate=0.05),
    LinkProfile(loss=0.20, delay=0.002),
], ids=["5pct-loss", "10pct-loss-reorder-dup", "20pct-loss"])
def test_random_impairment_still_delivers_identical_bytes(profile):
    data = payload(100_000, seed=7)
    result = run_transfer(data, profile, config=CONFIG, seed=3)
    assert result.intact
    assert result.link[FORWARD].lost > 0
    assert result.sender.retransmissions > 0


def test_corrupted_datagrams_are_dropped_by_checksum():
    data = payload(100_000, seed=1)
    result = run_transfer(data, LinkProfile(corrupt=0.05, delay=0.002), config=CONFIG, seed=5)
    assert result.intact
    assert result.receiver.corrupt_dropped + result.sender.corrupt_dropped > 0


def test_bottleneck_queue_overflow_triggers_congestion_response():
    profile = LinkProfile(delay=0.005, rate_bps=4e6, queue_bytes=8_000)
    result = run_transfer(payload(400_000, seed=2), profile, LinkProfile(delay=0.005), config=CONFIG)
    assert result.intact
    assert result.link[FORWARD].queue_drops > 0
    assert result.sender.fast_retransmits + result.sender.timeouts > 0
    cuts = [e for e in result.events if e.kind in ("fast_retransmit", "timeout")]
    assert all(e.ssthresh < float("inf") for e in cuts)


def test_slow_start_grows_window_on_clean_link():
    result = run_transfer(payload(200_000), LinkProfile(delay=0.005), config=CONFIG)
    cwnds = [e.cwnd for e in result.events if e.kind == "ack"]
    assert cwnds == sorted(cwnds)
    assert cwnds[-1] > CONFIG.initial_cwnd * 8
