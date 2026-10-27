"""Wire format: a fixed 25-byte header followed by an optional payload.

    0       4       8   9    11    13      17      21      25
    | seq   | ack   |flg| wnd | len | tsval | tsecr | crc32 | payload ...

All fields are big-endian. `wnd` is the receiver's free buffer in segments.
`tsval`/`tsecr` work like TCP timestamps (RFC 7323): the sender stamps each
segment in microseconds and the receiver echoes one back, which gives RTT
samples even for retransmitted data. Zero means "no timestamp".
The CRC32 covers the header (with the checksum field zeroed) and the payload;
any mismatch makes `decode` raise, so corrupted datagrams are dropped.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

HEADER = struct.Struct("!IIBHHIII")
HEADER_SIZE = HEADER.size
SEQ_MOD = 1 << 32
MAX_WINDOW = 0xFFFF

SYN = 0x01
ACK = 0x02
FIN = 0x04


class PacketError(ValueError):
    """Raised for datagrams that are truncated, malformed, or fail the checksum."""


@dataclass(frozen=True)
class Packet:
    seq: int
    ack: int
    flags: int
    wnd: int = 0
    payload: bytes = b""
    tsval: int = 0
    tsecr: int = 0

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)

    def encode(self) -> bytes:
        fields = (self.seq % SEQ_MOD, self.ack % SEQ_MOD, self.flags, min(self.wnd, MAX_WINDOW), len(self.payload),
                  self.tsval % SEQ_MOD, self.tsecr % SEQ_MOD)
        checksum = zlib.crc32(HEADER.pack(*fields, 0) + self.payload)
        return HEADER.pack(*fields, checksum) + self.payload


def decode(datagram: bytes) -> Packet:
    if len(datagram) < HEADER_SIZE:
        raise PacketError(f"datagram too short ({len(datagram)} bytes)")
    seq, ack, flags, wnd, length, tsval, tsecr, checksum = HEADER.unpack_from(datagram)
    payload = datagram[HEADER_SIZE:]
    if length != len(payload):
        raise PacketError(f"length field {length} != payload size {len(payload)}")
    if zlib.crc32(HEADER.pack(seq, ack, flags, wnd, length, tsval, tsecr, 0) + payload) != checksum:
        raise PacketError("checksum mismatch")
    return Packet(seq, ack, flags, wnd, bytes(payload), tsval, tsecr)
