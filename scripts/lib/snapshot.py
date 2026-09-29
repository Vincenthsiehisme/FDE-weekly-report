"""把 Jira 回應整理成每週的 snapshot。

snapshot 是這一期所有計算的唯一輸入，也是歷史紀錄：Jira API 很難回推「某個時間點的狀態」，
要做週與週的比較只能靠每週存下的 snapshot。
"""
from __future__ import annotations

import re
from datetime import datetime

PROJECTS = ("FDEPROJECT", "CS")
KICKOFF_TYPE = "Kick off"
POINTS_FIELD = "customfield_10016"  # Story point estimate，兩個專案共用同一個欄位
FIELDS = [
    "summary", "issuetype", "status", "parent", "assignee", "labels",
    "resolutiondate", POINTS_FIELD, "duedate", "updated", "project",
]
EPIC_LEVEL = 1  # issuetype.hierarchyLevel：epic 1、一般卡 0、子任務 -1
SCHEMA = 1


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


def fetch_snapshot(client, period, fetched_at: datetime) -> dict:
    scope = f"project in ({', '.join(PROJECTS)})"
    client.verify()
    issues = [normalize(i) for i in client.search(f"{scope} ORDER BY key", FIELDS)]
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
