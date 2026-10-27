"""Receiving side: accepts one connection, buffers out-of-order segments, sends cumulative ACKs.

Every arriving data segment is answered with an ACK for the next in-order byte,
so a gap produces duplicate ACKs, which is what drives the sender's fast retransmit.
"""

from __future__ import annotations

import random
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from .config import TransportConfig
from .packet import ACK, FIN, MAX_WINDOW, SEQ_MOD, SYN, Packet, PacketError, decode
from .rtt import RTTEstimator

LISTEN, SYN_RCVD, ESTABLISHED, LAST_ACK, CLOSED = "LISTEN", "SYN_RCVD", "ESTABLISHED", "LAST_ACK", "CLOSED"


@dataclass
class ReceiverStats:
    segments_received: int = 0
    out_of_order: int = 0
    duplicates: int = 0
    out_of_window: int = 0
    corrupt_dropped: int = 0
    acks_sent: int = 0


class Receiver:
    def __init__(self, bind: tuple[str, int] = ("127.0.0.1", 0), config: TransportConfig | None = None):
        self.config = config or TransportConfig()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(bind)
        self.isn = random.getrandbits(32)
        self.stats = ReceiverStats()
        self.state = LISTEN
        # Sampled from the handshake so the FIN-ACK timer tracks the path instead of initial_rto.
        self.rtt = RTTEstimator(self.config.initial_rto, self.config.min_rto, self.config.max_rto)

    @property
    def address(self) -> tuple[str, int]:
        return self.sock.getsockname()

    def close(self) -> None:
        self.sock.close()

    def receive(self, on_data: Callable[[bytes], None] | None = None, listen_timeout: float | None = None) -> bytes:
        """Accept one connection and return everything it delivered, in order."""
        self._capacity = self.config.rcv_buffer_segments * self.config.mss
        self._data = bytearray()
        self._on_data = on_data
        self._ooo: dict[int, bytes] = {}
        self._ooo_bytes = 0
        self._rcv_nxt = 0
        self._fin_off: int | None = None
        self._ts_recent = 0
        self._peer: tuple[str, int] | None = None
        self._peer_isn = 0
        self._ctl: Packet | None = None  # SYN-ACK or FIN-ACK awaiting acknowledgement
        self._ctl_deadline = 0.0
        self._ctl_rto = self.config.initial_rto
        self._ctl_retries = 0
        self.state = LISTEN
        last_heard = time.monotonic()

        while self.state != CLOSED:
            now = time.monotonic()
            if self.state == LISTEN:
                wait = None if listen_timeout is None else listen_timeout - (now - last_heard)
            else:
                wait = self.config.idle_timeout - (now - last_heard)
            if self._ctl is not None:
                wait = self._ctl_deadline - now if wait is None else min(wait, self._ctl_deadline - now)
            got = self._recv(wait)
            now = time.monotonic()
            if got is not None:
                pkt, addr = got
                if self.state == LISTEN or addr == self._peer:
                    last_heard = now
                    self._on_packet(pkt, addr, now)
            if self._ctl is not None and now >= self._ctl_deadline and self.state != CLOSED:
                self._on_ctl_timeout(now)
            limit = listen_timeout if self.state == LISTEN else self.config.idle_timeout
            if self.state != CLOSED and limit is not None and now - last_heard >= limit:
                raise ConnectionError(f"peer silent in state {self.state}")
        return bytes(self._data)

    # -- packet handling ----------------------------------------------------

    def _on_packet(self, pkt: Packet, addr: tuple[str, int], now: float) -> None:
        if self.state == LISTEN:
            if pkt.has(SYN) and not pkt.has(ACK):
                self._peer = addr
                self._peer_isn = pkt.seq
                self.state = SYN_RCVD
                self._send_ctl(Packet(self.isn, self._peer_isn + 1, SYN | ACK, self._window()), now)
            return

        if self.state == SYN_RCVD:
            if pkt.has(SYN):  # our SYN-ACK was lost
                self._resend_ctl(now)
                return
            if not (pkt.has(ACK) and pkt.ack == (self.isn + 1) % SEQ_MOD):
                return
            if self._ctl_retries == 0 and not self._ctl_resent:  # Karn's rule
                self.rtt.sample(now - self._ctl_sent_at)
            self.state = ESTABLISHED
            self._ctl = None

        if self.state == LAST_ACK:
            if pkt.has(ACK) and not pkt.has(FIN) and not pkt.payload and pkt.ack == (self.isn + 2) % SEQ_MOD:
                self.state = CLOSED
            else:  # retransmitted FIN or data: the FIN-ACK covers both
                self._resend_ctl(now)
            return

        if pkt.payload:
            self._on_segment(pkt)
        if pkt.has(FIN):
            self._fin_off = (pkt.seq - self._peer_isn - 1) % SEQ_MOD
        if self._fin_off is not None and self._rcv_nxt == self._fin_off:
            self.state = LAST_ACK
            self._send_ctl(Packet(self.isn + 1, self._peer_isn + 2 + self._fin_off, FIN | ACK, self._window()), now)
        elif pkt.payload or pkt.has(FIN):
            self._send_ack()

    def _on_segment(self, pkt: Packet) -> None:
        self.stats.segments_received += 1
        off = (pkt.seq - self._peer_isn - 1) % SEQ_MOD
        if off <= self._rcv_nxt:
            # RFC 7323: echo the stamp of the segment at the left edge, so out-of-order
            # arrivals never shorten the measured RTT and a hole-filling retransmission is timed.
            self._ts_recent = pkt.tsval
        if off == self._rcv_nxt:
            self._deliver(pkt.payload)
            while self._rcv_nxt in self._ooo:
                chunk = self._ooo.pop(self._rcv_nxt)
                self._ooo_bytes -= len(chunk)
                self._deliver(chunk)
        elif off < self._rcv_nxt:
            self.stats.duplicates += 1
        elif off + len(pkt.payload) > self._rcv_nxt + self._capacity:
            self.stats.out_of_window += 1
        elif off in self._ooo:
            self.stats.duplicates += 1
        else:
            self.stats.out_of_order += 1
            self._ooo[off] = pkt.payload
            self._ooo_bytes += len(pkt.payload)

    def _deliver(self, chunk: bytes) -> None:
        self._data += chunk
        self._rcv_nxt += len(chunk)
        if self._on_data is not None:
            self._on_data(chunk)

    def _window(self) -> int:
        return min((self._capacity - self._ooo_bytes) // self.config.mss, MAX_WINDOW)

    def _send_ack(self) -> None:
        self._xmit(Packet(self.isn + 1, self._peer_isn + 1 + self._rcv_nxt, ACK, self._window(), tsecr=self._ts_recent))
        self.stats.acks_sent += 1

    # -- control-packet retransmission (SYN-ACK, FIN-ACK) -------------------

    def _send_ctl(self, pkt: Packet, now: float) -> None:
        self._ctl = pkt
        self._ctl_rto = self.rtt.rto
        self._ctl_retries = 0
        self._ctl_sent_at = now
        self._ctl_resent = False
        self._xmit(pkt)
        self._ctl_deadline = now + self._ctl_rto

    def _resend_ctl(self, now: float) -> None:
        self._ctl_resent = True
        self._xmit(self._ctl)
        self._ctl_deadline = now + self._ctl_rto

    def _on_ctl_timeout(self, now: float) -> None:
        self._ctl_retries += 1
        if self.state == LAST_ACK and self._ctl_retries > self.config.last_ack_retries:
            self.state = CLOSED  # every byte is already delivered; the peer just missed our FIN-ACK
            return
        if self._ctl_retries > self.config.max_retries:
            raise ConnectionError(f"no acknowledgement in state {self.state}")
        self._ctl_rto = min(self._ctl_rto * 2, self.config.max_rto)
        self._resend_ctl(now)

    # -- I/O ----------------------------------------------------------------

    def _xmit(self, pkt: Packet) -> None:
        self.sock.sendto(pkt.encode(), self._peer)

    def _recv(self, timeout: float | None) -> tuple[Packet, tuple[str, int]] | None:
        self.sock.settimeout(None if timeout is None else max(timeout, 1e-4))
        try:
            datagram, addr = self.sock.recvfrom(65535)
        except TimeoutError:
            return None
        try:
            return decode(datagram), addr
        except PacketError:
            self.stats.corrupt_dropped += 1
            return None
