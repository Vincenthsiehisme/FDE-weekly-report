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


def metrics_for(issues=ISSUES, fetched_at="2026-09-22T23:59:30+08:00", tokens=None, cumulative_tokens=None, states=None, token_source=None):
    if token_source is None:
        token_source = cumulative_tokens is not None
    return compute(snapshot(issues, fetched_at=fetched_at), PERIOD, tokens, states or {}, [],
                   cumulative_tokens=cumulative_tokens, excluded={}, token_source=token_source)


def page(fetched_at="2026-09-22T23:59:30+08:00", in_reports_dir=False, metrics=None, narrative=NARRATIVE):
    metrics = metrics or metrics_for(fetched_at=fetched_at)
    return render(metrics, narrative, ["2026-W39", "2026-W38"], in_reports_dir=in_reports_dir)


def panel(html, name):
    start = html.index(f'id="{name}" role="tabpanel"')
    # 下一個頁籤、名詞定義、資料檢查或頁尾，哪個先出現就切在哪
    ends = [i for i in (html.find(m, start) for m in ('<section class="panel', '<section id="glossary"', '<section id="checks"', '<footer')) if i != -1]
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
        # 「持續累積」改名「總覽」，id 留 cumulative，已發出去的 #cumulative 連結照樣能開（2026-10-07 使用者裁）
        self.assertIn('aria-controls="cumulative" aria-selected="false">總覽</button>', html)
        self.assertNotIn("持續累積", html)
        # 沒有 JS 時頁籤列藏起來、三個面板都顯示：靠 head 裡加上的 js class 切換
        self.assertIn('document.documentElement.classList.add("js")', html)
        self.assertIn(".js .panel{display:none}", html)

    def test_missing_week_tokens_show_dash_with_reason_once_any_file_exists(self):
        self.assertEqual(fmt_tokens(None), "—")
        self.assertEqual(fmt_tokens(0), "0")
        self.assertEqual(fmt_tokens(1500), "1.5K")
        self.assertEqual(fmt_tokens(2_300_000), "2.3M")
        # 較早期有 Token 檔、這一期沒有：這一期顯示「—」並註明，不隱藏（AC-4 只管一個檔都沒有的情況）
        week = panel(page(metrics=metrics_for(cumulative_tokens={"CS-2": 10})), "week")
        self.assertIn('<li class=big><span>Token</span><strong class="muted">—</strong><small>尚未接資料來源</small></li>', week)

    def test_week_kpis_count_leaf_cards_and_all_week_tokens(self):
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="doing"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
            issue("CS-4", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Bob"),
        ]
        tokens = {"CS-1": 500, "CS-3": 100}
        week = panel(page(metrics=metrics_for(issues, tokens=tokens, cumulative_tokens=tokens)), "week")
        self.assertIn('<li><span>完成卡片</span><strong class="">2</strong></li>', week)   # 父卡 CS-2 不算一張
        self.assertNotIn("交付", week)
        self.assertIn('<li class=big><span>Token</span><strong class="">600</strong></li>', week)  # 含記在 epic 上的 500

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
        # 只看專案所在的本週頁籤：頁尾名詞定義本來就有「未填工時」這個詞
        week = panel(page(), "week")
        self.assertNotIn("未填", week)
        self.assertNotIn('class="kickoff"', week)

    def test_only_filled_fields_are_shown_and_decision_is_never_shown(self):
        week = panel(page(metrics=metrics_for(states=self.state(objective="讓 OP 少打字", decision="要不要買授權"))), "week")
        self.assertIn("<dt>目標</dt><dd>讓 OP 少打字</dd>", week)
        self.assertNotIn("<dt>Before</dt>", week)
        self.assertNotIn("要不要買授權", week)

    def test_modified_kickoff_is_only_in_the_data_checks(self):
        # 鎖定後被修改只列在資料檢查，三個頁籤都不顯示提示（2026-10-07 使用者裁）；頁面仍顯示鎖定版本
        states = self.state(objective="讓 OP 少打字", next_milestone="M2")
        states["CS-1"]["changes"] = [{"detected_at": "2026-09-21T10:00:00+08:00", "sections": ["objective"]}]
        html = page(metrics=metrics_for(states=states))
        for name in ("cumulative", "week", "team"):
            self.assertNotIn("鎖定後被修改", panel(html, name), name)
            self.assertNotIn('class="warn"', panel(html, name).replace('class="warn" id="stale"', ""), name)
        checks = html[html.index('<section id="checks"'):]
        self.assertIn("鎖定後被修改：Objective（偵測於 2026-09-21T10:00:00+08:00）", checks)
        self.assertIn("<dt>目標</dt><dd>讓 OP 少打字</dd>", panel(html, "week"))


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

    def test_team_tab_shows_tokens_only_when_a_file_exists(self):
        # 一個 Token 檔都沒有：團隊卡連 Token 字樣都沒有，說明只留在資源區（AC-4）
        self.assertNotIn("Token", panel(page(metrics=metrics_for(self.ISSUES)), "team"))
        tokens = {"CS-2": 10}
        team = panel(page(metrics=metrics_for(self.ISSUES, tokens=tokens, cumulative_tokens=tokens)), "team")
        self.assertIn("<b>1.5h</b> · Token 10 · 完成卡片 1", team)
        self.assertNotIn("尚未接資料來源", team)

    def test_team_card_lists_completed_and_in_progress(self):
        team = panel(page(metrics=metrics_for(self.ISSUES)), "team")
        self.assertIn("<summary>進行中 1 張</summary>", team[team.index("Carol"):])
        self.assertIn("card person warn-card", team)
        self.assertIn("這些卡在 Jira 上沒有 assignee", team)


    def test_team_tab_opens_with_people_and_hours_per_project(self):
        team = panel(page(metrics=metrics_for(self.ISSUES)), "team")
        self.assertLess(team.index("<h3>專案投入</h3>"), team.index("<h3>人</h3>"))
        # 人卡的本週、累計格底下不再列各專案時數（2026-10-07 使用者裁）
        self.assertNotIn("<p>CS-1 ", team)
        self.assertNotIn("交付", team)
        self.assertLess(team.index("<h3>人</h3>"), team.index("card person"))
        # CS-1：本週 Alice 與未指派的 CS-3；累計多了上週 Dan 的 CS-5。名字照人的排序，累計工時多的在前
        self.assertIn('alert(1)&lt;/script&gt;<small>Dan、Alice</small></div>'
                      '<span data-label="本週人數">1<small>另有未指派</small></span><span data-label="本週實際工時">3.5h</span>'
                      '<span data-label="累計人數">2<small>另有未指派</small></span><span data-label="累計工時">7.5h</span>', team)

    def test_unfilled_hours_are_shown_next_to_project_hours(self):
        issues = self.ISSUES + [issue("CS-6", level=0, parent="CS-1", status="done", resolved=IN_WEEK, assignee="Alice")]
        team = panel(page(metrics=metrics_for(issues)), "team")
        self.assertIn('<span data-label="本週實際工時">3.5h<small>1 張未填工時</small></span>', team)


