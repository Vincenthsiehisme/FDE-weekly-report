import unittest
from datetime import date

from helpers import IN_WEEK, LAST_WEEK, issue, snapshot
from lib.metrics import compute
from lib.period import period_ending
from lib.render import fmt_tokens, render

PERIOD = period_ending(date(2026, 9, 22))
ISSUES = [
    issue("CS-1", level=1, status="doing", summary="<script>alert(1)</script>"),
    issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK, assignee="Alice"),
]
NARRATIVE = {"highlights": ["完成 CS-2"], "epic_results": {"CS-1": "完成"}}


def metrics_for(issues=ISSUES, fetched_at="2026-09-22T23:59:30+08:00", tokens=None, cumulative_tokens=None, states=None):
    return compute(snapshot(issues, fetched_at=fetched_at), PERIOD, [], tokens, states or {}, [], cumulative_tokens=cumulative_tokens)


def page(fetched_at="2026-09-22T23:59:30+08:00", in_reports_dir=False, metrics=None, narrative=NARRATIVE):
    metrics = metrics or metrics_for(fetched_at=fetched_at)
    return render(metrics, narrative, ["2026-W39", "2026-W38"], in_reports_dir=in_reports_dir)


def panel(html, name):
    start = html.index(f'id="{name}" role="tabpanel"')
    # 下一個頁籤、資料待補或頁尾，哪個先出現就切在哪
    ends = [i for i in (html.find(m, start) for m in ('<section class="panel', '<section id="checks"', '<footer')) if i != -1]
    return html[start:min(ends)]


class RenderTest(unittest.TestCase):
    def test_text_is_escaped(self):
        html = page()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_removed_sections_stay_removed(self):
        html = page()
        for word in ("Attention", "Blocked", "Decision Needed", "Risk"):
            self.assertNotIn(word, html)

    def test_three_tabs_with_week_open_by_default(self):
        html = page()
        self.assertLess(html.index('aria-controls="cumulative"'), html.index('aria-controls="week"'))
        self.assertLess(html.index('aria-controls="week"'), html.index('aria-controls="team"'))
        self.assertIn('<section class="panel active" id="week"', html)
        self.assertIn('<section class="panel" id="cumulative"', html)
        self.assertIn('<section class="panel" id="team"', html)
        # 沒有 JS 時頁籤列藏起來、三個面板都顯示：靠 head 裡加上的 js class 切換
        self.assertIn('document.documentElement.classList.add("js")', html)
        self.assertIn(".js .panel{display:none}", html)

    def test_missing_tokens_show_dash_with_reason(self):
        self.assertEqual(fmt_tokens(None), "—")
        self.assertEqual(fmt_tokens(0), "0")
        self.assertEqual(fmt_tokens(1500), "1.5K")
        self.assertEqual(fmt_tokens(2_300_000), "2.3M")
        week = panel(page(), "week")
        self.assertIn('<li><span>Token</span><strong class="muted">—</strong><small>尚未接資料來源</small></li>', week)

    def test_week_kpis_use_unique_deliverables_and_all_week_tokens(self):
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="doing"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
            issue("CS-4", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Bob"),
        ]
        tokens = {"CS-1": 500, "CS-3": 100}
        week = panel(page(metrics=metrics_for(issues, tokens=tokens, cumulative_tokens=tokens)), "week")
        self.assertIn('<li><span>交付</span><strong class="">1</strong></li>', week)   # 同一張父卡只算一項
        self.assertIn('<li><span>Token</span><strong class="">600</strong></li>', week)  # 含記在 epic 上的 500

    def test_scheduled_run_is_not_provisional_but_early_run_is(self):
        self.assertNotIn("數字是產生當下的暫定值", page("2026-09-22T23:59:30+08:00"))
        self.assertIn("數字是產生當下的暫定值", page("2026-09-22T10:00:00+08:00"))

    def test_stale_check_week_nav_and_archive_links(self):
        html = page()
        self.assertIn('data-generated="2026-09-22T23:59:30+08:00"', html)
        self.assertIn('<a href="reports/2026-W38.html">‹ 2026-W38</a>', html)
        self.assertIn('<a href="reports/2026-W38.html">2026-W38</a>', html)
        in_reports = page(in_reports_dir=True)
        self.assertIn('<a href="2026-W38.html">2026-W38</a>', in_reports)
        self.assertIn('<a href="../index.html">最新一期</a>', in_reports)

    def test_old_schema_is_refused(self):
        metrics = metrics_for()
        metrics["schema"] = 1
        with self.assertRaises(ValueError):
            render(metrics, NARRATIVE, ["2026-W39"], in_reports_dir=False)


