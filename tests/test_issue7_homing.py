"""Issue #7: homing outside a print.

1. A G28 sent in the ~10 s between Klipper's "ready" and the end of the
   motor wake-up failed with "The X/Y/E motors did not wake up". G28 now
   waits for the wake (MOTOR_WAIT_READY) and retries it once if a motor is
   missing.
2. On the K2 Plus, G28 Z outside a print takes Z from the bottom switches,
   which levels both Z motors back to the switches and threw away a
   Z_TILT_ADJUST made just before. KZ_ALIGN_BOTTOM KEEP_TILT=1 puts the
   difference back, and z_tilt saves/applies tilt relative to the switches.
"""
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).parents[1]
KLIPPY = ROOT / "klippy"
sys.path.insert(0, str(KLIPPY))


class _FakeMCUModule(types.ModuleType):
    """Stand-in for Klipper's mcu module (needs cffi and a build)."""

    def __init__(self):
        super().__init__("mcu")

    @staticmethod
    def get_printer_mcu(printer, name):
        return FakeMCU()


class FakeMCU:
    def create_oid(self):
        return 7

    def register_config_callback(self, cb):
        pass

    def register_serial_response(self, cb, msg, oid):
        pass

    def add_config_cmd(self, cmd):
        pass

    def get_constant_float(self, name):
        return 72000000.


def _load(name, path, package=None):
    """Load a klippy module with a fake mcu (and, for z_tilt, a fake probe
    package) without touching sys.modules for other tests."""
    saved = sys.modules.get("mcu")
    sys.modules["mcu"] = _FakeMCUModule()
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        if package is not None:
            module.__package__ = package
        spec.loader.exec_module(module)
        return module
    finally:
        if saved is None:
            del sys.modules["mcu"]
        else:
            sys.modules["mcu"] = saved


# k2_motor_bus imports its sibling k2_motor_proto as "from . import"
_pkg = types.ModuleType("issue7_extras")
_pkg.__path__ = [str(KLIPPY / "extras")]
sys.modules.setdefault("issue7_extras", _pkg)
bus_mod = _load("issue7_extras.k2_motor_bus",
                KLIPPY / "extras" / "k2_motor_bus.py", "issue7_extras")
align_mod = _load("issue7_k2_z_align", KLIPPY / "extras" / "k2_z_align.py")
# z_tilt does "from . import probe": give it a stub package
_zpkg = types.ModuleType("issue7_ztilt_pkg")
_zpkg.__path__ = []
_zpkg.probe = types.SimpleNamespace()
sys.modules.setdefault("issue7_ztilt_pkg", _zpkg)
ztilt_mod = _load("issue7_ztilt_pkg.z_tilt", KLIPPY / "extras" / "z_tilt.py",
                  "issue7_ztilt_pkg")


# --- 1. motor wake-up -----------------------------------------------------

class GcodeError(Exception):
    pass


class FakeGcmd:
    def __init__(self, **params):
        self.params = params
        self.info = []

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return GcodeError(msg)


class WakeReactor:
    """Time only moves when someone pauses; at wake_end the start-up wake
    finishes (with ready_after as its result)."""

    def __init__(self, bus, wake_end, ready_after=True):
        self.now = 0.
        self.bus = bus
        self.wake_end = wake_end
        self.ready_after = ready_after
        self.pauses = 0

    def monotonic(self):
        return self.now

    def pause(self, when):
        self.pauses += 1
        self.now = max(self.now, when)
        if self.wake_end is not None and self.now >= self.wake_end:
            self.bus.wake_state = 'done'
            self.bus.motors_ready = self.ready_after
            self.wake_end = None


def make_bus(state, ready, wake_end=None, ready_after=True, rewake=None):
    bus = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
    bus.motors_ready = ready
    bus.wake_state = state
    bus.motors = {'x': True, 'y': ready, 'e': True}
    bus.reactor = WakeReactor(bus, wake_end, ready_after)
    bus.rewakes = 0

    def wake_motors():
        bus.rewakes += 1
        bus.motors_ready = bool(rewake)
        bus.wake_state = 'done'
        return bus.motors_ready
    bus.wake_motors = wake_motors
    return bus


