# Offline checks for the motor reply parser/decoder (k2_motor_proto) and the
# hark_safety trip rules. Run from the repo root:
#   python -m unittest tests/test_motor_proto.py
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "klippy"))

from extras import k2_motor_proto as proto  # noqa: E402
from extras import hark_safety  # noqa: E402


def reply(addr, status, func, payload):
    """A motor/CFS reply frame: f7 addr len status func payload crc."""
    return proto.build_frame(addr, [status, func] + list(payload))


HEALTHY = bytes.fromhex("f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb")
# Power-on reply captured from stock 10-24: status 02, error bit 8
FAULTED = reply(0x81, 0x02, 0x0c, [0x00, 0x01, 0, 0, 0, 0, 0, 0])
# Low supply voltage warning (warning bit 1), status 04
WARNED = reply(0x82, 0x04, 0x0c, [0, 0, 0, 0, 0x02, 0, 0, 0])
# Real CFS reply (EXTRUDE_ERR8 to a stage-5 load), captured 10-24
CFS_ERR8 = bytes.fromhex("f7 01 07 0c 10 c4 a7 18 42 50")


class CrcTest(unittest.TestCase):
    def test_documented_healthy_reply(self):
        self.assertTrue(proto.check_crc(HEALTHY))

    def test_captured_cfs_replies(self):
        self.assertTrue(proto.check_crc(CFS_ERR8))
        self.assertTrue(proto.check_crc(
            bytes.fromhex("f7 01 07 0c 10 44 b1 65 0c 18")))

    def test_known_request_frames(self):
        self.assertEqual(proto.build_frame(0x81, [0, 0x11, 1]).hex(),
                         "f781040011011d")

    def test_corrupt_byte_fails(self):
        bad = bytearray(HEALTHY)
        bad[7] ^= 0x10
        self.assertFalse(proto.check_crc(bad))
        with self.assertRaises(ValueError):
            proto.parse_frame(bytes(bad))


class ParseDecodeTest(unittest.TestCase):
    def test_healthy(self):
        d = proto.decode_protection(proto.parse_frame(HEALTHY))
        self.assertFalse(d['has_error'])
        self.assertFalse(d['active'])
        self.assertEqual(d['status'], 0)
        self.assertEqual(proto.describe_protection(d), "ok")

    def test_faulted_status_byte_02(self):
        f = proto.parse_frame(FAULTED)
        self.assertEqual((f['addr'], f['status'], f['func']),
                         (0x81, 0x02, 0x0c))
        d = proto.decode_protection(f)
        self.assertTrue(d['faulted'])
        self.assertTrue(d['has_error'])
        self.assertEqual(d['error_code'], 0x100)
        self.assertEqual(d['errors'], ["excessive position tracking error"])

    def test_old_search_missed_the_fault(self):
        # The old parser looked for 00 0c and found nothing in a 02 0c reply
        self.assertEqual(FAULTED.find(bytes([0x00, 0x0c])), -1)

    def test_warning_only(self):
        d = proto.decode_protection(proto.parse_frame(WARNED))
        self.assertFalse(d['has_error'])
        self.assertTrue(d['active'])
        self.assertEqual(d['warnings'], ["motor power supply too low"])
        self.assertIn("warning: motor power supply too low",
                      proto.describe_protection(d))

    def test_unknown_bits_named(self):
        self.assertEqual(proto.decode_bits(1 << 20, proto.ERROR_CODE_LABELS),
                         ["bit 20"])

    def test_short_and_empty(self):
        for rx in (b'', None, b'\xf7\x81\x04'):
            with self.assertRaises(ValueError):
                proto.parse_frame(rx)

    def test_near_zero_calibration(self):
        self.assertTrue(proto.is_near_zero_calibration(0.0))
        self.assertTrue(proto.is_near_zero_calibration(-0.15))
        self.assertFalse(proto.is_near_zero_calibration(2.3))
        self.assertFalse(proto.is_near_zero_calibration(None))


