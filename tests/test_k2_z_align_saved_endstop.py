"""Issue #21: a K2 Plus whose bottom switches sit higher than the default
position_endstop must still home, and KZ_ALIGN_MEASURE SAVE=1 keeps the
measured value across restarts."""
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_k2_z_align_switches as base  # noqa: E402

MODULE = base.MODULE
ROOT = pathlib.Path(__file__).parents[1]


def make(tmpdir, travel=4.0, endstop=349.5, endstop_pin="PA15, PA8"):
    z = base.FakeSection({"step_pin": "PB6", "dir_pin": "PB5",
                          "rotation_distance": 8, "microsteps": 16,
                          "position_max": 360})
    z1 = base.FakeSection({"step_pin": "PB15", "dir_pin": "PA1"})
    config = base.FakeConfig(
        {"endstop_pin": endstop_pin, "zd_up": 0,
         "position_endstop": endstop, "unmeasured_tap_travel": travel,
         "save_margin": 0.1, "max_saved_change": 5.0,
         "state_path": os.path.join(tmpdir, "k2_z_align.json")},
        {"stepper_z": z, "stepper_z1": z1})
    return MODULE.K2ZAlign(config)


class Gcmd:
    def __init__(self, **params):
        self.params = params
        self.info = []

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return Exception(msg)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_saved(self, value):
        with open(os.path.join(self.tmp, "k2_z_align.json"), "w") as f:
            json.dump({"position_endstop": value}, f)


class FirstTapTravel(Base):
    def test_unmeasured_first_tap_after_bottom_reference_goes_deeper(self):
        align = make(self.tmp)
        self.assertEqual(align.endstop_source, "config")
        self.assertIsNone(align.tap_min_z())
        align.frame_from_bottom = True
        self.assertEqual(align.tap_min_z(), -4.0)

    def test_k2_default_keeps_probe_minimum(self):
        align = make(self.tmp, travel=0.0, endstop_pin="PA15")
        align.frame_from_bottom = True
        self.assertIsNone(align.tap_min_z())

    def test_measured_value_keeps_the_narrow_search(self):
        self.write_saved(352.5)
        align = make(self.tmp)
        self.assertEqual(align.position_endstop, 352.5)
        self.assertEqual(align.endstop_source, "saved")
        align.frame_from_bottom = True
        self.assertIsNone(align.tap_min_z())

    def test_tap_ends_the_bottom_frame_and_reports_the_switches(self):
        align = make(self.tmp)
        align.frame_from_bottom = True
        gcmd = Gcmd()
        align.note_home_tap(-3.1, gcmd)
        self.assertFalse(align.frame_from_bottom)
        self.assertIn("Z=352.60", gcmd.info[0])
        self.assertIn("SAVE=1", gcmd.info[0])
        # Only the first tap after the switches
        align.note_home_tap(-0.2, gcmd)
        self.assertEqual(len(gcmd.info), 1)

    def test_measured_and_close_says_nothing(self):
        self.write_saved(352.5)
        align = make(self.tmp)
        align.frame_from_bottom = True
        gcmd = Gcmd()
        align.note_home_tap(-0.1, gcmd)
        self.assertEqual(gcmd.info, [])

    def test_z_homing_ends_the_bottom_frame(self):
        align = make(self.tmp)

        def rail(name):
            stepper = types.SimpleNamespace(get_name=lambda: name)
            return types.SimpleNamespace(get_steppers=lambda: [stepper])

        align.frame_from_bottom = True
        align._handle_home_rails_end(None, [rail("stepper_x")])
        self.assertTrue(align.frame_from_bottom)
        align._handle_home_rails_end(None, [rail("stepper_z")])
        self.assertFalse(align.frame_from_bottom)
        align.frame_from_bottom = True
        align._handle_frame_lost()
        self.assertFalse(align.frame_from_bottom)


class SavedValue(Base):
    def test_far_off_saved_value_is_ignored(self):
        self.write_saved(358.0)
        align = make(self.tmp)
        self.assertEqual(align.position_endstop, 349.5)
        self.assertEqual(align.endstop_source, "config")
        self.assertIn("ignored", align.saved_note)

    def test_garbage_file_is_ignored(self):
        with open(os.path.join(self.tmp, "k2_z_align.json"), "w") as f:
            f.write("{not json")
        align = make(self.tmp)
        self.assertEqual(align.endstop_source, "config")

    def test_save_keeps_lowest_less_margin_and_survives_restart(self):
        align = make(self.tmp)
        line = align._save_measured({0: 352.60, 1: 352.65}, [])
        self.assertIn("Saved position_endstop 352.50", line)
        self.assertEqual(align.position_endstop, 352.5)
        self.assertEqual(align.endstop_source, "saved")
        again = make(self.tmp)
        self.assertEqual(again.position_endstop, 352.5)
        self.assertEqual(again.endstop_source, "saved")

    def test_no_save_unless_every_switch_triggered(self):
        align = make(self.tmp)
        line = align._save_measured({0: 352.6}, [1])
        self.assertIn("Not saved", line)
        self.assertIn("PA8", line)
        self.assertEqual(align.endstop_source, "config")
        self.assertFalse(os.path.exists(align.state_path))

    def test_no_save_far_from_config(self):
        align = make(self.tmp)
        line = align._save_measured({0: 356.0, 1: 356.0}, [])
        self.assertIn("Not saved", line)
        self.assertEqual(align.position_endstop, 349.5)

    def test_forget(self):
        self.write_saved(352.5)
        align = make(self.tmp)
        align.cmd_KZ_ALIGN_FORGET(Gcmd(CONFIRM=1))
        self.assertEqual(align.position_endstop, 349.5)
        self.assertEqual(align.endstop_source, "config")
        self.assertFalse(os.path.exists(align.state_path))


