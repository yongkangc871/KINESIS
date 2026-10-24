import numpy as np

from wristmon import protocol as P
from wristmon.datatypes import DeviceStatus, EmgBlock, ImuSample


def _stream(n=50):
    data = bytearray()
    for i in range(n):
        data += P.encode_emg(2 * i, 1_000_000 + 10_000 * i, 1000, list(range(i, i + 10)))
        data += P.encode_imu(2 * i + 1, 1_000_000 + 10_000 * i, 0, 177.0, -12.5, 40.0, 1.5, -30.0, 0.2,
                             0.05, -0.02, -0.99)
    return bytes(data)


def test_roundtrip_all_types():
    p = P.PacketParser()
    data = (P.encode_emg(0, 123456, 1000, [1, 2, 4095]) +
            P.encode_imu(1, 123500, 1, 177.0, -12.5, 40.0, 1.5, -30.0, 0.2, 0.05, -0.02, -0.99) +
            P.encode_status(2, 124000, 3900, P.STATUS_STREAMING, 2, 1))
    out = p.feed(data)
    assert [type(x) for x in out] == [EmgBlock, ImuSample, DeviceStatus]
    e, i, s = out
    assert np.array_equal(e.samples, [1, 2, 4095]) and abs(e.t0 - 0.123456) < 1e-9 and e.dt == 1e-3
    assert i.imu_id == 1 and abs(i.pitch + 12.5) < 0.01 and abs(i.gy + 30.0) < 0.07 and abs(i.az + 0.99) < 1e-3
    assert s.battery_mv == 3900 and s.emg_hint == 2
    assert p.stats.lost == 0 and p.stats.crc_errors == 0


def test_split_feeding_is_identical():
    data = _stream()
    whole = P.PacketParser().feed(data)
    p = P.PacketParser()
    out = []
    rng = np.random.default_rng(0)
    i = 0
    while i < len(data):
        k = int(rng.integers(1, 40))
        out += p.feed(data[i:i + k])
        i += k
    assert len(out) == len(whole) == 100


def test_crc_error_resyncs_and_garbage_skipped():
    data = bytearray(_stream(10))
    data[66 + 20] ^= 0xFF      # corrupt the second EMG packet (EMG frame 35 B, IMU frame 31 B)
    p = P.PacketParser()
    out = p.feed(b"\x00\xa5\x13garbage" + bytes(data))
    assert p.stats.crc_errors >= 1
    assert len(out) == 19
    assert p.stats.lost == 1   # the corrupted packet shows up as a sequence gap


def test_sequence_gap_counts_dropouts():
    p = P.PacketParser()
    p.feed(P.encode_emg(10, 0, 1000, [0]) + P.encode_emg(15, 10000, 1000, [0]))
    assert p.stats.lost == 4
    assert abs(p.stats.loss_ratio - 4 / 6) < 1e-9


def test_sequence_wraps_without_loss():
    p = P.PacketParser()
    p.feed(P.encode_emg(0xFFFF, 0, 1000, [0]) + P.encode_emg(0, 10000, 1000, [0]))
    assert p.stats.lost == 0


def test_time_unwrap_across_u32_rollover():
    u = P.TimeUnwrapper()
    t1 = u(0xFFFFFF00)
    t2 = u(0x00000100)          # wrapped
    t3 = u(0xFFFFFFF0)          # a late packet from before the wrap
    assert t2 > t1 and abs((t2 - t1) - 512e-6) < 1e-9
    assert t3 < t2


def test_command_encoding():
    assert P.encode_command("BEEP_OK") == b"BEEP_OK\n"
