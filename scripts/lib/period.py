"""報告期間：上週三 00:00 到本週二 23:59:59（UTC+8）。

期間由「週二」這個日期決定，不由執行當下的時間決定。雲端排程在週二 23:59 啟動，
實際開跑常常晚幾分鐘、跨到週三凌晨；如果用 now() 往回推七天，晚一分鐘整期就錯開。
所以規則是：取執行當下（UTC+8）的日期，往回找最近的一個週二（當天是週二就是當天）。
週三任何時間跑，都落在前一天週二結束的那一期。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

TZ = timezone(timedelta(hours=8), "UTC+8")
TUESDAY = 1  # date.weekday()：週一是 0
# 排程在週二 23:59 啟動，比期間結束早不到一分鐘。抓資料要等到期間結束後，
# 最後幾十秒完成的卡才不會漏；多等 30 秒讓 Jira 寫完。超過 10 分鐘代表排程設錯，不乾等。
END_GRACE = timedelta(seconds=30)
MAX_WAIT = timedelta(minutes=10)


@dataclass(frozen=True)
class Period:
    week_ending: date  # 期間最後一天（週二）

    @property
    def start(self) -> datetime:
        return datetime.combine(self.week_ending - timedelta(days=6), time(0, 0), TZ)

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.week_ending + timedelta(days=1), time(0, 0), TZ)

    @property
    def week_id(self) -> str:
        year, week, _ = self.week_ending.isocalendar()
        return f"{year}-W{week:02d}"

    def contains(self, moment: datetime) -> bool:
        if moment.tzinfo is None:
            raise ValueError(f"時間沒有時區，無法判斷是否落在期間內：{moment!r}")
        return self.start <= moment < self.end_exclusive

    def as_dict(self) -> dict:
        return {
            "week": self.week_id,
            "week_ending": self.week_ending.isoformat(),
            "start": self.start.isoformat(),
            "end_exclusive": self.end_exclusive.isoformat(),
        }


def period_ending(week_ending: date) -> Period:
    if week_ending.weekday() != TUESDAY:
        raise ValueError(f"期間必須在週二結束，{week_ending.isoformat()} 是星期 {week_ending.isoweekday()}")
    return Period(week_ending)


def period_for(moment: datetime) -> Period:
    if moment.tzinfo is None:
        raise ValueError(f"時間沒有時區，無法換算 UTC+8：{moment!r}")
    local = moment.astimezone(TZ).date()
    back = (local.weekday() - TUESDAY) % 7
    return Period(local - timedelta(days=back))


def period_from_dict(data: dict) -> Period:
    period = period_ending(date.fromisoformat(data["week_ending"]))
    if period.week_id != data["week"]:
        raise ValueError(f"期間資料不一致：week_ending {data['week_ending']} 對應 {period.week_id}，檔案寫 {data['week']}")
    return period


def wait_for_end(period: Period, now: datetime) -> int:
    """回傳要等幾秒才能開始抓資料（期間結束＋END_GRACE）。已經過了就回 0。"""
    if now.tzinfo is None:
        raise ValueError(f"時間沒有時區：{now!r}")
    remaining = period.end_exclusive + END_GRACE - now
    if remaining <= timedelta(0):
        return 0
    if remaining > MAX_WAIT + END_GRACE:
        raise ValueError(f"離 {period.week_id} 期間結束還有 {remaining}，超過 {MAX_WAIT}：排程時間可能設錯，不等")
    return math.ceil(remaining.total_seconds())