class TokenHiddenTest(unittest.TestCase):
    """data/tokens/ 一個檔都沒有時 Token 整欄不顯示，只在本週與累計的資源區留一行說明；有任一檔就回到「—」規則
    （主管版二次優化 AC-4，2026-09-27 使用者裁）。"""
    ISSUES = PeopleTest.ISSUES

    def test_no_token_file_hides_every_token_column_and_leaves_one_note_per_resource_block(self):
        html = page(metrics=metrics_for(self.ISSUES))
        week, cumulative, team = panel(html, "week"), panel(html, "cumulative"), panel(html, "team")
        note = '<p class="note">Token 尚未接資料來源</p>'
        self.assertEqual(html.count("Token"), 2)
        self.assertIn(f'</ul>{note}\n  <h3>專案列表</h3>', week)
        self.assertIn(f'</ul>{note}\n  <h3>專案列表</h3>', cumulative)
        self.assertIn('<ul class="kpis" style="--kpis:2">', week)
        self.assertIn('<ul class="kpis" style="--kpis:2">', cumulative)
        self.assertNotIn("Token", team)
        # 團隊的列少一欄：欄數跟著變，窄螢幕才不會多一格空白
        self.assertIn('<div class="rows" style="--cols:3"><div class="row head"><span>執行人</span><span>實際工時</span><span>完成卡片</span><span>進行中</span>', week)
        self.assertIn('<div class="rows" style="--cols:2"><div class="row head"><span>執行人</span><span>累計工時</span><span>完成卡片</span></div>', cumulative)

    def test_a_file_for_an_earlier_week_brings_every_column_back(self):
        # 這一期沒有檔、較早期有：所有 Token 欄回來並顯示「—」，不會因為這一期沒資料就安靜少一欄
        html = page(metrics=metrics_for(self.ISSUES, cumulative_tokens={"CS-2": 10}))
        week, cumulative, team = panel(html, "week"), panel(html, "cumulative"), panel(html, "team")
        self.assertNotIn('<p class="note">Token', html)
        self.assertIn('<ul class="kpis" style="--kpis:3">', week)
        self.assertIn('<li class=big><span>累計 Token</span><strong class="">10</strong></li>', cumulative)
        self.assertIn("<span>Token <b>—</b></span>", week)                      # 專案卡
        self.assertIn("<span>執行人</span><span>實際工時</span><span>Token</span>", week)
        self.assertIn('<span class="nums">1.5h · Token —</span>', week)         # 完成清單
        self.assertIn('<span class="num">Token 10</span>', cumulative)          # 總覽專案列
        self.assertIn("<b>1.5h</b> · Token — · 完成卡片 1", team)               # 本週
        self.assertIn("<b>1.5h</b> · Token 10 · 完成卡片 1", team)              # 累計

    def test_a_file_for_a_later_week_only_still_shows_the_columns(self):
        # 來源接上後回補更早的週：資料夾有檔但這一期的累計是 None，欄要顯示「—」，不能從累計推成隱藏（Codex 2026-10-06 審出）
        html = page(metrics=metrics_for(self.ISSUES, token_source=True))
        week = panel(html, "week")
        self.assertNotIn('<p class="note">Token', html)
        self.assertIn('<ul class="kpis" style="--kpis:3">', week)
        self.assertIn("<span>執行人</span><span>實際工時</span><span>Token</span>", week)
        self.assertIn("<b>1.5h</b> · Token — · 完成卡片 1", panel(html, "team"))


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
    def test_progress_shows_cards_and_only_actual_hours_of_done_cards(self):
        # 工時是完工後回填的實際時數：未完成卡上的 0.5 不算進去，沒填的未完成卡也不數
        issues = ISSUES + [issue("CS-3", level=0, parent="CS-1", status="todo", points=0.5),
                           issue("CS-4", level=0, parent="CS-1", status="todo")]
        self.assertIn("<span>1/3 張完成 · 累計工時 1.5h</span>", panel(page(metrics=metrics_for(issues)), "week"))


