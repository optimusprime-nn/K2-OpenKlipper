"""Every CFS position in the K2 Plus config lies inside the K2 Plus travel
and is the K2 Plus value, not one left over from the K2.

Issue #23 was a K2 coordinate (PA_CAL_RUN's wiggle at X113-127) kept on
the Plus, where it ran into the chute wall. This checks the rest: the
purge route, chute swing, brush circles, cutter staging and cut/release
X, the unload shake, the stall retry sweep, the parks that the CFS
commands require (purge_return_x, purge_safe_y), and every literal X/Y
move in the generated config. Travel: X -10..352.5, Y -6.2..400 (stock
K2 Plus printer.cfg)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]
K2 = (ROOT / "config" / "mainline_stage2.cfg").read_text()
PLUS = (ROOT / "config" / "mainline_stage2_k2plus.cfg").read_text()
CFS_PY = (ROOT / "klippy" / "extras" / "k2_cfs.py").read_text()

# Stock K2 Plus box.cfg values, as make_k2plus_cfg.py cites them
STOCK_PLUS = {
    "purge_safe_y": 345, "purge_return_x": 225,     # safe_pos 225,345
    "purge_x": 133, "purge_y": 378,                 # extrude_pos 133,378
    "purge_gate_x": 135, "purge_entry_x": 139,      # chute left swing limit
    "clean_left_x": 135, "clean_right_x": 160,      # chute swing 135-160
    "clean_circle_left_x": 160, "clean_circle_right_x": 170,  # brush
    "clean_plate_x": 170, "clean_y": 374,
    "cutter_y": 200,                                # cut_pos_y
}
X_KEYS = ("purge_entry_x", "purge_gate_x", "purge_x", "purge_return_x",
          "clean_plate_x", "clean_left_x", "clean_right_x",
          "clean_circle_left_x", "clean_circle_right_x",
          "clean_brush_left_x", "clean_brush_right_x",
          "cutter_x", "cutter_cut_x", "cutter_release_x")
Y_KEYS = ("purge_safe_y", "purge_y", "clean_y", "cutter_y")


def section(text, name):
    m = re.search(r"^\[%s\]\n(.*?)(?=^\[)" % re.escape(name), text,
                  re.S | re.M)
    return m.group(1)


def value(text, sect, key):
    m = re.search(r"^%s:\s*([-\d.]+)" % key, section(text, sect), re.M)
    return float(m.group(1)) if m else None


def cfs(text, key):
    return value(text, "k2_cfs", key)


def travel(text):
    return (value(text, "stepper_x", "position_min"),
            value(text, "stepper_x", "position_max"),
            value(text, "stepper_y", "position_min"),
            value(text, "stepper_y", "position_max"))


class K2PlusCfsPositions(unittest.TestCase):
    def setUp(self):
        self.x0, self.x1, self.y0, self.y1 = travel(PLUS)

    def in_x(self, x, what):
        self.assertTrue(self.x0 <= x <= self.x1,
                        "%s X%.2f outside X %.1f..%.1f"
                        % (what, x, self.x0, self.x1))

    def in_y(self, y, what):
        self.assertTrue(self.y0 <= y <= self.y1,
                        "%s Y%.2f outside Y %.1f..%.1f"
                        % (what, y, self.y0, self.y1))

    def test_plus_travel(self):
        self.assertEqual((self.x0, self.x1, self.y0, self.y1),
                         (-10., 352.5, -6.2, 400.))

    def test_stock_k2_plus_box_cfg_values(self):
        for key, want in STOCK_PLUS.items():
            with self.subTest(key=key):
                self.assertEqual(cfs(PLUS, key), want)

    def test_no_k2_value_left(self):
        # Each chute/brush/cutter-row position differs from the K2 one
        for key in STOCK_PLUS:
            with self.subTest(key=key):
                self.assertNotEqual(cfs(PLUS, key), cfs(K2, key))

    def test_every_cfs_position_inside_the_travel(self):
        for key in X_KEYS:
            with self.subTest(key=key):
                self.assertIsNotNone(cfs(PLUS, key), key)
                self.in_x(cfs(PLUS, key), key)
        for key in Y_KEYS:
            with self.subTest(key=key):
                self.in_y(cfs(PLUS, key), key)

    def test_cut_depth_range_inside_the_travel(self):
        # cutter_cut_x is validated -10..-5 and install.sh clamps to that;
        # the deepest allowed ram must still be a legal X on the Plus
        self.assertIn("'cutter_cut_x', -7.2, minval=-10., maxval=-5.",
                      CFS_PY)
        self.in_x(-10., "deepest cutter_cut_x")
        self.in_x(value(PLUS, "k2_cfs", "cutter_cal_min_x") or -9.5,
                  "cutter_cal_min_x")

    def test_brush_circles_inside_the_brush_and_travel(self):
        r = float(re.search(r'"G2 I([\d.]+) J0', CFS_PY).group(1))
        self.assertIn('"G3 I-%g J0' % r, CFS_PY)
        cy = cfs(PLUS, "clean_y")
        left = cfs(PLUS, "clean_circle_left_x")
        right = cfs(PLUS, "clean_circle_right_x")
        # G2 from the left start goes round a centre r to its right, G3
        # from the right start round a centre r to its left
        lo, hi = left, right
        self.assertLessEqual(left + 2 * r, right + 1e-9)
        self.assertGreaterEqual(right - 2 * r, left - 1e-9)
        self.assertGreaterEqual(lo, cfs(PLUS, "clean_right_x"))
        self.assertLessEqual(hi, cfs(PLUS, "clean_plate_x"))
        self.in_y(cy - r, "circle front")
        self.in_y(cy + r, "circle back")

    def test_unload_shake_stays_on_the_chute(self):
        far = min(cfs(PLUS, "purge_x") + 8., cfs(PLUS, "clean_right_x"))
        self.assertIn("'unload_shake_distance', 8.", CFS_PY)
        self.assertLessEqual(far, cfs(PLUS, "clean_right_x"))
        self.in_x(far, "unload shake")

    def test_stall_retry_sweep(self):
        text = section(PLUS, "k2_cfs")
        moves = re.search(r"^stall_retry_moves:\s*(.*)$", text,
                          re.M).group(1)
        for item in moves.split(","):
            axis, v = item.strip()[0], float(item.strip()[1:])
            with self.subTest(move=item):
                if axis == "X":
                    self.assertTrue(0 <= v <= self.x1)
                else:
                    self.assertTrue(0 <= v <= cfs(PLUS, "purge_safe_y"))

    def test_macro_parks_are_the_cut_start(self):
        # CFS_CUT_UNLOAD and CFS_CUTTER_POSITION require the head at
        # (purge_return_x, purge_safe_y); the macros park there by literal
        x, y = cfs(PLUS, "purge_return_x"), cfs(PLUS, "purge_safe_y")
        body = section(PLUS, "gcode_macro CFS_UNLOAD_ACTIVE")
        self.assertIn("G1 Y%g F6000\n    G1 X%g F6000" % (y, x), body)
        self.assertIn("G1 X%g Y%g F12000" % (x, y),
                      section(PLUS, "gcode_macro PAUSE"))

    def test_every_literal_xy_move_inside_the_travel(self):
        for n, line in enumerate(PLUS.splitlines(), 1):
            code = line.split("#")[0].split(";")[0].strip()
            if not re.match(r"G[01]\s", code):
                continue
            for axis, v in re.findall(r"\b([XY])(-?\d+(?:\.\d+)?)\b", code):
                with self.subTest(line=n, move=code):
                    if axis == "X":
                        self.in_x(float(v), "line %d" % n)
                    else:
                        self.in_y(float(v), "line %d" % n)


if __name__ == "__main__":
    unittest.main()
