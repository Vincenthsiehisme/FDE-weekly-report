"""把 metrics 與 narrative 排成靜態 HTML。版面照 vault 的 jira-weekly-report · 週報範本，
拿掉 Attention 與 Blocked / Pending（使用者 2026-09-23 裁），另加依支援單位的表與資料檢查。

所有文字一律經過 html.escape；數字只從 metrics 來。
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from .kickoff import SECTIONS
from .period import TZ

MISSING = "未填"
DASH = "—"
STALE_DAYS = 8
# 排程在週二 23:59 跑，產生時間本來就比期間結束早一分鐘；早超過一小時才算期間還沒走完
PROVISIONAL_MARGIN = timedelta(hours=1)
SECTION_LABELS = dict(SECTIONS)


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def multiline(value) -> str:
    return esc(value).replace("\n", "<br>") if value else f'<span class="muted">{MISSING}</span>'


def fmt_hours(value) -> str:
    return DASH if value is None else f"{value:g}h"


def fmt_tokens(value) -> str:
    if value is None:
        return DASH
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def fmt_date(iso: str) -> str:
    return iso[:10].replace("-", "/")


def fmt_moment(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y/%m/%d %H:%M")


class _Page:
    def __init__(self, metrics: dict):
        self.site = metrics["site"]

    def issue(self, key) -> str:
        if not key:
            return DASH
        return f'<a href="https://{esc(self.site)}/browse/{esc(key)}">{esc(key)}</a>'


def _table(headers: list, rows: list, numeric: set, empty: str = "無") -> str:
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="muted">{esc(empty)}</td></tr>'
    else:
        body = "".join(
            "<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{cell}</td>' for i, cell in enumerate(row)) + "</tr>"
            for row in rows
        )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _status(value) -> str:
    if not value:
        return DASH
    return f'<span class="pill pill-{"done" if value == "Done" else "track"}">{esc(value)}</span>'


def _overview(metrics, narrative, page) -> str:
    highlights = narrative["highlights"]
    if highlights:
        items = "".join(f"<li><strong>{esc(h)}</strong></li>" for h in highlights)
        points = f"<ol class=\"highlights\">{items}</ol>"
    else:
        points = '<p class="muted">本週沒有完成的卡。</p>'

    rows = []
    for epic in metrics["epics"]:
        result = narrative["epic_results"].get(epic["id"]) if epic["active"] else None
        kickoff = epic["kickoff"]
        nxt = kickoff["sections"]["next_milestone"] if kickoff else None
        name = f'{page.issue(epic["key"])} {esc(epic["summary"])}' if epic["key"] else esc(epic["summary"])
        rows.append([
            name,
            esc(result) if result else '<span class="muted">本週無完成項目</span>',
            esc(epic["owner"] or DASH),
            fmt_hours(epic["week"]["hours"]),
            fmt_tokens(epic["week"]["tokens"]),
            _status(epic["status"]),
            multiline(nxt) if epic["key"] else DASH,
        ])
    t = metrics["totals"]
    unestimated = f'（其中 {t["unestimated_completed"]} 張未估點）' if t["unestimated_completed"] else ""
    return f"""
<section id="overview">
  <h2><span class="num-label">0</span>Weekly Overview</h2>
  <h3>本週重點</h3>
  {points}
  <h3>Delivery Overview</h3>
  {_table(["Project", "本週成果", "Owner", "Hours", "Token", "Status", "Next"], rows, {3, 4}, "沒有進行中的專案")}
  <h3>本週投入</h3>
  <ul class="stats">
    <li><span>Projects</span><strong>{t["projects"]}</strong></li>
    <li><span>Deliverables</span><strong>{t["deliverables"]}</strong></li>
    <li><span>Completed Jira</span><strong>{t["completed"]}</strong></li>
    <li><span>Total Hours</span><strong>{fmt_hours(t["hours"])}</strong></li>
    <li><span>Total Token</span><strong>{fmt_tokens(t["tokens"])}</strong></li>
  </ul>
  <p class="note">Hours 為 Story point estimate（預估），只計葉節點卡片{esc(unestimated)}。</p>
