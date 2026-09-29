import unittest
from datetime import date

from helpers import IN_WEEK, LAST_WEEK, issue, snapshot
from lib.metrics import UNASSIGNED_EPIC, UNIT_MULTIPLE, UNIT_UNCLASSIFIED, compute, drop_excluded_epics, kickoff_by_epic
from lib.period import period_ending

PERIOD = period_ending(date(2026, 9, 22))


def base_issues():
    return [
        issue("CS-1", level=1, status="doing", assignee="Epic Owner", summary="地接社資料彙整"),
        # 父卡自己估 5.0，子任務合計 2.5 已完成＋0.0 未完成：只能加子任務
        issue("CS-2", level=0, parent="CS-1", status="done", points=5.0, resolved=IN_WEEK, assignee="Hubert"),
        issue("CS-3", level=-1, parent="CS-2", status="done", points=0.5, resolved=IN_WEEK, labels=["資料組"]),
        issue("CS-4", level=-1, parent="CS-2", status="done", points=2.0, resolved=IN_WEEK, assignee="Amy", labels=["資料組", "未列單位"]),
        issue("CS-5", level=-1, parent="CS-2", status="todo", points=0.0),
        # 上週完成的不算本週
        issue("CS-6", level=0, parent="CS-1", status="done", points=3.0, resolved=LAST_WEEK),
        # 不在 epic 底下
        issue("CS-7", level=0, status="done", points=2.0, resolved=IN_WEEK, assignee="Hubert", labels=["資料組", "產品組"]),
        issue("CS-8", level=0, parent="CS-1", status="doing", assignee="Vincent", updated="2026-09-01T10:00:00.000+0800"),
        # 先前就完成的 epic 不列
        issue("CS-9", level=1, status="done", resolved=LAST_WEEK),
        issue("CS-10", level=0, parent="CS-9", status="done", points=1.0, resolved=LAST_WEEK),
    ]


def run(issues, units=("資料組", "產品組"), tokens=None, states=None, excluded=None):
    return compute(snapshot(issues), PERIOD, list(units), tokens, states or {}, [], cumulative_tokens=tokens, excluded=excluded or {})


class HoursTest(unittest.TestCase):
    def test_only_leaf_points_completed_this_week_count(self):
        m = run(base_issues())
        epic = next(e for e in m["epics"] if e["id"] == "CS-1")
        self.assertEqual(epic["week"]["hours"], 2.5)  # 0.5 + 2.0，不含父卡 5.0、不含上週 3.0
        self.assertEqual(m["totals"]["hours"], 4.5)   # 再加未歸屬的 CS-7 2.0
        self.assertEqual(epic["week"]["completed_keys"], ["CS-2", "CS-3", "CS-4"])
        self.assertEqual(m["totals"]["completed"], 4)

    def test_delivery_rolls_subtasks_up_to_parent(self):
        epic = next(e for e in run(base_issues())["epics"] if e["id"] == "CS-1")
        self.assertEqual([(d["key"], d["hours"], d["result"]) for d in epic["week"]["deliveries"]], [("CS-2", 2.5, "完成")])

    def test_progress_uses_all_leaves_of_the_epic(self):
        epic = next(e for e in run(base_issues())["epics"] if e["id"] == "CS-1")
        # 葉節點：CS-3、CS-4、CS-5、CS-6、CS-8
        self.assertEqual(epic["progress"], {"done_cards": 3, "total_cards": 5, "done_points": 5.5,
                                            "total_points": 5.5, "percent": 100, "unestimated_cards": 1})

    def test_unestimated_completed_leaf_is_zero_and_reported(self):
        issues = base_issues() + [issue("CS-11", level=0, parent="CS-1", status="done", resolved=IN_WEEK)]
        m = run(issues)
        self.assertEqual(m["totals"]["unestimated_completed"], 1)
        self.assertIn(("unestimated", "CS-11"), [(c["kind"], c["key"]) for c in m["checks"]])