class FindReplyTest(unittest.TestCase):
    def test_skips_late_cfs_reply(self):
        frame, rest, rejects = proto.find_reply(CFS_ERR8 + HEALTHY, 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['addr'])

    def test_skips_wrong_function(self):
        stall_echo = reply(0x81, 0x00, 0x11, [0x01])
        frame, rest, rejects = proto.find_reply(stall_echo + HEALTHY, 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['func'])

    def test_skips_bad_crc_and_noise(self):
        bad = bytearray(HEALTHY)
        bad[-1] ^= 0xff
        frame, rest, rejects = proto.find_reply(
            b'\x00\x13' + bytes(bad) + HEALTHY, 0x81, 0x0c)
        self.assertEqual(frame, HEALTHY)
        self.assertIn('crc', rejects)

    def test_incomplete_waits(self):
        frame, rest, rejects = proto.find_reply(HEALTHY[:7], 0x81, 0x0c)
        self.assertIsNone(frame)
        self.assertEqual(rest, HEALTHY[:7])
        frame, rest, rejects = proto.find_reply(rest + HEALTHY[7:], 0x81,
                                                0x0c)
        self.assertEqual(frame, HEALTHY)

    def test_oversize_len_is_noise(self):
        frame, rest, rejects = proto.find_reply(b'\xf7\x01\xf0' + HEALTHY,
                                                0x81, 0x0c, max_len=64)
        self.assertEqual(frame, HEALTHY)
        self.assertEqual(rejects, ['len'])

    def test_cfs_request_has_no_function_check(self):
        self.assertIsNone(proto.request_func(0x01, [0xff, 0x10, 0x01]))
        self.assertEqual(proto.request_func(0x81, [0x00, 0x0c, 0x0b]), 0x0c)
        frame, rest, rejects = proto.find_reply(CFS_ERR8, 0x01, None)
        self.assertEqual(frame, CFS_ERR8)

    def test_broadcast_accepts_any_sender(self):
        # The CFS answers an addressing broadcast from its new address
        frame, rest, rejects = proto.find_reply(CFS_ERR8, 0xfe, None)
        self.assertEqual(frame, CFS_ERR8)


class FakeBus:
    e_send = None
    ADDRS = {'x': 0x81, 'y': 0x82, 'z': 0x83, 'z1': 0x84, 'e': 0x81}

    def __init__(self, replies, names=('x', 'y')):
        self.replies = replies
        self.names = list(names)

    def motor_names(self):
        return self.names + (['e'] if self.e_send is not None else [])

    def motor_addr(self, name):
        return self.ADDRS[name]

    def query_protection(self, name, addr=None):
        rx = self.replies.get(name)
        if rx is None:
            return None
        d = proto.decode_protection(proto.parse_frame(rx))
        d['raw'] = rx.hex(' ')
        return d


class FakeGcode:
    def __init__(self):
        self.info, self.raw, self.scripts = [], [], []

    def respond_info(self, msg):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def run_script(self, script):
        self.scripts.append(script)


def make_watch(replies, fail_count=2, names=('x', 'y')):
    w = hark_safety.HarkSafety.__new__(hark_safety.HarkSafety)
    w.bus = FakeBus(replies, names)
    w.gcode = FakeGcode()
    w.motor_fail_count = fail_count
    w.xy_action, w.e_action = 'cancel', 'pause'
    w.e_addr = None
    w.motor_bad, w.motor_warned = {}, {}
    w.tripped = False
    w.last = {'fans': {}, 'motors': {}, 'event': None, 'e_fault': None}
    w.e_history = hark_safety.collections.deque()
    w.e_reported = False
    w.extruder = w.motion_report = None
    return w


class SafetyWatchTest(unittest.TestCase):
    def test_k2_plus_z_motor_fault_trips(self):
        w = make_watch({'x': HEALTHY, 'y': HEALTHY, 'z': HEALTHY,
                        'z1': FAULTED}, names=('x', 'y', 'z', 'z1'))
        w._check_motors(0.)
        w._check_motors(0.)
        self.assertTrue(w.tripped)
        self.assertIn('Z1 motor', w.gcode.raw[-1])

    def test_fault_trips_after_fail_count_with_name(self):
        w = make_watch({'x': FAULTED, 'y': HEALTHY})
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        w._check_motors(3.)
        self.assertTrue(w.tripped)
        self.assertIn("excessive position tracking error", w.last['event'])
        self.assertEqual(w.gcode.scripts, ["CANCEL_PRINT SAFE=1\nTURN_OFF_HEATERS"])

    def test_warning_reports_once_and_never_trips(self):
        w = make_watch({'x': HEALTHY, 'y': WARNED}, fail_count=1)
        for t in range(5):
            w._check_motors(float(t))
        self.assertFalse(w.tripped)
        self.assertEqual(w.gcode.scripts, [])
        self.assertEqual(len(w.gcode.info), 1)
        self.assertIn("motor power supply too low", w.gcode.info[0])

    def test_no_reply_never_trips(self):
        w = make_watch({}, fail_count=1)
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        self.assertIsNone(w.last['motors']['x'])

    def test_status_latch_without_code_does_not_trip(self):
        stall_only = reply(0x81, 0x01, 0x0c, [0] * 8)
        w = make_watch({'x': stall_only}, fail_count=1)
        w._check_motors(0.)
        self.assertFalse(w.tripped)
        self.assertEqual(w.gcode.info, [])