</section>"""


def _field(label: str, value_html: str) -> str:
    # 值要包一層：li 是 grid，連結與文字直接放進去會各自變成一個 grid 項目、一個一行
    return f"<li><span class=\"field\">{esc(label)}</span><div>{value_html}</div></li>"


def _delivery(epic, narrative, page) -> str:
    week, progress, kickoff = epic["week"], epic["progress"], epic["kickoff"]
    title = f'{page.issue(epic["key"])}｜{esc(epic["summary"])}' if epic["key"] else esc(epic["summary"])
    sections = kickoff["sections"] if kickoff else {key: None for key, _ in SECTIONS}

    notice = ""
    if epic["key"] and kickoff is None:
        notice = '<p class="warn">這個 epic 沒有 Kick off 卡，Context 與成果驗證未填。</p>'
    elif kickoff and kickoff["modified_at"]:
        notice = (f'<p class="warn">Kick off {page.issue(kickoff["key"])} 鎖定後被修改'
                  f'（最近一次偵測 {esc(fmt_moment(kickoff["modified_at"][-1]))}），以下顯示 {esc(fmt_moment(kickoff["locked_at"]))} 的鎖定版本。</p>')

    percent = f'（{progress["percent"]}%）' if progress["percent"] is not None else ""
    stage = (f'完成 {progress["done_cards"]}/{progress["total_cards"]} 張卡、'
             f'{progress["done_points"]:g}/{progress["total_points"]:g} 點{percent}')
    if progress["unestimated_cards"]:
        stage += f'，{progress["unestimated_cards"]} 張未估點'

    rows = [[f'{page.issue(d["key"])} {esc(d["summary"])}', esc(d["owner"]), fmt_hours(d["hours"]), fmt_tokens(d["tokens"]), esc(d["result"])]
            for d in week["deliveries"]]
    validated = "、".join(page.issue(k) for k in week["completed_keys"]) or DASH

    context = ""
    verify = _field("Validated", validated)
    nxt = ""
    if epic["key"]:
        context = f"""
    <h4>Context</h4>
    <ul class="fields">
      {_field("Objective", multiline(sections["objective"]))}
      {_field("Current Stage", esc(stage))}
      {_field("Why It Matters", multiline(sections["why"]))}
    </ul>"""
        verify = (_field("Before", multiline(sections["before"])) + _field("After", multiline(sections["after"]))
                  + verify + _field("Evidence", multiline(sections["evidence"])))
        nxt = f"""
    <h4>Next</h4>
    <ul class="fields">
      {_field("Next Milestone", multiline(sections["next_milestone"]))}
      {_field("Owner", esc(epic["owner"]))}
      {_field("Target", esc(fmt_date(epic["due"])) if epic["due"] else '<span class="muted">未設定</span>')}
    </ul>"""

    return f"""
  <article class="delivery">
    <h3>{title}</h3>
    {notice}
    <h4>結果</h4>
    <p class="result"><strong>{esc(narrative["epic_results"][epic["id"]])}</strong></p>{context}
    <h4>本週交付</h4>
    {_table(["Delivery", "Owner", "Hours", "Token", "Result"], rows, {2, 3})}
    <h4>成果驗證</h4>
    <ul class="fields">{verify}</ul>{nxt}
  </article>"""


def _resources(metrics) -> str:
    owners = metrics["owners"]
    rows = [[esc(o["owner"]), esc("、".join(o["deliveries"])), str(o["deliverables"]), fmt_hours(o["hours"]), fmt_tokens(o["tokens"])] for o in owners]
    if owners:
        # Total 用全週的值，不加總各列：同一張父卡的子任務分給兩個人時各列都會算一項，
        # Token 也可能記在 epic 或進行中的卡上，不屬於任何人的已完成卡片
        t = metrics["totals"]
        rows.append(["<strong>Total</strong>", "",
                     f'<strong>{t["deliverables"]}</strong>',
                     f'<strong>{fmt_hours(t["hours"])}</strong>',
                     f'<strong>{fmt_tokens(t["tokens"])}</strong>'])
    units = [[esc(u["unit"]), str(u["deliverables"]), fmt_hours(u["hours"])] for u in metrics["units"]]
    return f"""
<section id="resources">
  <h2><span class="num-label">2</span>Delivery &amp; Resource View</h2>
  {_table(["Owner", "主要交付", "Deliverables", "Hours", "Token"], rows, {2, 3, 4})}
  <h3>依支援單位</h3>
  {_table(["支援單位", "Deliverables", "Hours"], units, {1, 2})}
</section>"""


def _tracking(metrics, page) -> str:
    done = [[page.issue(c["key"]), esc(c["project"]), esc(c["owner"]), esc(c["summary"]),
             fmt_hours(c["hours"]) if c["leaf"] else '<span class="muted">子卡計</span>', fmt_tokens(c["tokens"])]
            for c in metrics["completed"]]
    doing = [[page.issue(c["key"]), esc(c["project"]), esc(c["owner"]), esc(c["summary"]), esc(c["current_state"]),
              esc(c["next"] or DASH), fmt_hours(c["hours"]), fmt_tokens(c["tokens"])]
             for c in metrics["in_progress"]]
    return f"""
<section id="tracking">
  <h2><span class="num-label">3</span>Jira Tracking</h2>
  <h3>Completed</h3>
  {_table(["Jira", "Project", "Owner", "Item", "Hours", "Token"], done, {4, 5})}
  <h3>In Progress</h3>
  {_table(["Jira", "Project", "Owner", "Item", "Current State", "Next", "Hours", "Token"], doing, {6, 7})}
</section>"""


def _checks(metrics, page) -> str:
    checks = metrics["checks"]
    if not checks:
        return ""
    items = "".join(f'<li>{page.issue(c["key"]) + "：" if c["key"] else ""}{esc(c["message"])}</li>' for c in checks)
    return f"""
