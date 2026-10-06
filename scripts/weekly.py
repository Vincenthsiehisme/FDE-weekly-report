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
from lib.metrics import compute, drop_excluded_epics, kickoff_by_epic  # noqa: E402
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


def load_excluded() -> tuple:
    """回傳 (cards, epics)，兩個都是 {卡號: 理由}。cards 列的卡不算完成；epics 列的 epic 連同底下所有卡不列入週報。
    檔案必須存在，兩個欄位都要有，沒有要排除的就寫 {}。
    卡號格式錯了直接失敗：打錯的卡號不會排除任何東西，而且看起來跟沒排除一樣。"""
    data = read_json(EXCLUDED_FILE)
    where = EXCLUDED_FILE.relative_to(ROOT)
    found = []
    for field in ("cards", "epics"):
        entries = data.get(field)
        if not isinstance(entries, dict):
            raise ValueError(f"{where} 的 {field} 必須是 {{卡號: 理由}}（沒有要排除的就寫 {{}}）")
        for key, reason in entries.items():
            if not CARD_KEY.fullmatch(key):
                raise ValueError(f"{where} 的 {field} 卡號格式不對：{key!r}")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{where} 的 {field} {key} 沒有寫排除理由")
        found.append(entries)
    return tuple(found)


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


def token_source_exists() -> bool:
    """data/tokens/ 有沒有任何檔，含比這一期晚的。較晚的週才有檔時，這一期的累計是 None，
    但來源已經接上，頁面要顯示「—」欄而不是整欄隱藏（AC-4）。"""
    folder = DATA / "tokens"
    return folder.is_dir() and any(folder.glob("*.json"))


def _read_tokens(path: Path) -> dict:
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} 必須是 {{卡號: token 數}}")
    for key, value in data.items():
        issue_sort_key(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path.relative_to(ROOT)} 的 {key} 必須是非負整數，收到 {value!r}")
    return data



def excluded_view(snapshot: dict, *, fresh_fetch: bool) -> tuple:
    """回傳 (cards, epics, 留下的卡, checks)：cards、epics 是 config/excluded.json 的兩欄，留下的卡已扣掉整棵排除的 epic。
    compute_week 與 finalize 共用，metrics 與 narrative 驗證才會是同一個範圍。
    fresh_fetch 只在 prepare 剛抓完 Jira 時為 True：這時找不到設定的卡或 epic 就是卡號打錯或卡不見了，直接失敗。
    重算與 finalize 放行但印警告：卡可能比那一期的 snapshot 晚建，下一次 prepare 會再驗。"""
    period = period_from_dict(snapshot["period"])
    cards, epics = load_excluded()
    present = {i["key"] for i in snapshot["issues"]}
    missing_cards = sorted((k for k in cards if k not in present), key=issue_sort_key)
    if missing_cards and fresh_fetch:
        raise ValueError(f"config/excluded.json 的 cards 在這一期的 snapshot 裡找不到：{'、'.join(missing_cards)}（卡號打錯，或卡被刪除、搬到別的專案）")
    issues, checks = drop_excluded_epics(snapshot["issues"], epics, period, allow_missing=not fresh_fetch)
    missing = sorted((k for k in [*cards, *epics] if k not in present), key=issue_sort_key)
    if missing:
        print(f"注意：config/excluded.json 列的 {'、'.join(missing)} 不在 {period.week_id} 的 snapshot 裡；"
              "這一期放行（可能是之後才建的卡），卡號打錯的話下一次 prepare 會失敗", file=sys.stderr)
    return cards, epics, issues, checks


def compute_week(snapshot: dict, *, fresh_fetch: bool) -> tuple:
    """只算不寫：回傳 (metrics, {路徑: 要寫的內容})。任何一步失敗都不會留下檔案。fresh_fetch 見 excluded_view。"""
    period = period_from_dict(snapshot["period"])
    # 整棵排除要在 Kick off 鎖定之前：被排除的 epic 不鎖定、不列資料檢查。存檔的 snapshot 仍是原始的全部卡片。
    excluded_cards, _, issues, checks = excluded_view(snapshot, fresh_fetch=fresh_fetch)
    found, kickoff_checks = kickoff_by_epic(issues)
    checks += kickoff_checks
    seen_at = snapshot["fetched_at"]
    epics = {i["key"] for i in issues if i["level"] == EPIC_LEVEL}
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
    # Token 檔是另一個來源：被整棵排除的卡的 Token 也要拿掉，否則會算進總量、還被當成不明卡號列進資料檢查
    dropped = {i["key"] for i in snapshot["issues"]} - {i["key"] for i in issues}
    metrics = compute(dict(snapshot, issues=issues), period, load_units(), _without(load_tokens(period.week_id), dropped),
                      states, checks, excluded=excluded_cards,
                      cumulative_tokens=_without(load_cumulative_tokens(period.week_id), dropped),
                      token_source=token_source_exists())
    writes[week_dir(period.week_id) / "metrics.json"] = metrics
    return metrics, writes



