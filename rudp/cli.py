"""Command line: run the receiver, sender, and impairment proxy as separate processes.

    python -m rudp recv  --bind 127.0.0.1:9000 --out received.bin
    python -m rudp proxy --listen 127.0.0.1:9100 --target 127.0.0.1:9000 --loss 0.02 --delay 0.01
    python -m rudp send  --to 127.0.0.1:9100 --file input.bin --events events.csv
"""

from __future__ import annotations

import argparse
import hashlib
import signal
import threading
from dataclasses import asdict
from pathlib import Path

from .config import TransportConfig
from .harness import write_events_csv
from .proxy import LinkProfile, LossyProxy
from .receiver import Receiver
from .sender import Sender


def _addr(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rudp")
    sub = parser.add_subparsers(dest="command", required=True)

    recv = sub.add_parser("recv", help="accept one connection and write what it delivers")
    recv.add_argument("--bind", type=_addr, default=("127.0.0.1", 9000))
    recv.add_argument("--out", type=Path, required=True)

    send = sub.add_parser("send", help="send a file over one connection")
    send.add_argument("--to", type=_addr, required=True)
    send.add_argument("--file", type=Path, required=True)
    send.add_argument("--events", type=Path, help="write the cwnd/RTO event trace as CSV")

    proxy = sub.add_parser("proxy", help="relay traffic through an impaired link")
    proxy.add_argument("--listen", type=_addr, default=("127.0.0.1", 9100))
    proxy.add_argument("--target", type=_addr, required=True)
    proxy.add_argument("--loss", type=float, default=0.0)
    proxy.add_argument("--delay", type=float, default=0.0, help="one-way delay in seconds")
    proxy.add_argument("--jitter", type=float, default=0.0)
    proxy.add_argument("--duplicate", type=float, default=0.0)
    proxy.add_argument("--corrupt", type=float, default=0.0)
    proxy.add_argument("--rate-mbps", type=float)
    proxy.add_argument("--queue-kb", type=float, default=64)
    proxy.add_argument("--seed", type=int, default=0)

    args = parser.parse_args(argv)

    if args.command == "recv":
        receiver = Receiver(args.bind, TransportConfig())
        print(f"listening on {receiver.address[0]}:{receiver.address[1]}")
        data = receiver.receive()
        args.out.write_bytes(data)
        print(f"received {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
        print(asdict(receiver.stats))
        receiver.close()

    elif args.command == "send":
        data = args.file.read_bytes()
        sender = Sender(args.to, TransportConfig())
        stats = sender.send(data)
        sender.close()
        print(f"sent {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
        print(f"{stats.duration:.3f}s, goodput {stats.goodput_mbps:.2f} Mbit/s")
        print(asdict(stats))
        if args.events:
            write_events_csv(sender.events, args.events)

    elif args.command == "proxy":
        rate = args.rate_mbps * 1e6 if args.rate_mbps else None
        profile = LinkProfile(args.loss, args.delay, args.jitter, args.duplicate, args.corrupt, rate,
                              int(args.queue_kb * 1024))
        done = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: done.set())
        with LossyProxy(args.target, profile, bind=args.listen, seed=args.seed) as relay:
            print(f"relaying {relay.address[0]}:{relay.address[1]} -> {args.target[0]}:{args.target[1]}; Ctrl-C to stop")
            done.wait()
        for direction, stats in relay.stats.items():
            print(direction, asdict(stats))
    return 0