class MotorWaitReadyTest(unittest.TestCase):
    def test_ready_returns_at_once(self):
        bus = make_bus('done', True)
        gcmd = FakeGcmd()
        bus.cmd_MOTOR_WAIT_READY(gcmd)
        self.assertEqual(bus.reactor.pauses, 0)
        self.assertEqual(gcmd.info, [])

    def test_g28_right_after_restart_waits_for_the_wake(self):
        # The issue's timing: G28 at ready+1 s, motors_ready at ready+9.9 s
        bus = make_bus('running', False, wake_end=9.9)
        gcmd = FakeGcmd()
        bus.cmd_MOTOR_WAIT_READY(gcmd)
        self.assertTrue(bus.motors_ready)
        self.assertGreaterEqual(bus.reactor.now, 9.9)
        self.assertEqual(bus.rewakes, 0)
        self.assertIn("Waiting for the motors", gcmd.info[0])

    def test_wake_not_started_yet_also_waits(self):
        bus = make_bus('pending', False, wake_end=3.)
        bus.cmd_MOTOR_WAIT_READY(FakeGcmd())
        self.assertTrue(bus.motors_ready)

    def test_wake_that_never_ends_times_out(self):
        bus = make_bus('running', False, wake_end=None)
        with self.assertRaises(GcodeError) as cm:
            bus.cmd_MOTOR_WAIT_READY(FakeGcmd(TIMEOUT=5))
        self.assertIn("still waking up", str(cm.exception))
        self.assertAlmostEqual(bus.reactor.now, 5.)

    def test_failed_wake_is_retried_once(self):
        bus = make_bus('running', False, wake_end=9.9, ready_after=False,
                       rewake=True)
        gcmd = FakeGcmd()
        bus.cmd_MOTOR_WAIT_READY(gcmd)
        self.assertEqual(bus.rewakes, 1)
        self.assertTrue(bus.motors_ready)

    def test_still_not_ready_raises_the_old_message(self):
        bus = make_bus('done', False, rewake=False)
        with self.assertRaises(GcodeError) as cm:
            bus.cmd_MOTOR_WAIT_READY(FakeGcmd())
        self.assertIn("did not wake up", str(cm.exception))
        self.assertEqual(bus.rewakes, 1)

    def test_wake_0_does_not_retry(self):
        bus = make_bus('done', False, rewake=True)
        with self.assertRaises(GcodeError):
            bus.cmd_MOTOR_WAIT_READY(FakeGcmd(WAKE=0))
        self.assertEqual(bus.rewakes, 0)

    def test_wake_motors_tracks_its_state(self):
        bus = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
        bus.wake_state = 'pending'
        bus.calibration_warnings = []
        seen = []

        def wake_once():
            seen.append(bus.wake_state)
            bus.motors = {'x': True, 'y': True}
            return True
        bus._wake_once = wake_once
        self.assertTrue(bus.wake_motors())
        self.assertEqual(seen, ['running'])
        self.assertEqual(bus.wake_state, 'done')

    def test_wake_motors_ends_done_after_an_exception(self):
        bus = bus_mod.K2MotorBus.__new__(bus_mod.K2MotorBus)
        bus.wake_state = 'pending'

        def wake_once():
            raise RuntimeError("bus gone")
        bus._wake_once = wake_once
        with self.assertRaises(RuntimeError):
            bus.wake_motors()
        self.assertEqual(bus.wake_state, 'done')


# --- config ---------------------------------------------------------------

def section(cfg, name):
    text = (ROOT / "config" / cfg).read_text(encoding="utf-8")
    m = re.search(r"^\[%s\]\n(.*?)^\[" % re.escape(name), text, re.S | re.M)
    return m.group(1)


