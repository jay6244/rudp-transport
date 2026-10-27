"""Runs one sender -> proxy -> receiver transfer in-process; shared by the tests and benchmarks."""

from __future__ import annotations

import csv
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import TransportConfig
from .proxy import DropFilter, LinkProfile, LinkStats, LossyProxy
from .receiver import Receiver, ReceiverStats
from .sender import Event, Sender, TransferStats


@dataclass
class TransferResult:
    sent: bytes
    received: bytes
    sender: TransferStats
    receiver: ReceiverStats
    link: dict[str, LinkStats]
    events: list[Event]

    @property
    def intact(self) -> bool:
        return self.received == self.sent


def run_transfer(data: bytes, forward: LinkProfile | None = None, reverse: LinkProfile | None = None,
                 config: TransportConfig | None = None, seed: int = 0, drop_filter: DropFilter | None = None,
                 timeout: float = 300.0) -> TransferResult:
    receiver = Receiver(config=config)
    outcome: dict[str, object] = {}

    def serve() -> None:
        try:
            outcome["data"] = receiver.receive(listen_timeout=timeout)
        except BaseException as exc:  # surfaced in the calling thread below
            outcome["error"] = exc

    thread = threading.Thread(target=serve, name="receiver", daemon=True)
    thread.start()
    try:
        with LossyProxy(receiver.address, forward, reverse, seed=seed, drop_filter=drop_filter) as proxy:
            sender = Sender(proxy.address, config)
            try:
                stats = sender.send(data)
            finally:
                sender.close()
            thread.join(timeout)
    finally:
        receiver.close()
    if "error" in outcome:
        raise outcome["error"]
    if thread.is_alive():
        raise TimeoutError("receiver did not finish")
    return TransferResult(data, outcome["data"], stats, receiver.stats, proxy.stats, sender.events)


def write_events_csv(events: list[Event], path: str | Path) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[fld.name for fld in fields(Event)])
        writer.writeheader()
        for event in events:
            writer.writerow(asdict(event))
