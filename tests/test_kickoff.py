import unittest

from helpers import *  # noqa: F401,F403
from lib.kickoff import adf_to_lines, as_of, parse_sections, reconcile


def para(*texts):
    content = []
    for i, t in enumerate(texts):
        if i:
            content.append({"type": "hardBreak"})
        content.append({"type": "text", "text": t})
    return {"type": "paragraph", "content": content}


def heading(text):
    return {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": text}]}


FULL = [
    "Objective", "把地接社房產品資料自動彙整",
    "Why It Matters：OP 每週手動整理 10 小時",
    "**Before/After**",
    "Before：人工複製貼上",
    "After: 自動解析產出 docx",
    "Evidence：Demo 與 PR",
    "Decision Needed：是否擴到亞洲地接社",
    "Next Milestone", "部署到正式 VM", "第二行說明",
]


class AdfTest(unittest.TestCase):
    def test_paragraphs_headings_lists_and_hard_breaks(self):
        doc = {"type": "doc", "content": [
            heading("Objective"),
            para("第一行", "第二行"),
            {"type": "bulletList", "content": [{"type": "listItem", "content": [para("清單項")]}]},
            para(""),
        ]}
        self.assertEqual(adf_to_lines(doc), ["Objective", "第一行", "第二行", "清單項"])

    def test_empty_description(self):
        self.assertEqual(adf_to_lines(None), [])


class ParseTest(unittest.TestCase):
    def test_all_accepted_forms(self):
        sections, problems = parse_sections(FULL)
        self.assertEqual(problems, [])
        self.assertEqual(sections, {
            "objective": "把地接社房產品資料自動彙整",
            "why": "OP 每週手動整理 10 小時",
            "before": "人工複製貼上",
            "after": "自動解析產出 docx",
            "evidence": "Demo 與 PR",
            "decision": "是否擴到亞洲地接社",
            "next_milestone": "部署到正式 VM\n第二行說明",
        })

    def test_missing_sections_are_none_not_guessed(self):
        sections, _ = parse_sections(["Objective：只寫了目標", "隨手備註"])
        self.assertEqual(sections["objective"], "只寫了目標\n隨手備註")
        self.assertIsNone(sections["why"])
        self.assertIsNone(sections["before"])

    def test_label_must_start_the_line(self):
        sections, _ = parse_sections(["這裡提到 Objective：不是標籤"])
        self.assertIsNone(sections["objective"])

    def test_evidence_subheadings_stay_in_evidence(self):
        # README 約定 Evidence 寫「可行性：」「完成證明：」兩行；兩個小標不能被當成新段落
        sections, problems = parse_sections([
            "Evidence：可行性：正式帳號只讀驗證過",
            "完成證明：一位業務實機跑通",
            "Decision Needed：",
        ])
        self.assertEqual(problems, [])
        self.assertEqual(sections["evidence"], "可行性：正式帳號只讀驗證過\n完成證明：一位業務實機跑通")
        self.assertIsNone(sections["decision"])

    def test_duplicate_label_is_reported_and_first_kept(self):
        sections, problems = parse_sections(["Evidence：A", "Evidence：B"])
        self.assertEqual(sections["evidence"], "A")
        self.assertEqual(problems, ["「Evidence」出現兩次，只取第一次"])


class LockTest(unittest.TestCase):
    def test_first_sight_locks(self):
        state = reconcile(None, "CS-1", "CS-20", FULL, "t1")
        self.assertEqual(state["locked_at"], "t1")
        self.assertEqual(state["changes"], [])

    def test_filling_a_blank_section_is_not_a_change(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標"], "t1")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標", "Evidence：Demo"], "t2")
        self.assertEqual(state["changes"], [])
        self.assertEqual(state["sections"]["evidence"], "Demo")

    def test_editing_a_locked_section_is_recorded_once_and_locked_value_kept(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標 A"], "t1")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標 B"], "t2")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標 B"], "t3")
        self.assertEqual(state["sections"]["objective"], "目標 A")
        self.assertEqual(state["changes"], [{"detected_at": "t2", "sections": ["objective"]}])

    def test_replacing_the_kickoff_card_is_recorded(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標"], "t1")
        state = reconcile(state, "CS-1", "CS-30", ["Objective：目標"], "t2")
        self.assertEqual(state["changes"], [{"detected_at": "t2", "kickoff_key": "CS-30"}])
        self.assertEqual(state["kickoff_key"], "CS-20")
        # 之後每週都還是 CS-30，不能每週再記一次
        state = reconcile(state, "CS-1", "CS-30", ["Objective：目標"], "t3")
        self.assertEqual(len(state["changes"]), 1)

    def test_kickoff_card_disappearing_keeps_lock_and_is_recorded_once(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標"], "t1")
        state = reconcile(state, "CS-1", None, None, "t2")
        state = reconcile(state, "CS-1", None, None, "t3")
        self.assertEqual(state["sections"]["objective"], "目標")
        self.assertEqual(state["changes"], [{"detected_at": "t2", "kickoff_missing": True}])

    def test_every_goal_section_records_changes(self):
        # 目標五段逐段改值都要記修改；少鎖一段，那一段改了就沒人知道
        for key, label in (("objective", "Objective"), ("why", "Why It Matters"), ("before", "Before"),
                           ("after", "After"), ("evidence", "Evidence")):
            with self.subTest(section=key):
                state = reconcile(None, "CS-1", "CS-20", [f"{label}：甲"], "t1")
                state = reconcile(state, "CS-1", "CS-20", [f"{label}：乙"], "t2")
                self.assertEqual(state["sections"][key], "甲")
                self.assertEqual(state["changes"], [{"detected_at": "t2", "sections": [key]}])

    def test_status_sections_follow_the_card_and_are_not_changes(self):
        # Decision Needed 與 Next Milestone 是狀態不是目標：決定做完、里程碑到了就該換，鎖住會顯示過期內容
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標", "Decision Needed：買不買授權", "Next Milestone：M1"], "t1")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標", "Decision Needed：擴不擴到亞洲", "Next Milestone：M2"], "t2")
        self.assertEqual(state["sections"]["decision"], "擴不擴到亞洲")
        self.assertEqual(state["sections"]["next_milestone"], "M2")
        self.assertEqual(state["changes"], [])

    def test_clearing_a_status_section_clears_it_on_the_page(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標", "Decision Needed：買不買授權"], "t1")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標"], "t2")
        self.assertIsNone(state["sections"]["decision"])
        self.assertEqual(state["changes"], [])

    def test_kickoff_card_disappearing_clears_status_but_keeps_goals(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標", "Decision Needed：買不買授權", "Next Milestone：M1"], "t1")
        state = reconcile(state, "CS-1", None, None, "t2")
        self.assertEqual(state["sections"]["objective"], "目標")
        self.assertIsNone(state["sections"]["decision"])
        self.assertIsNone(state["sections"]["next_milestone"])

    def test_as_of_takes_status_from_that_weeks_description(self):
        state = reconcile(None, "CS-1", "CS-20", ["Objective：目標", "Next Milestone：M1"], "t1")
        state = reconcile(state, "CS-1", "CS-20", ["Objective：目標", "Next Milestone：M2"], "t2")
        old = as_of(state, ["Objective：目標", "Next Milestone：M1"])
        self.assertEqual(old["sections"]["next_milestone"], "M1")
        self.assertEqual(old["sections"]["objective"], "目標")
        self.assertIsNone(as_of(state, None)["sections"]["next_milestone"])
        self.assertEqual(state["sections"]["next_milestone"], "M2")  # 不改到傳進來的鎖定狀態

    def test_as_of_does_not_fill_blank_goals_from_an_older_description(self):
        # 最新鎖定檔的目標是空的，舊一期的描述有目標：重算舊期仍照鎖定檔，只有狀態段落取那一期的
        state = reconcile(None, "CS-1", "CS-20", ["Next Milestone：M2"], "t2")
        old = as_of(state, ["Objective：舊目標", "Evidence：舊證據", "Next Milestone：M1"])
        self.assertIsNone(old["sections"]["objective"])
        self.assertIsNone(old["sections"]["evidence"])
        self.assertEqual(old["sections"]["next_milestone"], "M1")

    def test_state_for_another_epic_is_rejected(self):
        state = reconcile(None, "CS-1", "CS-20", [], "t1")
        with self.assertRaises(ValueError):
            reconcile(state, "CS-2", "CS-20", [], "t2")


if __name__ == "__main__":
    unittest.main()
