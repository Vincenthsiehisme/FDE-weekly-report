#!/usr/bin/env python3
"""FDE 週報的命令列入口。流程與 routine 的呼叫方式見 ROUTINE.md。

  prepare [--week-ending YYYY-MM-DD] [--wait-for-period-end]
                                      抓 Jira、存 snapshot、算 metrics，最後一行印出這期的 week id
  metrics <week>                      只從既有 snapshot 重算 metrics（不打 Jira）
  finalize <week>                     驗證 narrative.json，通過才產出 site/ 底下的 HTML

任何一步失敗都以非 0 結束，不產出部分結果。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import kickoff as kickoff_lib  # noqa: E402
from lib.jira import JiraClient, load_credentials  # noqa: E402
from lib.metrics import compute, kickoff_by_epic  # noqa: E402
from lib.narrative import validate  # noqa: E402
from lib.period import TZ, period_ending, period_for, period_from_dict, wait_for_end  # noqa: E402
from lib.render import render  # noqa: E402
from lib.snapshot import EPIC_LEVEL, fetch_snapshot, issue_sort_key  # noqa: E402

DATA = ROOT / "data"
SITE = ROOT / "site"
UNITS_FILE = ROOT / "config" / "units.json"
EXCLUDED_FILE = ROOT / "config" / "excluded.json"
CARD_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")
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


def load_excluded() -> dict:
    """不算完成的卡：{卡號: 理由}。檔案必須存在，沒有要排除的就寫 {"cards": {}}。
    卡號格式錯了直接失敗：打錯的卡號不會排除任何東西，而且看起來跟沒排除一樣。"""
    data = read_json(EXCLUDED_FILE)
    cards = data.get("cards")
    where = EXCLUDED_FILE.relative_to(ROOT)
    if not isinstance(cards, dict):
        raise ValueError(f"{where} 的 cards 必須是 {{卡號: 理由}}（沒有要排除的卡就寫 {{}}）")
    for key, reason in cards.items():
        if not CARD_KEY.fullmatch(key):
            raise ValueError(f"{where} 的卡號格式不對：{key!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{where} 的 {key} 沒有寫排除理由")
    return cards


def load_tokens(week: str):
    """Token 檔不存在是合法的：代表這期還沒有 Token 資料，頁面顯示「—」。"""
    path = DATA / "tokens" / f"{week}.json"
    if not path.is_file():
        return None
    return _read_tokens(path)


def load_cumulative_tokens(week: str):
    """週次不晚於 week 的 Token 檔逐卡相加。一個檔都沒有是合法的：累計 Token 顯示「—」。"""
    folder = DATA / "tokens"
    found = sorted(folder.glob("*.json")) if folder.is_dir() else []
    # 檔名打錯（例如 2026-w40.json）會安靜地少算一週，直接擋下
    misnamed = [p.name for p in found if not WEEK_ID.fullmatch(p.stem)]
    if misnamed:
        raise ValueError(f"data/tokens 裡的檔名要像 2026-W39.json：{'、'.join(misnamed)}")
    paths = [p for p in found if p.stem <= week]
    if not paths:
        return None
    total = {}
    for path in paths:
        for key, value in _read_tokens(path).items():
            total[key] = total.get(key, 0) + value
    return total


def _read_tokens(path: Path) -> dict:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} 必須是 {{卡號: token 數}}")
    for key, value in data.items():
        issue_sort_key(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path.relative_to(ROOT)} 的 {key} 必須是非負整數，收到 {value!r}")
    return data


def compute_week(snapshot: dict) -> tuple:
    """只算不寫：回傳 (metrics, {路徑: 要寫的內容})。任何一步失敗都不會留下檔案。"""
    period = period_from_dict(snapshot["period"])
    found, checks = kickoff_by_epic(snapshot["issues"])
    seen_at = snapshot["fetched_at"]
    epics = {i["key"] for i in snapshot["issues"] if i["level"] == EPIC_LEVEL}
    lock_dir = DATA / "kickoff"
    locked = {p.stem for p in lock_dir.glob("*.json")} if lock_dir.is_dir() else set()
    states, writes = {}, {}
    # 已經鎖定過的 epic 就算這期找不到 Kick off 卡，也要載入鎖定內容並記下卡片消失
    for epic in sorted(set(found) | (locked & epics), key=issue_sort_key):
        path = lock_dir / f"{epic}.json"
        existing = read_json(path) if path.is_file() else None
        card = found.get(epic)
        lines = kickoff_lib.adf_to_lines(card.get("description")) if card else None
        if existing is not None and datetime.fromisoformat(seen_at) < datetime.fromisoformat(existing["last_seen_at"]):
            # 重算舊的一期：鎖定檔代表最新的觀察，只讀不寫；不鎖的段落取那一期自己的描述
            states[epic] = kickoff_lib.as_of(existing, lines)
            continue
        state = kickoff_lib.reconcile(existing, epic, card["key"] if card else None, lines, seen_at)
        if state != existing:
            writes[path] = state
        states[epic] = state
    metrics = compute(snapshot, period, load_units(), load_tokens(period.week_id), states, checks, excluded=load_excluded(),
                      cumulative_tokens=load_cumulative_tokens(period.week_id))
    writes[week_dir(period.week_id) / "metrics.json"] = metrics
    return metrics, writes


def publish_week(snapshot: dict) -> dict:
    """全部算完才一次寫出 snapshot、metrics 與 Kick off 鎖定檔。"""
    metrics, writes = compute_week(snapshot)
    writes[week_dir(metrics["period"]["week"]) / "snapshot.json"] = snapshot
    for path in sorted(writes):
        write_json(path, writes[path])
    return metrics


def cmd_prepare(args) -> None:
    now = datetime.now(TZ)
    period = period_ending(date.fromisoformat(args.week_ending)) if args.week_ending else period_for(now)
    if args.wait_for_period_end:
        seconds = wait_for_end(period, now)
        if seconds:
            print(f"等 {seconds} 秒，到 {period.week_id} 期間結束後才抓資料", file=sys.stderr)
            time.sleep(seconds)
            now = datetime.now(TZ)
    snapshot = fetch_snapshot(JiraClient(load_credentials()), period, now)
    metrics = publish_week(snapshot)
    t = metrics["totals"]
    print(f"snapshot：{sum(snapshot['counts'].values())} 張卡 {snapshot['counts']}", file=sys.stderr)
    print(f"本週完成 {t['completed']} 張、交付 {t['deliverables']} 項、{t['hours']:g}h；資料檢查 {len(metrics['checks'])} 項", file=sys.stderr)
    print(period.week_id)


def cmd_metrics(args) -> None:
    snapshot = read_json(week_dir(args.week) / "snapshot.json")
    if snapshot["period"]["week"] != args.week:
        raise ValueError(f"snapshot 的期間是 {snapshot['period']['week']}，不是 {args.week}")
    metrics = publish_week(snapshot)
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
    # 首頁永遠是最新一期；補建舊的一期時也重繪，歷史清單才會多出那一期
    latest = weeks[0]
    if latest != week:
        metrics = read_json(week_dir(latest) / "metrics.json")
        narrative = read_json(week_dir(latest) / "narrative.json")
    (SITE / "index.html").write_text(render(metrics, narrative, weeks, in_reports_dir=False), encoding="utf-8")
    print(f"已更新 site/reports/{week}.html，site/index.html 為最新一期 {latest}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--week-ending", help="期間最後一天（週二），預設依執行當下推算")
    p.add_argument("--wait-for-period-end", action="store_true", help="期間還沒結束就等到結束後 30 秒再抓（routine 用，最多等 10 分鐘）")
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
