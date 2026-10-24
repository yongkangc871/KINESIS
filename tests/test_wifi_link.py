"""Wi-Fi transport: TcpSource against a fake ESP32 (TCP server + UDP beacon) on localhost."""
import socket
import threading
import time

import pytest

from wristmon import protocol as P
from wristmon.pipeline import Pipeline
from wristmon.simulator import EMG_FS, VirtualPatient
from wristmon.sources import TcpSource, discover_esp32


def stream_bytes(seconds: float) -> bytes:
    """What the firmware would send: the virtual patient encoded with the real protocol."""
    vp = VirtualPatient(seed=1)
    out = bytearray()
    seq = 0
    while vp.t < 1.0 + seconds:
        t0, emg, imu = vp.step()
        for ti, imu_id, v in imu:
            out += P.encode_imu(seq, int(ti * 1e6), imu_id, *v)
            seq += 1
        out += P.encode_emg(seq, int(t0 * 1e6), int(1e6 / EMG_FS), emg.astype(int))
        seq += 1
    return bytes(out)


class FakeEsp32:
    """Accepts one connection, waits for START, streams, records command lines."""

    def __init__(self, payload: bytes, close_after: bool = False):
        self.payload = payload
        self.close_after = close_after
        self.commands: list[str] = []
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.srv.accept()
        conn.settimeout(3)
        buf = b""
        while b"START\n" not in buf:
            buf += conn.recv(64)
        self.commands.append("START")
        # Send in Wi-Fi-like bursts of uneven size.
        for i in range(0, len(self.payload), 1400):
            conn.sendall(self.payload[i:i + 1400])
            if i % 14000 == 0:
                time.sleep(0.01)
        if self.close_after:
            conn.close()
            return
        conn.settimeout(0.2)
        end = time.monotonic() + 3
        rest = b""
        while time.monotonic() < end:
            try:
                data = conn.recv(256)
            except socket.timeout:
                continue
            if not data:
                break
            rest += data
            while b"\n" in rest:
                line, rest = rest.split(b"\n", 1)
                self.commands.append(line.decode())
        conn.close()

    def stop(self):
        self.srv.close()


def drain(src, pipe=None, seconds=3.0, until=None):
    packets = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for pk in src.read():
            packets.append(pk)
            if pipe:
                pipe.handle(pk)
        if until and until(packets):
            break
    return packets


def test_stream_over_tcp_reaches_pipeline_and_commands_return(cfg):
    payload = stream_bytes(2.0)
    esp = FakeEsp32(payload)
    src = TcpSource("127.0.0.1", esp.port)
    pipe = Pipeline(cfg, send_command=src.write)
    frames = []
    pipe.on_frame = lambda f, s: frames.append(f)
    try:
        src.open()
        expected = len(P.PacketParser().feed(payload))
        got = drain(src, pipe, until=lambda p: len(p) >= expected)
        assert len(got) == expected
        assert src.link_stats.lost == 0 and src.link_stats.crc_errors == 0
        assert len(frames) > 350           # ~2 s of 200 Hz hand-IMU frames
        src.write("BEEP_OK")
        pipe.feedback.request("bad", pipe.now + 10)   # feedback controller -> socket
        time.sleep(0.4)
    finally:
        src.close()
        esp.thread.join(timeout=5)
        esp.stop()
    assert esp.commands[0] == "START"
    assert "BEEP_OK" in esp.commands and "BEEP_BAD" in esp.commands


def test_dropped_connection_is_reported(cfg):
    esp = FakeEsp32(stream_bytes(0.3), close_after=True)
    src = TcpSource("127.0.0.1", esp.port)
    try:
        src.open()
        with pytest.raises(ConnectionError):
            drain(src, seconds=3.0)
    finally:
        src.close()
        esp.stop()


def test_discovery_beacon():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    stop = threading.Event()

    def beacon():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            while not stop.is_set():
                s.sendto(b"WRISTMON 192.168.4.1 3333\n", ("127.0.0.1", port))
                time.sleep(0.1)

    t = threading.Thread(target=beacon, daemon=True)
    t.start()
    try:
        assert discover_esp32(port, timeout=2.0) == ("192.168.4.1", 3333)
    finally:
        stop.set()
        t.join()


def test_no_esp32_gives_clear_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    src = TcpSource("127.0.0.1", free_port, connect_timeout=0.5)
    with pytest.raises(RuntimeError, match="not found on Wi-Fi"):
        src.open()
