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
METRICS = compute(snapshot(ISSUES), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
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
        self.metrics = compute(snapshot(self.ISSUES), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
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
            "label 累計工時": m["labels"][0]["cumulative"]["hours"],
        }
        allowed = allowed_numbers(m)
        for label, value in cases.items():
            self.assertNotIn(round(float(value), 2), allowed, f"{label} {value} 剛好等於某個本週數字，案例守不到東西")
            with self.assertRaisesRegex(NarrativeError, "本週數字以外的數字"):
                self.check(f"{label} {value:g}")

    def test_project_counts_come_from_the_program(self):
        # 專案有子任務時，完成張數只數葉節點（父卡不算）；專案層的張數與 label 表的本週工時要有程式算好的值才寫得出來
        issues = [issue("CS-1", level=1, status="doing", labels=("資料組",)),
                  issue("CS-2", level=0, parent="CS-1", status="done", resolved=IN_WEEK),
                  issue("CS-3", level=-1, parent="CS-2", status="done", points=1.0, resolved=IN_WEEK),
                  issue("CS-4", level=-1, parent="CS-2", status="done", points=2.0, resolved=IN_WEEK),
                  issue("CS-5", level=0, parent="CS-1", status="done", points=0.25, resolved=IN_WEEK)]
        m = compute(snapshot(issues), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        week = m["epics"][0]["week"]
        self.assertEqual((week["completed_count"], m["labels"][0]["week"]["hours"]), (3, 3.25))
        validate({"highlights": [f"[CS-1 摘要] 完成 {week['completed_count']} 張、{m['labels'][0]['week']['hours']:g} 小時"],
                  "epic_results": {"CS-1": f"完成 {week['completed_count']} 張"}}, m, {i["key"] for i in issues})


class NarrativeTest(unittest.TestCase):
    def test_valid(self):
        validate(narrative(), METRICS, KEYS)

    def test_issue_key_next_to_chinese_is_recognised(self):
        validate(narrative(highlights=["[地接社資料彙整] 完成CS-2，本週3.5h"]), METRICS, KEYS)

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
        validate(narrative(highlights=["[地接社資料彙整] CS-3 已完成"]), METRICS, KEYS)

    def test_must_cover_exactly_the_active_epics(self):
        with self.assertRaisesRegex(NarrativeError, "缺 \\['CS-1'\\]"):
            validate(narrative(results={}), METRICS, KEYS)
        with self.assertRaisesRegex(NarrativeError, "多 \\['CS-9'\\]"):
            validate(narrative(results={"CS-1": "完成", "CS-9": "多寫"}), METRICS, KEYS)

    def test_one_highlight_per_active_project(self):
        # 2026-10-07 使用者要求「完成多少就寫多少」：不再限 3 條，本週有完成的專案一個一條（W41 14 個專案只寫了 3 條）
        with self.assertRaisesRegex(NarrativeError, "1 個專案，實際 0 條"):
            validate(narrative(highlights=[]), METRICS, KEYS)
        with self.assertRaisesRegex(NarrativeError, "1 個專案，實際 2 條"):
            validate(narrative(highlights=["a", "b"]), METRICS, KEYS)
        issues = []
        names = "甲乙丙丁戊"    # 專案名不帶數字：本週重點裡的數字都要是本週數字
        for n in range(1, 6):   # 5 個專案本週各完成 1 張
            issues += [issue(f"CS-{n}", level=1, status="doing", summary=f"專案{names[n - 1]}"),
                       issue(f"CS-{n}0", level=0, parent=f"CS-{n}", status="done", points=1.0, resolved=IN_WEEK)]
        five = compute(snapshot(issues), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        keys = {i["key"] for i in issues}
        results = {f"CS-{n}": "完成" for n in range(1, 6)}
        validate({"highlights": [f"[專案{c}] 完成" for c in names], "epic_results": results}, five, keys)
        # 同一個專案寫五遍、順序錯，條數對也不過（Codex 審 PR D）
        with self.assertRaisesRegex(NarrativeError, "highlights\\[1\\] 要以「\\[專案乙\\]」開頭"):
            validate({"highlights": ["[專案甲] 完成"] * 5, "epic_results": results}, five, keys)
        with self.assertRaisesRegex(NarrativeError, "highlights\\[0\\] 要以「\\[專案甲\\]」開頭"):
            validate({"highlights": [f"[專案{c}] 完成" for c in reversed(names)], "epic_results": results}, five, keys)
        with self.assertRaisesRegex(NarrativeError, "5 個專案，實際 3 條"):
            validate({"highlights": [f"[專案{c}] 完成" for c in names[:3]], "epic_results": results}, five, keys)

    def test_settled_epic_with_cards_done_this_week_gets_a_highlight_too(self):
        # epic 上週就結案、底下的卡本週才完成：不在 epics 裡，但「完成多少就寫多少」也要寫一條，排在最後（Codex 審 PR D）
        issues = [issue("CS-7", level=1, status="done", resolved=LAST_WEEK, summary="已結案專案"),
                  issue("CS-8", level=0, parent="CS-7", status="done", points=1.0, resolved=IN_WEEK)]
        m = compute(snapshot(issues), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        self.assertEqual([e for e in m["epics"] if e["active"]], [])
        keys = {i["key"] for i in issues}
        validate({"highlights": ["[已結案專案] 完成 CS-8"], "epic_results": {}}, m, keys)
        with self.assertRaisesRegex(NarrativeError, "1 個專案，實際 0 條"):
            validate({"highlights": [], "epic_results": {}}, m, keys)

    def test_project_name_prefix_is_not_checked_as_ai_text(self):
        # 專案名帶本週以外的數字或卡號格式：開頭是 Jira 的名稱，AI 改不掉，不能因此發布不了（Codex 審 PR D 第二輪）；
        # 開頭之後 AI 寫的部分照樣驗
        issues = [issue("CS-1", level=1, status="doing", summary="Phase 999 對照 CS-999 規格"),
                  issue("CS-2", level=0, parent="CS-1", status="done", points=1.5, resolved=IN_WEEK)]
        m = compute(snapshot(issues), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        keys = {i["key"] for i in issues}
        validate({"highlights": ["[Phase 999 對照 CS-999 規格] 完成 CS-2，本週 1.5h"], "epic_results": {"CS-1": "完成"}}, m, keys)
        with self.assertRaisesRegex(NarrativeError, "以外的數字 999"):
            validate({"highlights": ["[Phase 999 對照 CS-999 規格] 完成 999 項"], "epic_results": {"CS-1": "完成"}}, m, keys)
        with self.assertRaisesRegex(NarrativeError, "不存在或已排除的卡號 CS-999"):
            validate({"highlights": ["[Phase 999 對照 CS-999 規格] 完成 CS-999"], "epic_results": {"CS-1": "完成"}}, m, keys)
        with self.assertRaisesRegex(NarrativeError, "只有專案名，沒有內容"):
            validate({"highlights": ["[Phase 999 對照 CS-999 規格]"], "epic_results": {"CS-1": "完成"}}, m, keys)

    def test_quiet_week_requires_empty_highlights(self):
        quiet = compute(snapshot([issue("CS-1", level=1, status="doing")]), period_ending(date(2026, 9, 22)), None, {}, [], cumulative_tokens=None, excluded={}, token_source=False)
        validate({"highlights": [], "epic_results": {}}, quiet, {"CS-1"})
        with self.assertRaisesRegex(NarrativeError, "必須是空陣列"):
            validate({"highlights": ["硬寫一條"], "epic_results": {}}, quiet, {"CS-1"})

    def test_shape(self):
        with self.assertRaisesRegex(NarrativeError, "欄位必須剛好"):
            validate({"highlights": []}, METRICS, KEYS)


if __name__ == "__main__":
    unittest.main()
