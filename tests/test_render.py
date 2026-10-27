import re
import unittest
from datetime import date

from helpers import IN_WEEK, LAST_WEEK, issue, snapshot
from lib.metrics import compute
from lib.period import period_ending
from lib.render import esc, fmt_tokens, render

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
                   cumulative_tokens=cumulative_tokens, excluded={}, token_source=token_source, previous=None)


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
        # 較早期有 Token 檔、這一期沒有：這一期的欄顯示「—」，不隱藏（AC-4 只管一個檔都沒有的情況）
        week = panel(page(metrics=metrics_for(cumulative_tokens={"CS-2": 10})), "week")
        self.assertIn('<span class="num tok" data-label="Token">Token —</span>', week)

    def test_week_kpis_count_leaf_cards_and_all_week_tokens(self):
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="doing"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
            issue("CS-4", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Bob"),
        ]
        tokens = {"CS-1": 500, "CS-3": 100}
        # 資源區只在總覽（2026-10-28 本週頁換成四個數字）
        overview = panel(page(metrics=metrics_for(issues, tokens=tokens, cumulative_tokens=tokens)), "cumulative")
        self.assertIn('<li><span>累計完成卡片</span><strong class="">2</strong></li>', overview)   # 父卡 CS-2 不算一張
        self.assertNotIn("交付", overview)
        self.assertIn('<li class=big><span>累計 Token</span><strong class="">600</strong></li>', overview)  # 含記在 epic 上的 500

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

    def test_only_filled_fields_are_shown_and_decision_is_only_in_the_step_in_list(self):
        html = page(metrics=metrics_for(states=self.state(objective="讓 OP 少打字", decision="要不要買授權")))
        overview, week = panel(html, "cumulative"), panel(html, "week")
        self.assertIn("<dt>目標</dt><dd>讓 OP 少打字</dd>", overview)
        self.assertNotIn("<dt>Before</dt>", overview)
        # 目標五段只在總覽展開看，本週頁不再印（2026-10-07 使用者裁，視覺化改版 AC-1）
        self.assertNotIn("<dt>目標</dt>", week)
        self.assertNotIn('class="kickoff"', week)
        self.assertNotIn("要不要買授權", overview)
        self.assertIn("要不要買授權", week[week.index('id="step-in"'):week.index("<h3>人</h3>")])
        self.assertNotIn("要不要買授權", week[week.index('class="proj-list"'):])

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
        self.assertIn("<dt>目標</dt><dd>讓 OP 少打字</dd>", panel(html, "cumulative"))


