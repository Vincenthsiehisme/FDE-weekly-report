import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def issue(key, level=0, parent=None, status="done", points=None, resolved=None, assignee=None,
          labels=(), type_=None, summary=None, updated="2026-09-20T10:00:00.000+0800", due=None, moved_in=None):
    category = {"done": "done", "doing": "indeterminate", "review": "indeterminate", "todo": "new"}[status]
    name = {"done": "完成", "doing": "進行中", "review": "審核中", "todo": "待辦事項"}[status]
    return {
        "key": key,
        "project": key.split("-")[0],
        "summary": summary or f"{key} 摘要",
        "type": type_ or {1: "大型工作", 0: "任務", -1: "子任務"}[level],
        "level": level,
        "status": name,
        "status_category": category,
        "parent": parent,
        "assignee": assignee,
        "labels": sorted(labels),
        "resolved": resolved,
        "points": points,
        "due": due,
        "updated": updated,
        "moved_in": moved_in,
    }


def snapshot(issues, fetched_at="2026-09-22T23:59:30+08:00"):
    return {
        "schema": 2,
        "site": "example.atlassian.net",
        "fetched_at": fetched_at,
        "period": {"week": "2026-W39", "week_ending": "2026-09-22"},
        "counts": {},
        "issues": issues,
    }


IN_WEEK = "2026-09-18T04:00:00.000+0800"
LAST_WEEK = "2026-09-10T04:00:00.000+0800"
