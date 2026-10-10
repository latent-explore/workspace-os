import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from gateway.scheduler import Scheduler, next_fire, parse_entries

D = datetime


class NextFireTests(unittest.TestCase):
    def test_daily_later_today_and_tomorrow(self):
        self.assertEqual(next_fire("daily 07:00", D(2026, 10, 10, 6, 0)), D(2026, 10, 10, 7, 0))
        self.assertEqual(next_fire("daily 07:00", D(2026, 10, 10, 7, 0)), D(2026, 10, 11, 7, 0))

    def test_weekly(self):
        # 2026-10-10 is a Saturday
        self.assertEqual(next_fire("weekly mon 08:30", D(2026, 10, 10, 9, 0)), D(2026, 10, 12, 8, 30))

    def test_every(self):
        self.assertEqual(next_fire("every 30m", D(2026, 10, 10, 6, 0)), D(2026, 10, 10, 6, 30))
        self.assertEqual(next_fire("every 2h", D(2026, 10, 10, 6, 0)), D(2026, 10, 10, 8, 0))

    def test_once_fires_only_until_run(self):
        self.assertEqual(next_fire("once 2026-10-12 09:00", None), D(2026, 10, 12, 9, 0))
        self.assertIsNone(next_fire("once 2026-10-12 09:00", D(2026, 10, 12, 9, 1)))

    def test_bad_when(self):
        for bad in ["", "daily", "daily 25:00", "every 0m", "weekly xyz 08:00", "sometimes"]:
            with self.assertRaises(ValueError, msg=bad):
                next_fire(bad, D(2026, 10, 10))


class SchedulerTests(unittest.TestCase):
    def make(self, yaml_text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "schedules.yaml"
        path.write_text(yaml_text)
        self.fired = []
        return Scheduler(path, Path(tmp.name), lambda e: self.fired.append(e.id)), tmp.name

    DAILY = "schedules:\n  - id: a\n    when: daily 07:00\n    run: {message: hi}\n"

    def test_new_entry_waits_then_fires(self):
        s, _ = self.make(self.DAILY)
        s.tick(D(2026, 10, 10, 9, 0))             # first sight: starts counting, no retroactive fire
        self.assertEqual(self.fired, [])
        s.tick(D(2026, 10, 11, 7, 0, 5))
        self.assertEqual(self.fired, ["a"])

    def test_catch_up_runs_once_after_downtime(self):
        s, d = self.make(self.DAILY)
        s.tick(D(2026, 10, 10, 9, 0))
        s2 = Scheduler(s.schedules_path, Path(d), lambda e: self.fired.append(e.id))  # "restart"
        s2.tick(D(2026, 10, 14, 12, 0))           # down for 4 days
        s2.tick(D(2026, 10, 14, 12, 1))
        self.assertEqual(self.fired, ["a"])       # once, not four times

    def test_no_double_fire_in_same_window(self):
        s, _ = self.make(self.DAILY)
        s.tick(D(2026, 10, 10, 9, 0))
        s.tick(D(2026, 10, 11, 7, 0, 5))
        s.tick(D(2026, 10, 11, 7, 1))
        self.assertEqual(self.fired, ["a"])

    def test_pause_and_resume(self):
        s, _ = self.make(self.DAILY)
        s.tick(D(2026, 10, 10, 9, 0))
        self.assertTrue(s.set_paused("a", True))
        s.tick(D(2026, 10, 11, 8, 0))
        self.assertEqual(self.fired, [])
        s.set_paused("a", False)
        s.tick(D(2026, 10, 11, 8, 1))
        self.assertEqual(self.fired, ["a"])
        self.assertFalse(s.set_paused("nope", True))

    def test_until_stops_entry(self):
        s, _ = self.make(self.DAILY + "    until: 2026-10-11\n")
        s.tick(D(2026, 10, 10, 9, 0))
        s.tick(D(2026, 10, 12, 8, 0))
        self.assertEqual(self.fired, [])

    def test_once_fires_one_time(self):
        s, _ = self.make("schedules:\n  - id: o\n    when: once 2026-10-10 09:00\n    run: {message: x}\n")
        s.tick(D(2026, 10, 10, 8, 59))
        s.tick(D(2026, 10, 10, 9, 1))
        s.tick(D(2026, 10, 10, 9, 2))
        self.assertEqual(self.fired, ["o"])

    def test_bad_entries_are_skipped_not_fatal(self):
        entries = parse_entries({"schedules": [
            {"id": "ok", "when": "daily 07:00", "run": {"message": "x"}},
            {"id": "bad", "when": "whenever", "run": {"message": "x"}},
            {"id": "norun", "when": "daily 07:00", "run": {}},
            {"when": "daily 07:00"},
        ]})
        self.assertEqual([e.id for e in entries], ["ok"])

    def test_status_text(self):
        s, _ = self.make(self.DAILY)
        s.tick(D(2026, 10, 10, 9, 0))
        self.assertIn("next Sun 11 Oct 07:00", s.status(D(2026, 10, 10, 9, 0))[0])


if __name__ == "__main__":
    unittest.main()
