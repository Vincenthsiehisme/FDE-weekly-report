"""可運用人日：成員名單（config/team.json）× 工作日（扣 config/holidays.json 的國定假日、加補班日）。

2026-11-02 使用者要求「透過每個人每週可運用的時間，一天八小時來看可運用的總額」（plan 在 vault 的 jira-weekly-report · 本週頁產值與探索）。
可運用只拿來當產出的分母，不跟回填的實際工時比：實際工時是完工後回填的數字，同一人一週可以超過 40h（填錯），比了會誤導。
超過的人週列進資料檢查（使用者裁：代表有填錯）。請假還沒有資料來源（fde-leave-calendar 2026-10-28 才上線、routine 讀不到），不扣。

兩個檔都由人維護、走 PR；格式錯或缺年份直接失敗，不用預設值補：少一年假日會把整年的可運用多算，看起來卻正常。
"""
from __future__ import annotations

from datetime import date, timedelta

HOURS_PER_DAY = 8


def parse_calendar(data: dict, where: str) -> dict:
    """holidays.json → {"years": set, "off": set[date], "workdays": set[date]}。日期一定要落在 years 列的年份裡。"""
    years = data.get("years")
    if not isinstance(years, list) or not years or not all(isinstance(y, int) for y in years):
        raise ValueError(f"{where} 的 years 必須是有填年份的整數清單，例如 [2026]")
    out = {"years": set(years)}
    for field in ("off", "workdays"):
        entries = data.get(field)
        if not isinstance(entries, dict):
            raise ValueError(f"{where} 的 {field} 必須是 {{日期: 名稱}}（沒有就寫 {{}}）")
        days = set()
        for text, name in entries.items():
            try:
                day = date.fromisoformat(text)
            except ValueError:
                raise ValueError(f"{where} 的 {field} 日期格式不對：{text!r}，要寫 YYYY-MM-DD") from None
            if day.year not in out["years"]:
                raise ValueError(f"{where} 的 {field} {text} 不在 years 列的年份裡")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{where} 的 {field} {text} 沒有寫名稱")
            days.add(day)
        out[field] = days
    return out


def parse_team(data: dict, where: str) -> list:
    """team.json → [{"name", "since": date, "until": date|None}]。名字重複、日期顛倒直接失敗。"""
    members = data.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError(f"{where} 的 members 必須是有成員的清單")
    out, seen = [], set()
    for m in members:
        name = m.get("name") if isinstance(m, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where} 有一位成員沒有 name")
        if name in seen:
            raise ValueError(f"{where} 的 {name} 出現兩次")
        seen.add(name)
        try:
            since = date.fromisoformat(m.get("since") or "")
            until = date.fromisoformat(m["until"]) if m.get("until") is not None else None
        except (ValueError, TypeError):
            raise ValueError(f"{where} 的 {name} since／until 要寫 YYYY-MM-DD（until 還在職就寫 null）") from None
        if until is not None and until < since:
            raise ValueError(f"{where} 的 {name} until 早於 since")
        out.append({"name": name, "since": since, "until": until})
    return out


def workdays(first: date, last: date, calendar: dict) -> list:
    """first～last（含）之間的工作日：平日扣 off、週末加 workdays。碰到 years 沒列的年份直接失敗。"""
    out, day = [], first
    while day <= last:
        if day.year not in calendar["years"]:
            raise ValueError(f"config/holidays.json 沒有 {day.year} 年的國定假日：照人事行政總處的辦公日曆表補上那一年再重算")
        if (day.weekday() < 5 and day not in calendar["off"]) or day in calendar["workdays"]:
            out.append(day)
        day += timedelta(days=1)
    return out


def member_days(member: dict, days: list) -> int:
    """這位成員在 days 裡在職的工作日數。"""
    return sum(1 for d in days if d >= member["since"] and (member["until"] is None or d <= member["until"]))