<section id="checks">
  <details>
    <summary>資料檢查（{len(checks)} 項）</summary>
    <ul class="checks">{items}</ul>
  </details>
</section>"""


def _archive(weeks: list, current: str, in_reports_dir: bool) -> str:
    prefix = "" if in_reports_dir else "reports/"
    items = []
    for w in weeks:
        if w == current:
            items.append(f"<li><strong>{esc(w)}</strong></li>")
        else:
            items.append(f'<li><a href="{prefix}{esc(w)}.html">{esc(w)}</a></li>')
    items = "".join(items)
    return f'<section id="archive"><h2>歷史各期</h2><ul class="archive">{items}</ul></section>'


def render(metrics: dict, narrative: dict, weeks: list, in_reports_dir: bool) -> str:
    page = _Page(metrics)
    period = metrics["period"]
    start = fmt_date(period["start"])
    end = fmt_date(period["week_ending"])
    generated = metrics["generated_at"]
    provisional = datetime.fromisoformat(generated) < datetime.fromisoformat(period["end_exclusive"]) - PROVISIONAL_MARGIN
    banner = '<p class="warn">這一期的報告期間還沒結束，數字是產生當下的暫定值。</p>' if provisional else ""
    deliveries = "".join(_delivery(e, narrative, page) for e in metrics["epics"] if e["active"]) or '<p class="muted">本週沒有交付。</p>'
    home = '<a href="../index.html">最新一期</a>' if in_reports_dir else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FDE Weekly Delivery Report {esc(period["week"])}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <p class="eyebrow">FDE · {esc(period["week"])} {home}</p>
  <h1>Weekly Delivery Report</h1>
  <p class="period">Period：{esc(start)} – {esc(end)}</p>
  <p class="generated" id="generated" data-generated="{esc(generated)}">資料截至 {esc(fmt_moment(generated))}（UTC+8）</p>
  <p class="warn" id="stale" hidden>這份報告超過 {STALE_DAYS} 天沒有更新，排程可能沒有成功執行。</p>
  {banner}
</header>
{_overview(metrics, narrative, page)}
<section id="deliveries">
  <h2><span class="num-label">1</span>Key Deliveries</h2>
  {deliveries}
</section>
{_resources(metrics)}
{_tracking(metrics, page)}
{_checks(metrics, page)}
{_archive(weeks, period["week"], in_reports_dir)}
</main>
<script>
(function () {{
  var el = document.getElementById("generated");
  var t = Date.parse(el.getAttribute("data-generated"));
  if (!isNaN(t) && Date.now() - t > {STALE_DAYS} * 86400000) document.getElementById("stale").hidden = false;
}})();
</script>
</body>
</html>
"""


CSS = """
:root{--bg:#f7f7f5;--surface:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#e3e3e0;--accent:#2f5bea;
--done-bg:#e3f4ea;--done-fg:#1b7a45;--track-bg:#e8eefc;--track-fg:#2f5bea;--warn-bg:#fff4e0;--warn-fg:#8a5300;}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--surface:#1c1c1f;--text:#ececef;--muted:#9a9aa1;--line:#2e2e33;
--accent:#7c9cff;--done-bg:#15321f;--done-fg:#6fd39a;--track-bg:#1b2544;--track-fg:#9fb6ff;--warn-bg:#3a2a0e;--warn-fg:#ffcf7a;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Noto Sans TC","PingFang TC","Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 16px 80px}
header{margin-bottom:32px}
h1{font-size:30px;line-height:1.2;margin:4px 0 8px}
h2{font-size:21px;margin:48px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
h3{font-size:17px;margin:28px 0 10px}
h4{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:20px 0 6px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.eyebrow{margin:0;color:var(--muted);font-size:13px;display:flex;gap:12px}
.period{margin:0;font-weight:600}
.generated{margin:2px 0 0;color:var(--muted);font-size:13px}
.num-label{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:7px;background:var(--text);color:var(--bg);font-size:14px}
.muted{color:var(--muted)}
.note{color:var(--muted);font-size:13px}
.warn{background:var(--warn-bg);color:var(--warn-fg);padding:8px 12px;border-radius:8px;font-size:14px}
.highlights{padding-left:22px}.highlights li{margin:6px 0}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:680px;font-size:14px}
th,td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill-done{background:var(--done-bg);color:var(--done-fg)}.pill-track{background:var(--track-bg);color:var(--track-fg)}
.stats{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stats li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column}
.stats span{color:var(--muted);font-size:12px}.stats strong{font-size:22px;font-variant-numeric:tabular-nums}
.delivery{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:4px 20px 18px;margin:16px 0}
.result{margin:4px 0}
.fields{list-style:none;padding:0;margin:0}.fields li{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:4px 0}
.field{color:var(--muted);font-size:13px}
.checks{font-size:14px}
details summary{cursor:pointer;font-weight:600}
.archive{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:8px 16px}
@media (max-width:640px){.fields li{grid-template-columns:1fr;gap:0}h1{font-size:25px}}
"""