class GroupingTest(unittest.TestCase):
    def test_unassigned_epic_bucket_and_settled_epic_hidden(self):
        m = run(base_issues())
        ids = [e["id"] for e in m["epics"]]
        self.assertEqual(ids, ["CS-1", UNASSIGNED_EPIC])
        self.assertEqual(m["totals"]["projects"], 2)

    def test_owner_falls_back_to_parent_assignee(self):
        owners = {o["owner"]: o for o in run(base_issues())["owners"]}
        self.assertEqual(owners["Hubert"]["hours"], 2.5)  # CS-3（歸父卡 Hubert）0.5 + CS-7 2.0
        self.assertEqual(owners["Amy"]["hours"], 2.0)

    def test_unassigned_ordinary_card_is_not_given_to_epic_owner(self):
        rows = {r["key"]: r for r in run(base_issues())["completed"]}
        self.assertEqual(rows["CS-3"]["owner"], "Hubert")  # 子任務歸父卡
        issues = base_issues() + [issue("CS-13", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK)]
        rows = {r["key"]: r for r in run(issues)["completed"]}
        self.assertEqual(rows["CS-13"]["owner"], "未指派")  # epic 有負責人也不往上算

    def test_units_whitelist(self):
        m = run(base_issues())
        units = {u["unit"]: u["hours"] for u in m["units"]}
        self.assertEqual(units, {"資料組": 2.5, UNIT_MULTIPLE: 2.0})
        self.assertIn("label「未列單位」不在 config/units.json 白名單，歸為未分類", [c["message"] for c in m["checks"]])
        self.assertNotIn("units_unconfigured", [c["kind"] for c in m["checks"]])
        m = run(base_issues(), units=())
        self.assertEqual({u["unit"] for u in m["units"]}, {UNIT_UNCLASSIFIED})
        self.assertIn("units_unconfigured", [c["kind"] for c in m["checks"]])

    def test_status_is_done_or_on_track_only(self):
        issues = base_issues()
        issues[0] = issue("CS-1", level=1, status="done", resolved=IN_WEEK)
        epic = next(e for e in run(issues)["epics"] if e["id"] == "CS-1")
        self.assertEqual(epic["status"], "Done")
        epic = next(e for e in run(base_issues())["epics"] if e["id"] == "CS-1")
        self.assertEqual(epic["status"], "On Track")


class DataQualityTest(unittest.TestCase):
    def test_done_without_resolution_is_not_counted(self):
        issues = base_issues() + [issue("CS-12", level=0, parent="CS-1", status="done", points=9.0)]
        m = run(issues)
        self.assertEqual(m["totals"]["hours"], 4.5)
        self.assertIn(("done_without_resolution", "CS-12"), [(c["kind"], c["key"]) for c in m["checks"]])

    def test_rows_carry_group_root_and_leaf(self):
        m = run(base_issues())
        for row in m["completed"] + m["in_progress"]:
            self.assertLessEqual({"group", "root", "leaf"}, set(row), row["key"])
        issues = base_issues() + [issue("CS-15", level=-1, parent="CS-8", status="doing")]
        rows = {r["key"]: r for r in run(issues)["in_progress"]}
        self.assertEqual((rows["CS-15"]["root"], rows["CS-15"]["group"], rows["CS-15"]["leaf"]), ("CS-8", "CS-1", True))

    def test_in_progress_state_and_next(self):
        rows = {r["key"]: r for r in run(base_issues())["in_progress"]}
        self.assertEqual(rows["CS-8"]["current_state"], "進行中，21 天未更新")
        self.assertIsNone(rows["CS-8"]["next"])

    def test_kickoff_missing_is_reported(self):
        m = run(base_issues())
        self.assertIn(("kickoff_missing", "CS-1"), [(c["kind"], c["key"]) for c in m["checks"]])


class TokenTest(unittest.TestCase):
    def test_no_token_file_means_dash_not_zero(self):
        m = run(base_issues())
        self.assertIsNone(m["totals"]["tokens"])
        self.assertIsNone(m["epics"][0]["week"]["tokens"])

    def test_tokens_sum_per_row(self):
        m = run(base_issues(), tokens={"CS-3": 100, "CS-4": 50, "CS-99": 7})
        epic = next(e for e in m["epics"] if e["id"] == "CS-1")
        self.assertEqual(epic["week"]["tokens"], 150)
        self.assertEqual(m["totals"]["tokens"], 157)
        self.assertIn(("token_unknown_key", "CS-99"), [(c["kind"], c["key"]) for c in m["checks"]])


