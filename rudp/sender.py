"""Sending side: 3-way handshake, sliding window with Reno, adaptive RTO, FIN teardown.

Sequence numbers on the wire are 32-bit and start at a random ISN. Internally the
sender tracks byte offsets into the data (snd_una / snd_nxt / snd_max), which
keeps the window arithmetic free of wraparound for streams under 4 GiB.
"""

from __future__ import annotations

import math
import random
import socket
import time
from dataclasses import dataclass

from .config import TransportConfig
from .congestion import RenoCongestionControl
from .packet import ACK, FIN, SEQ_MOD, SYN, Packet, PacketError, decode
from .rtt import RTTEstimator


@dataclass
class TransferStats:
    bytes_delivered: int = 0
    segments_sent: int = 0
    retransmissions: int = 0
    timeouts: int = 0
    fast_retransmits: int = 0
    dup_acks: int = 0
    rtt_samples: int = 0
    corrupt_dropped: int = 0
    duration: float = 0.0  # handshake start to FIN-ACK, excluding TIME_WAIT

    @property
    def goodput_mbps(self) -> float:
        return self.bytes_delivered * 8 / self.duration / 1e6 if self.duration > 0 else 0.0


@dataclass(frozen=True)
class Event:
    t: float
    kind: str
    cwnd: float
    ssthresh: float
    srtt: float | None
    rto: float
    snd_una: int
    flight: int


