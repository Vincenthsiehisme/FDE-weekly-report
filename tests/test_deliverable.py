"""成效說明頁籤：Deliverable 卡的抓取、解析、資料檢查與畫面（vault 的 jira-weekly-report · 成效說明頁籤，2026-10-29 鎖定）。"""
import unittest
from datetime import date, datetime

from helpers import IN_WEEK, issue, snapshot
from lib import deliverable
from lib.metrics import compute
from lib.period import TZ, period_ending
from lib.render import render
from lib.snapshot import fetch_snapshot
from test_jira import FakeClient, raw

PERIOD = period_ending(date(2026, 9, 22))


def adf(*paragraphs):
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs]}


def fields(**over):
    """Jira 回來的 Deliverable fields。預設每欄都有值，over 給 None 就是沒填。"""
    base = {
        "customfield_10479": "直團業務一次產生尾款付款連結",
        "customfield_10478": adf("勾選多張訂單一次產生", "連結帶入金額"),
        "customfield_10486": adf("刷卡失敗的訂單 → CS-90"),
        "customfield_10485": adf("每張連結製作時間"),
        "customfield_10484": "6 分鐘",
        "customfield_10483": "2026-09-01",
        "customfield_10482": "40 秒",
        "customfield_10481": "2026-09-20",
        "customfield_10480": "業務實測",
        "customfield_10487": "填寫時間：12 分鐘 → 5 分鐘；退件率：23% → 9%",
        "description": adf("Acceptance：金額全對 → 通過，正式帳號、2026/09/19；不重複產生 → 未通過，測試機",
                           "Code：https://github.com/x/y/pull/1；javascript:alert(1)",
                           "Env：https://github.com/Liontech-tw/fde-devcontainers/tree/main/projects/y"),
        "attachment": [{"filename": "list.png"}],
        "fixVersions": [{"name": "0.38.0", "releaseDate": "2026-09-20"}],
    }
    base.update(over)
    return base


def card(key, parent="CS-1", status="done", resolved=IN_WEEK, **over):
    item = issue(key, level=0, parent=parent, status=status, resolved=resolved, type_="Deliverable", points=None)
    item["deliverable"] = deliverable.normalize(fields(**over))
    return item


def epics():
    return [issue("CS-1", level=1, status="doing", summary="批次付款連結", labels=["直售"]),
            issue("CS-9", level=1, status="doing", summary="另一個專案", labels=["國旅"])]


class NormalizeTest(unittest.TestCase):
    def test_paragraph_adf_and_short_text_both_become_text(self):
        d = deliverable.normalize(fields())
        self.assertEqual(d["features"], "勾選多張訂單一次產生\n連結帶入金額")
        self.assertEqual(d["base"], "6 分鐘")
        self.assertEqual(d["attachments"], ["list.png"])
        self.assertEqual(d["versions"], [{"name": "0.38.0", "release_date": "2026-09-20"}])

    def test_blank_fields_are_none(self):
        d = deliverable.normalize(fields(customfield_10479="  ", customfield_10478=None))
        self.assertIsNone(d["delivered"])
        self.assertIsNone(d["features"])

    def test_unexpected_value_type_fails_loud(self):
        with self.assertRaisesRegex(ValueError, "不是字串也不是 ADF"):
            deliverable.normalize(fields(customfield_10484=12))


class DescriptionTest(unittest.TestCase):
    def test_three_labels(self):
        sections, problems = deliverable.parse_description(["Acceptance：a → 通過", "Code：https://x", "Env："])
        self.assertEqual(sections, {"acceptance": "a → 通過", "code": "https://x", "env": None})
        self.assertEqual(problems, [])

    def test_missing_duplicate_and_placeholder_are_problems(self):
        _, problems = deliverable.parse_description(["Acceptance：<條件>", "Acceptance：b"])
        self.assertIn("描述還留著 < > 提示，送出前要換成內容或刪掉", problems)
        self.assertIn("「Acceptance」出現兩次，只取第一次", problems)
        self.assertIn("描述少了「Code：」這一行", problems)
        self.assertIn("描述少了「Env：」這一行", problems)


