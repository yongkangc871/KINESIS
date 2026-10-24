"""Binary stream protocol (mirror of firmware/main/protocol.h), parser and clock synchroniser.

Frame:  A5 5A | type u8 | len u8 | seq u16 | payload[len] | crc16 u16   (little-endian)
CRC-16/CCITT-FALSE over type..payload. One global sequence counter, so a gap = lost packets.
"""
from __future__ import annotations

import struct
from binascii import crc_hqx
from dataclasses import dataclass

import numpy as np

from .datatypes import DeviceStatus, EmgBlock, ImuSample

SYNC = b"\xa5\x5a"
T_EMG, T_IMU, T_STATUS = 0x01, 0x02, 0x03
HEADER_LEN = 6
CRC_LEN = 2

ANGLE_SCALE = 180.0 / 32768.0
GYRO_SCALE = 2000.0 / 32768.0
ACC_SCALE = 16.0 / 32768.0

EMG_HINTS = {0: "unknown", 1: "raw", 2: "envelope"}
STATUS_STREAMING, STATUS_IMU_OK, STATUS_IMU2 = 0x01, 0x02, 0x04

COMMANDS = ("START", "STOP", "BEEP_OK", "BEEP_BAD", "BEEP_LONG")


def crc16(data: bytes) -> int:
    return crc_hqx(data, 0xFFFF)


def encode_frame(ptype: int, seq: int, payload: bytes) -> bytes:
    body = struct.pack("<BBH", ptype, len(payload), seq & 0xFFFF) + payload
    return SYNC + body + struct.pack("<H", crc16(body))


def encode_emg(seq: int, t0_us: int, dt_us: int, samples) -> bytes:
    s = np.clip(np.asarray(samples), 0, 65535).astype("<u2")
    payload = struct.pack("<IHB", t0_us & 0xFFFFFFFF, dt_us, len(s)) + s.tobytes()
    return encode_frame(T_EMG, seq, payload)


def _i16(v: float, scale: float) -> int:
    return int(max(-32768, min(32767, round(v / scale))))


def encode_imu(seq: int, t_us: int, imu_id: int, roll, pitch, yaw, gx, gy, gz, ax, ay, az) -> bytes:
    def wrap(a):
        return (a + 180.0) % 360.0 - 180.0
    vals = [_i16(wrap(roll), ANGLE_SCALE), _i16(wrap(pitch), ANGLE_SCALE), _i16(wrap(yaw), ANGLE_SCALE),
            _i16(gx, GYRO_SCALE), _i16(gy, GYRO_SCALE), _i16(gz, GYRO_SCALE),
            _i16(ax, ACC_SCALE), _i16(ay, ACC_SCALE), _i16(az, ACC_SCALE)]
    payload = struct.pack("<IB9h", t_us & 0xFFFFFFFF, imu_id, *vals)
    return encode_frame(T_IMU, seq, payload)


def encode_status(seq: int, t_us: int, battery_mv: int = 0, flags: int = 0, emg_hint: int = 0,
                  fw_version: int = 1) -> bytes:
    payload = struct.pack("<IHBBB", t_us & 0xFFFFFFFF, battery_mv, flags, emg_hint, fw_version)
    return encode_frame(T_STATUS, seq, payload)


def encode_command(cmd: str) -> bytes:
    if cmd not in COMMANDS:
        raise ValueError(f"unknown command {cmd}")
    return (cmd + "\n").encode("ascii")


class TimeUnwrapper:
    """Unwraps the device's u32 microsecond clock (wraps every ~71.6 min) into seconds.

    Packets of different types interleave slightly out of order, so each value is placed
    in the 2^32 window closest to the newest time seen rather than assuming monotonicity.
    """

    def __init__(self):
        self.latest = None

    def __call__(self, t_us: int) -> float:
        if self.latest is None:
            full = t_us
        else:
            base = self.latest - (self.latest & 0xFFFFFFFF)
            full = base + t_us
            if full - self.latest > (1 << 31):
                full -= 1 << 32
            elif self.latest - full > (1 << 31):
                full += 1 << 32
        if self.latest is None or full > self.latest:
            self.latest = full
        return full * 1e-6


@dataclass
class LinkStats:
    received: int = 0
    lost: int = 0
    crc_errors: int = 0
    bytes: int = 0

    @property
    def loss_ratio(self) -> float:
        total = self.received + self.lost
        return self.lost / total if total else 0.0

    def snapshot(self) -> "LinkStats":
        return LinkStats(self.received, self.lost, self.crc_errors, self.bytes)


class PacketParser:
    """Incremental byte-stream parser with resynchronisation and dropout (sequence gap) counting."""

    def __init__(self):
        self.buf = bytearray()
        self.stats = LinkStats()
        self._last_seq: int | None = None
        self._clock = TimeUnwrapper()

    def feed(self, data: bytes) -> list:
        self.stats.bytes += len(data)
        buf = self.buf
        buf.extend(data)
        out = []
        i = 0
        n = len(buf)
        while True:
            j = buf.find(SYNC, i)
            if j < 0:
                # Keep a trailing first sync byte; it may start the next frame.
                i = n - 1 if n and buf[-1] == SYNC[0] else n
                break
            if n - j < HEADER_LEN:
                i = j
                break
            ptype, ln, seq = struct.unpack_from("<BBH", buf, j + 2)
            total = HEADER_LEN + ln + CRC_LEN
            if n - j < total:
                i = j
                break
            body = bytes(buf[j + 2: j + HEADER_LEN + ln])
            (crc,) = struct.unpack_from("<H", buf, j + HEADER_LEN + ln)
            if crc16(body) != crc:
                self.stats.crc_errors += 1
                i = j + 1
                continue
            # Count every CRC-valid frame (even unknown types) so sequence gaps mean real loss.
            self._track_seq(seq)
            pkt = self._decode(ptype, seq, body[4:])
            i = j + total
            if pkt is not None:
                out.append(pkt)
        del buf[:i]
        return out

    def _track_seq(self, seq: int):
        self.stats.received += 1
        if self._last_seq is not None:
            gap = (seq - self._last_seq - 1) & 0xFFFF
            if 0 < gap < 0x8000:
                self.stats.lost += gap
        self._last_seq = seq

    def _decode(self, ptype: int, seq: int, p: bytes):
        try:
            if ptype == T_EMG:
                t0, dt_us, n = struct.unpack_from("<IHB", p, 0)
                samples = np.frombuffer(p, dtype="<u2", count=n, offset=7).astype(np.float64)
                return EmgBlock(self._clock(t0), dt_us * 1e-6, samples, seq)
            if ptype == T_IMU:
                t, imu_id, r, pi, y, gx, gy, gz, ax, ay, az = struct.unpack_from("<IB9h", p, 0)
                return ImuSample(self._clock(t), imu_id, r * ANGLE_SCALE, pi * ANGLE_SCALE, y * ANGLE_SCALE,
                                 gx * GYRO_SCALE, gy * GYRO_SCALE, gz * GYRO_SCALE,
                                 ax * ACC_SCALE, ay * ACC_SCALE, az * ACC_SCALE, seq)
            if ptype == T_STATUS:
                t, mv, flags, hint, fw = struct.unpack_from("<IHBBB", p, 0)
                return DeviceStatus(self._clock(t), mv, flags, hint, fw, seq)
        except struct.error:
            return None
        return None
