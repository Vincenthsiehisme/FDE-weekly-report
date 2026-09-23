import unittest
from datetime import date

from helpers import IN_WEEK, issue, snapshot
from lib.metrics import compute
from lib.period import period_ending
from lib.render import fmt_tokens, render

PERIOD = period_ending(date(2026, 9, 22))
ISSUES = [
    issue("CS-1", level=1, status="doing", summary="<script>alert(1)</script>"),
    issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK),
]
NARRATIVE = {"highlights": ["完成 CS-2"], "epic_results": {"CS-1": "完成"}}


def page(fetched_at="2026-09-22T23:59:30+08:00", in_reports_dir=False):
    metrics = compute(snapshot(ISSUES, fetched_at=fetched_at), PERIOD, [], None, {}, [])
    return render(metrics, NARRATIVE, ["2026-W39", "2026-W38"], in_reports_dir=in_reports_dir)


class RenderTest(unittest.TestCase):
    def test_text_is_escaped(self):
        html = page()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_removed_sections_stay_removed(self):
        html = page()
        for word in ("Attention", "Blocked", "Decision Needed", "Risk"):
            self.assertNotIn(word, html)

    def test_missing_tokens_show_dash(self):
        self.assertEqual(fmt_tokens(None), "—")
        self.assertEqual(fmt_tokens(0), "0")
        self.assertEqual(fmt_tokens(1500), "1.5K")
        self.assertEqual(fmt_tokens(2_300_000), "2.3M")
        self.assertIn("<span>Total Token</span><strong>—</strong>", page())

    def test_scheduled_run_is_not_provisional_but_early_run_is(self):
        self.assertNotIn("數字是產生當下的暫定值", page("2026-09-22T23:59:30+08:00"))
        self.assertIn("數字是產生當下的暫定值", page("2026-09-22T10:00:00+08:00"))

    def test_stale_check_and_archive_links(self):
        html = page()
        self.assertIn('data-generated="2026-09-22T23:59:30+08:00"', html)
        self.assertIn('<a href="reports/2026-W38.html">2026-W38</a>', html)
        self.assertIn('<a href="2026-W38.html">2026-W38</a>', page(in_reports_dir=True))
        self.assertIn('<a href="../index.html">最新一期</a>', page(in_reports_dir=True))

    def test_owner_total_uses_unique_deliverables_and_week_tokens(self):
        issues = [
            issue("CS-1", level=1, status="doing"),
            issue("CS-2", level=0, parent="CS-1", status="doing"),
            issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Alice"),
            issue("CS-4", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK, assignee="Bob"),
        ]
        metrics = compute(snapshot(issues), PERIOD, [], {"CS-1": 500, "CS-3": 100}, {}, [])
        html = render(metrics, {"highlights": ["完成"], "epic_results": {"CS-1": "完成"}}, ["2026-W39"], in_reports_dir=False)
        total = html[html.index("<strong>Total</strong>"):].split("</tr>")[0]
        self.assertIn("<strong>1</strong>", total)     # 同一張父卡只算一項
        self.assertIn("<strong>600</strong>", total)   # 全週 token，含記在 epic 上的 500

    def test_missing_kickoff_shows_unfilled(self):
        html = page()
        self.assertIn("這個 epic 沒有 Kick off 卡", html)
        self.assertIn('<span class="muted">未填</span>', html)


if __name__ == "__main__":
    unittest.main()
