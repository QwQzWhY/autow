"""Date reasoning must use the user's clock, not the runner's.

GitHub runners are on UTC; the user is on UTC+8. Between midnight and 08:00
Beijing time `date.today()` on the runner is still yesterday, which shifts
every deadline comparison — and every "今天是" the model is told — by a day.
"""

import os
import unittest
from datetime import timedelta

from autow import cli, clock


class TestClock(unittest.TestCase):
    def setUp(self):
        saved = os.environ.get("AUTOW_UTC_OFFSET")

        def restore():
            if saved is None:
                os.environ.pop("AUTOW_UTC_OFFSET", None)
            else:
                os.environ["AUTOW_UTC_OFFSET"] = saved

        self.addCleanup(restore)
        os.environ.pop("AUTOW_UTC_OFFSET", None)

    def test_defaults_to_beijing(self):
        self.assertEqual(clock.tz().utcoffset(None), timedelta(hours=8))

    def test_the_offset_is_configurable(self):
        os.environ["AUTOW_UTC_OFFSET"] = "0"
        self.assertEqual(clock.tz().utcoffset(None), timedelta(0))

    def test_a_bad_offset_falls_back_to_the_default(self):
        os.environ["AUTOW_UTC_OFFSET"] = "beijing"
        self.assertEqual(clock.tz().utcoffset(None), timedelta(hours=8))

    def test_now_is_timezone_aware(self):
        self.assertIsNotNone(clock.now().tzinfo)

    def test_today_agrees_with_now(self):
        self.assertEqual(clock.today(), clock.now().date())

    def test_iso_now_carries_the_offset(self):
        self.assertTrue(clock.iso_now().endswith("+08:00"))


class TestNormaliseTime(unittest.TestCase):
    """The phone is a third-party app whose date magic text we cannot verify."""

    def setUp(self):
        os.environ.pop("AUTOW_UTC_OFFSET", None)
        self.addCleanup(os.environ.pop, "AUTOW_UTC_OFFSET", None)

    def test_a_missing_value_is_stamped_with_our_clock(self):
        self.assertTrue(cli.normalise_time(None).startswith(clock.today().isoformat()))

    def test_an_empty_value_is_stamped_with_our_clock(self):
        self.assertTrue(cli.normalise_time("   ").startswith(clock.today().isoformat()))

    def test_an_unparseable_value_is_stamped_with_our_clock(self):
        self.assertTrue(cli.normalise_time("not a date").startswith(clock.today().isoformat()))

    def test_a_naive_value_is_read_as_local_time(self):
        # Read as UTC it would land eight hours off in the model's context.
        self.assertEqual(cli.normalise_time("2026-10-12T10:00"), "2026-10-12T10:00:00+08:00")

    def test_an_aware_value_is_converted_not_reinterpreted(self):
        self.assertEqual(cli.normalise_time("2026-10-12T02:00:00+00:00"), "2026-10-12T10:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
