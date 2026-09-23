"""從 snapshot 算出週報的所有數字。這裡是數字的唯一來源，AI 只能引用這裡算出來的值。

規則（決策正本在 vault 的 jira-weekly-report 專案 index）：
- 當週完成：狀態類別 done，且 resolutiondate 落在報告期間內。
- Hours = Story point estimate，只加葉節點。有子卡的卡自己的點數不算，epic 與 Kick off 卡不算。
  理由：父卡與子卡都估點，兩邊都加會灌水（CS-16 自己 5.0，子任務合計 7.5）。
- 專案以 epic 分組，不在任何 epic 底下的卡歸「未歸屬 epic」。
- 支援單位以 label 分組，只認 config/units.json 白名單裡的 label。
- Status 只有 Done（epic 已完成）與 On Track。
"""
from __future__ import annotations

from datetime import datetime

from .kickoff import SECTIONS
from .snapshot import EPIC_LEVEL, KICKOFF_TYPE, issue_sort_key, parse_jira_time

SECTION_LABELS = dict(SECTIONS)

UNASSIGNED_EPIC = "_unassigned"
UNASSIGNED_EPIC_LABEL = "未歸屬 epic"
UNASSIGNED_OWNER = "未指派"
UNIT_UNCLASSIFIED = "未分類"
UNIT_MULTIPLE = "多個單位（待釐清）"
STALE_DAYS = 7
SCHEMA = 1


def _round(value: float) -> float:
    return round(value + 0.0, 2)


class _Tree:
    def __init__(self, issues: list, checks: list):
        self.by_key = {i["key"]: i for i in issues}
        self.checks = checks
        for issue in issues:
            if issue["parent"] and issue["parent"] not in self.by_key:
                checks.append(_check("parent_missing", issue["key"], f"父系 {issue['parent']} 不在 FDEPROJECT／CS 的查詢結果裡，當作沒有父系"))
            if issue["level"] > EPIC_LEVEL:
                checks.append(_check("unsupported_level", issue["key"], f"階層 {issue['level']} 高於 epic，不列入計算"))
        self.epics = [i for i in issues if i["level"] == EPIC_LEVEL]
        self.kickoffs = [i for i in issues if i["type"] == KICKOFF_TYPE]
        self.work = [i for i in issues if i["level"] < EPIC_LEVEL and i["type"] != KICKOFF_TYPE]
        work_keys = {i["key"] for i in self.work}
        self.children = {k: [] for k in work_keys}
        for item in self.work:
            if item["parent"] in work_keys:
                self.children[item["parent"]].append(item)
        for kids in self.children.values():
            kids.sort(key=lambda i: issue_sort_key(i["key"]))
        self.work_keys = work_keys

    def parent_of(self, issue: dict):
        return self.by_key.get(issue["parent"]) if issue["parent"] else None

    def epic_of(self, issue: dict):
        seen = set()
        current = issue
        while current is not None:
            if current["level"] == EPIC_LEVEL:
                return current["key"]
            if current["key"] in seen:
                raise ValueError(f"父系關係有循環：{issue['key']}")
            seen.add(current["key"])
            current = self.parent_of(current)
        return None

    def is_leaf(self, item: dict) -> bool:
        return not self.children[item["key"]]

    def root_of(self, item: dict) -> dict:
        """交付單位：一般卡本身；子任務歸到它的父卡（父卡不在範圍內就以子任務本身為單位）。"""
        parent = self.parent_of(item)
        if item["level"] < 0 and parent is not None and parent["key"] in self.work_keys:
            return parent
        return item

    def owner_of(self, item: dict) -> str:
        """負責人：卡片自己的 assignee → 交付單位的 assignee → 未指派。
        子任務常常不指派人，歸給父卡的人。一般卡的交付單位就是自己，不往上算給 epic 負責人：
        epic 負責人是專案負責人，不一定是做這張卡的人，算給他等於用猜的。"""
        if item["assignee"]:
            return item["assignee"]
        root = self.root_of(item)
        return root["assignee"] or UNASSIGNED_OWNER


