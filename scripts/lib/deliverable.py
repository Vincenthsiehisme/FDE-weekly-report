"""Deliverable 卡（epic 交付時的成效說明）：snapshot 存什麼、頁籤「成效說明」顯示什麼、哪些列資料檢查。

規則正本是 jira-ops `references/deliverable.md`，決策在 vault 的 jira-weekly-report · 成效說明頁籤（2026-10-29 鎖定）。
- 關鍵內容在 FDEPROJECT 的自訂欄位（FIELDS），id 只在這個 team-managed 專案有效；CS 沒有 Deliverable 類型。
  段落欄位 REST v3 回 ADF、短文字回字串，兩種都收；全部選填，沒填是 None。
- 描述只剩 3 行標籤：Acceptance、Code、Env，接受的寫法比照 Kick off（行首標籤、全半形冒號、標題形式）。
- 畫面取附件檔名（公開頁不放圖，附件要登入才看得到）；版本取修正版本（fixVersions）。
- 頁籤只列已完成的 Deliverable，依完成時間所在的那一期分段、新的在上。不鎖定，每週取當下內容。
- 格式問題與沒填的列資料檢查，不擋週報：一張卡寫錯不該讓整份週報出不來。
"""
from __future__ import annotations

import re
from datetime import date

from .kickoff import adf_to_lines
from .period import period_containing
from .snapshot import EPIC_LEVEL, issue_sort_key, parse_jira_time

# 自訂欄位 id → snapshot 裡的名稱（2026-10-29 createmeta 讀回）
FIELDS = {
    "customfield_10479": "delivered",   # 交付了什麼，短文字
    "customfield_10478": "features",    # 交付功能，段落，一行一項
    "customfield_10486": "not_done",    # 沒做的，段落，一行一項
    "customfield_10485": "metric",      # 成效指標，段落，只放主要指標一個
    "customfield_10484": "base",        # 成效基準值，短文字，可帶單位
    "customfield_10483": "base_date",   # 成效基準日，日期
    "customfield_10482": "now",         # 成效最新值，短文字
    "customfield_10481": "now_date",    # 成效最新日，日期
    "customfield_10480": "source",      # 成效資料來源，短文字
    "customfield_10487": "other",       # 其他成效，短文字，第二個以後的指標用「；」分隔
}
FETCH_FIELDS = ["description", "attachment", "fixVersions", *FIELDS]
# 已完成卻沒填就列資料檢查的欄位（2026-10-29：成效三欄與交付兩欄都改選填，Jira 不擋空白）
REQUIRED_WHEN_DONE = (("delivered", "交付了什麼"), ("features", "交付功能"), ("metric", "成效指標"),
                      ("base", "成效基準值"), ("now", "成效最新值"))
SECTIONS = (("acceptance", "Acceptance"), ("code", "Code"), ("env", "Env"))
# 這天以後完成的 epic 要有完成的 Deliverable；之前已關閉的不補（2026-10-29 使用者裁）
REQUIRED_SINCE = date(2026, 10, 29)
RESULTS = ("通過", "未通過", "未驗")
_LABELS = {label.lower(): key for key, label in SECTIONS}
_LINE = re.compile(r"^[\s#>*•\-]*\**\s*(?P<label>Acceptance|Code|Env)\s*\**\s*(?:[:：]\s*(?P<rest>.*))?$", re.IGNORECASE)
_RESULT = re.compile(r"^(?P<cond>.+?)\s*(?:→|->)\s*(?P<result>通過|未通過|未驗)\s*(?:[，,]\s*(?P<where>.*))?$")


def _text(value):
    """短文字回字串、段落回 ADF：都轉成去頭尾空白的字串，空的回 None。"""
    if value is None:
        return None
    if isinstance(value, dict):
        text = "\n".join(adf_to_lines(value))
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError(f"Deliverable 欄位的值不是字串也不是 ADF：{type(value).__name__}")
    return text.strip() or None


def normalize(fields: dict) -> dict:
    """Jira 回來的 fields → snapshot 存的 deliverable。只留頁籤與資料檢查要的。"""
    out = {name: _text(fields.get(fid)) for fid, name in FIELDS.items()}
    out["description"] = adf_to_lines(fields.get("description"))
    out["attachments"] = [a["filename"] for a in fields.get("attachment") or []]
    out["versions"] = [{"name": v["name"], "release_date": v.get("releaseDate")} for v in fields.get("fixVersions") or []]
    return out


