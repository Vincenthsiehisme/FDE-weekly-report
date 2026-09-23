"""把 metrics 與 narrative 排成靜態 HTML。

版面（2026-09-23 改版，決策在 vault 的 jira-weekly-report 專案 index）：讀者是主管，
分三個頁籤：持續累積、本週（預設）、團隊。vault 的週報範本只當資訊清單，不照它的節次排。
Decision Needed 不顯示；Kick off 欄位有填才顯示；Token 沒資料顯示「—」，不顯示 0。
頁籤靠少量 JS 切換，沒有 JS 時三個頁籤全部攤開。

所有文字一律經過 html.escape；數字只從 metrics 來。
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from .metrics import SCHEMA, UNASSIGNED_OWNER
from .period import TZ

DASH = "—"
STALE_DAYS = 8
# routine 會等到期間結束後才抓（prepare --wait-for-period-end）；手動提早跑的才會早於期間結束。
# 留一小時寬限，週二深夜手動補跑不標暫定
PROVISIONAL_MARGIN = timedelta(hours=1)
NO_TOKEN_SOURCE = "尚未接資料來源"
# Decision Needed 刻意不在這裡（使用者 2026-09-23 裁）
KICKOFF_FIELDS = (
    ("objective", "目標"),
    ("why", "為什麼重要"),
    ("before", "Before"),
    ("after", "After"),
    ("evidence", "Evidence"),
    ("next_milestone", "下個里程碑"),
)
TABS = (("cumulative", "持續累積"), ("week", "本週"), ("team", "團隊"))
DEFAULT_TAB = "week"


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def multiline(value) -> str:
    return esc(value).replace("\n", "<br>")


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


def fmt_card_hours(row: dict) -> str:
    """完成卡片的工時：父卡的點數不算（歸子卡），葉節點沒估點寫明，不寫成「—」。"""
    if not row["leaf"]:
        return "子卡計"
    return "未估點" if row["hours"] is None else fmt_hours(row["hours"])


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

    def title(self, key, summary) -> str:
        """專案或卡片的名稱：卡號（連結）＋摘要；未歸屬 epic 沒有卡號。"""
        return f'<span class="key">{self.issue(key)}</span> {esc(summary)}' if key else esc(summary)


def _pill(status) -> str:
    if not status:
        return ""
    return f'<span class="pill pill-{"done" if status == "Done" else "track"}">{esc(status)}</span>'


def _kpis(tiles: list) -> str:
    """tiles：(標籤, 值, 註記或 None)。值已經格式化成字串。"""
    items = []
    for label, value, note in tiles:
        muted = " muted" if value == DASH else ""
        note_html = f'<small>{esc(note)}</small>' if note else ""
        items.append(f'<li><span>{esc(label)}</span><strong class="{muted.strip()}">{esc(value)}</strong>{note_html}</li>')
    return f'<ul class="kpis">{"".join(items)}</ul>'


def _resource_tiles(totals: dict, prefix: str) -> list:
    unestimated = f'{totals["unestimated_completed"]} 張完成卡未估點' if totals["unestimated_completed"] else None
    return [
        (f"{prefix}預估工時", fmt_hours(totals["hours"]), unestimated),
        (f"{prefix} Token".strip(), fmt_tokens(totals["tokens"]), NO_TOKEN_SOURCE if totals["tokens"] is None else None),
        (f"{prefix}交付", str(totals["deliverables"]), None),
        (f"{prefix}完成卡片", str(totals["completed"]), None),
    ]


def _rows(headers: list, rows: list, empty: str) -> str:
    """一般資料列。第一欄是名稱，其餘是數字；窄螢幕時數字欄帶標籤排到名稱底下，不用橫捲。"""
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f"<span>{esc(h)}</span>" for h in headers)
    body = []
    for cls, cells in rows:
        first, rest = cells[0], cells[1:]
        nums = "".join(f'<span data-label="{esc(h)}">{c}</span>' for h, c in zip(headers[1:], rest))
        body.append(f'<div class="row {cls}"><div class="name">{first}</div>{nums}</div>')
    return f'<div class="rows" style="--cols:{len(headers) - 1}"><div class="row head">{head}</div>{"".join(body)}</div>'


def _progress(progress: dict) -> str:
    total, done = progress["total_cards"], progress["done_cards"]
    width = round(done / total * 100) if total else 0
    unestimated = f'，{progress["unestimated_cards"]} 張未估點' if progress["unestimated_cards"] else ""
    return (f'<div class="progress"><div class="bar"><span style="width:{width}%"></span></div>'
            f'<span>{done}/{total} 張完成 · {progress["done_points"]:g}/{progress["total_points"]:g}h 預估{unestimated}</span></div>')


def _kickoff(kickoff, page) -> str:
    if kickoff is None:
        return ""
    notice = ""
    if kickoff["modified_at"]:
        notice = (f'<p class="warn">Kick off {page.issue(kickoff["key"])} 鎖定後被修改'
                  f'（最近一次偵測 {esc(fmt_moment(kickoff["modified_at"][-1]))}），以下是 {esc(fmt_moment(kickoff["locked_at"]))} 的鎖定版本。</p>')
    sections = kickoff["sections"]
    filled = [(label, sections[key]) for key, label in KICKOFF_FIELDS if sections.get(key)]
    if not filled:
        return notice
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{multiline(value)}</dd></div>" for label, value in filled)
    return f'{notice}<dl class="kickoff">{items}</dl>'


def _doing(doing: list, page, with_project: bool) -> str:
    """進行中清單。張數跟「人」同一個口徑只算葉節點；進行中的父卡另外列，子卡都還沒動的父卡才不會消失。"""
    if not doing:
        return ""
    leaves = [r for r in doing if r["leaf"]]
    parents = [r for r in doing if not r["leaf"]]
    counts = "，".join(filter(None, [f"{len(leaves)} 張" if leaves else "", f"父卡 {len(parents)} 張" if parents else ""]))

    def where(r):
        return f'{esc(r["project"])} · ' if with_project else ""

    parent_items = "".join(f'<li class="parent">{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {where(r)}{esc(r["owner"])}</span>：{esc(r["current_state"])}'
                           f'{"，下一步 " + esc(r["next"]) if r["next"] else ""}</li>' for r in parents)
    leaf_items = "".join(f'<li>{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {where(r)}{esc(r["owner"])} · {esc(r["current_state"])}</span></li>'
                         for r in leaves)
    return f'<details class="doing"><summary>進行中 {counts}</summary><ul>{parent_items}{leaf_items}</ul></details>'


def _other_doing(metrics, page) -> str:
    """不屬於任何專案卡的進行中卡片：未歸屬 epic 本週沒有完成項目（沒有專案列），或 epic 先前已結案。"""
    shown = {e["id"] for e in metrics["epics"]}
    rows = [r for r in metrics["in_progress"] if r["group"] not in shown]
    if not rows:
        return ""
    return f"""
    <article class="card project">
      <div class="card-head"><h3>其他進行中的卡</h3></div>
      <p class="meta">不在上面任何專案裡：未歸屬 epic，或所屬 epic 已結案</p>
      {_doing(rows, page, with_project=True)}
    </article>"""


def _week_project(epic, narrative, in_progress, page) -> str:
    week = epic["week"]
    if epic["active"]:
        result = f'<p class="result">{esc(narrative["epic_results"][epic["id"]])}</p>'
    else:
        result = '<p class="result muted">本週沒有完成項目</p>'
    meta = ""
    if epic["key"]:
        due = esc(fmt_date(epic["due"])) if epic["due"] else "未設定"
        meta = f'<p class="meta">負責人 {esc(epic["owner"])} · 目標日 {due}</p>'

    doing_html = _doing([r for r in in_progress if r["group"] == epic["id"]], page, with_project=False)

    return f"""
    <article class="card project">
      <div class="card-head">
        <h3>{page.title(epic["key"], epic["summary"])}</h3>
        <div class="figures"><span><b>{esc(fmt_hours(week["hours"]))}</b> 預估</span><span>Token <b>{esc(fmt_tokens(week["tokens"]))}</b></span>{_pill(epic["status"])}</div>
      </div>
      {meta}
      {_progress(epic["progress"])}
      {result}
      {_kickoff(epic["kickoff"], page)}
      {doing_html}
    </article>"""


def _person_cells(person: dict, scope: str) -> list:
    data = person[scope]
    cells = [fmt_hours(data["hours"]), fmt_tokens(data["tokens"]), str(data["deliverables"])]
    cells = [esc(c) for c in cells]
    if scope == "week":
        cells.append(f'{person["in_progress"]["count"]} 張')
    else:
        cells.append(str(data["completed"]))
    return cells


def _person_name(person: dict) -> str:
    if person["name"] == UNASSIGNED_OWNER:
        return f'{esc(person["name"])} <small>卡片沒有執行人</small>'
    return esc(person["name"])


def _people_rows(people: list, scope: str) -> str:
    if scope == "week":
        shown = [p for p in people if p["week"]["completed"] or p["in_progress"]["count"]]
        headers = ["執行人", "預估工時", "Token", "交付", "進行中"]
    else:
        shown = [p for p in people if p["cumulative"]["completed"]]
        headers = ["執行人", "預估工時", "Token", "交付", "完成卡片"]
    rows = [("warn-row" if p["name"] == UNASSIGNED_OWNER else "", [_person_name(p)] + _person_cells(p, scope)) for p in shown]
    return _rows(headers, rows, "沒有人有完成或進行中的卡")


def _completed_by_delivery(metrics, page) -> str:
    groups = []
    for epic in metrics["epics"]:
        rows = [c for c in metrics["completed"] if c["group"] == epic["id"]]
        if not rows:
            continue
        items = []
        for d in epic["week"]["deliveries"]:
            cards = [c for c in metrics["completed"] if c["root"] == d["key"] and c["key"] != d["key"]]
            sub_count = f' · {len(cards)} 張子卡' if cards else ""
            head = (f'<span>{page.issue(d["key"])} {esc(d["summary"])} <span class="muted">· {esc(d["owner"])} · {esc(d["result"])}{sub_count}</span></span>'
                    f'<span class="nums">{esc(fmt_hours(d["hours"]))} · Token {esc(fmt_tokens(d["tokens"]))}</span>')
            if cards:
                sub = "".join(f'<li><span>{page.issue(c["key"])} {esc(c["summary"])} <span class="muted">· {esc(c["owner"])}</span></span>'
                              f'<span class="nums">{esc(fmt_card_hours(c))}</span></li>' for c in cards)
                items.append(f'<li><details><summary>{head}</summary><ul>{sub}</ul></details></li>')
            else:
                items.append(f"<li>{head}</li>")
        # 交付單位只由本週完成的葉節點組成；父卡本週才結、子卡先前就完成（或還沒完成）時不在裡面，另外列
        delivered = {d["key"] for d in epic["week"]["deliveries"]}
        for c in rows:
            if c["root"] not in delivered:
                items.append(f'<li><span>{page.issue(c["key"])} {esc(c["summary"])} <span class="muted">· {esc(c["owner"])} · 完成</span></span>'
                             f'<span class="nums">{esc(fmt_card_hours(c))} · Token {esc(fmt_tokens(c["tokens"]))}</span></li>')
        groups.append(f'<h4>{page.title(epic["key"], epic["summary"])}</h4><ul class="list">{"".join(items)}</ul>')
    # epic 先前已結案時沒有專案列，但底下的卡本週仍可能完成，完成卡片 KPI 也有算它們
    shown = {e["id"] for e in metrics["epics"]}
    others = "".join(
        f'<li><span>{page.issue(c["key"])} {esc(c["summary"])} <span class="muted">· {esc(c["project"])} · {esc(c["owner"])}</span></span>'
        f'<span class="nums">{esc(fmt_card_hours(c))} · Token {esc(fmt_tokens(c["tokens"]))}</span></li>'
        for c in metrics["completed"] if c["group"] not in shown)
    if others:
        groups.append(f'<h4>其他完成的卡 <span class="muted">· 所屬 epic 已結案</span></h4><ul class="list">{others}</ul>')
    return "".join(groups) or '<p class="muted">本週沒有完成的卡。</p>'


def _units(metrics) -> str:
    rows = [("", [esc(u["unit"]), str(u["deliverables"]), esc(fmt_hours(u["hours"]))]) for u in metrics["units"]]
    return _rows(["支援單位", "交付", "預估工時"], rows, "本週沒有完成的卡")


def _week_panel(metrics, narrative, page) -> str:
    highlights = narrative["highlights"]
    if highlights:
        points = f'<ol class="highlights">{"".join(f"<li>{esc(h)}</li>" for h in highlights)}</ol>'
    else:
        points = '<p class="highlights muted">本週沒有完成的卡。</p>'
    projects = "".join(_week_project(e, narrative, metrics["in_progress"], page) for e in metrics["epics"]) + _other_doing(metrics, page) \
        or '<p class="muted">沒有進行中的專案。</p>'
    return f"""
  {points}
  <h3>資源</h3>
  {_kpis(_resource_tiles(metrics["totals"], ""))}
  <h3>專案列表</h3>
  {projects}
  <h3>人</h3>
  {_people_rows(metrics["people"], "week")}
  <h3>依支援單位</h3>
  {_units(metrics)}
  <h3>完成</h3>
  {_completed_by_delivery(metrics, page)}"""


def _cumulative_panel(metrics, page) -> str:
    c = metrics["cumulative"]
    since = f'從 {esc(fmt_date(c["since"]))} 第一張完成卡起算，到本期結束。' if c["since"] else "到本期結束還沒有任何完成的卡。"
    projects = [(
        "",
        [f'{page.title(e["key"], e["summary"])} {_pill(e["status"])}',
         f'{e["progress"]["done_cards"]}/{e["progress"]["total_cards"]}',
         esc(fmt_hours(e["hours"])), esc(fmt_tokens(e["tokens"])),
         esc(fmt_date(e["due"])) if e["due"] else DASH],
    ) for e in c["epics"]]
    weeks = []
    for w in c["weeks"]:
        cards = [r for r in c["completed"] if r["week"] == w["week"]]
        items = "".join(
            f'<li><span>{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {esc(r["project"])} · {esc(r["owner"])}</span></span>'
            f'<span class="nums">{esc(fmt_card_hours(r))}</span></li>' for r in cards)
        weeks.append(f'<details><summary>{esc(w["week"])} <span class="muted">· {w["completed"]} 張 · 交付 {w["deliverables"]} · {esc(fmt_hours(w["hours"]))}</span></summary>'
                     f'<ul class="list">{items}</ul></details>')
    return f"""
  <p class="note">{since}</p>
  <h3>資源</h3>
  {_kpis(_resource_tiles(c["totals"], "累計"))}
  <h3>專案列表</h3>
  {_rows(["專案", "完成張數", "預估工時", "Token", "目標日"], projects, "還沒有任何專案")}
  <h3>人</h3>
  {_people_rows(metrics["people"], "cumulative")}
  <h3>完成</h3>
  {"".join(weeks) or '<p class="muted">還沒有任何完成的卡。</p>'}"""


def _split(by_project: list) -> str:
    parts = []
    for b in by_project:
        label = b["key"] or b["summary"]
        value = fmt_hours(b["hours"]) if b["hours"] or not b["unestimated"] else "未估點"
        parts.append(f"{esc(label)} {esc(value)}")
    return " · ".join(parts) or DASH


def _team_panel(metrics, page) -> str:
    completed = {r["key"]: r for r in metrics["cumulative"]["completed"]}
    doing = {r["key"]: r for r in metrics["in_progress"]}
    cards = []
    for p in metrics["people"]:
        week, cum, prog = p["week"], p["cumulative"], p["in_progress"]
        done_items = "".join(f'<li><span>{page.issue(k)} {esc(completed[k]["summary"])} <span class="muted">· {esc(completed[k]["project"])}</span></span>'
                             f'<span class="nums">{esc(fmt_card_hours(completed[k]))}</span></li>' for k in week["completed_keys"])
        doing_items = "".join(f'<li><span>{page.issue(k)} {esc(doing[k]["summary"])} <span class="muted">· {esc(doing[k]["project"])} · {esc(doing[k]["current_state"])}</span></span></li>'
                              for k in prog["keys"])
        lists = ""
        if done_items:
            lists += f'<details><summary>本週完成 {week["completed"]} 張</summary><ul class="list">{done_items}</ul></details>'
        if doing_items:
            lists += f'<details><summary>進行中 {prog["count"]} 張</summary><ul class="list">{doing_items}</ul></details>'
        unassigned = p["name"] == UNASSIGNED_OWNER
        note = '<p class="warn">這些卡在 Jira 上沒有 assignee，工時算不到人。</p>' if unassigned else ""
        cards.append(f"""
    <article class="card person{' warn-card' if unassigned else ''}">
      <div class="card-head"><h3>{esc(p["name"])}</h3><div class="figures"><span>進行中 <b>{prog["count"]}</b> 張</span></div></div>
      {note}
      <div class="split">
        <div><span class="label">本週</span><b>{esc(fmt_hours(week["hours"]))}</b> 預估 · Token {esc(fmt_tokens(week["tokens"]))} · 交付 {week["deliverables"]}<p>{_split(week["by_project"])}</p></div>
        <div><span class="label">累計</span><b>{esc(fmt_hours(cum["hours"]))}</b> 預估 · Token {esc(fmt_tokens(cum["tokens"]))} · 交付 {cum["deliverables"]}<p>{_split(cum["by_project"])}</p></div>
      </div>
      {lists}
    </article>""")
    note = f'<p class="note">Token {NO_TOKEN_SOURCE}，下面的 Token 都顯示「{DASH}」。</p>' if metrics["cumulative"]["totals"]["tokens"] is None else ""
    return note + ("".join(cards) or '<p class="muted">還沒有任何人有完成或進行中的卡。</p>')


def _checks(metrics, page) -> str:
    checks = metrics["checks"]
    if not checks:
        return ""
    items = "".join(f'<li>{page.issue(c["key"]) + "：" if c["key"] else ""}{esc(c["message"])}</li>' for c in checks)
    return f"""