class ViewsTest(unittest.TestCase):
    def test_done_card_view(self):
        cards, checks = deliverable.views(epics() + [card("CS-50")])
        self.assertEqual(checks, [])
        c = cards[0]
        self.assertEqual((c["epic"], c["label"], c["week"]), ("CS-1", "直售", "2026-W39"))
        self.assertEqual(c["features"], ["勾選多張訂單一次產生", "連結帶入金額"])
        self.assertEqual(c["other"], ["填寫時間：12 分鐘 → 5 分鐘", "退件率：23% → 9%"])
        self.assertEqual((c["passed"], c["failed"], len(c["acceptance"])), (1, 1, 2))
        self.assertEqual(c["acceptance"][0], {"condition": "金額全對", "result": "通過", "where": "正式帳號、2026/09/19"})

    def test_only_done_cards_are_listed(self):
        # 重開過的卡 Jira 可能留著完成時間：狀態不是完成就不列，不看完成時間
        cards, checks = deliverable.views(epics() + [card("CS-50", status="review", resolved=IN_WEEK)])
        self.assertEqual((cards, checks), ([], []))

    def test_missing_fields_on_a_done_card_are_reported(self):
        _, checks = deliverable.views(epics() + [card("CS-50", customfield_10479=None, customfield_10484=None)])
        self.assertEqual([(c["kind"], c["message"]) for c in checks],
                         [("deliverable_missing", "Deliverable 已完成，沒填：交付了什麼、成效基準值")])

    def test_one_per_epic_and_parent_must_be_epic(self):
        cards, checks = deliverable.views(epics() + [card("CS-50"), card("CS-51"), card("CS-52", parent=None)])
        self.assertEqual([c["key"] for c in cards], ["CS-50"])
        self.assertEqual([(c["kind"], c["key"]) for c in checks],
                         [("deliverable_duplicate", "CS-51"), ("deliverable_without_epic", "CS-52")])

    def test_epic_done_after_the_rule_needs_a_done_deliverable(self):
        late = issue("CS-20", level=1, status="done", resolved="2026-10-30T10:00:00.000+0800")
        early = issue("CS-21", level=1, status="done", resolved="2026-10-28T10:00:00.000+0800")
        _, checks = deliverable.views([late, early])
        self.assertEqual([(c["kind"], c["key"]) for c in checks], [("deliverable_absent", "CS-20")])

    def test_newest_first(self):
        cards, _ = deliverable.views(epics() + [card("CS-50", resolved="2026-09-10T10:00:00.000+0800"),
                                                card("CS-51", parent="CS-9", resolved=IN_WEEK)])
        self.assertEqual([c["key"] for c in cards], ["CS-51", "CS-50"])


class FetchTest(unittest.TestCase):
    def test_snapshot_attaches_deliverable_fields(self):
        dv = raw("CS-50", "Deliverable", 0, parent={"key": "CS-1"}, **fields())
        client = FakeClient([raw("CS-1", "大型工作", 1), raw("CS-50", "Deliverable", 0, parent={"key": "CS-1"})], [], deliverables=[dv])
        snap = fetch_snapshot(client, PERIOD, datetime(2026, 9, 22, 23, 59, tzinfo=TZ))
        by_key = {i["key"]: i for i in snap["issues"]}
        self.assertEqual(by_key["CS-50"]["deliverable"]["base"], "6 分鐘")
        self.assertNotIn("deliverable", by_key["CS-1"])
        self.assertEqual(snap["schema"], 4)

    def test_deliverable_missing_from_full_query_fails(self):
        dv = raw("CS-50", "Deliverable", 0, **fields())
        with self.assertRaisesRegex(ValueError, "不在全量查詢結果裡"):
            fetch_snapshot(FakeClient([raw("CS-1", "大型工作", 1)], [], deliverables=[dv]), PERIOD,
                           datetime(2026, 9, 22, 23, 59, tzinfo=TZ))


def run(issues, schema=4):
    snap = dict(snapshot(issues), schema=schema)
    return compute(snap, PERIOD, None, {}, [], cumulative_tokens=None, excluded={}, token_source=False, previous=None)


class MetricsAndPageTest(unittest.TestCase):
    def page(self, metrics):
        return render(metrics, {"highlights": [], "epic_results": {}}, ["2026-W39"], in_reports_dir=False)

    def test_old_snapshot_has_no_deliverables_and_page_says_so(self):
        m = run(epics(), schema=3)
        self.assertIsNone(m["deliverables"])
        self.assertIn("這一期的資料沒有抓 Deliverable", self.page(m))

    def test_empty_state(self):
        m = run(epics())
        self.assertEqual(m["deliverables"], [])
        self.assertIn("還沒有已交付的專案。epic 完成、Deliverable 卡填好後，下一期週報會列在這裡。", self.page(m))

    def test_fourth_tab_with_card_and_jira_links(self):
        m = run(epics() + [card("CS-50")])
        html = self.page(m)
        panel = html.split('id="delivered"')[1]  # 總覽頁籤也連 CS-1，只看成效說明這一段
        self.assertIn('aria-controls="delivered" aria-selected="false">成效說明</button>', html)
        self.assertIn('aria-controls="week" aria-selected="true">', html)  # 預設仍是本週
        self.assertIn('<b><a href="https://example.atlassian.net/browse/CS-1">CS-1</a></b> 批次付款連結', panel)
        self.assertIn('Deliverable <a href="https://example.atlassian.net/browse/CS-50">CS-50</a>', panel)
        self.assertIn("<strong>6 分鐘 → 40 秒</strong>", html)
        self.assertIn("另 2 項，見完整說明", html)
        self.assertIn('<a href="https://github.com/x/y/pull/1">github.com/x/y/pull/1</a>', html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("圖在 Jira 卡", html)
        self.assertNotIn("<img", panel)

    def test_missing_outcome_is_shown_as_missing(self):
        m = run(epics() + [card("CS-50", customfield_10482=None)])
        self.assertIn('<div class="dv-outcome missing">成效未填</div>', self.page(m))

    def test_deliverable_checks_reach_the_page(self):
        m = run(epics() + [card("CS-50"), card("CS-51")])
        self.assertIn("CS-1 已經有 Deliverable CS-50，這張不採用", self.page(m))


if __name__ == "__main__":
    unittest.main()