class CumulativeTest(unittest.TestCase):
    def test_counts_all_history_until_period_end(self):
        # 本期結束後才完成的卡不算，重算舊的一期時結果才不會變
        issues = base_issues() + [issue("CS-14", level=0, parent="CS-1", status="done", points=4.0,
                                        resolved="2026-09-23T01:00:00.000+0800")]
        c = run(issues)["cumulative"]
        # 本週 4.5h，加上週的 CS-6 3.0 與已結案 epic 底下的 CS-10 1.0；CS-14 不算
        self.assertEqual(c["totals"]["hours"], 8.5)
        self.assertEqual(c["totals"]["completed"], 6)
        self.assertNotIn("CS-14", [r["key"] for r in c["completed"]])
        self.assertEqual(c["since"], "2026-09-10")

    def test_progress_and_status_stop_at_period_end(self):
        # 用現在的 snapshot 補算舊的一期：期間之後才完成的卡與 epic 不能算成已完成
        after = "2026-09-25T10:00:00.000+0800"
        issues = [issue("CS-1", level=1, status="done", resolved=after),
                  issue("CS-2", level=0, parent="CS-1", status="done", points=1.0, resolved=after),
                  issue("CS-3", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK)]
        epic = next(e for e in run(issues)["cumulative"]["epics"] if e["id"] == "CS-1")
        self.assertEqual((epic["completed"], epic["progress"]["done_cards"], epic["progress"]["total_cards"]), (1, 1, 2))
        self.assertEqual((epic["progress"]["done_points"], epic["progress"]["percent"]), (1.0, 50))
        self.assertEqual(epic["status"], "On Track")

    def test_settled_epic_is_listed(self):
        epics = {e["id"]: e for e in run(base_issues())["cumulative"]["epics"]}
        self.assertEqual(list(epics), ["CS-1", "CS-9", UNASSIGNED_EPIC])
        self.assertEqual((epics["CS-9"]["status"], epics["CS-9"]["hours"], epics["CS-9"]["completed"]), ("Done", 1.0, 1))
        self.assertEqual((epics["CS-1"]["hours"], epics["CS-1"]["deliverables"]), (5.5, 2))  # CS-2（子任務歸父卡）與 CS-6

    def test_completed_rows_are_tagged_with_their_week(self):
        weeks = {r["key"]: r["week"] for r in run(base_issues())["cumulative"]["completed"]}
        self.assertEqual((weeks["CS-3"], weeks["CS-6"]), ("2026-W39", "2026-W38"))
        rows = run(base_issues())["cumulative"]["weeks"]
        self.assertEqual([(w["week"], w["completed"], w["hours"], w["deliverables"]) for w in rows],
                         [("2026-W39", 4, 4.5, 2), ("2026-W38", 2, 4.0, 2)])

    def test_cumulative_tokens_are_separate_from_this_week(self):
        m = compute(snapshot(base_issues()), PERIOD, [], {"CS-3": 100}, {}, [], cumulative_tokens={"CS-3": 100, "CS-6": 40}, excluded={})
        self.assertEqual((m["totals"]["tokens"], m["cumulative"]["totals"]["tokens"]), (100, 140))

    def test_unknown_key_only_in_earlier_token_files_is_reported(self):
        m = compute(snapshot(base_issues()), PERIOD, [], {"CS-3": 100}, {}, [], cumulative_tokens={"CS-3": 100, "CS-98": 5}, excluded={})
        self.assertEqual(m["cumulative"]["totals"]["tokens"], 105)
        self.assertIn(("token_unknown_key", "CS-98"), [(c["kind"], c["key"]) for c in m["checks"]])
        self.assertNotIn(("token_unknown_key", "CS-3"), [(c["kind"], c["key"]) for c in m["checks"]])

    def test_week_tokens_without_cumulative_is_a_wiring_error(self):
        with self.assertRaises(ValueError):
            compute(snapshot(base_issues()), PERIOD, [], {"CS-3": 100}, {}, [], cumulative_tokens=None, excluded={})


