"""A UDP relay that impairs traffic: loss, delay, jitter (reordering), duplication,
corruption, and an optional rate-limited bottleneck with a drop-tail queue.

It stands in for Linux `tc netem` so experiments run the same on macOS and Linux.
Impairment decisions come from a seeded RNG; delivery timing still depends on the OS
scheduler, so runs are repeatable in which packets get hit but not bit-for-bit identical.
"""

from __future__ import annotations

import heapq
import itertools
import random
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

FORWARD, REVERSE = "forward", "reverse"  # client -> server, server -> client


@dataclass(frozen=True)
class LinkProfile:
    loss: float = 0.0
    delay: float = 0.0          # one-way, seconds
    jitter: float = 0.0         # extra uniform [0, jitter] delay per packet; reorders
    duplicate: float = 0.0
    corrupt: float = 0.0        # flips one random byte
    rate_bps: float | None = None  # bottleneck rate over UDP payload bytes; None = unlimited
    queue_bytes: int = 64 * 1024   # drop-tail queue in front of the bottleneck


@dataclass
class LinkStats:
    received: int = 0
    forwarded: int = 0
    lost: int = 0
    queue_drops: int = 0
    filtered: int = 0
    duplicated: int = 0
    corrupted: int = 0


DropFilter = Callable[[str, int, bytes], bool]


class LossyProxy:
    """Relays between one client and `target`. Use as a context manager."""

    def __init__(self, target: tuple[str, int], forward: LinkProfile | None = None, reverse: LinkProfile | None = None,
                 bind: tuple[str, int] = ("127.0.0.1", 0), seed: int = 0, drop_filter: DropFilter | None = None):
        self.target = (socket.gethostbyname(target[0]), target[1])
        forward = forward or LinkProfile()
        self.profiles = {FORWARD: forward, REVERSE: reverse if reverse is not None else forward}
        self.stats = {FORWARD: LinkStats(), REVERSE: LinkStats()}
        self.drop_filter = drop_filter
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(bind)
        self._rng = random.Random(seed)
        self._client: tuple[str, int] | None = None
        self._heap: list[tuple[float, int, bytes, tuple[str, int]]] = []
        self._order = itertools.count()
        self._link_free_at = {FORWARD: 0.0, REVERSE: 0.0}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lossy-proxy", daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        return self.sock.getsockname()

    def start(self) -> LossyProxy:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self.sock.close()

    def __enter__(self) -> LossyProxy:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            while self._heap and self._heap[0][0] <= now:
                _, _, datagram, dest = heapq.heappop(self._heap)
                self.sock.sendto(datagram, dest)
            timeout = min(0.05, self._heap[0][0] - now) if self._heap else 0.05
            self.sock.settimeout(max(timeout, 1e-4))
            try:
                datagram, addr = self.sock.recvfrom(65535)
            except TimeoutError:
                continue
            if addr == self.target:
                if self._client is None:
                    continue
                self._impair(REVERSE, datagram, self._client, time.monotonic())
            else:
                self._client = addr
                self._impair(FORWARD, datagram, self.target, time.monotonic())

    def _impair(self, direction: str, datagram: bytes, dest: tuple[str, int], now: float) -> None:
        profile, stats, rng = self.profiles[direction], self.stats[direction], self._rng
        index = stats.received
        stats.received += 1
        if self.drop_filter is not None and self.drop_filter(direction, index, datagram):
            stats.filtered += 1
            return
        if rng.random() < profile.loss:
            stats.lost += 1
            return
        copies = 1
        if rng.random() < profile.duplicate:
            copies = 2
            stats.duplicated += 1
        for _ in range(copies):
            out = datagram
            if rng.random() < profile.corrupt:
                pos = rng.randrange(len(out))
                out = out[:pos] + bytes([out[pos] ^ 0xFF]) + out[pos + 1:]
                stats.corrupted += 1
            departs = now
            if profile.rate_bps:
                free_at = self._link_free_at[direction]
                backlog = max(0.0, free_at - now) * profile.rate_bps / 8
                if backlog + len(out) > profile.queue_bytes:
                    stats.queue_drops += 1
                    continue
                departs = max(now, free_at) + len(out) * 8 / profile.rate_bps
                self._link_free_at[direction] = departs
            arrives = departs + profile.delay + rng.uniform(0, profile.jitter)
            heapq.heappush(self._heap, (arrives, next(self._order), out, dest))
            stats.forwarded += 1