class HomingOverrideConfigTest(unittest.TestCase):
    CFGS = ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg")

    def test_waits_for_the_motors_instead_of_failing(self):
        for cfg in self.CFGS:
            body = "\n".join(
                line for line in section(cfg, "homing_override").split("\n")
                if not line.strip().startswith("#"))
            # No render-time check that fails before the wake has ended
            self.assertNotIn("motors_ready", body, cfg)
            wait = body.index("MOTOR_WAIT_READY")
            for move in ("FORCE_MOVE", "KZ_ALIGN_BOTTOM", "G28 Y", "G1 "):
                if move in body:
                    self.assertLess(wait, body.index(move), (cfg, move))

    def test_k2_plus_g28_z_keeps_the_tilt(self):
        body = section("mainline_stage2_k2plus.cfg", "homing_override")
        self.assertRegex(body, r"\{% if z_from_bottom %\}\n\s+"
                               r"KZ_ALIGN_BOTTOM CONFIRM=1 KEEP_TILT=1\n")

    def test_print_start_still_levels_from_the_switches(self):
        # START_PRINT re-levels every print (then _Z_TILT_START)
        for cfg in self.CFGS:
            body = section(cfg, "gcode_macro START_PRINT")
            self.assertIn("KZ_ALIGN_BOTTOM CONFIRM=1\n", body)
            self.assertNotIn("KEEP_TILT", body)

    def test_k2_plus_cfg_is_generated_from_the_k2_cfg(self):
        gen = importlib.util.spec_from_file_location(
            "make_k2plus_cfg", ROOT / "tools" / "k2plus" /
            "make_k2plus_cfg.py")
        mod = importlib.util.module_from_spec(gen)
        gen.loader.exec_module(mod)
        k2 = (ROOT / "config" / "mainline_stage2.cfg").read_text(
            encoding="utf-8")
        plus = (ROOT / "config" / "mainline_stage2_k2plus.cfg").read_text(
            encoding="utf-8")
        out = mod.apply(k2)
        if isinstance(out, tuple):
            out = out[0]
        self.assertEqual(out, plus)


# --- 2. Z tilt kept through the bottom switches -----------------------------

STEP = 8. / (200 * 16)


class FakeStepper:
    def __init__(self, name, mcu_pos):
        self.name = name
        self.mcu_pos = mcu_pos

    def get_name(self):
        return self.name

    def get_mcu_position(self):
        return self.mcu_pos

    def get_step_dist(self):
        return STEP


class FakeKin:
    def __init__(self, steppers):
        self.rails = [None, None, types.SimpleNamespace(
            get_steppers=lambda: steppers)]
        self.cleared = []

    def clear_homing_state(self, axes):
        self.cleared.append(axes)


class FakeToolhead:
    def __init__(self, steppers, homed="xyz"):
        self.steppers = steppers
        self.kin = FakeKin(steppers)
        self.homed = homed
        self.pos = [175., 175., 100., 0.]

    def get_status(self, eventtime):
        return {"homed_axes": self.homed}

    def get_kinematics(self):
        return self.kin

    def wait_moves(self):
        pass

    def get_position(self):
        return list(self.pos)

    def set_position(self, pos, homing_axes=""):
        self.pos = list(pos)
        if homing_axes:
            self.homed = "xyz"

    def manual_move(self, pos, speed):
        # A normal move: every Z motor steps the same
        dz = pos[2] - self.pos[2]
        for s in self.steppers:
            s.mcu_pos += int(round(dz / STEP))
        self.pos = list(pos)


class FakeZHelper:
    """z_tilt's ZAdjustHelper: moves each motor by its adjustment relative
    to the others (only the differences matter)."""

    def __init__(self, steppers):
        self.z_steppers = steppers
        self.calls = []

    def adjust_steppers(self, adjustments, speed):
        self.calls.append(list(adjustments))
        base = min(adjustments)
        for s, a in zip(self.z_steppers, adjustments):
            s.mcu_pos += int(round((a - base) / STEP))


class FakeCompletion:
    def wait(self, waketime):
        return (1, 0)


class AlignReactor:
    def monotonic(self):
        return 0.

    def completion(self):
        return FakeCompletion()

    def pause(self, when):
        pass


class FakeAlignConfig:
    def __init__(self, sections, values):
        self.sections = sections
        self.values = values
        self.printer = None

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getfloat(self, name, default=None, **kw):
        return float(self.values.get(name, default))

    def getint(self, name, default=None, **kw):
        return int(self.values.get(name, default))

    def getboolean(self, name, default=None):
        return self.values.get(name, default)

    def getlist(self, name, default=None):
        value = self.values.get(name)
        if value is None:
            return default
        return [v.strip() for v in value.split(",")]

    def has_section(self, name):
        return name in self.sections

    def getsection(self, name):
        return FakeAlignConfig({}, self.sections[name])

    def error(self, msg):
        return Exception(msg)


class FakeEnable:
    def __init__(self):
        self.callbacks = []

    def register_state_callback(self, cb):
        self.callbacks.append(cb)