class PeopleTest(unittest.TestCase):
    def people(self, **kw):
        return {p["name"]: p for p in run(base_issues(), **kw)["people"]}

    def test_order_puts_unassigned_last(self):
        self.assertEqual([p["name"] for p in run(base_issues())["people"]], ["Hubert", "Amy", "Vincent", "未指派"])

    def test_week_and_cumulative_hours_and_deliverables(self):
        p = self.people()
        self.assertEqual((p["Hubert"]["week"]["hours"], p["Hubert"]["week"]["deliverables"], p["Hubert"]["week"]["completed"]), (2.5, 2, 3))
        # CS-6、CS-10 沒有 assignee：本週 0，累計 4.0
        self.assertEqual((p["未指派"]["week"]["hours"], p["未指派"]["cumulative"]["hours"]), (0.0, 4.0))

    def test_hours_split_by_project(self):
        split = [(b["id"], b["hours"]) for b in self.people()["Hubert"]["week"]["by_project"]]
        self.assertEqual(split, [("CS-1", 0.5), (UNASSIGNED_EPIC, 2.0)])

    def test_in_progress_counts_leaves_only(self):
        issues = base_issues() + [
            issue("CS-15", level=0, parent="CS-1", status="doing", assignee="Vincent"),
            issue("CS-16", level=-1, parent="CS-15", status="doing", points=1.5),
        ]
        p = {r["name"]: r for r in run(issues)["people"]}["Vincent"]["in_progress"]
        self.assertEqual((p["count"], p["keys"], p["hours"], p["unestimated"]), (2, ["CS-8", "CS-16"], 1.5, 1))

    def test_person_tokens_include_parent_cards(self):
        p = self.people(tokens={"CS-2": 10, "CS-3": 100})
        self.assertEqual(p["Hubert"]["week"]["tokens"], 110)
        self.assertIsNone(p["Amy"]["week"]["tokens"])


class DeliverablesTest(unittest.TestCase):
    """本週交付在資源 KPI、人、支援單位同一個口徑（2026-09-30 Fable 審 PR #8 finding 1）。"""

    def test_card_under_settled_epic_counts_as_a_deliverable_everywhere(self):
        issues = [
            issue("CS-1", level=1, status="done", resolved=LAST_WEEK),
            issue("CS-2", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK, assignee="Amy"),
        ]
        m = run(issues)
        self.assertEqual(m["totals"]["completed"], 1)
        self.assertEqual(m["totals"]["deliverables"], 1)
        amy = next(p for p in m["people"] if p["name"] == "Amy")
        self.assertEqual(amy["week"]["deliverables"], 1)
        self.assertEqual(sum(u["deliverables"] for u in m["units"]), 1)


class BreakpointTest(unittest.TestCase):
    """斷點：搬進來時就已完成的卡靜默不算；搬進來之後才結案的照算（2026-09-30 使用者裁，AC-16）。"""
    MOVED = "2026-09-18T07:00:00+00:00"  # 台北 09-18 15:00，IN_WEEK 是 09-18 04:00

    def issues(self):
        return [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK, assignee="Amy", labels=("資料組",), moved_in=self.MOVED),
            issue("CS-3", level=0, parent="CS-1", status="done", points=2.0, resolved=LAST_WEEK, assignee="Amy", moved_in=self.MOVED),
            issue("CS-4", level=0, parent="CS-1", status="done", points=3.0, resolved="2026-09-19T10:00:00.000+0800", assignee="Amy", moved_in=self.MOVED),
            issue("CS-5", level=0, parent="CS-1", status="done", points=0.5, resolved=IN_WEEK, assignee="Bob"),
        ]

    def test_done_before_move_counts_nowhere_and_raises_no_check(self):
        m = run(self.issues())
        self.assertEqual([c["key"] for c in m["completed"]], ["CS-4", "CS-5"])  # CS-4 搬入後才結案，照算
        self.assertEqual((m["totals"]["completed"], m["totals"]["deliverables"], m["totals"]["hours"]), (2, 2, 3.5))
        self.assertEqual([c["key"] for c in m["cumulative"]["completed"]], ["CS-4", "CS-5"])
        self.assertEqual(m["cumulative"]["totals"]["hours"], 3.5)
        amy = next(p for p in m["people"] if p["name"] == "Amy")
        self.assertEqual((amy["week"]["completed"], amy["cumulative"]["completed"], amy["cumulative"]["hours"]), (1, 1, 3.0))
        self.assertNotIn("資料組", [u["unit"] for u in m["units"]])  # 只有 CS-2 帶這個 label
        self.assertFalse([c for c in m["checks"] if c["key"] in ("CS-2", "CS-3")])

    def test_progress_still_follows_jira_and_history_is_listed_after_the_breakpoint(self):
        m = run(self.issues())
        epic = next(e for e in m["epics"] if e["key"] == "CS-1")
        self.assertEqual((epic["progress"]["done_cards"], epic["progress"]["total_cards"]), (4, 4))
        self.assertEqual([r["key"] for r in m["migrated"]["week"]], ["CS-2"])
        self.assertEqual([(r["key"], r["week"]) for r in m["migrated"]["cumulative"]], [("CS-2", "2026-W39"), ("CS-3", "2026-W38")])
        self.assertEqual(m["migrated"]["week"][0]["moved_in"], self.MOVED)

    def test_old_snapshot_without_moved_in_is_refused(self):
        old = snapshot(self.issues())
        old["schema"] = 1
        with self.assertRaisesRegex(ValueError, "重跑 prepare"):
            compute(old, PERIOD, [], None, {}, [], cumulative_tokens=None, excluded={})


