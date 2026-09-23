import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from helpers import IN_WEEK, issue, snapshot
import weekly


class AtomicWriteTest(unittest.TestCase):
    """算到一半失敗時，磁碟上不能留下這一期的任何新檔案（snapshot、metrics、Kick off 鎖定檔）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        (root / "config" / "units.json").write_text('{"units": []}', encoding="utf-8")
        self.patches = [
            mock.patch.object(weekly, "ROOT", root),
            mock.patch.object(weekly, "DATA", root / "data"),
            mock.patch.object(weekly, "UNITS_FILE", root / "config" / "units.json"),
        ]
        for p in self.patches:
            p.start()
        self.root = root

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_failure_after_kickoff_reconcile_leaves_nothing(self):
        snap = snapshot([
            issue("CS-1", level=1, status="doing"),
            dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="todo"), description=None),
            issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK),
        ])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        with mock.patch.object(weekly, "compute", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                weekly.publish_week(snap)
        self.assertEqual(list((self.root / "data").rglob("*")) if (self.root / "data").exists() else [], [])

    def test_success_writes_snapshot_metrics_and_lock(self):
        snap = snapshot([
            issue("CS-1", level=1, status="doing"),
            dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="todo"), description=None),
            issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK),
        ])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        weekly.publish_week(snap)
        written = sorted(str(p.relative_to(self.root / "data")) for p in (self.root / "data").rglob("*.json"))
        self.assertEqual(written, ["2026-W39/metrics.json", "2026-W39/snapshot.json", "kickoff/CS-1.json"])
        self.assertEqual(json.loads((self.root / "data" / "2026-W39" / "metrics.json").read_text())["totals"]["hours"], 1.0)


if __name__ == "__main__":
    unittest.main()