class AlignPrinter:
    def __init__(self, toolhead, z_tilt=None):
        self.objects = {"toolhead": toolhead, "z_tilt": z_tilt,
                        "print_stats": None}
        self.handlers = {}
        self.scripts = []
        self.enables = {}
        printer = self

        class StepperEnable:
            def lookup_enable(self, name):
                return printer.enables.setdefault(name, FakeEnable())
        self.objects["stepper_enable"] = StepperEnable()
        self.objects["gcode"] = types.SimpleNamespace(
            register_command=lambda *a, **k: None,
            run_script_from_command=self.scripts.append)
        self.reactor = AlignReactor()

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def load_object(self, config, name):
        return types.SimpleNamespace(register_buttons=lambda *a: None)

    def register_event_handler(self, event, cb):
        self.handlers[event] = cb


SWITCH_STEPS = 140000   # step count of each motor at its bottom switch


def make_align(homed="xyz", with_z_tilt=True, endstop_pin="PA15, PA8"):
    names = ["stepper_z"] + ["stepper_z%d" % i for i in
                             range(1, len(endstop_pin.split(",")))]
    steppers = [FakeStepper(n, 1000) for n in names]
    toolhead = FakeToolhead(steppers, homed)
    helper = FakeZHelper(steppers)
    z_tilt = types.SimpleNamespace(z_helper=helper) if with_z_tilt else None
    printer = AlignPrinter(toolhead, z_tilt)
    sections = {
        "stepper_z": {"step_pin": "PB6", "dir_pin": "PB5",
                      "rotation_distance": 8, "microsteps": 16,
                      "position_max": 360},
        "stepper_z1": {"step_pin": "PB15", "dir_pin": "PA1"},
    }
    config = FakeAlignConfig(sections, {
        "endstop_pin": endstop_pin, "zd_up": 0, "allow_motion": True,
        "position_endstop": 349.5})
    config.printer = printer
    align = align_mod.K2ZAlign(config)

    def send(args):
        # The MCU drives every Z motor to its own switch
        for s in steppers:
            s.mcu_pos = SWITCH_STEPS
    align.query_cmd = types.SimpleNamespace(send=send)
    align.abort_cmd = types.SimpleNamespace(send=lambda args: None)
    return align, steppers, helper, printer


def tilt_of(steppers):
    return (steppers[1].mcu_pos - steppers[0].mcu_pos) * STEP


class KeepTiltTest(unittest.TestCase):
    def level_then_tilt(self, align, steppers, helper, t=0.27):
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1))
        helper.adjust_steppers([0., t], 5.)       # a Z_TILT_ADJUST
        helper.calls.clear()

    def test_g28_z_keeps_a_z_tilt_adjust(self):
        align, steppers, helper, _ = make_align()
        self.level_then_tilt(align, steppers, helper)
        self.assertAlmostEqual(tilt_of(steppers), 0.27, delta=STEP)
        gcmd = FakeGcmd(CONFIRM=1, KEEP_TILT=1)
        align.cmd_KZ_ALIGN_BOTTOM(gcmd)
        self.assertAlmostEqual(tilt_of(steppers), 0.27, delta=STEP)
        self.assertTrue(any("Z tilt kept" in m for m in gcmd.info))
        # The new reference is the switches again, and the tilt is
        # measured from it
        tilt = align.tilt_offsets()
        self.assertAlmostEqual(tilt["stepper_z1"], 0.27, delta=STEP)
        self.assertEqual(tilt["stepper_z"], 0.)

    def test_without_keep_tilt_the_switches_level_the_bed(self):
        align, steppers, helper, _ = make_align()
        self.level_then_tilt(align, steppers, helper)
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1))
        self.assertEqual(tilt_of(steppers), 0.)
        self.assertEqual(helper.calls, [])

    def test_negative_tilt(self):
        align, steppers, helper, _ = make_align()
        self.level_then_tilt(align, steppers, helper, t=-0.15)
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1, KEEP_TILT=1))
        self.assertAlmostEqual(tilt_of(steppers), -0.15, delta=STEP)

    def test_nothing_to_keep_after_a_restart(self):
        # No bottom reference this session: Z unhomed, nothing noted
        align, steppers, helper, _ = make_align(homed="")
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1, KEEP_TILT=1))
        self.assertEqual(helper.calls, [])
        self.assertEqual(tilt_of(steppers), 0.)

    def test_level_bed_moves_nothing(self):
        align, steppers, helper, _ = make_align()
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1))
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1, KEEP_TILT=1))
        self.assertEqual(helper.calls, [])

    def test_motor_off_forgets_the_reference(self):
        align, steppers, helper, printer = make_align()
        printer.handlers["klippy:connect"]()
        self.level_then_tilt(align, steppers, helper)
        self.assertIsNotNone(align.tilt_offsets())
        for cb in printer.enables["stepper_z1"].callbacks:
            cb(0., False)
        self.assertIsNone(align.tilt_offsets())
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1, KEEP_TILT=1))
        self.assertEqual(helper.calls, [])
        # M84 / idle timeout too
        self.level_then_tilt(align, steppers, helper)
        printer.handlers["stepper_enable:motor_off"]()
        self.assertIsNone(align.tilt_offsets())

    def test_too_large_a_difference_is_not_put_back(self):
        align, steppers, helper, _ = make_align()
        self.level_then_tilt(align, steppers, helper, t=3.)
        gcmd = FakeGcmd(CONFIRM=1, KEEP_TILT=1)
        align.cmd_KZ_ALIGN_BOTTOM(gcmd)
        self.assertEqual(helper.calls, [])
        self.assertTrue(any("not kept" in m for m in gcmd.info))

    def test_k2_single_z_motor_unchanged(self):
        align, steppers, helper, _ = make_align(endstop_pin="PA15",
                                                with_z_tilt=False)
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1))
        self.assertIsNone(align.tilt_offsets())
        align.cmd_KZ_ALIGN_BOTTOM(FakeGcmd(CONFIRM=1, KEEP_TILT=1))
        self.assertEqual(steppers[0].mcu_pos,
                         SWITCH_STEPS - int(round(10 / STEP)))


