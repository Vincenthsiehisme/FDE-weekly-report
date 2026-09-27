"""Kick off 卡描述的解析與鎖定。

Kick off 開案寫一次後鎖定目標。描述裡的段落以固定標籤起頭，接受的寫法只有這幾種：
  - 標題（heading）只寫標籤，內容在下面的段落
  - 一行「標籤：內容」或「標籤: 內容」（全半形冒號都可以）
  - 標籤前後可以有粗體 ** 或清單符號
「Before/After」是容器標題，底下拆成「Before：」「After：」兩行。
缺哪一段就回 None，由頁面顯示「未填」，不讓 AI 補。

鎖定：第一次讀到某個 epic 的 Kick off 時存一份到 data/kickoff/<epic>.json。只鎖目標五段
（LOCKED_SECTIONS）：之後頁面一律顯示那份鎖定內容，已填的段落再被改，記下偵測到的時間，頁面標示「被修改」。
Decision Needed 與 Next Milestone 是狀態不是目標，決定做完、里程碑到了就該換，所以每次都取當次描述的值，
改了不算修改（2026-09-27 使用者裁，取代 09-23 的整份鎖定）。
"""
from __future__ import annotations

import re

SECTIONS = (
    ("objective", "Objective"),
    ("why", "Why It Matters"),
    ("before", "Before"),
    ("after", "After"),
    ("evidence", "Evidence"),
    ("decision", "Decision Needed"),
    ("next_milestone", "Next Milestone"),
)
# 鎖定只防目標漂移；其餘段落（Decision Needed、Next Milestone）每次取卡片當下的內容
LOCKED_SECTIONS = ("objective", "why", "before", "after", "evidence")
CONTAINER = "Before/After"
_LABELS = {label.lower(): key for key, label in SECTIONS}
_LINE = re.compile(
    r"^[\s#>*•\-]*\**\s*(?P<label>Objective|Why It Matters|Before/After|Before|After|Evidence|Decision Needed|Next Milestone)"
    r"\s*\**\s*(?:[:：]\s*(?P<rest>.*))?$",
    re.IGNORECASE,
)


def adf_to_lines(node) -> list:
    """ADF 轉成純文字行：每個段落、標題、清單項各一行，hardBreak 也斷行。"""
    if node is None:
        return []
    lines = []

    def text_of(n) -> str:
        if n.get("type") == "text":
            return n.get("text", "")
        if n.get("type") == "hardBreak":
            return "\n"
        return "".join(text_of(c) for c in n.get("content", []))

    def walk(n):
        kind = n.get("type")
        if kind in ("paragraph", "heading"):
            lines.extend(text_of(n).split("\n"))
        else:
            for child in n.get("content", []):
                walk(child)

    walk(node)
    return [line.strip() for line in lines if line.strip()]


def parse_sections(lines: list) -> tuple:
    """回傳 (sections, problems)。problems 是給頁面「資料檢查」看的描述，不讓一張卡的格式問題擋掉整份週報。"""
    sections = {key: None for key, _ in SECTIONS}
    collected, problems = {}, []
    current = None
    for line in lines:
        match = _LINE.match(line)
        if match:
            label = match.group("label").lower()
            if label == CONTAINER.lower():
                current = None
                continue
            key = _LABELS[label]
            if key in collected:
                problems.append(f"「{match.group('label')}」出現兩次，只取第一次")
                current = None
                continue
            current = key
            collected[current] = []
            rest = (match.group("rest") or "").strip()
            if rest:
                collected[current].append(rest)
        elif current is not None:
            collected[current].append(line)
    for key, parts in collected.items():
        text = "\n".join(parts).strip()
        sections[key] = text or None
    return sections, problems


def _shown(locked: dict, current) -> dict:
    """頁面要顯示的段落：目標五段取鎖定版本（還沒填過就取當下的），其餘段落取當下的。
    current 是 None 代表這次沒有卡片可讀，其餘段落就是空的，不沿用上次讀到的。"""
    shown = {}
    for key, _ in SECTIONS:
        now = current[key] if current is not None else None
        if key in LOCKED_SECTIONS:
            shown[key] = locked[key] if locked[key] is not None else now
        else:
            shown[key] = now
    return shown


def as_of(state: dict, lines) -> dict:
    """重算舊的一期時用：目標五段照鎖定檔原樣（空的就是空的，不拿舊描述補），其餘段落換成那一期
    snapshot 的描述。lines 是 None 代表那一期沒有 Kick off 卡。回傳新的 dict，不改傳進來的 state。"""
    current = parse_sections(lines)[0] if lines is not None else None
    view = dict(state)
    view["sections"] = {key: state["sections"][key] if key in LOCKED_SECTIONS
                        else (current[key] if current is not None else None)
                        for key, _ in SECTIONS}
    return view


def reconcile(existing, epic: str, kickoff_key: str, lines: list, seen_at: str) -> dict:
    """回傳新的鎖定狀態。鎖定以段落為單位，只鎖 LOCKED_SECTIONS：

    - 第一次讀到：當下每一段的內容就是鎖定版本（空的段落先記 None）。
    - 之後讀到：鎖定版本是 None 的段落，第一次出現內容就補進去，這是補填不是修改。
      已經鎖定有內容的段落，和上次讀到的不一樣，才記一筆修改。頁面一律顯示鎖定版本。
    - 不在 LOCKED_SECTIONS 的段落每次取當下的內容，改了不記修改。
    - 換了一張 Kick off 卡也記一筆（跟上次讀到的卡號比，換一次只記一次）。
    - 卡片不見了（kickoff_key 為 None）：目標段落照舊顯示，其餘段落清空，記一筆 kickoff_missing，只記一次。
    """
    if kickoff_key is None:
        if existing is None:
            raise ValueError(f"{epic} 沒有 Kick off 卡也沒有鎖定檔，不需要 reconcile")
        updated = dict(existing)
        updated["sections"] = _shown(existing["sections"], None)
        updated["last_seen_at"] = seen_at
        updated["last_seen_key"] = None
        if existing["last_seen_key"] is not None:
            updated["changes"] = existing["changes"] + [{"detected_at": seen_at, "kickoff_missing": True}]
        return updated
    current, problems = parse_sections(lines)
    if existing is None:
        return {
            "epic": epic,
            "kickoff_key": kickoff_key,
            "locked_at": seen_at,
            "sections": current,
            "last_seen": current,
            "last_seen_key": kickoff_key,
            "last_seen_at": seen_at,
            "problems": problems,
            "changes": [],
        }
    if existing["epic"] != epic:
        raise ValueError(f"鎖定檔屬於 {existing['epic']}，不是 {epic}")
    locked = existing["sections"]
    changed = [key for key in LOCKED_SECTIONS
               if locked[key] is not None and current[key] != existing["last_seen"][key]]
    change = {}
    if changed:
        change["sections"] = changed
    if kickoff_key != existing["last_seen_key"]:
        change["kickoff_key"] = kickoff_key
    updated = dict(existing)
    updated["sections"] = _shown(locked, current)
    updated["last_seen"] = current
    updated["last_seen_key"] = kickoff_key
    updated["last_seen_at"] = seen_at
    updated["kickoff_key"] = existing["kickoff_key"]
    updated["problems"] = problems
    updated["changes"] = existing["changes"] + ([dict(detected_at=seen_at, **change)] if change else [])
    return updated