class FakeMutex:
    def __init__(self):
        self.locked = False

    def test(self):
        return self.locked

    def __enter__(self):
        self.locked = True

    def __exit__(self, *args):
        self.locked = False


class FakeReactor:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def pause(self, when):
        self.now = when
        return when


class FakeSerial:
    """Hands out queued byte chunks, one list per write."""
    def __init__(self, per_write):
        self.per_write = list(per_write)
        self.pending = []
        self.writes = []
        self.timeout = 0

    def reset_input_buffer(self):
        self.pending = []

    def write(self, data):
        self.writes.append(bytes(data))
        self.pending = list(self.per_write.pop(0)) if self.per_write else []

    def flush(self):
        pass

    def read(self, n):
        return self.pending.pop(0) if self.pending else b''


def make_bus(per_write, reply_check='strict'):
    import types
    sys.modules.setdefault('mcu', types.ModuleType('mcu'))
    from extras import k2_motor_bus
    bus = k2_motor_bus.K2MotorBus.__new__(k2_motor_bus.K2MotorBus)
    bus.reactor = FakeReactor()
    bus._xfer_lock = FakeMutex()
    bus.ser = FakeSerial(per_write)
    bus.reply_check = reply_check
    bus.bus_errors = {'crc': 0, 'len': 0, 'addr': 0, 'func': 0,
                      'retries': 0, 'read_failures': 0}
    bus._reject_logged = 0.
    bus.bus_motors = k2_motor_bus.BUS_MOTORS
    bus.stall_motors = k2_motor_bus.BUS_MOTORS
    bus.extruder_addrs = (k2_motor_bus.EXTRUDER_ADDR,)
    bus.extruder_addr = k2_motor_bus.EXTRUDER_ADDR
    return bus


class FakeConfig:
    def __init__(self, items):
        self.items = items

    def getlist(self, name, default):
        return self.items.get(name, default)

    def error(self, msg):
        return ValueError(msg)


class BusMotorsTest(unittest.TestCase):
    def setUp(self):
        import types
        sys.modules.setdefault('mcu', types.ModuleType('mcu'))
        from extras import k2_motor_bus
        self.mod = k2_motor_bus

    def test_default_is_k2_xy(self):
        motors = self.mod.K2MotorBus._parse_motors(FakeConfig({}))
        self.assertEqual(motors, (('x', 0x81), ('y', 0x82)))

    def test_k2_plus_adds_z_motors(self):
        motors = self.mod.K2MotorBus._parse_motors(FakeConfig(
            {'bus_motors': ['x=0x81', 'y=0x82', 'z=0x83', 'Z1=0x84']}))
        self.assertEqual(motors, (('x', 0x81), ('y', 0x82), ('z', 0x83),
                                  ('z1', 0x84)))

    def test_bad_entries_rejected(self):
        for items in (['x=0x81', 'x=0x82'], ['e=0x81'], ['x=0x90']):
            with self.assertRaises(ValueError):
                self.mod.K2MotorBus._parse_motors(
                    FakeConfig({'bus_motors': items}))