class KickoffTest(unittest.TestCase):
    def state(self, **sections):
        base = {key: None for key in ("objective", "why", "before", "after", "evidence", "decision", "next_milestone")}
        base.update(sections)
        return {"CS-1": {"kickoff_key": "CS-9", "locked_at": "2026-09-20T10:00:00+08:00", "sections": base, "changes": []}}

    def test_missing_kickoff_shows_nothing_instead_of_unfilled_rows(self):
        html = page()
        self.assertNotIn("未填", html)
        self.assertNotIn('class="kickoff"', html)

    def test_only_filled_fields_are_shown_and_decision_is_never_shown(self):
        week = panel(page(metrics=metrics_for(states=self.state(objective="讓 OP 少打字", decision="要不要買授權"))), "week")
        self.assertIn("<dt>目標</dt><dd>讓 OP 少打字</dd>", week)
        self.assertNotIn("<dt>Before</dt>", week)
        self.assertNotIn("要不要買授權", week)

    def test_modified_notice_does_not_pin_all_goals_to_one_lock_time(self):
        # 空的段落之後補上也算鎖定，各段鎖定時間不同；通知不能把五段都歸給第一次讀到的時間
        states = self.state(objective="讓 OP 少打字", next_milestone="M2")
        states["CS-1"]["changes"] = [{"detected_at": "2026-09-21T10:00:00+08:00", "sections": ["objective"]}]
        week = panel(page(metrics=metrics_for(states=states)), "week")
        self.assertIn("目標到 Evidence 五段顯示鎖定版本，下個里程碑取這一期卡片上的內容", week)
        self.assertNotIn("2026/09/20 10:00", week)


class PeopleTest(unittest.TestCase):
    ISSUES = ISSUES + [
        issue("CS-3", level=0, parent="CS-1", status="done", points=2.0, resolved=IN_WEEK),
        issue("CS-4", level=0, parent="CS-1", status="doing", assignee="Carol"),
        issue("CS-5", level=0, parent="CS-1", status="done", points=4.0, resolved=LAST_WEEK, assignee="Dan"),
    ]

    def test_week_people_include_in_progress_and_unassigned_is_flagged(self):
        week = panel(page(metrics=metrics_for(self.ISSUES)), "week")
        # 專案卡的進行中清單也會出現人名，所以比對人的列本身
        self.assertIn('<div class="name">Carol</div>', week)   # 本週沒有完成、手上有進行中
        self.assertNotIn("Dan", week)                          # 只有上週完成
        self.assertIn('<div class="row warn-row"><div class="name">未指派 <small>卡片沒有執行人</small></div>', week)

    def test_cumulative_people_include_earlier_weeks(self):
        cumulative = panel(page(metrics=metrics_for(self.ISSUES)), "cumulative")
        self.assertIn('<div class="name">Dan</div>', cumulative)
        self.assertNotIn("Carol", cumulative)  # 沒有完成過任何卡

    def test_team_tab_explains_missing_tokens(self):
        self.assertIn("尚未接資料來源", panel(page(metrics=metrics_for(self.ISSUES)), "team"))
        tokens = {"CS-2": 10}
        self.assertNotIn("尚未接資料來源", panel(page(metrics=metrics_for(self.ISSUES, tokens=tokens, cumulative_tokens=tokens)), "team"))

    def test_team_card_lists_completed_and_in_progress(self):
        team = panel(page(metrics=metrics_for(self.ISSUES)), "team")
        self.assertIn("<summary>進行中 1 張</summary>", team[team.index("Carol"):])
        self.assertIn("card person warn-card", team)
        self.assertIn("這些卡在 Jira 上沒有 assignee", team)


class InProgressTest(unittest.TestCase):
    def test_parent_in_progress_without_in_progress_children_stays_visible(self):
        issues = ISSUES + [
            issue("CS-3", level=0, parent="CS-1", status="doing", assignee="Alice", summary="父卡進行中"),
            issue("CS-4", level=-1, parent="CS-3", status="todo"),
        ]
        week = panel(page(metrics=metrics_for(issues)), "week")
        self.assertIn("父卡進行中", week)
        self.assertIn("<summary>進行中 父卡 1 張</summary>", week)

    def test_summary_counts_leaves_and_parents_separately(self):
        issues = ISSUES + [
            issue("CS-3", level=0, parent="CS-1", status="doing", assignee="Alice"),
            issue("CS-4", level=-1, parent="CS-3", status="doing"),
            issue("CS-5", level=0, parent="CS-1", status="doing", assignee="Bob"),
        ]
        self.assertIn("<summary>進行中 2 張，父卡 1 張</summary>", panel(page(metrics=metrics_for(issues)), "week"))


class ProgressTest(unittest.TestCase):
    def test_progress_shows_cards_and_estimated_hours(self):
        issues = ISSUES + [issue("CS-3", level=0, parent="CS-1", status="todo", points=0.5),
                           issue("CS-4", level=0, parent="CS-1", status="todo")]
        self.assertIn("<span>1/3 張完成 · 1.5/2h 預估，1 張未估點</span>", panel(page(metrics=metrics_for(issues)), "week"))


