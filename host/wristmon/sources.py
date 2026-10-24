"""Packet sources: live serial, simulated patient, and recorded-session replay.

All sources share one interface:
    open(), close(), read() -> list of packets / ReplayEvent, write(cmd), hint(step),
    link_stats (or None), finished (replay only)
"""
from __future__ import annotations

import csv
import json
import select
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import protocol as P
from .datatypes import EmgBlock, ImuSample
from .simulator import EMG_FS, VirtualPatient

ESP_USB_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}   # CP210x, CH340, FTDI, Espressif native USB


class SerialSource:
    def __init__(self, port: str = "auto", baud: int = 921600):
        self.port_name = port
        self.baud = baud
        self.ser = None
        self.parser = P.PacketParser()
        self.link_stats = self.parser.stats
        self.sent: list[str] = []

    @staticmethod
    def find_port() -> str | None:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        for p in ports:
            if p.vid in ESP_USB_VIDS:
                return p.device
        return ports[0].device if ports else None

    def open(self):
        import serial
        port = self.find_port() if self.port_name == "auto" else self.port_name
        if not port:
            raise RuntimeError("No serial port found. Is the ESP32 plugged in?")
        s = serial.Serial()
        s.port = port
        s.baudrate = self.baud
        s.timeout = 0.005
        # Keep DTR/RTS low so opening the port does not reset the ESP32 through its auto-reset circuit.
        s.dtr = False
        s.rts = False
        s.open()
        self.ser = s
        self.port_name = port
        self.write("START")

    def close(self):
        if self.ser:
            try:
                self.write("STOP")
            finally:
                self.ser.close()
                self.ser = None

    def read(self) -> list:
        n = self.ser.in_waiting
        data = self.ser.read(n if n else 1)
        return self.parser.feed(data) if data else []

    def write(self, cmd: str):
        if self.ser:
            self.ser.write(P.encode_command(cmd))
            self.sent.append(cmd)

    def hint(self, step: str):
        pass


AP_DEFAULT_IP = "192.168.4.1"   # ESP32 soft-AP address