# --- z_tilt: saved relative to the switches ---------------------------------

class ZTiltSavedTest(unittest.TestCase):
    def make(self, tilt):
        zt = ztilt_mod.ZTilt.__new__(ztilt_mod.ZTilt)
        steppers = [FakeStepper("stepper_z", 0), FakeStepper("stepper_z1", 0)]
        zt.z_helper = FakeZHelper(steppers)
        zt.z_positions = [(-20., 175.), (370., 175.)]
        zt.z_status = types.SimpleNamespace(reset=lambda: None)
        zt.probe_helper = types.SimpleNamespace(get_lift_speed=lambda: 5.)
        z_align = types.SimpleNamespace(tilt_offsets=lambda: tilt[0])
        zt.printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None:
            z_align if name == "k2_z_align" else default)
        fd, zt.saved_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, zt.saved_path)
        return zt

    def test_second_run_saves_the_whole_tilt(self):
        # G28 Z kept a 0.27 tilt; a second Z_TILT_ADJUST only adds 0.005
        tilt = [{"stepper_z": 0., "stepper_z1": 0.275}]
        zt = self.make(tilt)
        zt.run_total = [0., 0.005]
        zt._save_run()
        with open(zt.saved_path) as f:
            self.assertEqual(json.load(f)["adjustments"], [0., 0.275])

    def test_falls_back_to_the_run_total(self):
        zt = self.make([None])
        zt.run_total = [0.1, 0.37]
        zt._save_run()
        with open(zt.saved_path) as f:
            self.assertEqual(json.load(f)["adjustments"], [0.1, 0.37])

    def test_apply_saved_only_moves_what_is_missing(self):
        tilt = [{"stepper_z": 0., "stepper_z1": 0.27}]
        zt = self.make(tilt)
        with open(zt.saved_path, "w") as f:
            json.dump({"adjustments": [0., 0.27]}, f)
        gcmd = FakeGcmd(MAX=2.)
        zt.cmd_Z_TILT_APPLY_SAVED(gcmd)
        # Already in place (kept by G28 Z or a warm start): nothing to add
        self.assertEqual(zt.z_helper.calls, [[0., 0.]])
        # Right after the switches it is applied in full
        tilt[0] = {"stepper_z": 0., "stepper_z1": 0.}
        zt.cmd_Z_TILT_APPLY_SAVED(gcmd)
        self.assertEqual(zt.z_helper.calls[-1], [0., 0.27])


if __name__ == "__main__":
    unittest.main()