class BreakpointRenderTest(unittest.TestCase):
    """「改組前」分隔線之後灰字列出搬入前已完成的卡，數字不算（AC-16）。"""
    MOVED = "2026-09-18T07:00:00+00:00"

    def html(self):
        issues = ISSUES + [
            issue("CS-7", level=0, parent="CS-1", status="done", points=4.0, resolved=IN_WEEK, assignee="Jin", summary="搬來的卡", moved_in=self.MOVED),
            issue("CS-8", level=0, parent="CS-1", status="done", points=2.0, resolved=LAST_WEEK, assignee="Jin", summary="更早搬來的卡", moved_in=self.MOVED),
        ]
        return page(metrics=metrics_for(issues))

    def test_week_and_cumulative_list_history_after_the_breakpoint(self):
        html = self.html()
        week, cum = panel(html, "week"), panel(html, "cumulative")
        for tab, keys in ((week, ["CS-7"]), (cum, ["CS-7", "CS-8"])):
            after = tab[tab.index('<div class="breakpoint">'):]
            self.assertIn(f"改組前已完成 {len(keys)} 張", after)
            self.assertIn("<span>改組前</span>", after)
            for key in keys:
                self.assertIn(f">{key}</a>", after)
                self.assertNotIn(f">{key}</a>", tab[:tab.index('<div class="breakpoint">')])
        self.assertIn("2026-W38 · 結案 09/10 · 搬入 09/18", cum)
        self.assertIn("<strong class=\"\">1.5h</strong>", week)  # 實際工時只有 CS-2 的 1.5h，搬來的 4h 不算
        self.assertNotIn("CS-7", html[html.index('<section id="checks"'):] if '<section id="checks"' in html else "")

    def test_dates_are_shown_in_taipei_time(self):
        # 09-18 17:00Z 是台北 09-19 01:00：搬入日要寫 09/19
        issues = ISSUES + [issue("CS-9", level=0, parent="CS-1", status="done", resolved=IN_WEEK, moved_in="2026-09-18T17:00:00+00:00")]
        self.assertIn("結案 09/18 · 搬入 09/19", panel(page(metrics=metrics_for(issues)), "week"))

    def test_no_breakpoint_without_migrated_cards(self):
        self.assertNotIn('class="breakpoint"', page())

    def test_glossary_explains_breakpoint(self):
        html = self.html()
        self.assertIn("<dt>改組前</dt>", html)
        self.assertNotIn("斷點", html)  # 頁面一律叫「改組前」（2026-09-30 使用者定名）