def discover_esp32(port: int = 3334, timeout: float = 3.0) -> tuple[str, int] | None:
    """Waits for the firmware's UDP beacon "WRISTMON <ip> <tcp_port>". Returns (ip, port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, addr = s.recvfrom(256)
            except socket.timeout:
                break
            parts = data.decode("ascii", "replace").split()
            if len(parts) == 3 and parts[0] == "WRISTMON":
                try:
                    return parts[1], int(parts[2])
                except ValueError:
                    continue
        return None
    finally:
        s.close()


class TcpSource:
    """ESP32 over Wi-Fi: the same binary stream as USB serial, on one TCP connection.

    Commands (START, BEEP_OK, ...) go back on the same socket. Timestamps come from the
    ESP32 clock, so Wi-Fi jitter does not affect the signal processing; only lost packets
    (sequence gaps) and long stalls (disconnect timeout) do.
    """

    def __init__(self, host: str = "auto", port: int = 3333, discovery_port: int = 3334,
                 discovery_timeout: float = 3.0, connect_timeout: float = 3.0):
        self.host = host
        self.port = port
        self.discovery_port = discovery_port
        self.discovery_timeout = discovery_timeout
        self.connect_timeout = connect_timeout
        self.sock: socket.socket | None = None
        self.parser = P.PacketParser()
        self.link_stats = self.parser.stats
        self.sent: list[str] = []
        self.address = ""

    def _candidates(self):
        if self.host != "auto":
            return [(self.host, self.port)]
        found = discover_esp32(self.discovery_port, self.discovery_timeout)
        return ([found] if found else []) + [(AP_DEFAULT_IP, self.port)]

    def open(self):
        last_err = None
        for host, port in self._candidates():
            try:
                s = socket.create_connection((host, port), timeout=self.connect_timeout)
            except OSError as e:
                last_err = e
                continue
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            s.setblocking(False)
            self.sock = s
            self.address = f"{host}:{port}"
            # A new connection restarts the stream: drop any half-parsed bytes.
            self.parser.buf.clear()
            self.write("START")
            return
        raise RuntimeError("ESP32 not found on Wi-Fi. Is the PC connected to the WristMonitor network? "
                           f"({last_err})")

    def close(self):
        if self.sock:
            try:
                self.write("STOP")
            except OSError:
                pass
            finally:
                self.sock.close()
                self.sock = None

    def read(self) -> list:
        r, _, _ = select.select([self.sock], [], [], 0.005)
        if not r:
            return []
        try:
            data = self.sock.recv(16384)
        except (BlockingIOError, InterruptedError):
            return []
        if not data:
            raise ConnectionError("ESP32 closed the Wi-Fi connection")
        return self.parser.feed(data)

    def write(self, cmd: str):
        if self.sock:
            self.sock.setblocking(True)
            self.sock.settimeout(0.5)
            try:
                self.sock.sendall(P.encode_command(cmd))
            finally:
                self.sock.setblocking(False)
            self.sent.append(cmd)

    def hint(self, step: str):
        pass


# Wizard step / app mode -> what the virtual patient does.
SIM_BEHAVIOUR = {"sensor_check": "rest", "rest": "rest", "zero": "hold_start", "top": "hold_top",
                 "rvc": "hold_half", "practice": "reps", "exercise": "reps", "idle": "idle"}


class SimSource:
    """Real-time virtual patient, encoded through the binary protocol."""

    def __init__(self, seed: int = 0, emg_mode: str = "raw", realtime: bool = True,
                 forearm_imu: bool = False, auto_reps: bool = False, announce_mode: bool = False,
                 **patient_kw):
        self.patient = VirtualPatient(seed=seed, emg_mode=emg_mode, forearm_imu=forearm_imu, **patient_kw)
        self.realtime = realtime
        self.parser = P.PacketParser()
        self.link_stats = self.parser.stats
        self.seq = 0
        self.sent: list[tuple[float, str]] = []
        self.auto_reps = auto_reps
        self._wall0 = None
        self._dev0 = None
        self.streaming = False
        # Like the firmware Kconfig hint: 0 = unknown, so the host has to auto-detect.
        self.emg_hint = {"raw": 1, "envelope": 2}.get(emg_mode, 0) if announce_mode else 0
        self._last_status = 0.0

    def open(self):
        self._wall0 = time.monotonic()
        self._dev0 = self.patient.t
        self.streaming = True

    def close(self):
        self.streaming = False

    def read(self) -> list:
        if not self.streaming:
            time.sleep(0.01)
            return []
        if self.realtime:
            target = self._dev0 + (time.monotonic() - self._wall0)
            if self.patient.t > target:
                time.sleep(min(0.005, self.patient.t - target))
                return []
        else:
            target = self.patient.t + 0.05
        data = bytearray()
        while self.patient.t <= target:
            t0, emg, imu = self.patient.step()
            for ti, imu_id, v in imu:
                data += P.encode_imu(self.seq, int(ti * 1e6), imu_id, *v)
                self.seq += 1
            data += P.encode_emg(self.seq, int(t0 * 1e6), int(1e6 / EMG_FS), emg.astype(int))
            self.seq += 1
            if t0 - self._last_status >= 1.0:
                self._last_status = t0
                data += P.encode_status(self.seq, int(t0 * 1e6), 9000, P.STATUS_STREAMING | P.STATUS_IMU_OK,
                                        self.emg_hint)
                self.seq += 1
        return self.parser.feed(bytes(data))

    def write(self, cmd: str):
        self.sent.append((self.patient.t, cmd))

    def hint(self, step: str):
        beh = SIM_BEHAVIOUR.get(step)
        if beh:
            self.patient.set_behaviour(beh)
            if beh == "reps" and self.auto_reps and not self.patient.auto_kind:
                self.patient.auto_kind = "good"

    def queue_reps(self, kinds):
        self.patient.queue_reps(kinds)


@dataclass
class ReplayEvent:
    t: float
    event: str
    arg: str


def load_session(session_dir: str | Path):
    """Reads a logged session. Returns (emg_t, emg_adc, imu_rows, events)."""
    d = Path(session_dir)
    emg = np.loadtxt(d / "emg.csv", delimiter=",", skiprows=1, ndmin=2)
    imu = np.loadtxt(d / "imu.csv", delimiter=",", skiprows=1, ndmin=2)
    events = []
    ev_path = d / "events.csv"
    if ev_path.exists():
        with open(ev_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                events.append(ReplayEvent(float(row["t"]), row["event"], row["arg"]))
    return emg[:, 0], emg[:, 1], imu, events


class ReplaySource:
    """Replays emg.csv / imu.csv / events.csv in device-time order through the same pipeline."""

    def __init__(self, session_dir: str | Path, realtime: bool = False, speed: float = 1.0, block: int = 10):
        self.dir = Path(session_dir)
        et, ea, imu, events = load_session(self.dir)
        items = []
        dt = float(np.median(np.diff(et[:200]))) if len(et) > 1 else 1e-3
        for i in range(0, len(et), block):
            t = et[i:i + block]
            items.append((float(t[0]), 1, EmgBlock(float(t[0]), dt, ea[i:i + block].astype(float))))
        for r in imu:
            items.append((float(r[0]), 0, ImuSample(float(r[0]), int(r[1]), *map(float, r[2:11]))))
        for e in events:
            items.append((e.t, 2, e))
        # Events at a timestamp apply after the data at that timestamp, as they did live.
        items.sort(key=lambda x: (x[0], x[1] == 2))
        self.items = [x[2] for x in items]
        self.times = [x[0] for x in items]
        self.pos = 0
        self.realtime = realtime
        self.speed = speed
        self.link_stats = None
        self.sent: list[str] = []
        meta = self.dir / "meta.json"
        self.meta = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        self._wall0 = None

    @property
    def finished(self) -> bool:
        return self.pos >= len(self.items)

    def open(self):
        self._wall0 = time.monotonic()

    def close(self):
        pass

    def read(self) -> list:
        if self.finished:
            time.sleep(0.01)
            return []
        if self.realtime:
            t_now = self.times[0] + (time.monotonic() - self._wall0) * self.speed
            end = self.pos
            while end < len(self.items) and self.times[end] <= t_now:
                end += 1
            if end == self.pos:
                time.sleep(0.003)
        else:
            end = min(len(self.items), self.pos + 200)
        out = self.items[self.pos:end]
        self.pos = end
        return out

    def write(self, cmd: str):
        self.sent.append(cmd)

    def hint(self, step: str):
        pass
