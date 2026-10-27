"""把 Jira 回應整理成每週的 snapshot。

snapshot 是這一期所有計算的唯一輸入，也是歷史紀錄：Jira API 很難回推「某個時間點的狀態」，
要做週與週的比較只能靠每週存下的 snapshot。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

PROJECTS = ("FDEPROJECT", "CS")
KICKOFF_TYPE = "Kick off"
POINTS_FIELD = "customfield_10016"  # Story point estimate，兩個專案共用同一個欄位
FIELDS = [
    "summary", "issuetype", "status", "parent", "assignee", "labels",
    "resolutiondate", POINTS_FIELD, "duedate", "updated", "project", "created",
]
EPIC_LEVEL = 1  # issuetype.hierarchyLevel：epic 1、一般卡 0、子任務 -1
# 「照流程走」只認這個狀態名（2026-10-28 鎖定 AC-1：結案前曾進「進行中」；審核中不算）
PROGRESS_STATUS = "進行中"
# 3：每張卡多了 created；完成與進行中的工作卡多了 progress_seen（曾進「進行中」）與 status_since（進入目前狀態的時間），
#    metrics 用來算補登、照流程走與進行中卡放了幾天（團隊績效評估 AC-1）
# 2：完成的工作卡多了 moved_in（從其他專案搬進 FDEPROJECT／CS 的時間），metrics 用它判斷搬入前已完成（改組前）
SCHEMA = 3


def _required(fields: dict, name: str, key: str):
    if name not in fields or fields[name] is None:
        raise ValueError(f"{key} 缺少必要欄位 {name}")
    return fields[name]


def normalize(issue: dict) -> dict:
    key = issue["key"]
    f = issue["fields"]
    issuetype = _required(f, "issuetype", key)
    status = _required(f, "status", key)
    if "hierarchyLevel" not in issuetype:
        raise ValueError(f"{key} 的 issuetype 沒有 hierarchyLevel，無法判斷 epic 與子任務")
    return {
        "key": key,
        "project": _required(f, "project", key)["key"],
        "summary": _required(f, "summary", key),
        "type": issuetype["name"],
        "level": issuetype["hierarchyLevel"],
        "status": status["name"],
        "status_category": status["statusCategory"]["key"],
        "parent": (f.get("parent") or {}).get("key"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "labels": sorted(f.get("labels") or []),
        "resolved": f.get("resolutiondate"),
        "points": f.get(POINTS_FIELD),
        "due": f.get("duedate"),
        "updated": _required(f, "updated", key),
        "created": _required(f, "created", key),
        "moved_in": None,
        "progress_seen": None,
        "status_since": None,
    }


def issue_sort_key(key: str):
    match = re.fullmatch(r"([A-Z][A-Z0-9_]*)-(\d+)", key)
    if not match:
        raise ValueError(f"卡號格式不對：{key!r}")
    return (match.group(1), int(match.group(2)))


def parse_jira_time(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"無法解析 Jira 時間：{value!r}")


def moved_in_at(histories: list, project_ids: set):
    """最後一次從其他專案搬進 project_ids 的時間；沒有就是 None。
    FDEPROJECT 與 CS 之間互搬不算：工作一直在我們的範圍裡，不是從外面帶進來的歷史。"""
    moves = [h["created"] for h in histories for item in h["items"]
             if item.get("fieldId") == "project" and item.get("to") in project_ids and item.get("from") not in project_ids]
    if not moves:
        return None
    return datetime.fromtimestamp(max(moves) / 1000, timezone.utc).isoformat()


def status_history(histories: list, current_status: str, created: str) -> tuple:
    """回傳 (progress_seen, status_since)：曾不曾待過「進行中」、最後一次進入目前狀態的時間（ISO）。
    建卡時就在進行中的卡沒有「轉進」那一筆，只有之後「從進行中轉出」，所以轉進、轉出都算待過。
    卡片建立時就是目前狀態、之後沒轉過的，changelog 沒有那一筆，進入時間就是建立時間：這是合法的，不是缺資料。"""
    moves = [(h["created"], item.get("fromString"), item.get("toString"))
             for h in histories for item in h["items"] if item.get("fieldId") == "status"]
    seen = any(PROGRESS_STATUS in (frm, to) for _, frm, to in moves)
    moves = [(t, to) for t, _, to in moves]
    into_current = [t for t, to in moves if to == current_status]
    since = datetime.fromtimestamp(max(into_current) / 1000, timezone.utc).isoformat() if into_current else parse_jira_time(created).isoformat()
    return seen, since


def fetch_snapshot(client, period, fetched_at: datetime) -> dict:
    scope = f"project in ({', '.join(PROJECTS)})"
    client.verify()
    raw_issues = client.search(f"{scope} ORDER BY key", FIELDS)
    issues = [normalize(i) for i in raw_issues]
    # 搬入時間只查完成的工作卡：改組前只管「搬進來時已經完成」的卡，沒完成的搬進來之後才會結案，照常算
    # 我們的專案 id 直接問 Jira，不從查到的卡反推：某個專案剛好沒有卡時，從它搬過來的卡會被當成外部搬入
    project_ids = set(client.project_ids(PROJECTS).values())
    done_work = {i["id"]: n for i, n in zip(raw_issues, issues) if n["status_category"] == "done" and n["level"] < EPIC_LEVEL}
    # 狀態歷程查完成與進行中的工作卡：完成卡算照流程走，進行中的卡算放了幾天；待辦的卡兩者都用不到
    tracked = {i["id"]: n for i, n in zip(raw_issues, issues)
               if n["status_category"] in ("done", "indeterminate") and n["level"] < EPIC_LEVEL}
    histories = client.changelogs(sorted(tracked), ["project", "status"]) if tracked else {}
    for issue_id, item in done_work.items():
        item["moved_in"] = moved_in_at(histories.get(issue_id, []), project_ids)
    for issue_id, item in tracked.items():
        item["progress_seen"], item["status_since"] = status_history(histories.get(issue_id, []), item["status"], item["created"])
    kickoffs = client.search(f'{scope} AND issuetype = "{KICKOFF_TYPE}" ORDER BY key', FIELDS + ["description"])
    by_key = {i["key"]: i for i in issues}
    for raw in kickoffs:
        if raw["key"] not in by_key:
            raise ValueError(f"Kick off 卡 {raw['key']} 不在全量查詢結果裡，兩次查詢之間資料有變，重跑一次")
        by_key[raw["key"]]["description"] = raw["fields"].get("description")

    counts = {p: 0 for p in PROJECTS}
    for issue in issues:
        if issue["project"] not in counts:
            raise ValueError(f"{issue['key']} 屬於查詢範圍外的專案 {issue['project']}")
        counts[issue["project"]] += 1
    if not issues:
        # 單一專案 0 張是合法的（FDEPROJECT 剛開），兩個加起來 0 張代表查詢或權限壞了；憑證錯已經在 verify 擋下
        raise ValueError(f"{scope} 查到 0 張卡：憑證有效，檢查這個帳號看不看得到這兩個專案，或專案 key 是否改了")

    issues.sort(key=lambda i: issue_sort_key(i["key"]))
    return {
        "schema": SCHEMA,
        "site": client.site,
        "fetched_at": fetched_at.isoformat(),
        "period": period.as_dict(),
        "counts": counts,
        "issues": issues,
    }