class PeopleTest(unittest.TestCase):
    ISSUES = ISSUES + [
        issue("CS-3", level=0, parent="CS-1", status="done", points=2.0, resolved=IN_WEEK),
        issue("CS-4", level=0, parent="CS-1", status="doing", assignee="Carol"),
        issue("CS-5", level=0, parent="CS-1", status="done", points=4.0, resolved=LAST_WEEK, assignee="Dan"),
    ]

    def test_week_person_cards_are_in_name_order_with_in_progress_and_recent_work(self):
        week = panel(page(metrics=metrics_for(self.ISSUES)), "week")
        cards = week[week.index("<h3>人</h3>"):week.index('class="proj-list"')]
        names = re.findall(r'<article class="pcard"><h4>(.*?)</h4>', cards)
        # Carol 本週沒有完成、手上有進行中；Dan 上週完成，在近 4 週裡；未指派最後；照名字排，不照張數或工時
        self.assertEqual(names, ["Alice", "Carol", "Dan", "未指派"])
        self.assertIn("近 2 週完成張數（只跟自己比，深色＝本週）· 本週 0 張", cards[cards.index("<h4>Dan</h4>"):])

    def test_overview_has_no_people_table_and_team_tab_has_everyone_with_work(self):
        html = page(metrics=metrics_for(self.ISSUES))
        cumulative, team = panel(html, "cumulative"), panel(html, "team")
        self.assertNotIn("<h3>團隊</h3>", cumulative)
        self.assertNotIn("執行人", cumulative)
        for name in ("Alice", "Carol", "Dan"):
            self.assertIn(f"<h3>{name}</h3>", team)

    def test_team_tab_shows_tokens_only_when_a_file_exists(self):
        # 一個 Token 檔都沒有：團隊卡連 Token 字樣都沒有，說明只留在資源區（AC-4）
        self.assertNotIn("Token", panel(page(metrics=metrics_for(self.ISSUES)), "team"))
        tokens = {"CS-2": 10}
        team = panel(page(metrics=metrics_for(self.ISSUES, tokens=tokens, cumulative_tokens=tokens)), "team")
        self.assertIn("累計 1 張 · 累計工時 1.5h · Token 10", team)
        self.assertNotIn("尚未接資料來源", team)

    def test_team_card_lists_in_progress_but_not_the_completed_cards(self):
        team = panel(page(metrics=metrics_for(self.ISSUES)), "team")
        self.assertIn("<summary>進行中 1 張</summary>", team[team.index("Carol"):])
        self.assertIn("card person warn-card", team)
        self.assertIn("這些卡在 Jira 上沒有 assignee", team)
        # 完成的逐張清單不列在人卡；累計張數與工時是他自己的（2026-10-28 團隊頁改累計版人卡）
        self.assertNotIn("本週完成", team)
        self.assertNotIn('browse/CS-2"', team)
        self.assertIn("累計 1 張 · 累計工時 1.5h", team)
        self.assertIn("累計投入比例：未分類 100%", team)

    def test_team_card_lists_open_projects_one_per_line_and_folds_closed_ones(self):
        # 2026-10-28 第二輪：「負責過」一整段太密，拆成負責中逐列、已結案收合
        issues = self.ISSUES + [issue("CS-30", level=1, status="done", resolved=IN_WEEK, assignee="Alice", summary="收掉的專案"),
                                issue("CS-31", level=0, parent="CS-30", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
                                issue("CS-40", level=1, status="doing", assignee="Alice", summary="還在做的專案")]
        narrative = {"highlights": ["完成 CS-2"], "epic_results": {"CS-1": "完成", "CS-30": "完成"}}
        team = panel(page(metrics=metrics_for(issues), narrative=narrative), "team")
        alice = team[team.index("<h3>Alice</h3>"):]
        alice = alice[:alice.index("</article>")]
        self.assertNotIn("負責過", team)
        self.assertIn('<p class="sub">負責中 1 個</p>', alice)
        self.assertIn('<details><summary>已結案 1 個</summary><ul class="owned"><li><span class="key"><a href="https://example.atlassian.net/browse/CS-30">CS-30</a></span> 收掉的專案</li></ul></details>', alice)
        self.assertIn('<ul class="owned"><li><span class="key"><a href="https://example.atlassian.net/browse/CS-40">CS-40</a></span> 還在做的專案</li></ul>', alice)
        self.assertLess(alice.index("負責中"), alice.index("已結案"))
        self.assertIn('<div class="tcards">', team)

    def test_people_column_on_the_overview_replaces_the_staffing_table(self):
        html = page(metrics=metrics_for(self.ISSUES))
        team, overview = panel(html, "team"), panel(html, "cumulative")
        self.assertNotIn("專案投入", team)
        self.assertNotIn("交付", team)
        self.assertNotIn("<p>CS-1 ", team)
        self.assertIn("<h3>人</h3>", team)
        # CS-1 累計：Alice 與上週的 Dan，另有未指派的 CS-3；名字照在這個專案的工時排，多的在前
        # CS-1 沒有 Kick off：不可展開的列（總覽 UI 盤點 U6）
        row = overview[overview.rindex('<div class="proj">', 0, overview.index('alert(1)&lt;/script&gt; <span class="pill')):]
        row = row[:row.index("</div></div>")]
        self.assertIn('<span class="people" data-label="人">2 <small>Dan、Alice</small><small>另有未指派</small></span>', row)
        self.assertIn('<span class="people">人</span>', overview)

    def test_unfilled_hours_are_shown_next_to_the_overview_project_hours(self):
        issues = self.ISSUES + [issue("CS-6", level=0, parent="CS-1", status="done", resolved=IN_WEEK, assignee="Alice")]
        overview = panel(page(metrics=metrics_for(issues)), "cumulative")
        self.assertIn('<span class="num" data-label="累計工時">7.5h<small>1 張未填工時</small></span>', overview)

class TokenHiddenTest(unittest.TestCase):
    """data/tokens/ 一個檔都沒有時 Token 整欄不顯示；有任一檔就回到「—」規則（主管版二次優化 AC-4，2026-09-27 使用者裁）。
    2026-10-28 起本週頁沒有資源區與團隊表，Token 只剩總覽資源、兩頁的專案列與團隊頁人卡。"""
    ISSUES = PeopleTest.ISSUES

    def test_no_token_file_hides_every_token_column(self):
        html = page(metrics=metrics_for(self.ISSUES))
        self.assertEqual(html.count("Token"), 0)
        self.assertNotIn("尚未接資料來源", html)
        self.assertIn('<ul class="kpis" style="--kpis:3">', panel(html, "cumulative"))  # 累計工時、累計完成卡片、epic 數

    def test_a_file_for_an_earlier_week_brings_every_column_back(self):
        # 這一期沒有檔、較早期有：所有 Token 欄回來並顯示「—」，不會因為這一期沒資料就安靜少一欄
        html = page(metrics=metrics_for(self.ISSUES, cumulative_tokens={"CS-2": 10}))
        week, cumulative, team = panel(html, "week"), panel(html, "cumulative"), panel(html, "team")
        self.assertIn('<ul class="kpis" style="--kpis:4">', cumulative)
        self.assertIn('<li class=big><span>累計 Token</span><strong class="">10</strong></li>', cumulative)
        self.assertIn('<span class="num tok" data-label="Token">Token —</span>', week)  # 本週一覽表的列
        self.assertIn('<span class="num tok" data-label="Token">Token 10</span>', cumulative)  # 總覽專案列
        self.assertIn("累計工時 1.5h · Token 10", team)

    def test_a_file_for_a_later_week_only_still_shows_the_columns(self):
        # 來源接上後回補更早的週：資料夾有檔但這一期的累計是 None，欄要顯示「—」，不能從累計推成隱藏（Codex 2026-10-06 審出）
        html = page(metrics=metrics_for(self.ISSUES, token_source=True))
        self.assertIn('<span class="num">本週工時</span><span class="num tok">Token</span>', panel(html, "week"))
        self.assertIn("累計工時 1.5h · Token —", panel(html, "team"))


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
        week = panel(page(metrics=metrics_for(issues)), "week")
        # 三段進度條：之前完成 0、本週完成 1、未完成 2；本週工時只有完成卡的 1.5h，未完成卡上的 0.5 不算
        self.assertIn('<div class="bar" role="img" aria-label="1/3 張完成，本週 1 張"><span style="width:0%"></span>'
                      '<span class=wk style=width:33.3%></span></div><span class="num">1/3</span>', week)
        self.assertIn('<span class="num" data-label="本週工時">1.5h</span>', week)
        # 一覽表不再印專案的累計工時（總覽才有）；本週頁剩下的「累計工時」只在 label 橫條的圖例
        self.assertNotIn("累計工時", week[week.index('class="proj-list"'):])


class BreakpointRenderTest(unittest.TestCase):
    """「改組前」分隔線之後灰字列出搬入前已完成的卡，數字不算（AC-16）。"""
    MOVED = "2026-09-18T07:00:00+00:00"

    def html(self):
        issues = ISSUES + [
            issue("CS-7", level=0, parent="CS-1", status="done", points=4.0, resolved=IN_WEEK, assignee="Jin", summary="搬來的卡", moved_in=self.MOVED),
            issue("CS-8", level=0, parent="CS-1", status="done", points=2.0, resolved=LAST_WEEK, assignee="Jin", summary="更早搬來的卡", moved_in=self.MOVED),
        ]
        return page(metrics=metrics_for(issues))

    def test_overview_lists_history_after_the_breakpoint(self):
        html = self.html()
        week, cum = panel(html, "week"), panel(html, "cumulative")
        self.assertNotIn('<div class="breakpoint">', week)  # 本週頁沒有完成清單（2026-10-28）
        for tab, keys in ((cum, ["CS-7", "CS-8"]),):
            after = tab[tab.index('<div class="breakpoint">'):]
            self.assertIn(f"改組前已完成 {len(keys)} 張", after)
            self.assertIn("<span>改組前</span>", after)
            for key in keys:
                self.assertIn(f">{key}</a>", after)
                self.assertNotIn(f">{key}</a>", tab[:tab.index('<div class="breakpoint">')])
        self.assertIn("2026-W38 · 結案 09/10 · 搬入 09/18", cum)
        self.assertIn('<li class=big><span>累計工時</span><strong class="">1.5h</strong>', cum)  # 只有 CS-2 的 1.5h，搬來的 4h、2h 不算
        self.assertNotIn("CS-7", html[html.index('<section id="checks"'):] if '<section id="checks"' in html else "")

    def test_dates_are_shown_in_taipei_time(self):
        # 09-18 17:00Z 是台北 09-19 01:00：搬入日要寫 09/19
        issues = ISSUES + [issue("CS-9", level=0, parent="CS-1", status="done", resolved=IN_WEEK, moved_in="2026-09-18T17:00:00+00:00")]
        self.assertIn("結案 09/18 · 搬入 09/19", panel(page(metrics=metrics_for(issues)), "cumulative"))

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
        self.assertIn("<dt>實際工時</dt>", html)
        self.assertIn("1 張完成卡未填工時", panel(html, "cumulative"))
        # 只寫「實際工時」，不單獨寫「實際」（2026-09-30 使用者裁）
        self.assertEqual(html.count("實際"), html.count("實際工時"))

    def test_glossary_spells_out_the_period_with_weekdays(self):
        html = page()
        self.assertIn("<h1>2026/09/16（三） – 2026/09/22（二）</h1>", html)
        glossary = html[html.index('<section id="glossary"'):html.index("</section>", html.index('<section id="glossary"'))]
        self.assertIn("2026/09/16（三） 00:00 到 2026/09/22（二） 23:59（UTC+8）", glossary)
        for term in ("本週", "累計", "完成卡片", "實際工時", "累計工時", "未填工時", "人", "進度", "label", "Done／On Track", "資料檢查",
                     "停滯", "照流程走", "投入比例"):
            self.assertIn(f"<dt>{term}</dt>", glossary)
        self.assertNotIn("<dt>補登</dt>", glossary)
        self.assertNotIn("<dt>專案投入</dt>", glossary)
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
        self.assertIn('<span class="owner" data-label="負責人">Alice</span>', panel(html, "week"))  # 本週有產出的人，不是 epic 的 assignee


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
        overview = panel(page(metrics=m, narrative={"highlights": [], "epic_results": {}}), "cumulative")
        self.assertNotIn("本週才結的父卡", overview[overview.index("<h3>完成</h3>"):])
        self.assertNotIn("<summary>2026-W39", overview)

    def test_completion_under_settled_epic_is_listed(self):
        issues = ISSUES + [
            issue("CS-7", level=1, status="done", resolved=LAST_WEEK, summary="已結案專案"),
            issue("CS-8", level=0, parent="CS-7", status="done", points=1.0, resolved=IN_WEEK, summary="結案後才完成的卡"),
        ]
        overview = panel(page(metrics=metrics_for(issues)), "cumulative")
        done = overview[overview.index("<h3>完成</h3>"):]
        self.assertIn("結案後才完成的卡", done)
        self.assertIn("已結案專案", done)

    def test_unassigned_in_progress_card_is_shown_without_completions(self):
        issues = ISSUES + [issue("CS-9", level=0, status="doing", assignee="Eve", summary="沒有 epic 的進行中")]
        week = panel(page(metrics=metrics_for(issues)), "week")
        projects = week[week.index('class="proj-list"'):]
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
        projects = week[week.index('class="proj-list"'):]
        people = week[week.index("<h3>人</h3>"):week.index('class="proj-list"')]
        cumulative = panel(html, "cumulative")
        done_section = cumulative[cumulative.index("<h3>完成</h3>"):]
        team = panel(html, "team")
        for row in m["completed"]:
            self.assertIn(f'browse/{row["key"]}"', done_section, row["key"])  # 本週頁沒有完成清單（2026-10-28），總覽依週有
            self.assertNotIn(f'browse/{row["key"]}"', team, row["key"])  # 人卡不列完成清單
        for row in m["cumulative"]["completed"]:
            self.assertIn(f'browse/{row["key"]}"', cumulative, row["key"])
        for row in m["in_progress"]:
            self.assertIn(f'browse/{row["key"]}"', projects, row["key"])
        for row in m["in_progress"]:
            if row["leaf"]:
                self.assertIn(f'browse/{row["key"]}"', team, row["key"])
                self.assertIn(f'browse/{row["key"]}"', people, row["key"])


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
        projects = o[o.index("<h3>專案列表</h3>"):o.index("<h3>完成</h3>")]
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
        # 門市客服 1/2 完成、那 1 張是本週：之前段 0、本週段 50
        self.assertIn('<div class="bar" role="img" aria-label="1/2 張完成，本週 1 張"><span style="width:0%"></span>'
                      '<span class=wk style=width:50%></span></div><span class="num">1/2</span>', row)
        self.assertIn('<span class="num" data-label="累計工時">1.5h</span>', row)
        # 已結案的直售專案本週沒有專案列，本週段是 0、不畫
        settled = o[o.index("已結案的直售專案"):o.index("門市客服")]
        self.assertIn('<div class="bar" role="img" aria-label="1/1 張完成"><span style="width:100%"></span></div><span class="num">1/1</span>', settled)
        self.assertIn('<span class="people" data-label="人">1 <small>Bob</small></span>', settled)
        self.assertIn("<dt>目標</dt><dd>少打字</dd>", row)
        self.assertIn("<dt>Before</dt><dd>手打</dd>", row)
        self.assertNotIn("M2", row)          # 下個里程碑不在效益說明
        self.assertNotIn("買不買", row)
        self.assertNotIn("<details class=\"proj\" open", o)

    def test_label_bars_are_shared_and_replace_the_table(self):
        html = page(metrics=metrics_for(self.ISSUES), narrative={"highlights": ["完成"], "epic_results": {"CS-1": "完成", "_unassigned": "完成"}})
        week, o = panel(html, "week"), panel(html, "cumulative")
        # 直售累計 3.5h 是最長的一條：之前段 3.0/3.5、本週段 0.5/3.5（2026-10-07 使用者裁：兩頁都改橫條）
        row = ('<div class="lrow"><span class="lname">直售 <span class="muted">· 1 個 epic</span></span>'
               '<div class="bar" role="img" aria-label="累計工時 3.5h，本週 0.5h"><span style="width:85.7%"></span>'
               '<span class=wk style=width:14.3%></span></div><span class="num">本週 0.5h · 累計 3.5h</span></div>')
        self.assertIn(row, o)
        self.assertIn('<div class="labels">', o)
        self.assertNotIn("<span>label</span>", o)
        self.assertNotIn('<div class="labels">', week)  # 本週頁的投入看「力氣花在哪」（2026-10-28）
        self.assertNotIn("支援單位", html)
        self.assertNotIn("<h3>團隊</h3>", week)
        self.assertNotIn("<h3>團隊</h3>", o)
        self.assertIn('<li class=big><span>累計工時</span><strong class="">5h</strong></li>', o)
        self.assertIn('<li><span>累計完成卡片</span><strong class="">3</strong></li>', o)


class OverviewLayoutTest(unittest.TestCase):
    """總覽的版面跟本週頁對齊（2026-10-07 總覽 UI 盤點第一批，視覺化改版 AC-8～12）。"""
    ISSUES = OverviewTest.ISSUES + [
        # 父卡上週完成、底下子卡本週完成：父卡只列、不算張數與工時
        issue("CS-11", level=0, parent="CS-1", status="done", points=9.0, resolved=LAST_WEEK, assignee="Alice", labels=("門市",), summary="上週的父卡"),
        issue("CS-12", level=-1, parent="CS-11", status="done", points=2.0, resolved=IN_WEEK, assignee="Alice", labels=("門市",), summary="本週的子卡"),
    ]

    def html(self, **kw):
        return page(metrics=metrics_for(self.ISSUES, states=OverviewTest.STATES, **kw),
                    narrative={"highlights": ["完成"], "epic_results": {"CS-1": "完成", "_unassigned": "完成"}})

    def test_number_columns_have_a_fixed_width_so_a_note_cannot_push_the_row(self):
        # AC-8：「N 張未填工時」把 min-width 撐寬、名稱欄吃掉差額，那一列整排左偏（W41 FDEPROJECT-71 偏 16px）
        html = self.html()
        self.assertIn(".proj-head>.num,.proj.head>.num{flex:none;width:56px;", html)
        self.assertIn("#cumulative .proj-head>.num,#cumulative .proj.head>.num{width:88px}", html)
        self.assertNotIn(".proj-head .num", html)   # 子孫選擇器會吃到進度條旁的「1/2」
        self.assertIn(".prog .num{min-width:56px;", html)

    def test_token_cells_get_a_header_so_the_columns_still_line_up(self):
        html = self.html(cumulative_tokens={"CS-2": 10})
        week, o = panel(html, "week"), panel(html, "cumulative")
        self.assertIn('<span class="num">本週工時</span><span class="num tok">Token</span><span class="sigs">', week)
        self.assertIn('<span class="num">累計工時</span><span class="num tok">Token</span></div>', o)

    def test_resources_lead_then_labels_projects_and_completed(self):
        # 2026-10-07 使用者要求，取代同日「跟本週頁一致」；2026-10-28 團隊表拿掉（不排名）
        o = panel(self.html(), "cumulative")
        heads = ["<h3>資源</h3>", "<h3>依 label</h3>", "<h3>專案列表</h3>", "<h3>完成</h3>"]
        positions = [o.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))

    def test_resources_show_the_epic_count_of_the_project_list(self):
        # 門市客服、已結案的直售專案、沒有 label 的專案：3 個 epic；沒有 epic 的卡不算一個 epic
        html = self.html()
        o, week = panel(html, "cumulative"), panel(html, "week")
        self.assertIn('<ul class="kpis" style="--kpis:3">', o)
        self.assertIn('<li><span>epic 數</span><strong class="">3</strong></li>', o)
        self.assertEqual(o.count('<div class="proj"><div class="proj-head">') + o.count('<details class="proj">'), 4)  # 3 個 epic＋未歸屬 epic 一列
        self.assertNotIn("epic 數</span>", week)

    def test_project_without_any_kickoff_section_does_not_expand(self):
        o = panel(self.html(), "cumulative")
        self.assertNotIn("Kick off 未填", o)
        idle = o[o.rindex('<div class="proj">', 0, o.index("沒有 label 的專案")):o.index("沒有 label 的專案")]
        self.assertIn('<div class="proj"><div class="proj-head"><span class="name">', idle)
        self.assertIn('<details class="proj"><summary class="proj-head"><span class="name"><span class="key"><a href="https://example.atlassian.net/browse/CS-1">', o)

    def test_completed_weeks_are_grouped_by_project_with_leaf_counts(self):
        o = panel(self.html(), "cumulative")
        done = o[o.index("<h3>完成</h3>"):]
        # 本週：門市客服 CS-2 1.5h、CS-12 2h；沒有 epic 的 CS-7 0.5h。上週的 CS-11 是父卡，不是完成卡片，不列也不算
        self.assertIn('<details class="done-group"><summary>2026-W39 <span class="muted">· 3 張 · 4h</span></summary>', done)
        self.assertIn('<li><details><summary><span>CS-1 門市客服</span><span class="nums">2 張 · 3.5h</span></summary>', done)
        self.assertNotIn("上週的父卡", done)
        # 專案順序照 cumulative.epics（卡號序，未歸屬 epic 最後）；週標題與本週頁的完成同一種樣式，預設收合
        w39 = done[:done.index("2026-W38")]
        self.assertLess(w39.index("CS-1 門市客服"), w39.index("沒有 epic 的卡"))
        self.assertNotIn('<details class="done-group" open', done)

    def test_one_disclosure_marker_for_the_whole_page(self):
        html = self.html()
        self.assertIn('details summary{cursor:pointer;list-style:none}', html)
        self.assertIn('details>summary::before{content:"▸";', html)
        self.assertIn('details[open]>summary::before{content:"▾"}', html)

    def test_people_and_hours_share_a_line_on_narrow_screens(self):
        html = self.html()
        narrow = html[html.index("@media (max-width:640px)"):]
        self.assertIn(".people{width:auto;flex:1 1 0;min-width:0}", narrow)
        self.assertNotIn(".people{width:auto;white-space:normal;flex-basis:100%}", narrow)
        self.assertNotIn(",.people{", narrow)