def parse_description(lines: list) -> tuple:
    """回傳 (sections, problems)：描述 3 行標籤的內容，與格式問題。"""
    sections = {key: None for key, _ in SECTIONS}
    collected, problems, current = {}, [], None
    for line in lines:
        if "<" in line and ">" in line:
            problems.append("描述還留著 < > 提示，送出前要換成內容或刪掉")
        match = _LINE.match(line)
        if match:
            key = _LABELS[match.group("label").lower()]
            if key in collected:
                problems.append(f"「{match.group('label')}」出現兩次，只取第一次")
                current = None
                continue
            current = key
            collected[key] = []
            rest = (match.group("rest") or "").strip()
            if rest:
                collected[key].append(rest)
        elif current is not None:
            collected[current].append(line)
    for key, label in SECTIONS:
        if key not in collected:
            problems.append(f"描述少了「{label}：」這一行")
    for key, parts in collected.items():
        sections[key] = "；".join(p for p in parts if p).strip() or None
    return sections, list(dict.fromkeys(problems))


def _items(text, sep: str) -> list:
    if not text:
        return []
    return [part.strip() for part in re.split(sep, text) if part.strip()]


def _acceptance(text) -> list:
    """「條件 → 通過，在哪驗、日期；…」→ [{condition, result, where}]。對不上格式的整條當條件、result None。"""
    out = []
    for part in _items(text, "[；;\n]"):
        match = _RESULT.match(part)
        if match:
            out.append({"condition": match.group("cond"), "result": match.group("result"), "where": match.group("where")})
        else:
            out.append({"condition": part, "result": None, "where": None})
    return out


def views(issues: list) -> tuple:
    """回傳 (頁籤要的卡片清單, checks)。issues 是排除過整棵 epic 之後的 snapshot 卡片。
    只列已完成的 Deliverable；一個 epic 多張只取卡號最小的那張，其餘列資料檢查。"""
    by_key = {i["key"]: i for i in issues}
    checks, cards, seen = [], [], {}
    for item in sorted((i for i in issues if i.get("deliverable") is not None), key=lambda i: issue_sort_key(i["key"])):
        parent = by_key.get(item["parent"]) if item["parent"] else None
        if parent is None or parent["level"] != EPIC_LEVEL:
            checks.append(_check("deliverable_without_epic", item["key"], "Deliverable 卡的父系不是 epic，對不到專案"))
            continue
        if parent["key"] in seen:
            checks.append(_check("deliverable_duplicate", item["key"], f"{parent['key']} 已經有 Deliverable {seen[parent['key']]}，這張不採用"))
            continue
        seen[parent["key"]] = item["key"]
        if item["status_category"] != "done":
            continue
        d = item["deliverable"]
        sections, problems = parse_description(d["description"])
        for problem in problems:
            checks.append(_check("deliverable_format", item["key"], problem))
        missing = [label for key, label in REQUIRED_WHEN_DONE if not d[key]]
        if missing:
            checks.append(_check("deliverable_missing", item["key"], f"Deliverable 已完成，沒填：{'、'.join(missing)}"))
        if not item["resolved"]:
            checks.append(_check("deliverable_no_resolution", item["key"], "Deliverable 是完成但沒有完成時間，不知道放哪一週"))
            continue
        acceptance = _acceptance(sections["acceptance"])
        cards.append({
            "key": item["key"],
            "epic": parent["key"],
            "epic_summary": parent["summary"],
            "label": parent["labels"][0] if len(parent["labels"]) == 1 else None,
            "done": item["resolved"],
            "week": period_containing(parse_jira_time(item["resolved"])).week_id,
            "delivered": d["delivered"],
            "features": _items(d["features"], "\n"),
            "not_done": _items(d["not_done"], "\n"),
            "metric": d["metric"],
            "base": d["base"],
            "base_date": d["base_date"],
            "now": d["now"],
            "now_date": d["now_date"],
            "source": d["source"],
            "other": _items(d["other"], "[；;]"),
            "acceptance": acceptance,
            "passed": sum(1 for a in acceptance if a["result"] == "通過"),
            "failed": sum(1 for a in acceptance if a["result"] == "未通過"),
            "code": _items(sections["code"], "[；;]"),
            "env": _items(sections["env"], "[；;]"),
            "attachments": d["attachments"],
            "versions": d["versions"],
        })
    for epic in sorted((i for i in issues if i["level"] == EPIC_LEVEL and i["status_category"] == "done" and i["resolved"]),
                       key=lambda i: issue_sort_key(i["key"])):
        finished = parse_jira_time(epic["resolved"]).date()
        has_done = any(c["epic"] == epic["key"] for c in cards)
        if finished >= REQUIRED_SINCE and not has_done:
            checks.append(_check("deliverable_absent", epic["key"], f"epic {finished.isoformat()} 完成，底下沒有完成的 Deliverable"))
    cards.sort(key=lambda c: (c["done"], c["key"]), reverse=True)
    return cards, checks


def _check(kind: str, key, message: str) -> dict:
    return {"kind": kind, "key": key, "message": message}
