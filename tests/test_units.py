import math

import pytest

from rudp.congestion import RenoCongestionControl
from rudp.packet import ACK, FIN, HEADER_SIZE, SEQ_MOD, SYN, Packet, PacketError, decode
from rudp.rtt import RTTEstimator


# -- packet -------------------------------------------------------------------

def test_packet_round_trip():
    pkt = Packet(123456, 654321, SYN | ACK, 64, b"hello", tsval=42, tsecr=7)
    assert decode(pkt.encode()) == pkt
    assert len(pkt.encode()) == HEADER_SIZE + 5


def test_sequence_numbers_wrap_at_32_bits():
    assert decode(Packet(SEQ_MOD + 5, SEQ_MOD - 1, ACK).encode()).seq == 5


@pytest.mark.parametrize("pos", [0, 8, HEADER_SIZE - 1, HEADER_SIZE + 2])
def test_any_flipped_byte_fails_checksum(pos):
    raw = bytearray(Packet(1, 2, FIN, 3, b"payload").encode())
    raw[pos] ^= 0x01
    with pytest.raises(PacketError):
        decode(bytes(raw))


def test_truncated_datagrams_rejected():
    raw = Packet(1, 2, ACK, 0, b"abcdef").encode()
    with pytest.raises(PacketError):
        decode(raw[:HEADER_SIZE - 1])
    with pytest.raises(PacketError):
        decode(raw[:-1])


# -- RTT estimator ------------------------------------------------------------

def test_first_sample_initialises_srtt_and_rttvar():
    est = RTTEstimator(min_rto=0.0)
    est.sample(0.1)
    assert est.srtt == pytest.approx(0.1)
    assert est.rttvar == pytest.approx(0.05)
    assert est.rto == pytest.approx(0.1 + 4 * 0.05)


def test_later_samples_follow_rfc6298():
    est = RTTEstimator(min_rto=0.0)
    est.sample(0.1)
    est.sample(0.2)
    rttvar = 0.75 * 0.05 + 0.25 * abs(0.1 - 0.2)
    srtt = 0.875 * 0.1 + 0.125 * 0.2
    assert est.rttvar == pytest.approx(rttvar)
    assert est.srtt == pytest.approx(srtt)
    assert est.rto == pytest.approx(srtt + 4 * rttvar)


def test_rto_is_clamped_and_backs_off_exponentially():
    est = RTTEstimator(min_rto=0.2, max_rto=1.0)
    est.sample(0.001)
    assert est.rto == 0.2
    est.backoff()
    est.backoff()
    assert est.rto == pytest.approx(0.8)
    est.backoff()
    assert est.rto == 1.0


# -- Reno ---------------------------------------------------------------------

def test_slow_start_roughly_doubles_per_window():
    cc = RenoCongestionControl(initial_cwnd=2)
    for _ in range(2):
        cc.on_new_ack(1)
    assert cc.cwnd == 4
    assert cc.state == "slow_start"


def test_congestion_avoidance_adds_about_one_segment_per_window():
    cc = RenoCongestionControl(initial_cwnd=10, initial_ssthresh=10)
    for _ in range(10):
        cc.on_new_ack(1)
    assert cc.state == "congestion_avoidance"
    assert 10.9 < cc.cwnd < 11.0


def test_third_dup_ack_triggers_fast_retransmit_and_recovery():
    cc = RenoCongestionControl(initial_cwnd=20)
    assert not cc.on_dup_ack(20)
    assert not cc.on_dup_ack(20)
    assert cc.on_dup_ack(20)
    assert cc.ssthresh == 10
    assert cc.cwnd == 13
    assert cc.state == "fast_recovery"
    cc.on_dup_ack(20)
    assert cc.cwnd == 14              # window inflation
    cc.on_new_ack(1)
    assert cc.cwnd == 10              # deflation on exit
    assert cc.state == "congestion_avoidance"


def test_timeout_collapses_window_to_one():
    cc = RenoCongestionControl(initial_cwnd=16)
    cc.on_timeout(16)
    assert (cc.cwnd, cc.ssthresh, cc.state) == (1, 8, "slow_start")


def test_ssthresh_floor_is_two_segments():
    cc = RenoCongestionControl(initial_cwnd=2)
    cc.on_timeout(1)
    assert cc.ssthresh == 2


def test_window_does_not_grow_when_not_cwnd_limited():
    cc = RenoCongestionControl(initial_cwnd=8, initial_ssthresh=math.inf)
    cc.on_new_ack(1, cwnd_limited=False)
    assert cc.cwnd == 8