<section id="checks">
  <details>
    <summary>資料待補（{len(checks)} 項）</summary>
    <ul class="checks">{items}</ul>
  </details>
</section>"""


def _week_link(week: str, in_reports_dir: bool) -> str:
    return f'{"" if in_reports_dir else "reports/"}{esc(week)}.html'


def _week_nav(weeks: list, current: str, in_reports_dir: bool) -> str:
    """weeks 由新到舊排。上一期是比較舊的那一期。"""
    i = weeks.index(current)
    older = f'<a href="{_week_link(weeks[i + 1], in_reports_dir)}">‹ {esc(weeks[i + 1])}</a>' if i + 1 < len(weeks) else ""
    newer = f'<a href="{_week_link(weeks[i - 1], in_reports_dir)}">{esc(weeks[i - 1])} ›</a>' if i > 0 else ""
    latest = '<a href="../index.html">最新一期</a>' if in_reports_dir else ""
    return f'<nav class="weeknav">{older}{latest}{newer}</nav>'


def _archive(weeks: list, current: str, in_reports_dir: bool) -> str:
    items = []
    for w in weeks:
        if w == current:
            items.append(f"<li><strong>{esc(w)}</strong></li>")
        else:
            items.append(f'<li><a href="{_week_link(w, in_reports_dir)}">{esc(w)}</a></li>')
    return f'<footer id="archive"><h2>歷史各期</h2><ul class="archive">{"".join(items)}</ul></footer>'


def _tabs(panels: dict) -> str:
    buttons = "".join(
        f'<button type="button" role="tab" id="tab-{key}" aria-controls="{key}" aria-selected="{str(key == DEFAULT_TAB).lower()}">{esc(label)}</button>'
        for key, label in TABS)
    sections = "".join(
        f'<section class="panel{" active" if key == DEFAULT_TAB else ""}" id="{key}" role="tabpanel" aria-labelledby="tab-{key}">'
        f'<h2 class="panel-title">{esc(label)}</h2>{panels[key]}</section>'
        for key, label in TABS)
    return f'<div class="tabs" role="tablist">{buttons}</div>{sections}'


def render(metrics: dict, narrative: dict, weeks: list, in_reports_dir: bool) -> str:
    if metrics.get("schema") != SCHEMA:
        raise ValueError(f"metrics 的 schema 是 {metrics.get('schema')}，頁面需要 {SCHEMA}：先用 weekly.py metrics <week> 重算")
    page = _Page(metrics)
    period = metrics["period"]
    start = fmt_date(period["start"])
    end = fmt_date(period["week_ending"])
    generated = metrics["generated_at"]
    provisional = datetime.fromisoformat(generated) < datetime.fromisoformat(period["end_exclusive"]) - PROVISIONAL_MARGIN
    banner = '<p class="warn">這一期的報告期間還沒結束，數字是產生當下的暫定值。</p>' if provisional else ""
    panels = {
        "cumulative": _cumulative_panel(metrics, page),
        "week": _week_panel(metrics, narrative, page),
        "team": _team_panel(metrics, page),
    }
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FDE Weekly Delivery Report {esc(period["week"])}</title>
<script>document.documentElement.classList.add("js")</script>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <div class="top">
    <p class="eyebrow">FDE 週報 · {esc(period["week"])}</p>
    {_week_nav(weeks, period["week"], in_reports_dir)}
  </div>
  <h1>{esc(start)} – {esc(end)}</h1>
  <p class="generated" id="generated" data-generated="{esc(generated)}">資料截至 {esc(fmt_moment(generated))}（UTC+8）· 工時為 Story point 預估值</p>
  <p class="warn" id="stale" hidden>這份報告超過 {STALE_DAYS} 天沒有更新，排程可能沒有成功執行。</p>
  {banner}
</header>
{_tabs(panels)}
{_checks(metrics, page)}
{_archive(weeks, period["week"], in_reports_dir)}
</main>
<script>
(function () {{
  var el = document.getElementById("generated");
  var t = Date.parse(el.getAttribute("data-generated"));
  if (!isNaN(t) && Date.now() - t > {STALE_DAYS} * 86400000) document.getElementById("stale").hidden = false;
  var tabs = [].slice.call(document.querySelectorAll('[role="tab"]'));
  function show(id) {{
    tabs.forEach(function (tab) {{
      var on = tab.getAttribute("aria-controls") === id;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      document.getElementById(tab.getAttribute("aria-controls")).classList.toggle("active", on);
    }});
  }}
  tabs.forEach(function (tab) {{
    tab.addEventListener("click", function () {{
      var id = tab.getAttribute("aria-controls");
      show(id);
      history.replaceState(null, "", "#" + id);
    }});
  }});
  function fromHash() {{
    var wanted = location.hash.slice(1);
    if (tabs.some(function (tab) {{ return tab.getAttribute("aria-controls") === wanted; }})) show(wanted);
  }}
  window.addEventListener("hashchange", fromHash);
  fromHash();
}})();
</script>
</body>
</html>
"""


