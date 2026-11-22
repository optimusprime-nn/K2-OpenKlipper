"""Homing/probing ("drip") steps are sent far enough ahead (GitHub #24).

A K2 Plus START_PRINT tap twice ended in "Timer too close" then "Stepper
too far in past": the first tap steps reached the main MCU after their
step time. Upstream sends drip steps only 0.1 s ahead, less than one host
stall on the K2's own board. [printer] drip_headroom widens that window.
"""
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
try:
    import cffi  # noqa: F401  (chelper needs it; only __init__ uses chelper)
except ImportError:
    import types
    sys.modules.setdefault("chelper", types.ModuleType("chelper"))
mq_mod = importlib.import_module("extras.motion_queuing")


class FakeClock:
    """MCU print time and host time run together, starting at 100 s."""
    NEVER = 9999999999.
    NOW = 0.

    def __init__(self):
        self.now = 100.

    def monotonic(self):
        return self.now

    def estimated_print_time(self, eventtime):
        return eventtime

    def update_timer(self, timer, waketime):
        pass


class FakeCompletion:
    def __init__(self, clock):
        self.clock = clock

    def test(self):
        return False

    def wait(self, waketime):
        self.clock.now = max(self.clock.now, waketime)


def make_queue(headroom=None):
    clock = FakeClock()
    mq = mq_mod.PrinterMotionQueuing.__new__(mq_mod.PrinterMotionQueuing)
    mq.reactor = clock
    mq.mcu = clock
    mq.can_pause = True
    mq.kin_flush_delay = mq_mod.SDS_CHECK_TIME
    mq.flush_timer = None
    mq.do_kick_flush_timer = False
    mq.last_flush_time = mq.last_step_gen_time = 0.
    mq.need_flush_time = mq.need_step_gen_time = 0.
    mq.drip_start_times = []
    mq.drip_time = mq_mod.DRIP_TIME
    if headroom is not None:
        mq.set_drip_headroom(headroom)
    mq.sent = sent = []

    def advance(want_flush, want_sg=0.):
        # (steps generated up to, host/MCU time when they were sent)
        sent.append((want_sg, clock.now))
    mq._advance_flush_time = advance
    mq._await_flush_time = lambda want: None
    return mq, clock


def first_step_lead(headroom=None):
    """How far ahead of its step time the first tap segment is sent.

    The move is started the way toolhead.drip_move() does after
    get_last_move_time(): 0.25 s (BUFFER_TIME_START) plus the homing and
    kin flush delays ahead of the MCU clock.
    """
    mq, clock = make_queue(headroom)
    start = clock.now + 0.25 + 0.002
    mq.drip_update_time(start, start + 2., FakeCompletion(clock))
    for sg_time, sent_at in mq.sent:
        if sg_time > start:
            return start - sent_at
    raise AssertionError("no tap steps were sent")


class DripHeadroomTest(unittest.TestCase):
    def test_default_matches_upstream(self):
        mq, clock = make_queue()
        self.assertEqual(mq.drip_time, mq_mod.DRIP_TIME)
        self.assertAlmostEqual(first_step_lead(), mq_mod.DRIP_TIME, places=6)

    def test_headroom_sends_first_tap_steps_further_ahead(self):
        # The issue #24 shutdowns: the first steps were late by a few ms
        # beyond the 0.1 s window. 0.15 s of headroom more than doubles it.
        self.assertGreaterEqual(first_step_lead(0.15), 0.249)

    def test_headroom_keeps_whole_move_ahead(self):
        mq, clock = make_queue(0.15)
        start = clock.now + 0.252
        mq.drip_update_time(start, start + 1., FakeCompletion(clock))
        for sg_time, sent_at in mq.sent[1:-1]:
            self.assertGreaterEqual(sg_time - sent_at,
                                    0.15 + mq_mod.DRIP_TIME - 1e-6)

    def test_segments_unchanged(self):
        mq, clock = make_queue(0.15)
        start = clock.now + 0.252
        mq.drip_update_time(start, start + 1., FakeCompletion(clock))
        steps = [sg for sg, unused in mq.sent[1:-1]]
        gaps = [b - a for a, b in zip(steps, steps[1:])]
        for gap in gaps[:-2]:
            self.assertAlmostEqual(gap, mq_mod.DRIP_SEGMENT_TIME, places=6)


class ConfigTest(unittest.TestCase):
    @staticmethod
    def printer_section(name):
        text = (ROOT / "config" / name).read_text(encoding='utf-8')
        section = text.split("[printer]\n", 1)[1]
        return section.split("\n[", 1)[0]

    def test_toolhead_reads_option(self):
        toolhead = (ROOT / "klippy" / "toolhead.py").read_text(
            encoding='utf-8')
        self.assertIn("config.getfloat('drip_headroom'", toolhead)
        self.assertIn('set_drip_headroom(self.drip_headroom)', toolhead)

    def test_k2plus_sets_headroom(self):
        self.assertIn("drip_headroom: 0.15",
                      self.printer_section("mainline_stage2_k2plus.cfg"))

    def test_k2_unchanged(self):
        self.assertNotIn("drip_headroom",
                         self.printer_section("mainline_stage2.cfg"))


if __name__ == '__main__':
    unittest.main()