class WordingTest(unittest.TestCase):
    """2026-09-30 使用者確認 Story point estimate 是完工後回填的實際時數；每個詞的口徑寫在頁尾名詞定義。"""

    def test_hours_are_called_actual_and_never_estimated(self):
        issues = ISSUES + [issue("CS-3", level=0, parent="CS-1", status="done", resolved=IN_WEEK)]
        html = page(metrics=metrics_for(issues))
        self.assertNotIn("預估", html)
        self.assertNotIn("估點", html)
        self.assertIn("<span>實際工時</span>", html)
        self.assertIn("1 張完成卡未填工時", panel(html, "week"))
        # 只寫「實際工時」，不單獨寫「實際」（2026-09-30 使用者裁）
        self.assertEqual(html.count("實際"), html.count("實際工時"))

    def test_glossary_spells_out_the_period_with_weekdays(self):
        html = page()
        self.assertIn("<h1>2026/09/16（三） – 2026/09/22（二）</h1>", html)
        glossary = html[html.index('<section id="glossary"'):html.index("</section>", html.index('<section id="glossary"'))]
        self.assertIn("2026/09/16（三） 00:00 到 2026/09/22（二） 23:59（UTC+8）", glossary)
        for term in ("本週", "累計", "完成卡片", "實際工時", "累計工時", "未填工時", "進度", "label", "Done／On Track", "資料檢查"):
            self.assertIn(f"<dt>{term}</dt>", glossary)
        self.assertNotIn("交付", html)

    def test_checks_are_called_data_checks(self):
        issues = ISSUES + [issue("CS-3", level=0, parent="CS-1", status="done", resolved=IN_WEEK)]
        html = page(metrics=metrics_for(issues))
        self.assertIn("<summary>資料檢查（", html)
        self.assertNotIn("資料待補", html)


class DueDateHiddenTest(unittest.TestCase):
    """目標日不顯示（2026-10-06 使用者裁：不是當前重點）。metrics 仍帶 due，之後要回來只改 render。"""

    def test_due_date_never_appears_even_when_set(self):
        issues = [issue("CS-1", level=1, status="doing", due="2026-10-31")] + ISSUES[1:]
        html = page(metrics=metrics_for(issues))
        self.assertNotIn("目標日", html)
        self.assertNotIn("2026/10/31", html)
        self.assertNotIn("未設定", html)
        self.assertIn('<p class="meta">負責人 未指派</p>', panel(html, "week"))


class CompletedSectionTest(unittest.TestCase):
    def test_parent_completed_alone_is_not_a_completed_card(self):
        # 父卡本週完成、子卡上週就完成：父卡的張數計在子卡（上週），本週不算、完成區也不列（2026-10-07 使用者裁）
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="done", resolved=IN_WEEK, summary="本週才結的父卡"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=LAST_WEEK),
        ]
        m = metrics_for(issues)
        self.assertEqual(m["totals"]["completed"], 0)
        week = panel(page(metrics=m, narrative={"highlights": [], "epic_results": {}}), "week")
        self.assertNotIn("本週才結的父卡", week[week.index("<h3>完成</h3>"):])
        self.assertIn("本週沒有完成的卡。", week[week.index("<h3>完成</h3>"):])

    def test_subtasks_are_listed_under_their_parent(self):
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="doing", assignee="Alice", summary="父卡"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, summary="子卡甲"),
            issue("CS-4", level=-1, parent="CS-2", status="done", points=0.5, resolved=IN_WEEK, summary="子卡乙"),
        ]
        done = panel(page(metrics=metrics_for(issues)), "week")
        done = done[done.index("<h3>完成</h3>"):]
        self.assertIn("父卡 <span class=\"muted\">· Alice · 進行中（子任務 2/2 完成） · 2 張子卡</span>", done)
        self.assertLess(done.index("父卡"), done.index("子卡甲"))

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
        projects = week[week.index("<h3>專案列表</h3>"):week.index("<h3>團隊</h3>")]
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
        projects = week[week.index("<h3>專案列表</h3>"):week.index("<h3>團隊</h3>")]
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