class Sender:
    def __init__(self, remote: tuple[str, int], config: TransportConfig | None = None):
        self.remote = (socket.gethostbyname(remote[0]), remote[1])
        self.config = config or TransportConfig()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtt = RTTEstimator(self.config.initial_rto, self.config.min_rto, self.config.max_rto)
        self.cc = RenoCongestionControl(self.config.initial_cwnd, self.config.initial_ssthresh)
        self.stats = TransferStats()
        self.events: list[Event] = []
        self.isn = random.getrandbits(32)
        self.peer_isn = 0
        self.peer_wnd = 1
        self._t0 = time.monotonic()
        self._snd_una = self._snd_nxt = self._snd_max = 0
        self._sent_at: dict[int, tuple[float, bool]] = {}  # segment start -> (send time, was retransmitted)
        self._timer: float | None = None
        self._retries = 0

    def send(self, data: bytes) -> TransferStats:
        """Open a connection, reliably deliver `data`, and close it."""
        self._t0 = time.monotonic()
        self._connect()
        self._transfer(data)
        peer_fin = self._close(len(data))
        self.stats.duration = time.monotonic() - self._t0
        self.stats.bytes_delivered = len(data)
        self._time_wait(len(data), peer_fin)
        return self.stats

    def close(self) -> None:
        self.sock.close()

    # -- connection setup and teardown --------------------------------------

    def _connect(self) -> None:
        syn = Packet(self.isn, 0, SYN)
        retries = 0
        while True:
            sent = time.monotonic()
            self._xmit(syn)
            deadline = sent + self.rtt.rto
            while (now := time.monotonic()) < deadline:
                pkt = self._recv(deadline - now)
                if pkt and pkt.has(SYN) and pkt.has(ACK) and pkt.ack == (self.isn + 1) % SEQ_MOD:
                    self.peer_isn = pkt.seq
                    self.peer_wnd = pkt.wnd
                    if retries == 0:  # Karn: a retransmitted SYN gives an ambiguous sample
                        self.rtt.sample(time.monotonic() - sent)
                        self.stats.rtt_samples += 1
                    self._send_handshake_ack()
                    self._log("established", time.monotonic())
                    return
            retries += 1
            if retries > self.config.max_retries:
                raise ConnectionError("no SYN-ACK from peer")
            self.stats.timeouts += 1
            self.stats.retransmissions += 1
            self.rtt.backoff()

    def _send_handshake_ack(self) -> None:
        self._xmit(Packet(self.isn + 1, self.peer_isn + 1, ACK))

    def _close(self, total: int) -> int:
        """Send FIN until the peer's FIN-ACK arrives; returns the peer's FIN sequence number."""
        fin_seq = self.isn + 1 + total
        fin = Packet(fin_seq, self.peer_isn + 1, FIN | ACK)
        retries = 0
        while True:
            self._xmit(fin)
            deadline = time.monotonic() + self.rtt.rto
            while (now := time.monotonic()) < deadline:
                pkt = self._recv(deadline - now)
                if pkt and pkt.has(FIN) and pkt.has(ACK) and pkt.ack == (fin_seq + 1) % SEQ_MOD:
                    self._xmit(Packet(fin_seq + 1, pkt.seq + 1, ACK))
                    return pkt.seq
            retries += 1
            if retries > self.config.max_retries:
                raise ConnectionError("no FIN-ACK from peer")
            self.rtt.backoff()

    def _time_wait(self, total: int, peer_fin: int) -> None:
        """Linger so that a retransmitted FIN-ACK (our final ACK was lost) still gets answered."""
        final_ack = Packet(self.isn + 2 + total, peer_fin + 1, ACK)
        deadline = time.monotonic() + self.config.time_wait
        while (now := time.monotonic()) < deadline:
            pkt = self._recv(deadline - now)
            if pkt and pkt.has(FIN):
                self._xmit(final_ack)

    # -- data transfer ------------------------------------------------------

    def _transfer(self, data: bytes) -> None:
        total = len(data)
        while self._snd_una < total:
            now = time.monotonic()
            self._fill_window(data, now)
            wait = self._timer - now if self._timer is not None else self.config.idle_timeout
            pkt = self._recv(wait)
            now = time.monotonic()
            if pkt is not None:
                self._on_packet(pkt, data, now)
            if self._timer is not None and now >= self._timer and self._snd_una < total:
                self._on_timeout(data, now)

    def _fill_window(self, data: bytes, now: float) -> None:
        flight = self._snd_nxt - self._snd_una
        window = min(self.cc.cwnd, self.peer_wnd)
        if flight == 0:
            window = max(window, 1)  # acts as a window probe if the peer advertised zero
        limit = int(window) * self.config.mss
        while self._snd_nxt < len(data) and self._snd_nxt - self._snd_una < limit:
            self._snd_nxt = self._send_segment(data, self._snd_nxt, now)
            if self._timer is None:
                self._timer = now + self.rtt.rto

    def _send_segment(self, data: bytes, start: int, now: float) -> int:
        end = min(start + self.config.mss, len(data))
        retransmit = start < self._snd_max
        tsval = self._timestamp(now) if self.config.timestamps else 0
        self._xmit(Packet(self.isn + 1 + start, self.peer_isn + 1, ACK, 0, data[start:end], tsval))
        self.stats.segments_sent += 1
        if retransmit:
            self.stats.retransmissions += 1
        self._sent_at[start] = (now, retransmit)
        self._snd_max = max(self._snd_max, end)
        return end

    def _on_packet(self, pkt: Packet, data: bytes, now: float) -> None:
        if not pkt.has(ACK):
            return
        if pkt.has(SYN):
            # The peer is still in SYN_RCVD, so our handshake ACK was lost.
            self._send_handshake_ack()
            return
        ack_off = (pkt.ack - self.isn - 1) % SEQ_MOD
        if ack_off > self._snd_max:
            return
        self.peer_wnd = pkt.wnd
        if ack_off > self._snd_una:
            self._on_new_ack(ack_off, pkt.tsecr, now)
        elif ack_off == self._snd_una and not pkt.payload and self._snd_nxt > self._snd_una:
            self._on_dup_ack(data, now)

    def _on_new_ack(self, ack_off: int, tsecr: int, now: float) -> None:
        mss = self.config.mss
        acked_starts = [s for s in self._sent_at if s < ack_off]
        if self.config.timestamps and tsecr:
            # The echoed stamp names the transmission that advanced the receiver, retransmission or not.
            self.rtt.sample(((self._timestamp(now) - tsecr) % SEQ_MOD) / 1e6)
            self.stats.rtt_samples += 1
        elif not self.config.timestamps and acked_starts and not any(self._sent_at[s][1] for s in acked_starts):
            # Karn's rule: only sample when nothing in the acknowledged range was retransmitted,
            # otherwise the ACK cannot be matched to a single transmission.
            self.rtt.sample(now - self._sent_at[max(acked_starts)][0])
            self.stats.rtt_samples += 1
        for s in acked_starts:
            del self._sent_at[s]

        flight_segments = math.ceil((self._snd_nxt - self._snd_una) / mss)
        acked_segments = math.ceil((ack_off - self._snd_una) / mss)
        self._snd_una = ack_off
        # After a go-back the receiver may already hold data past snd_nxt.
        self._snd_nxt = max(self._snd_nxt, ack_off)
        self._retries = 0
        self.cc.on_new_ack(acked_segments, cwnd_limited=flight_segments + 1 > self.cc.cwnd)
        self._timer = now + self.rtt.rto if self._snd_nxt > self._snd_una else None
        self._log("ack", now)

    def _on_dup_ack(self, data: bytes, now: float) -> None:
        self.stats.dup_acks += 1
        flight_segments = math.ceil((self._snd_nxt - self._snd_una) / self.config.mss)
        if self.cc.on_dup_ack(flight_segments):
            self.stats.fast_retransmits += 1
            self._send_segment(data, self._snd_una, now)
            self._timer = now + self.rtt.rto
            self._log("fast_retransmit", now)
        else:
            self._log("dup_ack", now)

    def _on_timeout(self, data: bytes, now: float) -> None:
        self._retries += 1
        if self._retries > self.config.max_retries:
            raise ConnectionError(f"{self._retries - 1} consecutive timeouts at offset {self._snd_una}")
        self.stats.timeouts += 1
        self.cc.on_timeout(math.ceil((self._snd_nxt - self._snd_una) / self.config.mss))
        self.rtt.backoff()
        # Go back to snd_una: without SACK the sender cannot know what else arrived.
        self._snd_nxt = self._send_segment(data, self._snd_una, now)
        self._timer = now + self.rtt.rto
        self._log("timeout", now)

    # -- I/O ----------------------------------------------------------------

    def _xmit(self, pkt: Packet) -> None:
        self.sock.sendto(pkt.encode(), self.remote)

    def _recv(self, timeout: float) -> Packet | None:
        self.sock.settimeout(max(timeout, 1e-4))
        try:
            datagram, addr = self.sock.recvfrom(65535)
        except TimeoutError:
            return None
        if addr != self.remote:
            return None
        try:
            return decode(datagram)
        except PacketError:
            self.stats.corrupt_dropped += 1
            return None

    def _timestamp(self, now: float) -> int:
        """Microseconds since the connection started, offset by one so zero can mean 'absent'."""
        return (int((now - self._t0) * 1e6) + 1) % SEQ_MOD

    def _log(self, kind: str, now: float) -> None:
        self.events.append(Event(now - self._t0, kind, self.cc.cwnd, self.cc.ssthresh, self.rtt.srtt,
                                 self.rtt.rto, self._snd_una, self._snd_nxt - self._snd_una))
