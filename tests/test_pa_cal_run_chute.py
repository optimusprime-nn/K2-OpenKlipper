"""Issue #23: PA_CAL_RUN's wiggle must stay over the chute on each printer.

The wiggle runs at purge_y between XA and XB. The only X range known to be
clear there is the one CFS_PURGE_POSITION already travels (purge_x up to
purge_entry_x), plus the K2's 2 mm left of purge_x that it has always used.
On the K2 Plus the K2 defaults (X113-127) ran 20 mm left of the Plus purge
spot into the chute wall."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).parents[1]
CONFIGS = ("mainline_stage2.cfg", "mainline_stage2_k2plus.cfg")


def section(text, name):
    m = re.search(r"^\[%s\]\n(.*?)^\[" % re.escape(name), text, re.S | re.M)
    return m.group(1)


def cfs_value(text, key):
    m = re.search(r"^%s:\s*([-\d.]+)" % key, section(text, "k2_cfs"), re.M)
    return float(m.group(1))


def wiggle(text):
    body = section(text, "gcode_macro PA_CAL_RUN")
    get = lambda p: float(re.search(
        r"params\.%s\|default\(([-\d.]+)\)" % p, body).group(1))
    return get("XA"), get("XB"), get("SPEED")


class PaCalRunStaysOverTheChute(unittest.TestCase):
    def test_wiggle_inside_the_purge_route(self):
        for cfg in CONFIGS:
            text = (ROOT / "config" / cfg).read_text()
            xa, xb, _ = wiggle(text)
            low = cfs_value(text, "purge_x") - 2
            high = max(cfs_value(text, "purge_entry_x"),
                       cfs_value(text, "purge_gate_x"))
            with self.subTest(cfg=cfg):
                self.assertGreaterEqual(min(xa, xb), low)
                self.assertLessEqual(max(xa, xb), high)

    def test_segment_time_matches_the_k2(self):
        # The analysis overlays slow/fast steps; keep each step about as
        # long as on the K2 (14 mm at 40 mm/s = 0.35 s).
        for cfg in CONFIGS:
            xa, xb, v = wiggle((ROOT / "config" / cfg).read_text())
            with self.subTest(cfg=cfg):
                self.assertAlmostEqual(abs(xb - xa) / v, 0.35, delta=0.03)


if __name__ == "__main__":
    unittest.main()