class BusXferTest(unittest.TestCase):
    def test_late_cfs_reply_is_not_a_motor_reply(self):
        bus = make_bus([[CFS_ERR8, HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, HEALTHY)
        self.assertEqual(bus.bus_errors['addr'], 1)

    def test_read_is_retried_once(self):
        bus = make_bus([[], [HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, HEALTHY)
        self.assertEqual(len(bus.ser.writes), 2)
        self.assertEqual(bus.bus_errors['retries'], 1)

    def test_clear_is_not_retried(self):
        bus = make_bus([[], [HEALTHY]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x05), timeout=.05)
        self.assertEqual(resp, b'')
        self.assertEqual(len(bus.ser.writes), 1)

    def test_cfs_reply_passes(self):
        bus = make_bus([[CFS_ERR8[:4], CFS_ERR8[4:]]])
        frame, resp = bus.xfer(0x01, [0xff, 0x10, 0x01, 0x05])
        self.assertEqual(resp, CFS_ERR8)
        self.assertEqual(len(bus.ser.writes), 1)

    def test_bad_crc_gives_no_reply_in_strict(self):
        bad = bytearray(HEALTHY)
        bad[-1] ^= 1
        bus = make_bus([[bytes(bad)], [bytes(bad)]])
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, b'')
        self.assertEqual(bus.bus_errors['crc'], 2)
        self.assertEqual(bus.bus_errors['read_failures'], 1)

    def test_off_mode_keeps_old_behaviour(self):
        bus = make_bus([[CFS_ERR8, HEALTHY]], reply_check='off')
        frame, resp = bus.xfer(0x81, (0x00, 0x0c, 0x0b))
        self.assertEqual(resp, CFS_ERR8)

    def test_stall_mode_echo(self):
        bus = make_bus([[reply(0x81, 0, 0x11, [1])],
                        [reply(0x82, 0, 0x11, [2])],
                        [reply(0x82, 0, 0x11, [2])]])
        bus.stall_mode_check = 'warn'
        results = bus.set_stall_mode(1)
        self.assertEqual([r[2] for r in results], [True, False])
        # unconfirmed Y got one more try
        self.assertEqual(len(bus.ser.writes), 3)


# Issue #22: the reply that paused a K2 PETG print from the rear spool after
# ~30 min (status 02, error bit 3)
ISSUE22 = bytes.fromhex("f7 81 0b 02 0c 08 00 00 00 00 00 00 00 b3")


def float_reply(addr, func, value):
    import struct
    return reply(addr, 0x00, func, struct.pack('<f', value))


class Issue22DecodeTest(unittest.TestCase):
    def test_issue_frame_is_continuous_overcurrent(self):
        f = proto.parse_frame(ISSUE22)
        self.assertEqual((f['addr'], f['status'], f['func']),
                         (0x81, 0x02, 0x0c))
        d = proto.decode_protection(f)
        self.assertEqual((d['error_code'], d['warning_code']), (0x08, 0))
        self.assertEqual(d['errors'], ["software continuous overcurrent"])
        self.assertTrue(d['has_error'])
        self.assertTrue(d['faulted'])
        self.assertFalse(d['warned'])

    def test_overcurrent_hint_is_not_a_plain_jam(self):
        d = proto.decode_protection(proto.parse_frame(ISSUE22))
        hint = proto.extruder_fault_hint(d)
        self.assertIn("current limit", hint)
        self.assertNotIn("likely a clog or jam", hint)

    def test_tracking_error_still_reads_as_clog(self):
        d = proto.decode_protection(proto.parse_frame(FAULTED))
        self.assertEqual(proto.extruder_fault_hint(d), "likely a clog or jam")
        both = proto.decode_protection(proto.parse_frame(
            reply(0x81, 0x02, 0x0c, [0x08, 0x01, 0, 0, 0, 0, 0, 0])))
        self.assertEqual(proto.extruder_fault_hint(both),
                         "likely a clog or jam")

    def test_other_errors(self):
        d = proto.decode_protection(proto.parse_frame(
            reply(0x81, 0x02, 0x0c, [0x01, 0, 0, 0, 0, 0, 0, 0])))
        self.assertEqual(proto.extruder_fault_hint(d),
                         "an extruder controller fault")


class FakeExtruder:
    def __init__(self, temp=245., target=245., pa=0.07):
        self.status = {'temperature': temp, 'target': target,
                       'pressure_advance': pa, 'filament_diameter': 1.75}

    def get_status(self, eventtime):
        return dict(self.status)


class FakeMotionReport:
    def __init__(self):
        self.vel = 0.

    def get_status(self, eventtime):
        return {'live_extruder_velocity': self.vel}


class FakeEBus(FakeBus):
    """FakeBus with the extruder and the read-only diagnostic reads."""
    e_send = object()

    def __init__(self, replies, values=None, limits=None):
        FakeBus.__init__(self, replies)
        self.values = values or {}
        self.limits = limits or {}
        self.reads = []

    def read_value(self, name, index, addr=None):
        self.reads.append(('value', name, index, addr))
        return self.values.get(index)

    def read_params(self, name, pids, addr=None):
        self.reads.append(('params', name, tuple(pids), addr))
        return dict((pid, self.limits.get(pid)) for pid in pids)


def make_e_watch(e_reply, action='pause'):
    w = make_watch({'x': HEALTHY, 'y': HEALTHY, 'e': e_reply})
    w.bus = FakeEBus(w.bus.replies,
                     values={proto.GET_IQ: 2.71, proto.GET_ID: 0.02,
                             proto.GET_SPEED: 4.2, proto.GET_MCU_TEMP: 61.},
                     limits={0xb4: 2.5, 0xb5: 6., 0xe3: .5, 0xb3: 999.,
                             0x29: 2.})
    w.e_action = action
    w.extruder = FakeExtruder()
    w.motion_report = FakeMotionReport()
    return w


class Issue22WatchTest(unittest.TestCase):
    def test_overcurrent_still_pauses_with_the_right_reason(self):
        w = make_e_watch(ISSUE22)
        w._check_motors(0.)
        w._check_motors(3.)
        self.assertTrue(w.tripped)
        self.assertEqual(w.gcode.scripts, ["PAUSE"])
        self.assertIn("software continuous overcurrent", w.gcode.raw[-1])
        self.assertIn("current limit", w.gcode.raw[-1])
        self.assertNotIn("likely a clog or jam", w.gcode.raw[-1])

    def test_tracking_error_on_e_pauses_as_clog(self):
        w = make_e_watch(FAULTED)
        w._check_motors(0.)
        w._check_motors(3.)
        self.assertTrue(w.tripped)
        self.assertEqual(w.gcode.scripts, ["PAUSE"])
        self.assertIn("likely a clog or jam", w.gcode.raw[-1])

    def test_fault_report_has_motor_flow_and_limits(self):
        w = make_e_watch(ISSUE22)
        for t, vel in ((0., 4.), (3., 6.), (6., 5.)):
            w.motion_report.vel = vel
            w._note_extruder(t)
        w._check_motors(6.)
        w._check_motors(9.)
        rep = w.last['e_fault']
        self.assertEqual(rep['error_code'], 8)
        self.assertEqual(rep['motor']['iq'], 2.71)
        self.assertEqual(rep['limits']['continuous_A'], 2.5)
        self.assertEqual(rep['limits']['continuous_s'], 6.)
        self.assertAlmostEqual(rep['flow_max'], 6. * 2.405, places=1)
        self.assertAlmostEqual(rep['flow_now'], 5. * 2.405, places=1)
        self.assertEqual(rep['temp'], 245.)
        self.assertEqual(rep['pa'], 0.07)
        details = [m for m in w.gcode.info if "Extruder fault details" in m]
        self.assertEqual(len(details), 1)
        self.assertIn("iq 2.71 A", details[0])
        self.assertIn("continuous 2.5 A for 6 s", details[0])
        # The motor is read before the pause, the limits after it, and
        # only with read requests
        self.assertEqual([r[0] for r in w.bus.reads],
                         ['value'] * 4 + ['params'])
        self.assertEqual(w.bus.reads[-1][2],
                         tuple(pid for pid, _ in proto.PROTECTION_LIMIT_IDS))

    def test_history_keeps_a_minute(self):
        w = make_e_watch(ISSUE22)
        for t in range(0, 300, 3):
            w._note_extruder(float(t))
        self.assertLessEqual(w.e_history[-1]['time'] - w.e_history[0]['time'],
                             hark_safety.E_HISTORY_TIME)

    def test_log_action_reports_details_once(self):
        w = make_e_watch(ISSUE22, action='log')
        for t in range(6):
            w._check_motors(float(t))
        self.assertFalse(w.tripped)
        self.assertEqual(w.gcode.scripts, [])
        details = [m for m in w.gcode.info if "Extruder fault details" in m]
        self.assertEqual(len(details), 1)
        self.assertEqual(sum(1 for r in w.bus.reads if r[0] == 'params'), 1)

    def test_no_diagnostic_reads_without_a_fault(self):
        w = make_e_watch(HEALTHY)
        for t in range(4):
            w._check_motors(float(t))
        self.assertEqual(w.bus.reads, [])
        self.assertIsNone(w.last['e_fault'])

    def test_safety_status_shows_last_fault(self):
        w = make_e_watch(ISSUE22)
        w._check_motors(0.)
        w._check_motors(3.)
        w.fan_action = 'pause'
        out = []

        class Cmd:
            def get_int(self, *a, **k):
                return 0

            def respond_info(self, msg):
                out.append(msg)
        w.cmd_SAFETY_STATUS(Cmd())
        self.assertIn("Last extruder fault", out[-1])
        self.assertIn("f7 81 0b 02 0c 08", out[-1])


class ReadParamsTest(unittest.TestCase):
    def test_reads_then_ends_and_never_writes(self):
        bus = make_bus([[float_reply(0x81, 0x06, 2.5)],
                        [reply(0x81, 0x00, 0x06, [0xaf])],
                        [reply(0x81, 0x00, 0x06, [0x01])]])
        values = bus.read_params('x', [0xb4, 0xb5])
        self.assertEqual(values[0xb4], 2.5)
        self.assertIsNone(values[0xb5])
        sent = [w[3:-1] for w in bus.ser.writes]
        for payload in sent:
            self.assertEqual(payload[:2], b'\x00\x06')
            self.assertIn(payload[2], (0x02, 0x03))
        self.assertEqual(sent[-1], b'\x00\x06\x03')

if __name__ == "__main__":
    unittest.main()