class FakeToolhead:
    def __init__(self, align, triggers, z=5.):
        self.align = align
        self.triggers = triggers
        self.pos = [175., 175., z, 0.]
        self.max_z = z

    def get_status(self, eventtime):
        return {"homed_axes": "xyz"}

    def get_position(self):
        return list(self.pos)

    def manual_move(self, pos, speed):
        self.pos = list(pos)
        self.max_z = max(self.max_z, pos[2])
        state = 0
        for i, trig in enumerate(self.triggers):
            if pos[2] < trig:
                state |= 1 << i  # high = open with zes_untrig 1
        self.align._handle_switch(None, state)

    def wait_moves(self):
        pass


class MeasureSave(Base):
    def measure(self, **params):
        align = make(self.tmp)
        toolhead = FakeToolhead(align, (352.60, 352.65))
        toolhead.manual_move(toolhead.pos, 10.)
        align.printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None:
            toolhead if name == "toolhead" else default)
        align.reactor = types.SimpleNamespace(monotonic=lambda: 0.,
                                              pause=lambda t: None)
        gcmd = Gcmd(CONFIRM=1, **params)
        align.cmd_KZ_ALIGN_MEASURE(gcmd)
        return align, toolhead, gcmd.info[-1]

    def test_measure_without_save_changes_nothing(self):
        align, toolhead, out = self.measure()
        self.assertIn("PA15 triggers at Z=352.6", out)
        self.assertIn("SAVE=1", out)
        self.assertEqual(align.endstop_source, "config")
        self.assertFalse(os.path.exists(align.state_path))
        self.assertLessEqual(toolhead.max_z, 360.)

    def test_measure_save(self):
        align, _, out = self.measure(SAVE=1)
        self.assertIn("Saved position_endstop", out)
        self.assertEqual(align.endstop_source, "saved")
        # lowest switch (352.60, found within one 0.05 step) less 0.1
        self.assertAlmostEqual(align.position_endstop, 352.5, delta=0.051)
        self.assertLess(align.position_endstop, 352.60)
        with open(align.state_path) as f:
            data = json.load(f)
        self.assertEqual(set(data["measured"]), {"PA15", "PA8"})


class ProbeFloor(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "klippy"))
        import importlib
        self.mod = importlib.import_module("extras.prtouch_mainline")
        self.p = self.mod.PRTouchMainlineProbe.__new__(
            self.mod.PRTouchMainlineProbe)
        self.p.probe_minimum_z = -0.6
        self.p.config = None
        patcher = mock.patch.object(self.mod.probe_helpers,
                                    "lookup_minimum_z", return_value=-10.)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_z_align(self):
        self.assertEqual(self.p.home_floor_z(None, Gcmd()), -0.6)

    def test_bottom_frame_unmeasured(self):
        z_align = types.SimpleNamespace(tap_min_z=lambda: -4.0)
        gcmd = Gcmd()
        self.assertEqual(self.p.home_floor_z(z_align, gcmd), -4.0)
        self.assertIn("Z=-4.00", gcmd.info[0])

    def test_measured(self):
        z_align = types.SimpleNamespace(tap_min_z=lambda: None)
        self.assertEqual(self.p.home_floor_z(z_align, Gcmd()), -0.6)

    def test_never_shallower_than_probe_minimum(self):
        z_align = types.SimpleNamespace(tap_min_z=lambda: -0.2)
        self.assertEqual(self.p.home_floor_z(z_align, Gcmd()), -0.6)

    def test_never_below_position_min(self):
        self.mod.probe_helpers.lookup_minimum_z.return_value = -3.
        z_align = types.SimpleNamespace(tap_min_z=lambda: -4.0)
        self.assertEqual(self.p.home_floor_z(z_align, Gcmd()), -3.)


def section(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    m = re.search(r"^\[%s\]\n(.*?)(?=^\[)" % re.escape(name), text,
                  re.S | re.M)
    return dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))


class Configs(unittest.TestCase):
    def test_k2_plus_covers_the_measured_spread(self):
        sec = section("config/mainline_stage2_k2plus.cfg", "k2_z_align")
        low = float(sec["position_endstop"])
        travel = float(sec["unmeasured_tap_travel"])
        # Too low is the safe side: below every printer measured so far
        for measured in (349.85, 350.10, 352.60):
            self.assertLess(low, measured)
            # ... and the first tap reaches the bed on each of them
            self.assertGreaterEqual(low + travel, measured + 0.5)

    def test_k2_unchanged(self):
        sec = section("config/mainline_stage2.cfg", "k2_z_align")
        self.assertNotIn("unmeasured_tap_travel", sec)


if __name__ == "__main__":
    unittest.main()
