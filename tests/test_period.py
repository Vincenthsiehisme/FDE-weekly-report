import unittest
from datetime import date, datetime, timedelta, timezone

from helpers import *  # noqa: F401,F403  (設定 sys.path)
from lib.period import TZ, period_containing, period_ending, period_for, wait_for_end


class PeriodContainingTest(unittest.TestCase):
    def test_moment_maps_to_the_period_that_contains_it(self):
        # 週三 00:00 是新一期的開始，週二 23:59:59 是這一期的最後一刻
        cases = [
            (datetime(2026, 9, 16, 0, 0, tzinfo=TZ), date(2026, 9, 22)),
            (datetime(2026, 9, 18, 4, 0, tzinfo=TZ), date(2026, 9, 22)),
            (datetime(2026, 9, 22, 23, 59, 59, tzinfo=TZ), date(2026, 9, 22)),
            (datetime(2026, 9, 23, 0, 0, tzinfo=TZ), date(2026, 9, 29)),
            (datetime(2026, 9, 22, 16, 0, tzinfo=timezone.utc), date(2026, 9, 29)),  # 台北週三 00:00
        ]
        for moment, ending in cases:
            period = period_containing(moment)
            self.assertEqual(period.week_ending, ending, moment)
            self.assertTrue(period.contains(moment), moment)


class PeriodTest(unittest.TestCase):
    def test_scheduled_run_on_tuesday_night_is_that_week(self):
        self.assertEqual(period_for(datetime(2026, 9, 22, 23, 59, tzinfo=TZ)).week_ending, date(2026, 9, 22))

    def test_late_start_after_midnight_still_counts_as_tuesday_week(self):
        # 排程晚幾分鐘跨到週三，期間不能往後錯開一週
        self.assertEqual(period_for(datetime(2026, 9, 23, 0, 5, tzinfo=TZ)).week_ending, date(2026, 9, 22))
        self.assertEqual(period_for(datetime(2026, 9, 23, 23, 0, tzinfo=TZ)).week_ending, date(2026, 9, 22))

    def test_utc_input_is_converted_before_picking_the_day(self):
        utc = timezone.utc
        self.assertEqual(period_for(datetime(2026, 9, 22, 15, 59, tzinfo=utc)).week_ending, date(2026, 9, 22))
        self.assertEqual(period_for(datetime(2026, 9, 22, 16, 5, tzinfo=utc)).week_ending, date(2026, 9, 22))
        self.assertEqual(period_for(datetime(2026, 9, 22, 1, 0, tzinfo=utc)).week_ending, date(2026, 9, 22))

    def test_manual_run_later_in_week_reports_last_complete_week(self):
        self.assertEqual(period_for(datetime(2026, 9, 28, 12, 0, tzinfo=TZ)).week_ending, date(2026, 9, 22))

    def test_boundaries(self):
        p = period_ending(date(2026, 9, 22))
        self.assertEqual(p.start.isoformat(), "2026-09-16T00:00:00+08:00")
        self.assertEqual(p.end_exclusive.isoformat(), "2026-09-23T00:00:00+08:00")
        self.assertTrue(p.contains(p.start))
        self.assertFalse(p.contains(p.start - timedelta(microseconds=1)))
        self.assertTrue(p.contains(p.end_exclusive - timedelta(microseconds=1)))
        self.assertFalse(p.contains(p.end_exclusive))
        self.assertEqual(p.week_id, "2026-W39")

    def test_wait_for_period_end(self):
        p = period_ending(date(2026, 9, 22))
        # 23:59:10 開跑：等到週三 00:00:30
        self.assertEqual(wait_for_end(p, datetime(2026, 9, 22, 23, 59, 10, tzinfo=TZ)), 80)
        # 期間已結束就不等
        self.assertEqual(wait_for_end(p, datetime(2026, 9, 23, 0, 5, tzinfo=TZ)), 0)
        # 離結束還很久，代表排程設錯，不要乾等
        with self.assertRaisesRegex(ValueError, "排程"):
            wait_for_end(p, datetime(2026, 9, 22, 12, 0, tzinfo=TZ))

    def test_rejects_non_tuesday_and_naive_time(self):
        with self.assertRaises(ValueError):
            period_ending(date(2026, 9, 23))
        with self.assertRaises(ValueError):
            period_for(datetime(2026, 9, 22, 23, 59))
        with self.assertRaises(ValueError):
            period_ending(date(2026, 9, 22)).contains(datetime(2026, 9, 20))


if __name__ == "__main__":
    unittest.main()