class CompletedSectionTest(unittest.TestCase):
    def test_parent_completed_without_leaves_this_week_is_listed(self):
        # 父卡本週完成、子卡上週就完成：完成卡片 KPI 有算它，完成區也要列出來
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="done", resolved=IN_WEEK, summary="本週才結的父卡"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=LAST_WEEK),
        ]
        m = metrics_for(issues)
        self.assertEqual(m["totals"]["completed"], 1)
        week = panel(page(metrics=m), "week")
        self.assertIn("本週才結的父卡", week[week.index("<h3>完成</h3>"):])
        self.assertNotIn("本週沒有完成的卡。", week[week.index("<h3>完成</h3>"):])

    def test_completion_under_settled_epic_is_listed(self):
        issues = ISSUES + [
            issue("CS-7", level=1, status="done", resolved=LAST_WEEK, summary="已結案專案"),
            issue("CS-8", level=0, parent="CS-7", status="done", points=1.0, resolved=IN_WEEK, summary="結案後才完成的卡"),
        ]
        week = panel(page(metrics=metrics_for(issues)), "week")
        done = week[week.index("<h3>完成</h3>"):]
        self.assertIn("結案後才完成的卡", done)
        self.assertIn("已結案專案", done)

    def test_unassigned_in_progress_card_is_shown_without_completions(self):
        issues = ISSUES + [issue("CS-9", level=0, status="doing", assignee="Eve", summary="沒有 epic 的進行中")]
        week = panel(page(metrics=metrics_for(issues)), "week")
        projects = week[week.index("<h3>專案列表</h3>"):week.index("<h3>人</h3>")]
        self.assertIn("沒有 epic 的進行中", projects)
        self.assertIn("未歸屬 epic", projects)


class NothingDroppedTest(unittest.TestCase):
    """metrics 裡的每張完成卡與進行中卡都要出現在頁面上。Codex 兩輪各抓到一種漏列，這條把整類關掉：
    把已知會讓卡片沒有專案列可掛的狀態全部放進同一份資料，逐張比對。"""
    ISSUES = [
        issue("CS-1", level=1, status="doing"),
        issue("CS-2", level=0, parent="CS-1", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
        # 父卡本週才結，子卡上週就完成
        issue("CS-3", level=0, parent="CS-1", status="done", resolved=IN_WEEK),
        issue("CS-4", level=-1, parent="CS-3", status="done", points=1.0, resolved=LAST_WEEK),
        # 父卡進行中，子卡都還沒開始
        issue("CS-5", level=0, parent="CS-1", status="doing", assignee="Bob"),
        issue("CS-6", level=-1, parent="CS-5", status="todo"),
        # epic 上週就結案，底下的卡本週才完成、另一張還在進行
        issue("CS-7", level=1, status="done", resolved=LAST_WEEK),
        issue("CS-8", level=0, parent="CS-7", status="done", points=2.0, resolved=IN_WEEK, assignee="Carol"),
        issue("CS-9", level=0, parent="CS-7", status="doing", assignee="Carol"),
        # 沒有 epic、本週也沒有任何沒有 epic 的卡完成
        issue("CS-10", level=0, status="doing", assignee="Dan"),
    ]

    def test_every_completed_and_in_progress_card_is_rendered(self):
        m = metrics_for(self.ISSUES)
        html = page(metrics=m, narrative={"highlights": ["完成"], "epic_results": {e["id"]: "完成" for e in m["epics"] if e["active"]}})
        week = panel(html, "week")
        done_section = week[week.index("<h3>完成</h3>"):]
        projects = week[week.index("<h3>專案列表</h3>"):week.index("<h3>人</h3>")]
        cumulative = panel(html, "cumulative")
        team = panel(html, "team")
        for row in m["completed"]:
            self.assertIn(f'browse/{row["key"]}"', done_section, row["key"])
            self.assertIn(f'browse/{row["key"]}"', team, row["key"])
        for row in m["cumulative"]["completed"]:
            self.assertIn(f'browse/{row["key"]}"', cumulative, row["key"])
        for row in m["in_progress"]:
            self.assertIn(f'browse/{row["key"]}"', projects, row["key"])
        for row in m["in_progress"]:
            if row["leaf"]:
                self.assertIn(f'browse/{row["key"]}"', team, row["key"])


class CumulativeTest(unittest.TestCase):
    def test_settled_epic_only_in_cumulative(self):
        issues = ISSUES + [
            issue("CS-7", level=1, status="done", resolved=LAST_WEEK, summary="已結案專案"),
            issue("CS-8", level=0, parent="CS-7", status="done", points=1.0, resolved=LAST_WEEK),
        ]
        html = page(metrics=metrics_for(issues))
        self.assertNotIn("已結案專案", panel(html, "week"))
        self.assertIn("已結案專案", panel(html, "cumulative"))
        self.assertIn("<summary>2026-W38", panel(html, "cumulative"))


if __name__ == "__main__":
    unittest.main()
