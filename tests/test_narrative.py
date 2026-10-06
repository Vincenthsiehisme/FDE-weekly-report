import unittest
from datetime import date

from helpers import IN_WEEK, LAST_WEEK, issue, snapshot
from lib.metrics import compute
from lib.narrative import NarrativeError, allowed_numbers, validate
from lib.period import period_ending

ISSUES = [
    issue("CS-1", level=1, status="doing", summary="地接社資料彙整"),
    issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK),
    issue("CS-3", level=0, parent="CS-1", status="done", points=2.0, resolved=IN_WEEK),
]
METRICS = compute(snapshot(ISSUES), period_ending(date(2026, 9, 22)), [], None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
KEYS = {i["key"] for i in ISSUES}


def narrative(highlights=None, results=None):
    return {
        "highlights": ["[地接社資料彙整] 完成 CS-2 與 CS-3，本週 3.5h"] if highlights is None else highlights,
        "epic_results": {"CS-1": "完成 2 項交付"} if results is None else results,
    }


class ProgramNumbersOnlyTest(unittest.TestCase):
    """任何數值都必須由程式算、只放行本週數字（2026-09-30 使用者裁）。W40 寫了「累計完成 61 張」，61 是 progress。"""
    ISSUES = [
        issue("CS-1", level=1, status="doing", summary="地接社資料彙整"),
        issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK),
        issue("CS-3", level=0, parent="CS-1", status="done", points=4.0, resolved=LAST_WEEK),  # 累計多 1 張、4h
        issue("CS-4", level=0, parent="CS-1", status="todo", points=7.0),
        issue("CS-5", level=0, parent="CS-1", status="done", points=9.0, resolved=IN_WEEK, moved_in="2026-09-18T07:00:00+00:00"),  # 改組前
        issue("CS-6", level=0, parent="CS-1", status="doing", points=6.0, assignee="Amy"),       # 進行中
    ]

    def setUp(self):
        self.metrics = compute(snapshot(self.ISSUES), period_ending(date(2026, 9, 22)), [], None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        self.keys = {i["key"] for i in self.ISSUES}

    def check(self, text):
        validate({"highlights": [text], "epic_results": {"CS-1": "完成 1 項"}}, self.metrics, self.keys)

    def test_this_week_numbers_pass(self):
        self.check("[地接社資料彙整] 完成 1 張，本週 1.5h")

    def test_cumulative_progress_migrated_and_in_progress_numbers_are_rejected(self):
        # 案例裡的數字一律從程式算出的 metrics 讀，不手寫（先前手寫的「進度 12.5h」算錯，整條案例沒守到東西）
        m = self.metrics
        epic = m["epics"][0]
        cases = {
            "累計完成張數": m["cumulative"]["totals"]["completed"],
            "累計工時": m["cumulative"]["totals"]["hours"],
            "進度完成張數": epic["progress"]["done_cards"],
            "進度總工時": epic["progress"]["total_points"],
            "進度百分比": epic["progress"]["percent"],
            "改組前工時": m["migrated"]["week"][0]["hours"],
            "進行中工時": next(r["hours"] for r in m["in_progress"] if r["key"] == "CS-6"),
        }
        allowed = allowed_numbers(m)
        for label, value in cases.items():
            self.assertNotIn(round(float(value), 2), allowed, f"{label} {value} 剛好等於某個本週數字，案例守不到東西")
            with self.assertRaisesRegex(NarrativeError, "本週數字以外的數字"):
                self.check(f"{label} {value:g}")

    def test_project_deliverables_come_from_the_program(self):
        # 專案有子任務時，交付數不等於完成張數；專案層的交付數要有程式算好的值才寫得出來
        issues = [issue("CS-1", level=1, status="doing"),
                  issue("CS-2", level=0, parent="CS-1", status="done", resolved=IN_WEEK),
                  issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK),
                  issue("CS-4", level=-1, parent="CS-2", status="done", points=2.0, resolved=IN_WEEK),
                  issue("CS-5", level=0, parent="CS-1", status="done", points=0.5, resolved=IN_WEEK)]
        m = compute(snapshot(issues), period_ending(date(2026, 9, 22)), [], None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        week = m["epics"][0]["week"]
        self.assertEqual((week["completed_count"], week["deliverables"]), (4, 2))
        validate({"highlights": [f"完成 {week['completed_count']} 張、交付 {week['deliverables']} 項"],
                  "epic_results": {"CS-1": f"交付 {week['deliverables']} 項"}}, m, {i["key"] for i in issues})


class NarrativeTest(unittest.TestCase):
    def test_valid(self):
        validate(narrative(), METRICS, KEYS)

    def test_issue_key_next_to_chinese_is_recognised(self):
        validate(narrative(highlights=["完成CS-2，本週3.5h"]), METRICS, KEYS)

    def test_unknown_issue_key_rejected(self):
        with self.assertRaisesRegex(NarrativeError, "不存在或已排除的卡號 CS-99"):
            validate(narrative(highlights=["完成 CS-99"]), METRICS, KEYS)

    def test_invented_number_rejected(self):
        with self.assertRaisesRegex(NarrativeError, "本週數字以外的數字 62"):
            validate(narrative(results={"CS-1": "進度 62%"}), METRICS, KEYS)

    def test_thousands_separator_is_one_number(self):
        with self.assertRaisesRegex(NarrativeError, "本週數字以外的數字 1,000"):
            validate(narrative(results={"CS-1": "投入 1,000 小時"}), METRICS, KEYS)

    def test_chinese_numerals_with_a_measure_word_rejected(self):
        for text in ("完成兩項交付", "完成十張卡", "投入三小時", "進度五成"):
            with self.assertRaisesRegex(NarrativeError, "中文數字"):
                validate(narrative(results={"CS-1": text}), METRICS, KEYS)
        # 不是數量的一般詞彙不擋
        validate(narrative(results={"CS-1": "第一階段完成，進一步整合中，一句話說完"}), METRICS, KEYS)

    def test_first_ordinal_and_one_off_are_not_quantities(self):
        # 「第一次」只說是第一個、「一次性」是形容詞，都沒有宣稱數量；2026-09-27 W40 預覽時「第一次發版」被誤擋
        for text in ("首次之後第一次發版", "第一週上線", "第一張卡完成", "一次性匯入"):
            with self.subTest(text=text):
                validate(narrative(results={"CS-1": text}), METRICS, KEYS)
        # 其他序數等於宣稱了數量（第九次 = 試了 9 次），照擋，要寫就寫阿拉伯數字讓 metrics 比對
        for text in ("完成一次", "試了十一次", "再跑兩次", "每一次都失敗",
                     "第九次嘗試才成功", "第十二天完成", "第十一次試跑", "第一百次"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(NarrativeError, "中文數字"):
                    validate(narrative(results={"CS-1": text}), METRICS, KEYS)

    def test_numbers_inside_issue_keys_are_ignored(self):
        validate(narrative(highlights=["CS-3 已完成"]), METRICS, KEYS)

    def test_must_cover_exactly_the_active_epics(self):
        with self.assertRaisesRegex(NarrativeError, "缺 \\['CS-1'\\]"):
            validate(narrative(results={}), METRICS, KEYS)
        with self.assertRaisesRegex(NarrativeError, "多 \\['CS-9'\\]"):
            validate(narrative(results={"CS-1": "完成", "CS-9": "多寫"}), METRICS, KEYS)

    def test_highlight_count(self):
        with self.assertRaisesRegex(NarrativeError, "1 到 3 條"):
            validate(narrative(highlights=[]), METRICS, KEYS)
        with self.assertRaisesRegex(NarrativeError, "1 到 3 條"):
            validate(narrative(highlights=["a", "b", "c", "d"]), METRICS, KEYS)

    def test_quiet_week_requires_empty_highlights(self):
        quiet = compute(snapshot([issue("CS-1", level=1, status="doing")]), period_ending(date(2026, 9, 22)), [], None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        validate({"highlights": [], "epic_results": {}}, quiet, {"CS-1"})
        with self.assertRaisesRegex(NarrativeError, "必須是空陣列"):
            validate({"highlights": ["硬寫一條"], "epic_results": {}}, quiet, {"CS-1"})

    def test_shape(self):
        with self.assertRaisesRegex(NarrativeError, "欄位必須剛好"):
            validate({"highlights": []}, METRICS, KEYS)


if __name__ == "__main__":
    unittest.main()
