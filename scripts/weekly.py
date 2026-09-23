#!/usr/bin/env python3
"""FDE 週報的命令列入口。流程與 routine 的呼叫方式見 ROUTINE.md。

  prepare [--week-ending YYYY-MM-DD]  抓 Jira、存 snapshot、算 metrics，最後一行印出這期的 week id
  metrics <week>                      只從既有 snapshot 重算 metrics（不打 Jira）
  finalize <week>                     驗證 narrative.json，通過才產出 site/ 底下的 HTML

任何一步失敗都以非 0 結束，不產出部分結果。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import kickoff as kickoff_lib  # noqa: E402
from lib.jira import JiraClient, load_credentials  # noqa: E402
from lib.metrics import compute, kickoff_by_epic  # noqa: E402
from lib.narrative import validate  # noqa: E402
from lib.period import TZ, period_ending, period_for, period_from_dict  # noqa: E402
from lib.render import render  # noqa: E402
from lib.snapshot import fetch_snapshot, issue_sort_key  # noqa: E402

DATA = ROOT / "data"
SITE = ROOT / "site"
UNITS_FILE = ROOT / "config" / "units.json"
WEEK_ID = re.compile(r"\d{4}-W\d{2}")


def read_json(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"找不到 {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def week_dir(week: str) -> Path:
    if not WEEK_ID.fullmatch(week):
        raise ValueError(f"week id 格式要像 2026-W39，收到 {week!r}")
    return DATA / week


def load_units() -> list:
    data = read_json(UNITS_FILE)
    units = data.get("units")
    if not isinstance(units, list) or not all(isinstance(u, str) and u.strip() for u in units):
        raise ValueError(f"{UNITS_FILE.relative_to(ROOT)} 的 units 必須是非空字串陣列（可以是空陣列）")
    return units


def load_tokens(week: str):
    """Token 檔不存在是合法的：代表這期還沒有 Token 資料，頁面顯示「—」。"""
    path = DATA / "tokens" / f"{week}.json"
    if not path.is_file():
        return None
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} 必須是 {{卡號: token 數}}")
    for key, value in data.items():
        issue_sort_key(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path.relative_to(ROOT)} 的 {key} 必須是非負整數，收到 {value!r}")
    return data


def build_metrics(week: str) -> dict:
    snapshot = read_json(week_dir(week) / "snapshot.json")
    period = period_from_dict(snapshot["period"])
    if period.week_id != week:
        raise ValueError(f"snapshot 的期間是 {period.week_id}，不是 {week}")
    found, checks = kickoff_by_epic(snapshot["issues"])
    states = {}
    for epic in sorted(found, key=issue_sort_key):
        card = found[epic]
        path = DATA / "kickoff" / f"{epic}.json"
        existing = read_json(path) if path.is_file() else None
        lines = kickoff_lib.adf_to_lines(card.get("description"))
        state = kickoff_lib.reconcile(existing, epic, card["key"], lines, snapshot["fetched_at"])
        if state != existing:
            write_json(path, state)
        states[epic] = state
    metrics = compute(snapshot, period, load_units(), load_tokens(week), states, checks)
    write_json(week_dir(week) / "metrics.json", metrics)
    return metrics


def cmd_prepare(args) -> None:
    now = datetime.now(TZ)
    period = period_ending(date.fromisoformat(args.week_ending)) if args.week_ending else period_for(now)
    snapshot = fetch_snapshot(JiraClient(load_credentials()), period, now)
    write_json(week_dir(period.week_id) / "snapshot.json", snapshot)
    metrics = build_metrics(period.week_id)
    t = metrics["totals"]
    print(f"snapshot：{sum(snapshot['counts'].values())} 張卡 {snapshot['counts']}", file=sys.stderr)
    print(f"本週完成 {t['completed']} 張、交付 {t['deliverables']} 項、{t['hours']:g}h；資料檢查 {len(metrics['checks'])} 項", file=sys.stderr)
    print(period.week_id)


def cmd_metrics(args) -> None:
    metrics = build_metrics(args.week)
    print(f"{args.week}：本週完成 {metrics['totals']['completed']} 張", file=sys.stderr)


def cmd_finalize(args) -> None:
    week = args.week
    folder = week_dir(week)
    snapshot = read_json(folder / "snapshot.json")
    metrics = read_json(folder / "metrics.json")
    narrative = read_json(folder / "narrative.json")
    validate(narrative, metrics, {i["key"] for i in snapshot["issues"]})

    reports = SITE / "reports"
    weeks = sorted({p.stem for p in reports.glob("*.html") if WEEK_ID.fullmatch(p.stem)} | {week}, reverse=True)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{week}.html").write_text(render(metrics, narrative, weeks, in_reports_dir=True), encoding="utf-8")
    if week == weeks[0]:
        (SITE / "index.html").write_text(render(metrics, narrative, weeks, in_reports_dir=False), encoding="utf-8")
        print(f"已更新 site/index.html 與 site/reports/{week}.html", file=sys.stderr)
    else:
        print(f"{week} 不是最新一期，只更新 site/reports/{week}.html", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--week-ending", help="期間最後一天（週二），預設依執行當下推算")
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("metrics")
    p.add_argument("week")
    p.set_defaults(func=cmd_metrics)
    p = sub.add_parser("finalize")
    p.add_argument("week")
    p.set_defaults(func=cmd_finalize)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