class ExclusionTest(unittest.TestCase):
    """config/excluded.json 列的卡不算完成：本週、累計的張數、工時、交付、人、支援單位都不算，
    epic 進度照 Jira 狀態算完成。2026-09-27 使用者裁：Squad 8 移轉時補建的 8 張不算當週。"""
    EXCLUDED = {"CS-7": "移轉補登", "CS-6": "移轉補登"}

    def test_excluded_cards_do_not_count_this_week(self):
        m = run(base_issues(), excluded=self.EXCLUDED)
        self.assertEqual(m["totals"]["completed"], 3)       # CS-2、CS-3、CS-4；CS-7 排除
        self.assertEqual(m["totals"]["hours"], 2.5)         # 少了 CS-7 的 2.0
        self.assertEqual(m["totals"]["deliverables"], 1)    # 只剩 CS-2
        self.assertNotIn("CS-7", [c["key"] for c in m["completed"]])
        self.assertNotIn(UNASSIGNED_EPIC, [e["id"] for e in m["epics"]])  # 未歸屬 epic 只有 CS-7，整組不列
        self.assertNotIn(UNIT_MULTIPLE, [u["unit"] for u in m["units"]])  # CS-7 的兩個 label 不再算
        hubert = next(p for p in m["people"] if p["name"] == "Hubert")
        before = next(p for p in run(base_issues())["people"] if p["name"] == "Hubert")
        self.assertEqual(before["week"]["completed_keys"], ["CS-2", "CS-3", "CS-7"])  # CS-3 無指派，歸父卡的 Hubert
        self.assertEqual(hubert["week"]["completed_keys"], ["CS-2", "CS-3"])
        self.assertNotIn("CS-7", [k for o in m["owners"] for k in o["deliveries"]])

    def test_excluded_cards_do_not_count_cumulatively_but_progress_follows_jira(self):
        m = run(base_issues(), excluded=self.EXCLUDED)
        self.assertEqual(sorted(r["key"] for r in m["cumulative"]["completed"]), ["CS-10", "CS-2", "CS-3", "CS-4"])
        self.assertEqual(m["cumulative"]["totals"]["completed"], 4)
        week_epic = next(e for e in m["epics"] if e["id"] == "CS-1")
        cum_epic = next(e for e in m["cumulative"]["epics"] if e["id"] == "CS-1")
        # CS-6 在 Jira 上已完成：進度照算，本週頁與累計頁一致
        self.assertEqual(week_epic["progress"]["done_cards"], 3)
        self.assertEqual(cum_epic["progress"], week_epic["progress"])
        self.assertEqual(cum_epic["completed"], 3)           # 張數不算 CS-6

    def test_exclusion_that_touches_this_week_is_listed_with_its_reason(self):
        m = run(base_issues(), excluded=self.EXCLUDED)
        listed = [(c["key"], c["message"]) for c in m["checks"] if c["kind"] == "excluded"]
        # CS-6 是上週完成的，不影響本週，不列
        self.assertEqual(listed, [("CS-7", "依 config/excluded.json 不算本週與累計完成：移轉補登")])

    def test_epic_or_kickoff_in_the_list_is_an_error_not_a_silent_no_op(self):
        # 完成集合只從一般工作卡算，epic 與 Kick off 列進來不會有任何作用，卻看起來像排除了
        issues = base_issues() + [issue("CS-12", level=0, parent="CS-1", type_="Kick off", status="done", resolved=IN_WEEK)]
        for key in ("CS-1", "CS-12"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    run(issues, excluded={key: "誤列"})

    def test_key_not_in_snapshot_is_allowed(self):
        # 重算舊期時卡還不存在是正常的，不擋也不列
        m = run(base_issues(), excluded={"FDEPROJECT-3": "移轉補登"})
        self.assertEqual(m, run(base_issues()))

    def test_no_exclusions_changes_nothing(self):
        self.assertEqual(run(base_issues(), excluded={}), run(base_issues()))


class ExcludedEpicTest(unittest.TestCase):
    """config/excluded.json 的 epics 列的 epic 連同底下所有卡整棵移出週報：不列專案、不算數字、不做 Kick off 鎖定與資料檢查。
    2026-09-29 使用者裁：行政作業 epic（FDEPROJECT-119）不放入成效統計。"""
    EXCLUDED = {"CS-30": "行政作業"}

    def admin_issues(self):
        return base_issues() + [
            issue("CS-30", level=1, status="doing", summary="行政作業"),
            issue("CS-31", level=0, parent="CS-30", status="done", points=1.0, resolved=IN_WEEK, assignee="Vincent", labels=["資料組"]),
            issue("CS-32", level=-1, parent="CS-31", status="done", points=0.5, resolved=IN_WEEK),
            issue("CS-33", level=0, parent="CS-30", status="done", points=2.0, resolved=LAST_WEEK, assignee="Vincent"),
            issue("CS-34", level=0, parent="CS-30", type_="Kick off", status="done", resolved=IN_WEEK),
            issue("CS-35", level=0, parent="CS-30", status="todo", assignee="Vincent"),
        ]

    def test_the_whole_tree_is_dropped(self):
        kept, _ = drop_excluded_epics(self.admin_issues(), self.EXCLUDED, PERIOD, allow_missing=False)
        self.assertEqual([i["key"] for i in kept], [i["key"] for i in base_issues()])

    def test_dropped_tree_leaves_no_trace_in_the_numbers(self):
        kept, checks = drop_excluded_epics(self.admin_issues(), self.EXCLUDED, PERIOD, allow_missing=False)
        m = compute(snapshot(kept), PERIOD, ["資料組", "產品組"], None, {}, checks, cumulative_tokens=None, excluded={})
        without = run(base_issues())
        for part in ("totals", "epics", "owners", "units", "completed", "in_progress", "cumulative", "people"):
            with self.subTest(part=part):
                self.assertEqual(m[part], without[part])

    def test_completion_this_week_is_listed_once_with_its_reason(self):
        _, checks = drop_excluded_epics(self.admin_issues(), self.EXCLUDED, PERIOD, allow_missing=False)
        # CS-31、CS-32 本週完成；CS-33 上週完成、CS-34 是 Kick off，都不算
        self.assertEqual(checks, [{"kind": "excluded_epic", "key": "CS-30",
                                   "message": "依 config/excluded.json 整個 epic 不列入週報，本週完成的 2 張不算：行政作業"}])

    def test_no_completion_this_week_lists_nothing(self):
        issues = [i for i in self.admin_issues() if i["key"] not in ("CS-31", "CS-32")]
        self.assertEqual(drop_excluded_epics(issues, self.EXCLUDED, PERIOD, allow_missing=False)[1], [])

    def test_listing_a_non_epic_is_an_error_not_a_silent_no_op(self):
        for key in ("CS-2", "CS-3", "CS-34"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, key):
                    drop_excluded_epics(self.admin_issues(), {key: "誤列"}, PERIOD, allow_missing=False)

    def test_key_not_in_snapshot_is_allowed_only_when_not_freshly_fetched(self):
        # 重算與 finalize：epic 可能比那一期的 snapshot 晚建，不擋也不列
        self.assertEqual(drop_excluded_epics(base_issues(), self.EXCLUDED, PERIOD, allow_missing=True), (base_issues(), []))
        # prepare 剛抓完 Jira 還找不到：卡號打錯，或 epic 被刪、搬走。放行的話整個專案會照樣上週報，所以直接失敗
        with self.assertRaisesRegex(ValueError, "CS-30"):
            drop_excluded_epics(base_issues(), self.EXCLUDED, PERIOD, allow_missing=False)

    def test_broken_parent_chain_is_an_error_when_something_is_excluded(self):
        # CS-32 的父卡 CS-31 不在 snapshot：無法判斷 CS-32 是不是 CS-30 底下的，放行就可能把行政作業的卡算回去
        issues = [i for i in self.admin_issues() if i["key"] != "CS-31"]
        for allow_missing in (False, True):
            with self.subTest(allow_missing=allow_missing):
                with self.assertRaisesRegex(ValueError, "CS-32.*CS-31"):
                    drop_excluded_epics(issues, self.EXCLUDED, PERIOD, allow_missing=allow_missing)

    def test_card_under_an_epic_outside_the_snapshot_is_left_to_the_usual_check(self):
        # 一般卡的父系一定是 epic；不在 snapshot 的 epic 不會是被排除的那個，整份週報不必為它停
        # 子任務 → 一般卡 → snapshot 外的 epic：斷的是一般卡的父系，看的是斷在哪一層，不是起點是子任務
        issues = self.admin_issues() + [issue("CS-40", level=0, parent="OTHER-1", status="done", points=1.0, resolved=IN_WEEK),
                                        issue("CS-43", level=-1, parent="CS-40", status="done", points=0.5, resolved=IN_WEEK)]
        kept, _ = drop_excluded_epics(issues, self.EXCLUDED, PERIOD, allow_missing=False)
        self.assertEqual([i["key"] for i in kept][-2:], ["CS-40", "CS-43"])

    def test_card_whose_missing_parent_is_the_excluded_epic_is_dropped(self):
        # 重算舊期時 epic 不在那一期的 snapshot，但卡已經掛在它底下
        issues = base_issues() + [issue("CS-41", level=0, parent="CS-50", status="done", points=1.0, resolved=IN_WEEK),
                                  issue("CS-42", level=-1, parent="CS-41", status="done", points=0.5, resolved=IN_WEEK)]
        kept, checks = drop_excluded_epics(issues, {"CS-50": "行政作業"}, PERIOD, allow_missing=True)
        self.assertEqual([i["key"] for i in kept], [i["key"] for i in base_issues()])
        self.assertEqual([c["key"] for c in checks], ["CS-50"])

    def test_broken_parent_chain_is_left_to_the_usual_check_when_nothing_is_excluded(self):
        issues = [i for i in self.admin_issues() if i["key"] != "CS-31"]
        self.assertEqual(drop_excluded_epics(issues, {}, PERIOD, allow_missing=False), (issues, []))


class KickoffByEpicTest(unittest.TestCase):
    def test_one_kickoff_per_epic(self):
        issues = base_issues() + [
            issue("CS-20", level=0, parent="CS-1", type_="Kick off", status="todo"),
            issue("CS-21", level=0, parent="CS-1", type_="Kick off", status="todo"),
            issue("CS-22", level=0, type_="Kick off", status="todo"),
        ]
        found, checks = kickoff_by_epic(issues)
        self.assertEqual({k: v["key"] for k, v in found.items()}, {"CS-1": "CS-20"})
        self.assertEqual([(c["kind"], c["key"]) for c in checks], [("kickoff_duplicate", "CS-21"), ("kickoff_without_epic", "CS-22")])

    def test_kickoff_cards_are_not_work(self):
        issues = base_issues() + [issue("CS-20", level=0, parent="CS-1", type_="Kick off", status="done", points=8.0, resolved=IN_WEEK)]
        self.assertEqual(run(issues)["totals"]["hours"], 4.5)


if __name__ == "__main__":
    unittest.main()
