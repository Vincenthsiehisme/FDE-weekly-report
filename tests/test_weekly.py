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
        (root / "config" / "excluded.json").write_text('{"cards": {}, "epics": {}}', encoding="utf-8")
        self.patches = [
            mock.patch.object(weekly, "ROOT", root),
            mock.patch.object(weekly, "DATA", root / "data"),
            mock.patch.object(weekly, "UNITS_FILE", root / "config" / "units.json"),
            mock.patch.object(weekly, "EXCLUDED_FILE", root / "config" / "excluded.json"),
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


class CumulativeTokensTest(AtomicWriteTest):
    def _write(self, name, data):
        folder = self.root / "data" / "tokens"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(data), encoding="utf-8")

    def test_no_token_files_means_none(self):
        self.assertIsNone(weekly.load_cumulative_tokens("2026-W39"))

    def test_sums_weeks_up_to_this_one(self):
        self._write("2026-W38.json", {"CS-1": 10, "CS-2": 5})
        self._write("2026-W39.json", {"CS-1": 7})
        self._write("2026-W40.json", {"CS-1": 1000})
        self.assertEqual(weekly.load_cumulative_tokens("2026-W39"), {"CS-1": 17, "CS-2": 5})

    def test_misnamed_file_is_rejected(self):
        self._write("2026-w39.json", {"CS-1": 10})
        with self.assertRaises(ValueError):
            weekly.load_cumulative_tokens("2026-W39")


class KickoffStateTest(AtomicWriteTest):
    def _snap(self, fetched_at, with_kickoff=True, text="Objective：目標"):
        doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
        issues = [issue("CS-1", level=1, status="doing"),
                  issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK)]
        if with_kickoff:
            issues.append(dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="todo"), description=doc))
        snap = snapshot(issues, fetched_at=fetched_at)
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        return snap

    def _lock(self):
        return json.loads((self.root / "data" / "kickoff" / "CS-1.json").read_text())

    def test_missing_card_still_shows_locked_version(self):
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00"))
        metrics = weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", with_kickoff=False))
        epic = metrics["epics"][0]
        self.assertEqual(epic["kickoff"]["sections"]["objective"], "目標")
        [message] = [c["message"] for c in metrics["checks"] if c["kind"] == "kickoff_modified"]
        # 狀態段落不鎖，訊息只能說目標五段是鎖定版本
        self.assertTrue(message.endswith("頁面的目標五段仍顯示鎖定版本"), message)

    def test_recomputing_an_older_week_does_not_touch_the_lock(self):
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Objective：舊目標"))
        weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", text="Objective：新目標"))
        before = self._lock()
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Objective：舊目標"))
        self.assertEqual(self._lock(), before)

    def test_status_sections_show_each_weeks_own_value(self):
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Next Milestone：M1"))
        latest = weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", text="Next Milestone：M2"))
        self.assertEqual(latest["epics"][0]["kickoff"]["sections"]["next_milestone"], "M2")
        self.assertNotIn("kickoff_modified", [c["kind"] for c in latest["checks"]])
        before = self._lock()
        older = weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Next Milestone：M1"))
        self.assertEqual(older["epics"][0]["kickoff"]["sections"]["next_milestone"], "M1")
        self.assertEqual(self._lock(), before)


class ExcludedConfigTest(AtomicWriteTest):
    def _snap(self):
        snap = snapshot([issue("CS-1", level=1, status="doing"),
                         issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK)])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        return snap

    def _write(self, text):
        (self.root / "config" / "excluded.json").write_text(text, encoding="utf-8")

    def test_excluded_file_is_applied(self):
        self._write('{"cards": {"CS-3": "移轉補登"}, "epics": {}}')
        metrics = weekly.publish_week(self._snap())
        self.assertEqual(metrics["totals"]["completed"], 0)
        self.assertIn(("excluded", "CS-3"), [(c["kind"], c["key"]) for c in metrics["checks"]])

    def test_malformed_file_is_rejected(self):
        for text in ('{"cards": ["CS-3"], "epics": {}}', '{"cards": {"CS-3": ""}, "epics": {}}',
                     '{"cards": {"cs-3": "小寫"}, "epics": {}}', '{}', '{"cards": {}}', '{"cards": {}, "epics": ["CS-1"]}',
                     '{"cards": {}, "epics": {"CS-1": " "}}', '{"cards": {}, "epics": {"cs-1": "小寫"}}'):
            with self.subTest(text=text):
                self._write(text)
                with self.assertRaises(ValueError):
                    weekly.load_excluded()


    def test_excluded_epic_is_dropped_before_kickoff_lock_and_metrics(self):
        # 接線：整棵移出要發生在 Kick off 鎖定之前，否則行政作業的 Kick off 也會被鎖定、列進資料檢查
        self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
        snap = self._snap()
        snap["issues"].append(dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="done"), description=None))
        metrics = weekly.publish_week(snap)
        self.assertEqual(metrics["totals"]["completed"], 0)
        self.assertEqual([e["id"] for e in metrics["epics"]], [])
        self.assertEqual([(c["kind"], c["key"]) for c in metrics["checks"]], [("excluded_epic", "CS-1"), ("units_unconfigured", None)])
        self.assertFalse((self.root / "data" / "kickoff").exists())
        saved = json.loads((self.root / "data" / "2026-W39" / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual([i["key"] for i in saved["issues"]], ["CS-1", "CS-3", "CS-2"])  # 原始快照照存，不刪卡

    def test_tokens_of_an_excluded_epic_do_not_count(self):
        # Token 檔是另一個來源，只過濾 issues 的話，被排除的卡的 Token 會算進總量、還會被當成不明卡號列進資料檢查
        self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
        (self.root / "data" / "tokens").mkdir(parents=True)
        (self.root / "data" / "tokens" / "2026-W38.json").write_text('{"CS-3": 40}', encoding="utf-8")
        (self.root / "data" / "tokens" / "2026-W39.json").write_text('{"CS-1": 7, "CS-3": 100, "CS-9": 5}', encoding="utf-8")
        snap = self._snap()
        snap["issues"].append(issue("CS-9", level=0, status="done", points=1.0, resolved=IN_WEEK))
        metrics = weekly.publish_week(snap)
        self.assertEqual(metrics["totals"]["tokens"], 5)
        self.assertEqual(metrics["cumulative"]["totals"]["tokens"], 5)
        self.assertEqual([c["kind"] for c in metrics["checks"] if c["kind"] == "token_unknown_key"], [])

class FinalizeArchiveTest(AtomicWriteTest):
    def test_backfilling_an_older_week_updates_the_index_archive(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            for week, ending in (("2026-W40", "2026-09-29"), ("2026-W39", "2026-09-22")):
                snap = snapshot([issue("CS-1", level=1, status="doing")], fetched_at=f"{ending}T23:59:00+08:00")
                snap["period"] = {"week": week, "week_ending": ending, "start": "", "end_exclusive": ""}
                weekly.publish_week(snap)
                (self.root / "data" / week / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
                weekly.main(["finalize", week])
            index = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn("2026-W40</title>", index)
        self.assertIn('<a href="reports/2026-W39.html">2026-W39</a>', index)