def _without(tokens, keys: set):
    """None 代表沒有 Token 資料，照傳；有資料就拿掉 keys。"""
    return None if tokens is None else {k: v for k, v in tokens.items() if k not in keys}

def publish_week(snapshot: dict, *, fresh_fetch: bool) -> dict:
    """全部算完才一次寫出 snapshot、metrics 與 Kick off 鎖定檔。只有 prepare 剛抓完 Jira 時傳 fresh_fetch=True。"""
    metrics, writes = compute_week(snapshot, fresh_fetch=fresh_fetch)
    writes[week_dir(metrics["period"]["week"]) / "snapshot.json"] = snapshot
    for path in sorted(writes):
        write_json(path, writes[path])
    return metrics


def cmd_prepare(args) -> None:
    now = datetime.now(TZ)
    period = period_ending(date.fromisoformat(args.week_ending)) if args.week_ending else period_for(now)
    client = JiraClient(load_credentials())
    # 等期間結束前先驗一次憑證：錯的話現在就停，不要等到 00:00:30 才發現（fetch_snapshot 抓之前會再驗一次）
    client.verify()
    if args.wait_for_period_end:
        seconds = wait_for_end(period, now)
        if seconds:
            print(f"等 {seconds} 秒，到 {period.week_id} 期間結束後才抓資料", file=sys.stderr)
            time.sleep(seconds)
            now = datetime.now(TZ)
    snapshot = fetch_snapshot(client, period, now)
    metrics = publish_week(snapshot, fresh_fetch=True)
    t = metrics["totals"]
    print(f"snapshot：{sum(snapshot['counts'].values())} 張卡 {snapshot['counts']}", file=sys.stderr)
    print(f"本週完成 {t['completed']} 張、交付 {t['deliverables']} 項、{t['hours']:g}h；資料檢查 {len(metrics['checks'])} 項", file=sys.stderr)
    print(period.week_id)


def cmd_metrics(args) -> None:
    snapshot = read_json(week_dir(args.week) / "snapshot.json")
    if snapshot["period"]["week"] != args.week:
        raise ValueError(f"snapshot 的期間是 {snapshot['period']['week']}，不是 {args.week}")
    metrics = publish_week(snapshot, fresh_fetch=False)
    print(f"{args.week}：本週完成 {metrics['totals']['completed']} 張", file=sys.stderr)



def refuse_stale_metrics(week: str, metrics: dict, excluded_cards: dict, excluded_epics: dict) -> None:
    """metrics.json 是之後才把卡或 epic 加進排除前算的：頁面會照樣把排除的卡列成完成、照樣列出排除的專案，直接失敗。"""
    shown_epics = {e["key"] for e in metrics["epics"] + metrics["cumulative"]["epics"] if e["key"]}
    shown_cards = {c["key"] for c in metrics["completed"] + metrics["cumulative"]["completed"]}
    stale = sorted((set(excluded_epics) & shown_epics) | (set(excluded_cards) & shown_cards), key=issue_sort_key)
    if stale:
        raise ValueError(f"{week} 的 metrics.json 還算著 config/excluded.json 排除的卡或 epic：{'、'.join(stale)}，先跑 metrics {week} 重算")


def cmd_finalize(args) -> None:
    week = args.week
    folder = week_dir(week)
    snapshot = read_json(folder / "snapshot.json")
    metrics = read_json(folder / "metrics.json")
    narrative = read_json(folder / "narrative.json")
    # 可引用的卡號跟 metrics 同一個範圍：被整棵排除的卡不能經由文字回到公開頁面
    cards, epics, issues, _ = excluded_view(snapshot, fresh_fetch=False)
    refuse_stale_metrics(week, metrics, cards, epics)
    validate(narrative, metrics, {i["key"] for i in issues})

    reports = SITE / "reports"
    weeks = sorted({p.stem for p in reports.glob("*.html") if WEEK_ID.fullmatch(p.stem)} | {week}, reverse=True)
    # 首頁永遠是最新一期；補建舊的一期時也重繪，歷史清單才會多出那一期。重繪用的那份 metrics 也要檢查，寫檔前一起擋
    latest = weeks[0]
    index_metrics, index_narrative = metrics, narrative
    if latest != week:
        index_metrics = read_json(week_dir(latest) / "metrics.json")
        index_narrative = read_json(week_dir(latest) / "narrative.json")
        refuse_stale_metrics(latest, index_metrics, cards, epics)
    # 兩頁都畫完才寫：首頁畫失敗時不能留下已更新的週報頁
    report_html = render(metrics, narrative, weeks, in_reports_dir=True)
    index_html = render(index_metrics, index_narrative, weeks, in_reports_dir=False)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{week}.html").write_text(report_html, encoding="utf-8")
    (SITE / "index.html").write_text(index_html, encoding="utf-8")
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