CSS = """
:root{--bg:#f7f7f5;--surface:#fff;--soft:#f0f0ed;--text:#1d1d1f;--muted:#6e6e73;--line:#e3e3e0;--accent:#2f5bea;
--done-bg:#e3f4ea;--done-fg:#1b7a45;--bar:#2e9e62;--track-bg:#e8eefc;--track-fg:#2f5bea;--warn-bg:#fff4e0;--warn-fg:#8a5300;}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--surface:#1c1c1f;--soft:#26262a;--text:#ececef;--muted:#9a9aa1;--line:#2e2e33;
--accent:#7c9cff;--done-bg:#15321f;--done-fg:#6fd39a;--bar:#4cc184;--track-bg:#1b2544;--track-fg:#9fb6ff;--warn-bg:#3a2a0e;--warn-fg:#ffcf7a;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Noto Sans TC","PingFang TC","Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:32px 16px 72px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:26px;line-height:1.25;margin:2px 0 4px}
h2{font-size:18px;margin:32px 0 12px}
h3{font-size:15px;margin:28px 0 10px;color:var(--muted);font-weight:600}
h4{font-size:14px;margin:18px 0 6px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.eyebrow{margin:0;color:var(--muted);font-size:13px}
.weeknav{display:flex;gap:14px;font-size:13px}
.generated{margin:0;color:var(--muted);font-size:13px}
.muted{color:var(--muted)}
.note{color:var(--muted);font-size:13px;margin:12px 0 0}
.warn{background:var(--warn-bg);color:var(--warn-fg);padding:8px 12px;border-radius:8px;font-size:14px;margin:8px 0}
.tabs{display:none;gap:4px;border-bottom:1px solid var(--line);margin:24px 0 0;position:sticky;top:0;background:var(--bg);z-index:1}
.js .tabs{display:flex}
.tabs button{font:inherit;font-size:15px;background:none;border:0;border-bottom:2px solid transparent;padding:10px 12px;color:var(--muted);cursor:pointer}
.tabs button[aria-selected="true"]{color:var(--text);border-bottom-color:var(--text);font-weight:600}
.js .panel{display:none}.js .panel.active{display:block}
.js .panel-title{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.panel-title{border-top:1px solid var(--line);padding-top:24px}
.highlights{margin:20px 0 0;padding:12px 16px 12px 36px;background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:0 10px 10px 0}
.highlights li{margin:4px 0}
p.highlights{padding-left:16px}
.kpis{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.kpis li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;min-width:0}
.kpis li:nth-child(-n+2) strong{font-size:28px}
.kpis span{color:var(--muted);font-size:12px}
.kpis strong{font-size:22px;font-variant-numeric:tabular-nums;line-height:1.3}
.kpis small{color:var(--muted);font-size:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:0 0 10px}
.card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px 16px;flex-wrap:wrap}
.card-head h3{margin:0;color:var(--text);font-size:16px}
.key{font-size:13px;font-weight:400}
.figures{display:flex;align-items:center;gap:12px;font-size:13px;color:var(--muted);white-space:nowrap}
.figures b{color:var(--text);font-size:15px;font-variant-numeric:tabular-nums}
.meta{margin:2px 0 0;color:var(--muted);font-size:13px}
.progress{display:flex;align-items:center;gap:10px;margin:10px 0 6px;font-size:12px;color:var(--muted)}
.bar{flex:1;height:6px;background:var(--soft);border-radius:3px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--bar)}
.result{margin:6px 0}
.kickoff{margin:8px 0;padding:8px 12px;background:var(--soft);border-radius:8px;font-size:14px}
.kickoff div{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;padding:2px 0}
.kickoff dt{color:var(--muted);font-size:13px}.kickoff dd{margin:0}
details summary{cursor:pointer}
.list summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:0 8px;list-style:none}
.list summary::-webkit-details-marker{display:none}
.list summary::before{content:"▸";color:var(--muted)}
.list details[open]>summary::before{content:"▾"}
.card details{margin-top:8px;font-size:14px}
.card details summary{color:var(--muted)}
.card ul{margin:6px 0 0;padding-left:18px}
.doing .parent{list-style:none;margin-left:-18px;color:var(--muted)}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill-done{background:var(--done-bg);color:var(--done-fg)}.pill-track{background:var(--track-bg);color:var(--track-fg)}
.rows{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px}
.row{display:grid;grid-template-columns:minmax(0,1fr) repeat(var(--cols),minmax(64px,90px));gap:10px;padding:9px 0;border-bottom:1px solid var(--line);align-items:baseline;font-size:14px}
.row:last-child{border-bottom:0}
.row>span{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.row.head{font-size:12px;color:var(--muted);padding:8px 0}.row.head span:first-child{text-align:left}
.name small{color:var(--muted);font-size:12px;margin-left:4px}
.warn-row .name,.warn-row>span:nth-child(2){color:var(--warn-fg)}
.list{list-style:none;padding:0;margin:0}
.list li{display:flex;flex-wrap:wrap;justify-content:space-between;gap:0 12px;padding:6px 0;border-bottom:1px solid var(--line);font-size:14px}
.list li:last-child{border-bottom:0}
.list details{width:100%}
.list details ul{list-style:none;padding:0 0 0 16px;margin:4px 0 0}
.list details li{border-bottom:0;padding:3px 0}
.nums{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.panel>details{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin:0 0 8px}
.panel>details .list{margin-top:6px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0 0;font-size:14px}
.split>div{background:var(--soft);border-radius:8px;padding:8px 12px}
.split .label{display:block;color:var(--muted);font-size:12px}
.split b{font-size:18px;font-variant-numeric:tabular-nums}
.split p{margin:2px 0 0;color:var(--muted);font-size:13px}
.warn-card{border-color:var(--warn-fg)}
.warn-card h3{color:var(--warn-fg)}
#checks{margin-top:40px}
#checks summary{font-weight:600}
.checks{font-size:14px}
#archive{margin-top:32px;border-top:1px solid var(--line)}
.archive{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:8px 16px}
@media (max-width:640px){
  h1{font-size:22px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .split{grid-template-columns:1fr}
  .row{grid-template-columns:repeat(var(--cols),minmax(0,1fr));gap:4px 8px}
  .row .name{grid-column:1/-1}
  .row>span{text-align:left;font-size:13px}
  .row>span::before{content:attr(data-label);display:block;color:var(--muted);font-size:11px}
  .row.head{display:none}
  .kickoff div{grid-template-columns:1fr;gap:0}
  .list summary{grid-template-columns:auto minmax(0,1fr)}
  .list summary .nums{grid-column:2;margin-left:0}
  .tabs button{flex:1;padding:10px 4px}
}
"""