class OverviewTest(unittest.TestCase):
    """總覽：專案依 label 分段、進度條、展開看 Kick off 目標五段，label 表與本週頁共用（2026-10-07 使用者裁）。"""
    ISSUES = [
        issue("CS-1", level=1, status="doing", labels=("門市",), summary="門市客服"),
        issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK, assignee="Alice", labels=("門市",)),
        issue("CS-3", level=0, parent="CS-1", status="todo", labels=("門市",)),
        issue("CS-4", level=1, status="done", resolved=LAST_WEEK, labels=("直售",), summary="已結案的直售專案"),
        issue("CS-5", level=0, parent="CS-4", status="done", points=3.0, resolved=LAST_WEEK, assignee="Bob", labels=("直售",)),
        issue("CS-6", level=1, status="todo", summary="沒有 label 的專案"),
        issue("CS-7", level=0, status="done", points=0.5, resolved=IN_WEEK, assignee="Bob", labels=("直售",), summary="沒有 epic 的卡"),
    ]
    STATES = {"CS-1": {"kickoff_key": "CS-9", "locked_at": "2026-09-20T10:00:00+08:00", "changes": [],
                       "sections": {"objective": "少打字", "why": None, "before": "手打", "after": "自動", "evidence": None,
                                    "decision": "買不買", "next_milestone": "M2"}}}

    def overview(self):
        return panel(page(metrics=metrics_for(self.ISSUES, states=self.STATES),
                          narrative={"highlights": ["完成"], "epic_results": {"CS-1": "完成", "_unassigned": "完成"}}), "cumulative")

    def test_projects_are_grouped_by_label_with_unassigned_last(self):
        o = self.overview()
        projects = o[o.index("<h3>專案列表</h3>"):o.index("<h3>依 label</h3>")]
        heads = ["<h4>直售 <span class=\"muted\">· 1 個專案 · 累計工時 3.5h</span></h4>",
                 "<h4>門市 <span class=\"muted\">· 1 個專案 · 累計工時 1.5h</span></h4>",
                 "<h4>未分類 <span class=\"muted\">· 1 個專案 · 累計工時 0h</span></h4>",
                 "<h4>未歸屬 epic"]
        for h in heads:
            self.assertIn(h, projects)
        positions = [projects.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(projects.index("已結案的直售專案"), projects.index("門市客服"))

    def test_row_has_a_bar_and_expands_to_the_five_goal_sections_collapsed_by_default(self):
        o = self.overview()
        self.assertNotIn("<details class=\"proj\" open", o)
        self.assertIn('<details class="proj"><summary class="proj-head">', o)
        row = o[o.rindex('<details class="proj">', 0, o.index("門市客服")):]
        row = row[:row.index("</details>")]
        self.assertIn('<div class="bar"><span style="width:50%"></span></div><span>1/2</span>', row)
        self.assertIn('<span class="num">1.5h</span>', row)
        self.assertIn("<dt>目標</dt><dd>少打字</dd>", row)
        self.assertIn("<dt>Before</dt><dd>手打</dd>", row)
        self.assertNotIn("M2", row)          # 下個里程碑不在效益說明
        self.assertNotIn("買不買", row)
        self.assertNotIn("<details class=\"proj\" open", o)
        self.assertIn("Kick off 未填", o)    # 沒有 Kick off 的專案

    def test_label_table_is_shared_and_replaces_support_units(self):
        html = page(metrics=metrics_for(self.ISSUES), narrative={"highlights": ["完成"], "epic_results": {"CS-1": "完成", "_unassigned": "完成"}})
        week, o = panel(html, "week"), panel(html, "cumulative")
        head = '<div class="row head"><span>label</span><span>本週實際工時</span><span>累計工時</span><span>epic 數</span></div>'
        row = '<div class="row "><div class="name">直售</div><span data-label="本週實際工時">0.5h</span><span data-label="累計工時">3.5h</span><span data-label="epic 數">1</span></div>'
        for tab in (week, o):
            self.assertIn(head, tab)
            self.assertIn(row, tab)
        self.assertNotIn("支援單位", html)
        self.assertIn("<h3>團隊</h3>", week)
        self.assertIn("<h3>團隊</h3>", o)
        self.assertIn('<li class=big><span>累計工時</span><strong class="">5h</strong></li>', o)
        self.assertIn('<li><span>累計完成卡片</span><strong class="">3</strong></li>', o)


if __name__ == "__main__":
    unittest.main()
