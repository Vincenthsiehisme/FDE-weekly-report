import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from helpers import IN_WEEK, LAST_WEEK, issue, snapshot
import weekly
from lib.jira import CredentialError
from lib.narrative import NarrativeError


FIXTURE_TEAM = {"members": [{"name": "Alice", "since": "2026-09-01", "until": None}]}
FIXTURE_HOLIDAYS = {"years": [2026], "off": {}, "workdays": {}}


class RepoConfigTest(unittest.TestCase):
    def test_repo_config_loads(self):
        # repo 的正式 team.json、holidays.json 格式要對，routine 才不會在 metrics 那步停
        team, calendar = weekly.load_capacity()
        self.assertTrue(team)
        self.assertIn(2026, calendar["years"])


class AtomicWriteTest(unittest.TestCase):
    """算到一半失敗時，磁碟上不能留下這一期的任何新檔案（snapshot、metrics、Kick off 鎖定檔）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        (root / "config" / "excluded.json").write_text('{"cards": {}, "epics": {}}', encoding="utf-8")
        # 名單與假日用固定的最小設定，不讀 repo 的正式檔：正式名單改了不該讓這些測試跟著壞（Codex 審 ab4aff4）；
        # 缺檔會讓 metrics 直接失敗（2026-11-02 本週頁產值與探索），正式檔載不載得進來另有 test_repo_config_loads
        (root / "config" / "team.json").write_text(json.dumps(FIXTURE_TEAM, ensure_ascii=False), encoding="utf-8")
        (root / "config" / "holidays.json").write_text(json.dumps(FIXTURE_HOLIDAYS, ensure_ascii=False), encoding="utf-8")
        self.patches = [
            mock.patch.object(weekly, "ROOT", root),
            mock.patch.object(weekly, "DATA", root / "data"),
            mock.patch.object(weekly, "EXCLUDED_FILE", root / "config" / "excluded.json"),
            mock.patch.object(weekly, "TEAM_FILE", root / "config" / "team.json"),
            mock.patch.object(weekly, "HOLIDAYS_FILE", root / "config" / "holidays.json"),
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
                weekly.publish_week(snap, fresh_fetch=False)
        self.assertEqual(list((self.root / "data").rglob("*")) if (self.root / "data").exists() else [], [])

    def test_success_writes_snapshot_metrics_and_lock(self):
        snap = snapshot([
            issue("CS-1", level=1, status="doing"),
            dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="todo"), description=None),
            issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK),
        ])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        weekly.publish_week(snap, fresh_fetch=False)
        written = sorted(str(p.relative_to(self.root / "data")) for p in (self.root / "data").rglob("*.json"))
        self.assertEqual(written, ["2026-W39/metrics.json", "2026-W39/snapshot.json", "kickoff/CS-1.json"])
        self.assertEqual(json.loads((self.root / "data" / "2026-W39" / "metrics.json").read_text())["totals"]["hours"], 1.0)



class KickoffHoursTest(AtomicWriteTest):
    """kickoff-hours：只把 snapshot 裡 Kick off 的工時換成 Jira 現值，範圍與狀態留在那一期（2026-11-02 W44 重抓混入期末後狀態）。"""

    def _snap(self):
        snap = snapshot([
            issue("CS-1", level=1, status="doing"),
            dict(issue("CS-2", level=0, parent="CS-1", type_="Kick off", status="done", resolved=IN_WEEK, assignee="Alice"), description=None),
            dict(issue("CS-4", level=0, parent="CS-1", type_="Kick off", status="review", points=0.1, assignee="Alice"), description=None),
            issue("CS-3", level=0, parent="CS-1", status="done", points=3.0, resolved=IN_WEEK, assignee="Alice"),
        ])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        return snap

    def client(self, points):
        c = mock.Mock()
        c.search.return_value = [{"key": k, "fields": {"customfield_10016": v}} for k, v in points.items()]
        return c

    def test_only_kickoff_points_change(self):
        snap = self._snap()
        now = weekly.datetime(2026, 11, 2, 15, 0, tzinfo=weekly.TZ)
        # Jira 現在多了期末後建立的卡也不會進來：只查 snapshot 裡已有的 Kick off 卡號
        client = self.client({"CS-2": 1.0, "CS-4": 0.1})
        new, changed = weekly.refresh_kickoff_hours(snap, client, now)
        self.assertEqual(changed, [("CS-2", None, 1.0)])
        self.assertIn("key in (CS-2, CS-4)", client.search.call_args[0][0])
        self.assertEqual([i["key"] for i in new["issues"]], [i["key"] for i in snap["issues"]])
        for before, after in zip(snap["issues"], new["issues"]):
            self.assertEqual({k: v for k, v in after.items() if k != "points"}, {k: v for k, v in before.items() if k != "points"})
        self.assertEqual(new["fetched_at"], snap["fetched_at"])
        self.assertEqual(new["kickoff_points_fetched_at"], now.isoformat())
        self.assertIsNone(snap["issues"][1]["points"])  # 原 snapshot 不被改到

    def test_kickoff_missing_in_jira_fails(self):
        with self.assertRaisesRegex(ValueError, "CS-4"):
            weekly.refresh_kickoff_hours(self._snap(), self.client({"CS-2": 1.0}), weekly.datetime.now(weekly.TZ))

    def test_command_rewrites_snapshot_and_metrics_with_the_new_hours(self):
        path = self.root / "data" / "2026-W39"
        path.mkdir(parents=True)
        (path / "snapshot.json").write_text(json.dumps(self._snap(), ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=self.client({"CS-2": 1.0, "CS-4": 0.1})), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            weekly.main(["kickoff-hours", "2026-W39"])
        metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
        # Alice：一般卡 3h、Kick off 1h → 探索 25%；實際工時不算 Kick off
        self.assertEqual(metrics["health"][-1]["explore_share"], 0.25)
        self.assertEqual(metrics["totals"]["hours"], 3.0)
        saved = json.loads((path / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(next(i for i in saved["issues"] if i["key"] == "CS-2")["points"], 1.0)

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

    def test_source_exists_looks_at_the_whole_folder_not_this_week(self):
        # 來源接上後回補更早的週：資料夾有檔但這一期的累計是 None，頁面要靠這個旗標維持「—」欄（AC-4）
        self.assertFalse(weekly.token_source_exists())
        self._write("2026-W40.json", {"CS-1": 1000})
        self.assertTrue(weekly.token_source_exists())
        self.assertIsNone(weekly.load_cumulative_tokens("2026-W39"))


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
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00"), fresh_fetch=False)
        metrics = weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", with_kickoff=False), fresh_fetch=False)
        epic = metrics["epics"][0]
        self.assertEqual(epic["kickoff"]["sections"]["objective"], "目標")
        [message] = [c["message"] for c in metrics["checks"] if c["kind"] == "kickoff_modified"]
        # 狀態段落不鎖，訊息只能說目標五段是鎖定版本
        self.assertTrue(message.endswith("頁面的目標五段仍顯示鎖定版本"), message)

    def test_recomputing_an_older_week_does_not_touch_the_lock(self):
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Objective：舊目標"), fresh_fetch=False)
        weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", text="Objective：新目標"), fresh_fetch=False)
        before = self._lock()
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Objective：舊目標"), fresh_fetch=False)
        self.assertEqual(self._lock(), before)

    def test_status_sections_show_each_weeks_own_value(self):
        weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Next Milestone：M1"), fresh_fetch=False)
        latest = weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00", text="Next Milestone：M2"), fresh_fetch=False)
        self.assertEqual(latest["epics"][0]["kickoff"]["sections"]["next_milestone"], "M2")
        self.assertNotIn("kickoff_modified", [c["kind"] for c in latest["checks"]])
        before = self._lock()
        older = weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00", text="Next Milestone：M1"), fresh_fetch=False)
        self.assertEqual(older["epics"][0]["kickoff"]["sections"]["next_milestone"], "M1")
        self.assertEqual(self._lock(), before)


class PreviousWeekTest(AtomicWriteTest):
    """本週頁四個數字的上週值：有完成的專案與停滯讀上一期的 metrics.json（團隊績效評估 AC-1）。"""

    def _snap(self, fetched_at):
        # 期間由 week_ending 決定：W39 在 09-22 結束、W40 在 09-29 結束；IN_WEEK 的卡兩期都在累計裡，W40 本週沒有完成
        snap = snapshot([issue("CS-1", level=1, status="doing"),
                         issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK)], fetched_at=fetched_at)
        ending = fetched_at[:10]
        snap["period"] = {"week": weekly.period_ending(date.fromisoformat(ending)).week_id, "week_ending": ending, "start": "", "end_exclusive": ""}
        return snap

    def test_previous_metrics_feed_last_week_values(self):
        first = weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00"), fresh_fetch=False)
        self.assertEqual(first["kpis"]["projects"], {"now": 1, "prev": None})  # 2026-W38 沒有檔
        second = weekly.publish_week(self._snap("2026-09-29T23:59:00+08:00"), fresh_fetch=False)
        self.assertEqual(second["period"]["week"], "2026-W40")
        self.assertEqual(second["kpis"]["projects"], {"now": 0, "prev": 1})  # 讀的是剛寫的 2026-W39
        self.assertEqual(second["kpis"]["stalled"]["prev"], 0)

    def test_broken_previous_metrics_fails_instead_of_dropping_the_comparison(self):
        folder = self.root / "data" / "2026-W38"
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text("{", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            weekly.publish_week(self._snap("2026-09-22T23:59:00+08:00"), fresh_fetch=False)


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
        metrics = weekly.publish_week(self._snap(), fresh_fetch=False)
        self.assertEqual(metrics["totals"]["completed"], 0)
        self.assertIn(("excluded", "CS-3"), [(c["kind"], c["key"]) for c in metrics["checks"]])

    def test_missing_team_or_holiday_file_fails_instead_of_dropping_capacity(self):
        # 2026-11-02 本週頁產值與探索：沒有名單就靜靜算成 null 會看不出缺檔，直接失敗、什麼都不寫
        for name in ("team.json", "holidays.json"):
            saved = (self.root / "config" / name).read_text(encoding="utf-8")
            (self.root / "config" / name).unlink()
            with self.subTest(name), self.assertRaisesRegex(FileNotFoundError, name):
                weekly.publish_week(self._snap(), fresh_fetch=False)
            self.assertFalse((self.root / "data").exists() and any((self.root / "data").rglob("metrics.json")))
            (self.root / "config" / name).write_text(saved, encoding="utf-8")

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
        metrics = weekly.publish_week(snap, fresh_fetch=False)
        self.assertEqual(metrics["totals"]["completed"], 0)
        self.assertEqual([e["id"] for e in metrics["epics"]], [])
        self.assertEqual([(c["kind"], c["key"]) for c in metrics["checks"]], [("excluded_epic", "CS-1")])
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
        metrics = weekly.publish_week(snap, fresh_fetch=False)
        self.assertEqual(metrics["totals"]["tokens"], 5)
        self.assertEqual(metrics["cumulative"]["totals"]["tokens"], 5)
        self.assertEqual([c["kind"] for c in metrics["checks"] if c["kind"] == "token_unknown_key"], [])

    def test_token_source_from_a_later_week_file_is_carried_into_metrics(self):
        # 接線：資料夾狀態要從 weekly 傳進 metrics，render 才不會從累計推成隱藏
        self._write('{"cards": {}, "epics": {}}')
        (self.root / "data" / "tokens").mkdir(parents=True)
        (self.root / "data" / "tokens" / "2026-W40.json").write_text('{"CS-3": 40}', encoding="utf-8")
        metrics = weekly.publish_week(self._snap(), fresh_fetch=False)
        self.assertTrue(metrics["token_source"])
        self.assertIsNone(metrics["cumulative"]["totals"]["tokens"])

    def test_excluded_epic_missing_right_after_fetching_is_an_error(self):
        # prepare 剛抓完 Jira 還找不到：卡號打錯，或 epic 被刪、搬走。放行的話整個專案會照樣上週報
        self._write('{"cards": {}, "epics": {"CS-99": "打錯的卡號"}}')
        with mock.patch.object(weekly, "fetch_snapshot", return_value=self._snap()), \
                mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient"):
            with self.assertRaisesRegex(ValueError, "CS-99"):
                weekly.main(["prepare", "--week-ending", "2026-09-22"])
        self.assertFalse((self.root / "data").exists())

    def test_prepare_checks_credentials_before_waiting_for_the_period_end(self):
        # 憑證錯就在 23:59 開跑時停，不要等到 00:00:30 才發現
        client = mock.MagicMock()
        client.verify.side_effect = CredentialError("Jira 不接受這組憑證")
        with mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=client), \
                mock.patch.object(weekly, "wait_for_end", return_value=90), mock.patch.object(weekly.time, "sleep") as sleep, \
                mock.patch.object(weekly, "fetch_snapshot") as fetch:
            with self.assertRaises(CredentialError):
                weekly.main(["prepare", "--week-ending", "2026-09-22", "--wait-for-period-end"])
        sleep.assert_not_called()
        fetch.assert_not_called()

    def test_publish_week_must_be_told_whether_it_is_a_fresh_fetch(self):
        # 沒有預設值：新的呼叫端忘了講，不能安靜變成寬鬆的重算
        with self.assertRaises(TypeError):
            weekly.publish_week(self._snap())

    def test_excluded_card_missing_right_after_fetching_is_an_error(self):
        self._write('{"cards": {"CS-98": "打錯的卡號"}, "epics": {}}')
        with mock.patch.object(weekly, "fetch_snapshot", return_value=self._snap()), \
                mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient"):
            with self.assertRaisesRegex(ValueError, "CS-98"):
                weekly.main(["prepare", "--week-ending", "2026-09-22"])
        self.assertFalse((self.root / "data").exists())

    def test_recompute_warns_about_excluded_keys_missing_from_the_snapshot(self):
        # 重算放行，但要看得到：打錯的卡號不能等到下一次 prepare 才被發現
        self._write('{"cards": {"CS-98": "打錯"}, "epics": {"CS-99": "打錯"}}')
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            weekly.publish_week(self._snap(), fresh_fetch=False)
        self.assertRegex(err.getvalue(), "CS-98.*CS-99")

    def test_excluded_epic_missing_when_recomputing_is_allowed(self):
        # metrics 重算：epic 可能比這一期的 snapshot 晚建（FDEPROJECT-119 之於 W39），下一次 prepare 會再驗
        self._write('{"cards": {}, "epics": {"CS-99": "之後才建的 epic"}}')
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(weekly.publish_week(self._snap(), fresh_fetch=False)["totals"]["completed"], 1)

    def test_finalize_refuses_metrics_computed_before_the_epic_was_excluded(self):
        site = self.root / "site"
        weekly.publish_week(self._snap(), fresh_fetch=False)
        folder = self.root / "data" / "2026-W39"
        (folder / "narrative.json").write_text('{"highlights": ["完成 CS-3"], "epic_results": {"CS-1": "完成"}}', encoding="utf-8")
        self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
        with mock.patch.object(weekly, "SITE", site):
            with self.assertRaisesRegex(ValueError, "CS-1.*metrics 2026-W39"):
                weekly.main(["finalize", "2026-W39"])
        self.assertFalse(site.exists())

    def test_finalize_refuses_metrics_computed_before_the_card_was_excluded(self):
        # 跟 epic 同一件事：metrics 算完才把卡加進 cards，不重算就 finalize，裁定不算的卡會照樣列成完成
        site = self.root / "site"
        weekly.publish_week(self._snap(), fresh_fetch=False)
        (self.root / "data" / "2026-W39" / "narrative.json").write_text(
            '{"highlights": ["完成 CS-3"], "epic_results": {"CS-1": "完成"}}', encoding="utf-8")
        self._write('{"cards": {"CS-3": "移轉補登"}, "epics": {}}')
        with mock.patch.object(weekly, "SITE", site):
            with self.assertRaisesRegex(ValueError, "CS-3.*metrics 2026-W39"):
                weekly.main(["finalize", "2026-W39"])
        self.assertFalse(site.exists())

    def test_finalize_refuses_metrics_computed_before_a_parent_card_was_excluded(self):
        # 完成清單只列葉節點：排除有子卡的父卡時它不在任何清單裡，要靠 metrics 記下的排除清單擋（Codex 審 PR #17）
        snap = snapshot([issue("CS-1", level=1, status="doing"),
                         issue("CS-3", level=0, parent="CS-1", status="done", resolved=IN_WEEK, assignee="Amy"),
                         issue("CS-4", level=-1, parent="CS-3", status="done", points=1.0, resolved=IN_WEEK)])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        weekly.publish_week(snap, fresh_fetch=False)
        (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": ["完成 CS-4"], "epic_results": {"CS-1": "完成"}}', encoding="utf-8")
        self._write('{"cards": {"CS-3": "誤建"}, "epics": {}}')
        with mock.patch.object(weekly, "SITE", self.root / "site"):
            with self.assertRaisesRegex(ValueError, "另一份 config/excluded.json.*CS-3.*metrics 2026-W39"):
                weekly.main(["finalize", "2026-W39"])
        self.assertFalse((self.root / "site").exists())

    def test_finalize_refuses_an_excluded_card_that_only_shows_on_the_cumulative_page(self):
        snap = snapshot([issue("CS-1", level=1, status="doing"),
                         issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=LAST_WEEK)])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        weekly.publish_week(snap, fresh_fetch=False)
        (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
        self._write('{"cards": {"CS-3": "移轉補登"}, "epics": {}}')
        with mock.patch.object(weekly, "SITE", self.root / "site"):
            with self.assertRaisesRegex(ValueError, "CS-3"):
                weekly.main(["finalize", "2026-W39"])

    def test_finalize_refuses_an_excluded_epic_that_only_shows_on_the_cumulative_page(self):
        # 上一期就結案的 epic 不在本週專案列，只在累計頁
        snap = snapshot([issue("CS-1", level=1, status="done", resolved=LAST_WEEK),
                         issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=LAST_WEEK)])
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        weekly.publish_week(snap, fresh_fetch=False)
        (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
        self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
        with mock.patch.object(weekly, "SITE", self.root / "site"):
            with self.assertRaisesRegex(ValueError, "CS-1"):
                weekly.main(["finalize", "2026-W39"])

    def test_backfill_refuses_to_redraw_the_index_from_stale_latest_metrics(self):
        # 補建舊的一期時首頁用最新一期的 metrics 重繪；那份是排除前算的，被排除的 epic 會回到公開首頁
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            for week, ending in (("2026-W40", "2026-09-29"), ("2026-W39", "2026-09-22")):
                snap = snapshot([issue("CS-1", level=1, status="doing", summary="行政作業")], fetched_at=f"{ending}T23:59:00+08:00")
                snap["period"] = {"week": week, "week_ending": ending, "start": "", "end_exclusive": ""}
                if week == "2026-W40":
                    weekly.publish_week(snap, fresh_fetch=False)
                    (self.root / "data" / week / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
                    weekly.main(["finalize", week])
                    self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
                else:
                    weekly.publish_week(snap, fresh_fetch=False)
                    (self.root / "data" / week / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
                    before = (site / "index.html").read_text(encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "CS-1.*metrics 2026-W40"):
                        weekly.main(["finalize", week])
            self.assertEqual((site / "index.html").read_text(encoding="utf-8"), before)
            self.assertFalse((site / "reports" / "2026-W39.html").exists())

    def test_metrics_command_recomputes_leniently(self):
        # metrics <週> 是重算：epic 比那一期晚建是正常的（FDEPROJECT-119 之於 W39），只警告不失敗
        weekly.publish_week(self._snap(), fresh_fetch=False)
        self._write('{"cards": {}, "epics": {"CS-99": "之後才建的 epic"}}')
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(weekly.main(["metrics", "2026-W39"]), 0)
        self.assertIn("CS-99", err.getvalue())

    def test_render_failure_leaves_no_page(self):
        # 兩頁都畫完才寫：首頁畫失敗時不能留下已更新的週報頁
        site = self.root / "site"
        weekly.publish_week(self._snap(), fresh_fetch=False)
        (self.root / "data" / "2026-W39" / "narrative.json").write_text(
            '{"highlights": ["[CS-1 摘要] 完成 CS-3"], "epic_results": {"CS-1": "完成"}}', encoding="utf-8")
        with mock.patch.object(weekly, "SITE", site), \
                mock.patch.object(weekly, "render", side_effect=["<html>週報</html>", RuntimeError("boom")]):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                weekly.main(["finalize", "2026-W39"])
        self.assertFalse(site.exists())

    def test_narrative_cannot_cite_cards_of_an_excluded_epic(self):
        # narrative 驗證用的卡號要跟 metrics 同一個範圍，不然行政作業的卡號可以寫進公開頁面
        site = self.root / "site"
        snap = self._snap()
        snap["issues"].append(issue("CS-9", level=0, status="done", points=1.0, resolved=IN_WEEK))
        self._write('{"cards": {}, "epics": {"CS-1": "行政作業"}}')
        weekly.publish_week(snap, fresh_fetch=False)
        folder = self.root / "data" / "2026-W39"
        with mock.patch.object(weekly, "SITE", site):
            for key, ok in (("CS-9", True), ("CS-3", False), ("CS-1", False)):
                with self.subTest(key=key):
                    (folder / "narrative.json").write_text(json.dumps({"highlights": [f"[未歸屬 epic] 完成 {key}"], "epic_results": {"_unassigned": "完成"}}, ensure_ascii=False), encoding="utf-8")
                    if ok:
                        weekly.main(["finalize", "2026-W39"])
                    else:
                        with self.assertRaisesRegex(NarrativeError, key):
                            weekly.main(["finalize", "2026-W39"])

class FinalizeArchiveTest(AtomicWriteTest):
    def test_backfilling_an_older_week_updates_the_index_archive(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            for week, ending in (("2026-W40", "2026-09-29"), ("2026-W39", "2026-09-22")):
                snap = snapshot([issue("CS-1", level=1, status="doing")], fetched_at=f"{ending}T23:59:00+08:00")
                snap["period"] = {"week": week, "week_ending": ending, "start": "", "end_exclusive": ""}
                weekly.publish_week(snap, fresh_fetch=False)
                (self.root / "data" / week / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
                weekly.main(["finalize", week])
            index = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn("2026-W40</title>", index)
        self.assertIn('<a href="reports/2026-W39.html">2026-W39</a>', index)


class DeliverableImagesTest(AtomicWriteTest):
    """成效說明顯示截圖（2026-11-03 AC-1）：prepare 先下載圖才寫資料；finalize 缺圖就擋。"""

    def _snap(self):
        from test_deliverable import card
        snap = snapshot([issue("CS-1", level=1, status="doing", summary="批次付款連結", labels=["直售"]), card("CS-50")])
        snap["schema"] = 5
        snap["period"] = {"week": "2026-W39", "week_ending": "2026-09-22", "start": "", "end_exclusive": ""}
        return snap

    def _client(self, error=None):
        from test_deliverable import PNG, FakeDownloads
        client = FakeDownloads({"501": PNG})
        if error:
            client.attachment = mock.Mock(side_effect=error)
        client.verify = mock.Mock()
        return client

    def test_publish_with_a_client_downloads_before_writing(self):
        site = self.root / "site"
        client = self._client()
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._snap(), fresh_fetch=False, client=client)
        self.assertTrue((site / "assets" / "deliverables" / "501.png").is_file())
        self.assertTrue((self.root / "data" / "2026-W39" / "metrics.json").is_file())
        self.assertEqual([a for a, _ in client.asked], ["501"])

    def test_prepare_passes_its_client_so_images_are_downloaded(self):
        # 接線：prepare 剛抓完 Jira 就要下載，不然 finalize 會因為缺圖停下
        site = self.root / "site"
        client = self._client()
        with mock.patch.object(weekly, "SITE", site), mock.patch.object(weekly, "fetch_snapshot", return_value=self._snap()), \
                mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=client):
            weekly.main(["prepare", "--week-ending", "2026-09-22"])
        self.assertTrue((site / "assets" / "deliverables" / "501.png").is_file())

    def test_images_refuses_metrics_computed_before_the_epic_was_excluded(self):
        # metrics 算完才把 epic 加進整棵排除：照舊 metrics 補圖，排除的 epic 的圖會進公開的 site/（Codex 審 e0c5b6c）
        site = self.root / "site"
        client = self._client()
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._snap(), fresh_fetch=False)
            (self.root / "config" / "excluded.json").write_text('{"cards": {}, "epics": {"CS-1": "之後才排除"}}', encoding="utf-8")
            with mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=client):
                with self.assertRaisesRegex(ValueError, "CS-1"):
                    weekly.main(["images", "2026-W39"])
        self.assertEqual(client.asked, [])
        self.assertFalse(site.exists())

    def test_download_failure_writes_no_data(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            with self.assertRaisesRegex(RuntimeError, "連不到"):
                weekly.publish_week(self._snap(), fresh_fetch=False, client=self._client(RuntimeError("連不到")))
        self.assertFalse((self.root / "data").exists())
        self.assertFalse(site.exists())

    def _video_snap(self):
        from test_deliverable import MP4, att, card
        snap = self._snap()
        snap["issues"] = [i for i in snap["issues"] if i["key"] != "CS-50"] + [card("CS-50", attachment=[att("7", "v.mp4", "video/mp4", len(MP4))])]
        return snap

    def _video_client(self):
        from test_deliverable import MP4, FakeDownloads
        client = FakeDownloads({"7": MP4})
        client.verify = mock.Mock()
        return client

    def test_publish_uploads_videos_before_writing(self):
        from test_deliverable import FakeR2
        site, store = self.root / "site", FakeR2()
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._video_snap(), fresh_fetch=False, client=self._video_client(), r2=store)
        self.assertEqual(store.put_calls, [("7.mp4", "video/mp4")])
        self.assertTrue((self.root / "data" / "2026-W39" / "metrics.json").is_file())

    def test_upload_failure_writes_no_data_and_no_images(self):
        from test_deliverable import FakeR2
        store = FakeR2()
        store.put = mock.Mock(side_effect=RuntimeError("R2 掛了"))
        snap = self._video_snap()
        from test_deliverable import att, card
        snap["issues"][-1] = card("CS-50", attachment=[att("501", "list.png", "image/png", 32), att("7", "v.mp4", "video/mp4", 32)])
        client = self._video_client()
        from test_deliverable import PNG
        client.contents["501"] = PNG
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            with self.assertRaisesRegex(RuntimeError, "R2 掛了"):
                weekly.publish_week(snap, fresh_fetch=False, client=client, r2=store)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse(site.exists())  # 影片先傳：失敗時圖還沒寫

    def test_week_without_videos_needs_no_r2_credentials(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site), mock.patch.object(weekly.r2_lib, "load_credentials", side_effect=AssertionError("不該讀")):
            weekly.publish_week(self._snap(), fresh_fetch=False, client=self._client())
        self.assertTrue((site / "assets" / "deliverables" / "501.png").is_file())

    def test_prepare_builds_an_r2_client_when_there_are_videos(self):
        # 接線：prepare 有影片時要自己讀 R2 設定與憑證、真的上傳
        from test_deliverable import FakeR2
        store = FakeR2()
        with mock.patch.object(weekly, "SITE", self.root / "site"), mock.patch.object(weekly, "fetch_snapshot", return_value=self._video_snap()), \
                mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=self._video_client()), \
                mock.patch.object(weekly.r2_lib, "load_credentials", return_value={}), mock.patch.object(weekly.r2_lib, "R2Client", return_value=store):
            weekly.main(["prepare", "--week-ending", "2026-09-22"])
        self.assertEqual(store.put_calls, [("7.mp4", "video/mp4")])

    def test_finalize_refuses_videos_missing_from_r2(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._video_snap(), fresh_fetch=False)
            (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
            with mock.patch.object(weekly.r2_lib, "public_exists", return_value=False) as head:
                with self.assertRaisesRegex(ValueError, "影片不在 R2：7.mp4.*weekly.py images 2026-W39"):
                    weekly.main(["finalize", "2026-W39"])
            head.assert_called_with(weekly.r2_lib.public_url("7.mp4"))
            self.assertFalse((site / "index.html").exists())
            with mock.patch.object(weekly.r2_lib, "public_exists", return_value=True):
                weekly.main(["finalize", "2026-W39"])
        self.assertIn(weekly.r2_lib.public_url("7.mp4"), (site / "index.html").read_text(encoding="utf-8"))

    def test_finalize_names_the_old_schema_before_checking_media(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._video_snap(), fresh_fetch=False)
            path = self.root / "data" / "2026-W39" / "metrics.json"
            old = json.loads(path.read_text(encoding="utf-8"))
            old["schema"] = 18
            path.write_text(json.dumps(old), encoding="utf-8")
            (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
            with mock.patch.object(weekly.r2_lib, "public_exists", side_effect=AssertionError("不該查")):
                with self.assertRaisesRegex(ValueError, "schema 是 18.*weekly.py metrics 2026-W39"):
                    weekly.main(["finalize", "2026-W39"])

    def test_reviewing_card_images_are_downloaded_and_checked(self):
        # 驗收中的圖也公開（2026-11-10 使用者裁）：prepare 要下載，finalize 缺了要擋
        from test_deliverable import card
        snap = self._snap()
        snap["issues"] = [i for i in snap["issues"] if i["key"] != "CS-50"] + [card("CS-50", status="review")]
        site = self.root / "site"
        client = self._client()
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(snap, fresh_fetch=False, client=client)
            self.assertTrue((site / "assets" / "deliverables" / "501.png").is_file())
            (site / "assets" / "deliverables" / "501.png").unlink()
            (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "assets/deliverables/501.png"):
                weekly.main(["finalize", "2026-W39"])

    def test_finalize_refuses_missing_images_until_the_images_command_runs(self):
        site = self.root / "site"
        with mock.patch.object(weekly, "SITE", site):
            weekly.publish_week(self._snap(), fresh_fetch=False)  # 重算：不打 Jira，不下載
            (self.root / "data" / "2026-W39" / "narrative.json").write_text('{"highlights": [], "epic_results": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "assets/deliverables/501.png.*weekly.py images 2026-W39"):
                weekly.main(["finalize", "2026-W39"])
            self.assertFalse((site / "index.html").exists())
            with mock.patch.object(weekly, "load_credentials"), mock.patch.object(weekly, "JiraClient", return_value=self._client()):
                weekly.main(["images", "2026-W39"])
            weekly.main(["finalize", "2026-W39"])
            index = (site / "index.html").read_text(encoding="utf-8")
            report = (site / "reports" / "2026-W39.html").read_text(encoding="utf-8")
        self.assertIn('<img src="assets/deliverables/501.png"', index)
        self.assertIn('<img src="../assets/deliverables/501.png"', report)