def _check(kind: str, key, message: str) -> dict:
    return {"kind": kind, "key": key, "message": message}


def _done(issue: dict) -> bool:
    return issue["status_category"] == "done"


def _token_sum(keys: list, tokens):
    if tokens is None:
        return None
    present = [tokens[k] for k in keys if k in tokens]
    return sum(present) if present else None


def kickoff_by_epic(issues: list) -> tuple:
    """回傳 ({epic: kickoff issue}, checks)。一個 epic 多張 Kick off 取卡號最小的一張並列進資料檢查。"""
    checks = []
    tree = _Tree(issues, [])
    found = {}
    for card in sorted(tree.kickoffs, key=lambda i: issue_sort_key(i["key"])):
        parent = tree.parent_of(card)
        if parent is None or parent["level"] != EPIC_LEVEL:
            checks.append(_check("kickoff_without_epic", card["key"], "Kick off 卡的父系不是 epic，無法對應專案"))
            continue
        if parent["key"] in found:
            checks.append(_check("kickoff_duplicate", card["key"], f"{parent['key']} 已經有 Kick off {found[parent['key']]['key']}，這張不採用"))
            continue
        found[parent["key"]] = card
    return found, checks


def compute(snapshot: dict, period, units: list, tokens, kickoff_states: dict, extra_checks: list) -> dict:
    checks = list(extra_checks)
    tree = _Tree(snapshot["issues"], checks)
    unit_set = set(units)

    def completed_this_week(item: dict) -> bool:
        if not _done(item):
            return False
        if not item["resolved"]:
            checks.append(_check("done_without_resolution", item["key"], "狀態是完成但沒有完成時間（resolutiondate），不計入當週完成"))
            return False
        return period.contains(parse_jira_time(item["resolved"]))

    week_done = {i["key"] for i in tree.work if completed_this_week(i)}
    group_of = {i["key"]: (tree.epic_of(i) or UNASSIGNED_EPIC) for i in tree.work}
    leaves = [i for i in tree.work if tree.is_leaf(i)]
    week_leaves = [i for i in leaves if i["key"] in week_done]

    for leaf in week_leaves:
        if leaf["points"] is None:
            checks.append(_check("unestimated", leaf["key"], "本週完成但沒有估點，Hours 以 0 計"))

    if tokens is not None:
        for key in sorted(tokens, key=issue_sort_key):
            if key not in tree.by_key:
                checks.append(_check("token_unknown_key", key, "Token 檔裡的卡號不在這期的 snapshot 裡，只計入總量"))

    # 專案（epic）分組
    epic_rows = []
    groups = [(e["key"], e) for e in sorted(tree.epics, key=lambda i: issue_sort_key(i["key"]))]
    groups.append((UNASSIGNED_EPIC, None))
    for group_id, epic in groups:
        members = [i for i in tree.work if group_of[i["key"]] == group_id]
        group_leaves = [i for i in leaves if group_of[i["key"]] == group_id]
        group_week_leaves = [i for i in week_leaves if group_of[i["key"]] == group_id]
        completed_keys = sorted((i["key"] for i in members if i["key"] in week_done), key=issue_sort_key)
        epic_done_this_week = bool(epic and _done(epic) and epic["resolved"] and period.contains(parse_jira_time(epic["resolved"])))
        active = bool(completed_keys) or epic_done_this_week
        if epic is None and not active:
            continue
        if epic is not None and _done(epic) and not epic_done_this_week:
            continue  # 先前就已結算的專案不再列

        total_points = sum(i["points"] for i in group_leaves if i["points"] is not None)
        done_points = sum(i["points"] for i in group_leaves if i["points"] is not None and _done(i))
        progress = {
            "done_cards": sum(1 for i in group_leaves if _done(i)),
            "total_cards": len(group_leaves),
            "done_points": _round(done_points),
            "total_points": _round(total_points),
            "percent": round(done_points / total_points * 100) if total_points else None,
            "unestimated_cards": sum(1 for i in group_leaves if i["points"] is None),
        }

        roots = {}
        for leaf in group_week_leaves:
            roots.setdefault(tree.root_of(leaf)["key"], []).append(leaf)
        deliveries = []
        for root_key in sorted(roots, key=issue_sort_key):
            root = tree.by_key[root_key]
            kids = tree.children[root_key]
            if root_key in week_done:
                result = "完成"
            elif _done(root):
                result = "完成（先前已結）"
            else:
                done_kids = sum(1 for k in kids if _done(k))
                result = f"{root['status']}（子任務 {done_kids}/{len(kids)} 完成）" if kids else root["status"]
            deliveries.append({
                "key": root_key,
                "summary": root["summary"],
                "owner": root["assignee"] or UNASSIGNED_OWNER,
                "hours": _round(sum(l["points"] or 0 for l in roots[root_key])),
                "tokens": _token_sum([root_key] + [k["key"] for k in kids], tokens),
                "result": result,
            })

        kickoff = None
        if epic is not None:
            state = kickoff_states.get(epic["key"])
            if state is None:
                checks.append(_check("kickoff_missing", epic["key"], "這個 epic 沒有 Kick off 卡，Context 與成果驗證顯示未填"))
            else:
                kickoff = {
                    "key": state["kickoff_key"],
                    "locked_at": state["locked_at"],
                    "sections": state["sections"],
                    "modified_at": [c["detected_at"] for c in state["changes"]],
                }
                for problem in state.get("problems", []):
                    checks.append(_check("kickoff_format", state["kickoff_key"], problem))
                for change in state["changes"]:
                    what = "、".join(SECTION_LABELS[s] for s in change.get("sections", []))
                    if "kickoff_key" in change:
                        what = "、".join(filter(None, [what, f"改用另一張 Kick off {change['kickoff_key']}"]))
                    if change.get("kickoff_missing"):
                        what = "、".join(filter(None, [what, "Kick off 卡不見了（刪除、移出 epic 或改了類型）"]))
                    checks.append(_check("kickoff_modified", state["kickoff_key"], f"鎖定後被修改：{what}（偵測於 {change['detected_at']}），頁面仍顯示鎖定版本"))

        epic_rows.append({
            "id": group_id,
            "key": epic["key"] if epic else None,
            "summary": epic["summary"] if epic else UNASSIGNED_EPIC_LABEL,
            "project": epic["project"] if epic else None,
            "owner": (epic["assignee"] or UNASSIGNED_OWNER) if epic else None,
            "status": ("Done" if _done(epic) else "On Track") if epic else None,
            "due": epic["due"] if epic else None,
            "active": active,
            "progress": progress,
            "week": {
                "completed_count": len(completed_keys),
                "completed_keys": completed_keys,
                "hours": _round(sum(l["points"] or 0 for l in group_week_leaves)),
                "tokens": _token_sum(([epic["key"]] if epic else []) + [i["key"] for i in members], tokens),
                "deliveries": deliveries,
            },
            "kickoff": kickoff,
        })

    # 依負責人
    owners = {}
    for leaf in week_leaves:
        row = owners.setdefault(tree.owner_of(leaf), {"roots": set(), "hours": 0.0, "keys": []})
        row["roots"].add(tree.root_of(leaf)["key"])
        row["hours"] += leaf["points"] or 0
        row["keys"].append(leaf["key"])
    owner_rows = [{
        "owner": name,
        "deliveries": [tree.by_key[k]["summary"] for k in sorted(row["roots"], key=issue_sort_key)],
        "deliverables": len(row["roots"]),
        "hours": _round(row["hours"]),
        "tokens": _token_sum(row["keys"], tokens),
    } for name, row in owners.items()]
    owner_rows.sort(key=lambda r: (-r["hours"], r["owner"] == UNASSIGNED_OWNER, r["owner"]))

    # 依支援單位
    unknown_labels = set()
    units_acc = {}
    for leaf in week_leaves:
        matched = [l for l in leaf["labels"] if l in unit_set]
        unknown_labels.update(l for l in leaf["labels"] if l not in unit_set)
        unit = matched[0] if len(matched) == 1 else (UNIT_UNCLASSIFIED if not matched else UNIT_MULTIPLE)
        if len(matched) > 1:
            checks.append(_check("unit_multiple", leaf["key"], f"同時有多個支援單位 label：{'、'.join(matched)}"))
        row = units_acc.setdefault(unit, {"roots": set(), "hours": 0.0})
        row["roots"].add(tree.root_of(leaf)["key"])
        row["hours"] += leaf["points"] or 0
    for label in sorted(unknown_labels):
        checks.append(_check("unit_unknown_label", None, f"label「{label}」不在 config/units.json 白名單，歸為{UNIT_UNCLASSIFIED}"))
    unit_rows = [{"unit": u, "deliverables": len(r["roots"]), "hours": _round(r["hours"])} for u, r in units_acc.items()]
    special = {UNIT_UNCLASSIFIED: 1, UNIT_MULTIPLE: 2}
    unit_rows.sort(key=lambda r: (special.get(r["unit"], 0), -r["hours"], r["unit"]))

    # Jira Tracking
    def group_label(key: str) -> str:
        group = group_of[key]
        return UNASSIGNED_EPIC_LABEL if group == UNASSIGNED_EPIC else f"{group} {tree.by_key[group]['summary']}"

    completed = []
    for key in sorted(week_done, key=issue_sort_key):
        item = tree.by_key[key]
        leaf = tree.is_leaf(item)
        completed.append({
            "key": key,
            "project": group_label(key),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "leaf": leaf,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if leaf else None,
            "tokens": _token_sum([key], tokens),
        })

    in_progress = []
    for item in sorted((i for i in tree.work if i["status_category"] == "indeterminate"), key=lambda i: issue_sort_key(i["key"])):
        kids = tree.children[item["key"]]
        state = item["status"]
        if kids:
            state += f"，子任務 {sum(1 for k in kids if _done(k))}/{len(kids)} 完成"
        idle = (period.end_exclusive - parse_jira_time(item["updated"])).days
        if idle >= STALE_DAYS:
            state += f"，{idle} 天未更新"
        upcoming = next((k for k in kids if not _done(k)), None)
        in_progress.append({
            "key": item["key"],
            "project": group_label(item["key"]),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "current_state": state,
            "next": f"{upcoming['key']} {upcoming['summary']}" if upcoming else None,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if not kids else None,
            "tokens": _token_sum([item["key"]], tokens),
        })

    active_rows = [r for r in epic_rows if r["active"]]
    return {
        "schema": SCHEMA,
        "site": snapshot["site"],
        "period": period.as_dict(),
        "generated_at": snapshot["fetched_at"],
        "totals": {
            "projects": len(active_rows),
            "deliverables": sum(len(r["week"]["deliveries"]) for r in active_rows),
            "completed": len(completed),
            "hours": _round(sum(l["points"] or 0 for l in week_leaves)),
            "tokens": sum(tokens.values()) if tokens is not None else None,
            "unestimated_completed": sum(1 for l in week_leaves if l["points"] is None),
        },
        "epics": epic_rows,
        "owners": owner_rows,
        "units": unit_rows,
        "completed": completed,
        "in_progress": in_progress,
        "checks": _dedupe(checks),
    }


def _dedupe(checks: list) -> list:
    seen, out = set(), []
    for c in checks:
        marker = (c["kind"], c["key"], c["message"])
        if marker not in seen:
            seen.add(marker)
            out.append(c)
    return out