class WeekTableTest(unittest.TestCase):
    """本週頁的專案是一張一覽表，不是一張張卡（2026-10-07 使用者裁，視覺化改版 AC-1）。"""
    ISSUES = OverviewTest.ISSUES + [
        issue("CS-8", level=1, status="doing", labels=("門市",), summary="本週沒動的門市專案"),
        issue("CS-9", level=0, parent="CS-8", status="todo", labels=("門市",)),
        issue("CS-10", level=1, status="done", resolved=IN_WEEK, labels=("直售",), summary="本週結案但沒完成卡的專案"),
    ]
    NARRATIVE = {"highlights": ["完成"], "epic_results": {"CS-1": "門市本週做完一張", "CS-10": "結案", "_unassigned": "完成"}}

    def week(self, states=None):
        week = panel(page(metrics=metrics_for(self.ISSUES, states=states), narrative=self.NARRATIVE), "week")
        return week[week.index('class="proj-list"'):]

    def test_week_tab_order_and_the_table_is_collapsed_at_the_end(self):
        # 2026-10-28 鎖定 AC-2、AC-3：四個數字 → 四格圖 → 這週要你出手的地方 → 人卡 → 專案一覽（收合）；本週重點不獨立列
        week = panel(page(metrics=metrics_for(self.ISSUES), narrative=self.NARRATIVE), "week")
        marks = ['class="kpis wk-kpis"', 'class="viz"', '<h3 id="step-in">', "<h3>人</h3>", '<details class="proj-list"><summary>專案一覽（']
        positions = [week.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('class="highlights"', week)
        self.assertNotIn('<details class="proj-list" open', week)
        self.assertNotIn("card project", week)
        self.assertIn('<div class="proj head"><span class="name">專案</span><span class="owner">負責人</span>', week)

    def test_rows_are_grouped_by_label_with_week_hours_and_unassigned_last(self):
        table = self.week()
        # 段的順序跟 label 表一樣（累計工時多的在前、未分類倒數、未歸屬 epic 最後）；沒結案的 epic 本週沒動也列，跟原本的專案卡一樣
        heads = ['<h4>直售 <span class="muted">· 1 個專案 · 本週 0.5h</span></h4>',  # 含沒有 epic 的直售卡 CS-7
                 '<h4>門市 <span class="muted">· 2 個專案 · 本週 1.5h</span></h4>',
                 '<h4>未分類 <span class="muted">· 1 個專案 · 本週 0h</span></h4>',
                 "<h4>未歸屬 epic</h4>"]
        positions = [table.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(table.index("沒有 label 的專案"), table.index("<h4>未歸屬 epic</h4>"))

    def test_row_cells_signal_and_next_milestone(self):
        states = {"CS-1": {"kickoff_key": "CS-9", "locked_at": "2026-09-20T10:00:00+08:00", "changes": [],
                           "sections": {"objective": "少打字", "why": None, "before": None, "after": None, "evidence": None,
                                        "decision": None, "next_milestone": "M2 上線\n之後再做 M3"}}}
        week = self.week(states)
        row = week[week.rindex('<details class="proj">', 0, week.index("門市客服")):]
        row = row[:row.index("</details>")]
        self.assertIn('<span class="owner" data-label="負責人">Alice</span>', row)
        self.assertIn('<span class="plus" data-label="本週"><b>+1</b></span><span class="num" data-label="本週工時">1.5h</span>', row)
        self.assertIn('<span class="sigs" data-label="訊號"></span><span class="next" data-label="下一步">M2 上線</span>', row)  # 只取第一行
        self.assertIn('<p class="result">門市本週做完一張</p>', row)
        self.assertNotIn("<dt>目標</dt>", row)
        # 本週沒動的專案：負責人退回 epic 的 assignee（未指派）、+0、訊號「本週無完成」、沒有 Kick off 下一步是「—」
        idle = week[week.rindex('<details class="proj">', 0, week.index("本週沒動的門市專案")):]
        idle = idle[:idle.index("</details>")]
        self.assertIn('<span class="owner" data-label="負責人">未指派</span>', idle)
        self.assertIn('<span class="plus" data-label="本週"><span class="muted">0</span></span><span class="num" data-label="本週工時">0h</span>', idle)
        self.assertIn('<span class="sigs" data-label="訊號"><span class="sig">本週無完成</span></span><span class="next" data-label="下一步">—</span>', idle)
        self.assertIn('<p class="result muted">本週沒有完成項目</p>', idle)
        # 本週結案的 epic 是 Done：沒有完成卡也不亮「本週無完成」
        done = week[week.rindex('<details class="proj">', 0, week.index("本週結案但沒完成卡的專案")):]
        done = done[:done.index("</details>")]
        self.assertIn('<span class="sigs" data-label="訊號"></span>', done)
        self.assertIn('<span class="bar-empty">尚無卡片</span>', done)

    def test_unassigned_epic_without_a_named_owner_says_unassigned(self):
        # 未歸屬 epic 沒有 owner、本週的完成卡又沒有 assignee：負責人寫「未指派」，不留空白（Codex 審 PR A）
        issues = [issue("CS-1", level=1, status="doing"),
                  issue("CS-2", level=0, status="done", points=2.0, resolved=IN_WEEK, summary="沒有 epic 也沒有人的卡")]
        week = panel(page(metrics=metrics_for(issues), narrative={"highlights": ["完成"], "epic_results": {"_unassigned": "完成"}}), "week")
        week = week[week.index('class="proj-list"'):]
        row = week[week.index("<h4>未歸屬 epic</h4>"):]
        self.assertIn('<span class="owner" data-label="負責人">未指派</span>', row)
        self.assertNotIn('<span class="owner" data-label="負責人"></span>', week)

    def test_every_cell_carries_a_label_for_narrow_screens(self):
        # 640px 以下藏掉表頭，每格靠 data-label 當欄名（Codex 審 PR A）；總覽的人與累計工時也一樣
        html = page(metrics=metrics_for(self.ISSUES), narrative=self.NARRATIVE)
        week, overview = panel(html, "week"), panel(html, "cumulative")
        for label in ("負責人", "進度", "本週", "本週工時", "訊號", "下一步"):
            self.assertIn(f'data-label="{label}"', week, label)
        for label in ("進度", "人", "累計工時"):
            self.assertIn(f'data-label="{label}"', overview, label)
        self.assertIn('.proj-head>[data-label]::before{content:attr(data-label)', html)
        self.assertIn(".proj-head>[data-label]:empty{display:none}", html)

    def test_in_progress_cards_open_under_their_row(self):
        issues = self.ISSUES + [issue("CS-11", level=0, parent="CS-1", status="doing", assignee="Carol", summary="門市進行中的卡")]
        week = panel(page(metrics=metrics_for(issues), narrative=self.NARRATIVE), "week")
        week = week[week.index('class="proj-list"'):]
        row = week[week.rindex('<details class="proj">', 0, week.index("門市客服")):]
        row = row[:row.index("</details>")]
        self.assertIn("<summary>進行中 1 張</summary>", row)
        self.assertIn("門市進行中的卡", row)


class StepInTest(unittest.TestCase):
    """團隊績效評估（2026-10-28 鎖定 AC-2～6）：四個數字、四格圖、這週要你出手的地方、人卡、不排名。期望值寫死。"""
    ISSUES = [
        issue("CS-1", level=1, status="doing", assignee="Owner", labels=("門市",), summary="門市客服"),
        # Zed 的工時比 amy 多：照名字排與照工時排的結果不同，排序測試才分得出來
        issue("CS-2", level=0, parent="CS-1", status="done", points=9.0, resolved=IN_WEEK, assignee="Zed", labels=("門市",),
              created="2026-09-18T03:58:00.000+0800"),  # 補登
        issue("CS-3", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK, assignee="amy", labels=("門市",), progress_seen=True),
        # 進行中 20 天（09-02 10:00 到 09-23 00:00）：列進原因清單、標警示色
        issue("CS-4", level=0, parent="CS-1", status="doing", assignee="amy", summary="放很久的卡", status_since="2026-09-02T10:00:00+08:00"),
        # 停滯：底下的卡 21 天沒動（09-01 10:00 到 09-23 00:00）
        issue("CS-10", level=1, status="doing", assignee="Bea Lin", labels=("直售",), summary="[直售]很久沒動的專案"),
        issue("CS-11", level=0, parent="CS-10", status="todo", labels=("直售",), updated="2026-09-01T10:00:00.000+0800"),
    ]
    STATES = {"CS-1": {"kickoff_key": "CS-9", "locked_at": "2026-09-20T10:00:00+08:00", "changes": [],
                       "sections": {"objective": None, "why": None, "before": None, "after": None, "evidence": None,
                                    "decision": "要不要買授權", "next_milestone": None}}}
    NARRATIVE = {"highlights": ["完成"], "epic_results": {"CS-1": "完成"}}

    def html(self, previous=None):
        m = compute(snapshot(self.ISSUES), PERIOD, None, self.STATES, [], cumulative_tokens=None, excluded={}, token_source=False,
                    previous=previous)
        return render(m, self.NARRATIVE, ["2026-W39"], in_reports_dir=False)

    def test_three_numbers_with_last_week(self):
        week = panel(self.html(previous={"totals": {"projects": 3, "stalled": 0}}), "week")
        kpis = week[week.index('class="kpis wk-kpis"'):week.index('class="viz"')]
        # 停滯是主數字、唯一用警示色、連到清單；照流程走 50%；上週沒有完成卡，比例的上週值寫明沒有；補登 2026-10-28 拿掉
        self.assertIn('<li class="lead"><span>停滯專案</span><strong class=alert>1</strong><small>上週 0，多 1</small>', kpis)
        self.assertIn('<a href="#step-in">看是哪幾個</a>', kpis)
        self.assertIn('<span>有完成的專案</span><strong>1</strong><small>上週 3，少 2</small>', kpis)
        self.assertIn('<span>照流程走</span><strong>50%</strong><small>沒有上週的值</small>', kpis)
        self.assertIn('style="--kpis:3"', kpis)
        self.assertEqual(kpis.count("<li"), 3)
        self.assertEqual(kpis.count("class=alert"), 1)

    def test_backfill_is_gone_from_every_tab(self):
        # 2026-10-28 使用者裁補登不算：數字、圖、原因清單、人卡、名詞定義都沒有
        self.assertNotIn("補登", self.html())

    def test_percent_change_is_in_points(self):
        from lib.render import _delta
        self.assertEqual(_delta(8, 13, "%"), "上週 13%，少 5 個百分點")
        self.assertEqual(_delta(3, 0, ""), "上週 0，多 3")
        self.assertEqual(_delta(2, 2, ""), "上週 2，持平")
        self.assertEqual(_delta(None, 40, "%"), "上週 40%")

    def test_four_charts_are_inline_svg_with_their_numbers_written(self):
        week = panel(self.html(), "week")
        viz = week[week.index('class="viz"'):week.index('id="step-in"')]
        for title in ("專案地圖", "力氣花在哪", "產出", "資料健康"):
            self.assertIn(f"<h4>{title}</h4>", viz)
        self.assertEqual(viz.count("<svg"), 4)
        # 地圖 x 軸最右的「100%」置中在 x=580，右邊留 20 才不超出 600 寬的 viewBox（fable 審 06bbba4）
        self.assertIn('<text x="580.0" y="248" text-anchor="middle" class="t-axis">100%</text>', viz)
        # 停滯的點標名字（去掉 [單位] 前綴）與負責人的第一個字
        self.assertIn(">很久沒動的專案 · Bea</text>", viz)
        # 投入比例：門市一個 label，兩人各 100% → 100%，數字直接寫在條上
        self.assertIn('<text x="44.0" y="16" class="t-in">100%</text>', viz)
        self.assertIn('<p class="legend swatches"><span><i class="s1"></i>門市</span></p>', viz)
        # 產出：本週 2 張；資料健康：照流程走 50%
        self.assertIn("<title>W39 完成 2 張</title>", viz)
        self.assertIn("W39 照流程走 1/2 張", viz)
        self.assertIn('class="t-val">50%</text>', viz)
        self.assertNotIn("hatch", viz)

    def test_step_in_list_has_three_reasons_and_opens_those_with_items(self):
        week = panel(self.html(), "week")
        step = week[week.index('id="step-in"'):week.index("<h3>人</h3>")]
        self.assertEqual(step.count('<details class="cause" open>'), 3)
        self.assertIn('<b class="cnt alert">1</b> 個專案停滯', step)
        self.assertIn("[直售]很久沒動的專案 <span class=\"muted\">· Bea Lin · 21 天沒動 · 完成 0/1 張</span>", step)
        self.assertIn('<b class="cnt alert">1</b> 張卡放在進行中超過 14 天', step)
        self.assertIn("放很久的卡 <span class=\"muted\">· amy · 20 天</span>", step)
        self.assertIn("門市客服：<span class=\"muted\">要不要買授權</span>", step)
        self.assertEqual(step.count('<details class="cause"'), 3)

    def test_people_are_in_name_order_and_only_compared_with_themselves(self):
        html = self.html()
        week = panel(html, "week")
        cards = week[week.index("<h3>人</h3>"):week.index('class="proj-list"')]
        # Zed 本週 9h、amy 1.5h：照名字（不分大小寫）排，不照工時或張數
        self.assertEqual(re.findall(r'<article class="pcard"><h4>(.*?)</h4>', cards), ["amy", "Zed"])
        self.assertIn("近 1 週完成張數（只跟自己比，深色＝本週）· 本週 1 張", cards)
        self.assertIn('放很久的卡 <span class="alert">20 天</span>', cards)
        self.assertIn("近 1 週落在：門市 1 張", cards)
        team = panel(html, "team")
        self.assertEqual(re.findall(r'<div class="card-head"><h3>(.*?)</h3>', team), ["amy", "Zed"])

    def test_no_axis_puts_people_side_by_side(self):
        # AC-5：三個頁籤都沒有人與人並排的工時或張數長條／表
        html = self.html()
        self.assertNotIn('class="hbar"', html)
        self.assertNotIn('class="rows"', html)
        for name in ("cumulative", "week", "team"):
            self.assertNotIn("<h3>團隊</h3>", panel(html, name), name)
            self.assertNotIn("<span>執行人</span>", panel(html, name), name)  # 名詞定義與頁首的「執行人」是說明字，不是欄

    def test_long_trend_never_draws_negative_widths(self):
        from lib.render import _spark
        svg = _spark([{"week": f"2026-W{i:02d}", "completed": i % 3} for i in range(1, 41)])
        widths = [float(w) for w in re.findall(r'<rect x="[^"]+" y="[^"]+" width="([^"]+)"', svg)]
        self.assertEqual(len(widths), 40)
        self.assertGreater(min(widths), 0)
        # 超過 12 週改窄格：每週 14px 加頭尾各 4px，週次每 4 根標一次（含最新一週），張數只標最新一週
        self.assertIn('viewBox="0 0 568 50" width="568"', svg)
        self.assertEqual(len(re.findall(r">W\d\d</text>", svg)), 10)
        self.assertEqual(svg.count('class="t-val"'), 1)

    def test_short_trend_labels_every_week_and_count(self):
        # 2026-10-28 第二輪：右上小長條看不出是哪幾週、幾張，每根底下標週次、頂端標張數
        from lib.render import _spark
        svg = _spark([{"week": "2026-W43", "completed": 5}, {"week": "2026-W44", "completed": 0}])
        self.assertIn('viewBox="0 0 64 50"', svg)
        # 窄格 13 週時第一根也標週次：置中後不超出左邊，中心 x 至少 9（10px 字的「W30」約 17px 寬；fable 審 26350ed）
        dense = _spark([{"week": f"2026-W{30 + i}", "completed": 1} for i in range(13)])
        first = float(re.search(r'<text x="([\d.]+)" y="48"[^>]*>W30<', dense).group(1))
        self.assertGreaterEqual(first, 9)
        self.assertEqual(re.findall(r">(W\d\d)</text>", svg), ["W43", "W44"])
        self.assertEqual(re.findall(r'class="t-val">(\d+)</text>', svg), ["5", "0"])

    def test_series_colors_have_light_and_dark_steps(self):
        html = self.html()
        self.assertIn("--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;", html)
        self.assertIn("--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;", html)
        self.assertIn(".viz,.pcards,.tcards{grid-template-columns:minmax(0,1fr)}", html[html.index("@media (max-width:640px)"):])
        self.assertIn("main{max-width:1280px;", html)


if __name__ == "__main__":
    unittest.main()
