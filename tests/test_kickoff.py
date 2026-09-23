import unittest

from helpers import *  # noqa: F401,F403
from lib.kickoff import adf_to_lines, parse_sections, reconcile


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

    def test_state_for_another_epic_is_rejected(self):
        state = reconcile(None, "CS-1", "CS-20", [], "t1")
        with self.assertRaises(ValueError):
            reconcile(state, "CS-2", "CS-20", [], "t2")


if __name__ == "__main__":
    unittest.main()
