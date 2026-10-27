"""把 metrics 與 narrative 排成靜態 HTML。

版面（2026-09-23 改版，決策在 vault 的 jira-weekly-report 專案 index）：讀者原本是主管（2026-10-28 改成團隊負責人本人，見下），
分三個頁籤：總覽（2026-10-07 由「持續累積」改名）、本週（預設）、團隊。vault 的週報範本只當資訊清單，不照它的節次排。
總覽依序是資源（多一格 epic 數）、依 label、團隊、專案列表、完成；專案依 label 分段、每列可展開看 Kick off 目標五段（一段都沒填就不給展開）；Kick off 鎖定後被修改只列在資料檢查，頁面不顯示提示（2026-10-07 使用者裁）。
Decision Needed 不顯示；Kick off 欄位有填才顯示；Token 一個檔都沒有時整欄不顯示、資源區只留一行說明，
有檔但沒記錄的卡顯示「—」，不顯示 0。目標日不顯示（2026-10-06 使用者裁：不是當前重點；metrics 仍帶 due）。
工時是實際花費時數（2026-09-30 使用者確認：Story point estimate 由執行人完工後回填），頁面一律叫「實際工時」；
頁尾的名詞定義是每個詞的口徑正本，改算法時同一次改那裡。
頁籤靠少量 JS 切換，沒有 JS 時三個頁籤全部攤開。
視覺化改版（2026-10-07 使用者裁，plan 在 vault 的 jira-weekly-report · 視覺化改版）：本週頁的專案是一張一覽表，
不是一張張卡；進度條分三段（之前完成｜本週完成｜未完成），總覽與本週同一條；依 label 是橫條；團隊表的完成卡片畫長條、
工時只給數字（每人填工時的粒度不同，畫成長度比的是填法）；完成清單預設收合；專案投入併成總覽的「人」欄；
Kick off 目標五段只在總覽展開看。圖都是 inline SVG 或純 CSS，沒有 JS 也顯示。
團隊績效評估（2026-10-28 使用者鎖定，plan 在 vault 的 jira-weekly-report · 團隊績效評估）：讀者改成團隊負責人本人管理用。
本週頁是三個數字（停滯專案、有完成的專案、照流程走）、四格圖（專案地圖、投入比例、產出、資料健康）、
這週要你出手的地方、人卡、收合的專案一覽；本週重點不再獨立列。人一律照名字排、只跟自己比，三個頁籤都不放人與人並排的長條或表。
同日第二輪：補登不算（metrics schema 8 拿掉）；改桌機寬版（主欄 1280px，第一屏以 1440×900 量），圖放大、人卡多欄、
人卡的小長條每根標週次與張數，團隊頁「負責過」拆成負責中與已結案（收合）。

所有文字一律經過 html.escape；數字只從 metrics 來。
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from .metrics import SCHEMA, UNASSIGNED_EPIC_LABEL, UNASSIGNED_OWNER
from .period import TZ
from .snapshot import parse_jira_time

DASH = "—"
STALE_DAYS = 8
# routine 會等到期間結束後才抓（prepare --wait-for-period-end）；手動提早跑的才會早於期間結束。
# 留一小時寬限，週二深夜手動補跑不標暫定
PROVISIONAL_MARGIN = timedelta(hours=1)
NO_TOKEN_SOURCE = "尚未接資料來源"
UNFILLED = "未填工時"
WEEKDAYS = "一二三四五六日"
# Decision Needed 刻意不在這裡（使用者 2026-09-23 裁）
KICKOFF_FIELDS = (
    ("objective", "目標"),
    ("why", "為什麼重要"),
    ("before", "Before"),
    ("after", "After"),
    ("evidence", "Evidence"),
    ("next_milestone", "下個里程碑"),
)
# 總覽的 id 留 cumulative：網址 #cumulative 已經發出去過，改名不改連結
TABS = (("cumulative", "總覽"), ("week", "本週"), ("team", "團隊"))
# 總覽專案展開後的效益說明：Kick off 鎖定的目標五段（2026-10-07 使用者裁），下個里程碑留在本週頁
BENEFIT_FIELDS = KICKOFF_FIELDS[:5]
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
    """完成卡片的工時：父卡自己的數字不算（計在子卡），葉節點沒填寫明，不寫成「—」。"""
    if not row["leaf"]:
        return "計在子卡"
    return UNFILLED if row["hours"] is None else fmt_hours(row["hours"])


def fmt_date(iso: str) -> str:
    return iso[:10].replace("-", "/")


def fmt_day(iso: str) -> str:
    return f"{fmt_date(iso)}（{WEEKDAYS[datetime.fromisoformat(iso[:10]).weekday()]}）"


def fmt_moment(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y/%m/%d %H:%M")


class _Page:
    def __init__(self, metrics: dict):
        self.site = metrics["site"]
        # data/tokens/ 一個檔都沒有時整欄不顯示，資源區只留一行說明；有任一檔就回到「—」規則，不會安靜少顯示
        # （主管版二次優化 AC-4，2026-09-27 使用者裁）。不從累計 Token 推：較晚的週才有檔時累計是 None，但來源已經接上
        self.tokens = metrics["token_source"]

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
    """tiles：(標籤, 值, 註記或 None, 是否放大)。值已經格式化成字串；用量（工時、Token）放大，張數不放大。"""
    items = []
    for label, value, note, big in tiles:
        muted = " muted" if value == DASH else ""
        note_html = f'<small>{esc(note)}</small>' if note else ""
        items.append(f'<li{" class=big" if big else ""}><span>{esc(label)}</span><strong class="{muted.strip()}">{esc(value)}</strong>{note_html}</li>')
    return f'<ul class="kpis" style="--kpis:{len(tiles)}">{"".join(items)}</ul>'


def _resources(totals: dict, prefix: str, hours_label: str, page, epics=None) -> str:
    """資源 KPI：工時、Token、完成卡片。Token 一個檔都沒有時不放 Token 格，也不留說明
    （2026-10-07 使用者裁，取代 2026-09-27「資源區留一行」：AI 用量整合已擱置，沒有可預期的接入日）。
    epics 只有總覽給：專案列表裡的 epic 數，多一格（2026-10-07 使用者要求）。"""
    unfilled = f'{totals["unestimated_completed"]} 張完成卡{UNFILLED}' if totals["unestimated_completed"] else None
    tiles = [(hours_label, fmt_hours(totals["hours"]), unfilled, True)]
    if page.tokens:
        tiles.append((f"{prefix} Token".strip(), fmt_tokens(totals["tokens"]), NO_TOKEN_SOURCE if totals["tokens"] is None else None, True))
    tiles.append((f"{prefix}完成卡片", str(totals["completed"]), None, False))
    if epics is not None:
        tiles.append(("epic 數", str(epics), None, False))
    return _kpis(tiles)


def _token_suffix(value, page) -> str:
    """完成清單與團隊卡裡接在工時後面的「 · Token N」；整欄隱藏時是空字串。"""
    return f" · Token {esc(fmt_tokens(value))}" if page.tokens else ""


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


def _bar(progress: dict, week_done: int) -> str:
    """三段進度條：之前完成｜本週完成（深色）｜未完成。總覽與本週頁同一條，本週新增畫在同一個量裡，不另開一個類別。
    week_done 是本週完成的葉節點數，先前已結案的 epic 本週沒有專案列，傳 0。"""
    total, done = progress["total_cards"], progress["done_cards"]
    if not total:
        return '<span class="bar-empty">尚無卡片</span>'
    before = round((done - week_done) / total * 100, 1)
    week = round(week_done / total * 100, 1)
    label = f"{done}/{total} 張完成" + (f"，本週 {week_done} 張" if week_done else "")
    return (f'<div class="bar" role="img" aria-label="{label}"><span style="width:{before:g}%"></span>'
            f'{f"<span class=wk style=width:{week:g}%></span>" if week_done else ""}</div>')


def _progress_cell(progress: dict, week_done: int) -> str:
    return f'<span class="prog" data-label="進度">{_bar(progress, week_done)}<span class="num">{progress["done_cards"]}/{progress["total_cards"]}</span></span>'


def _kickoff(kickoff, fields=KICKOFF_FIELDS) -> str:
    """Kick off 有填的段。鎖定後被修改只列在資料檢查，頁面不顯示提示（2026-10-07 使用者裁）。"""
    if kickoff is None:
        return ""
    sections = kickoff["sections"]
    filled = [(label, sections[key]) for key, label in fields if sections.get(key)]
    if not filled:
        return ""
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{multiline(value)}</dd></div>" for label, value in filled)
    return f'<dl class="kickoff">{items}</dl>'


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


# 數字欄固定寬、表頭跟列同一組欄（2026-10-07 總覽 UI 盤點 U1）：Token 有檔時列多一格，表頭也要多一格，不然整排錯位
TOKEN_HEAD = '<span class="num tok">Token</span>'


def _week_table_head(page) -> str:
    return ('<div class="proj head"><span class="name">專案</span><span class="owner">負責人</span><span class="prog">進度（深色＝本週）</span>'
            f'<span class="plus">本週</span><span class="num">本週工時</span>{TOKEN_HEAD if page.tokens else ""}'
            '<span class="sigs">訊號</span><span class="next">下一步</span></div>')


NO_COMPLETION_SIGNAL = "本週無完成"


def _week_row(epic, narrative, metrics, page) -> str:
    """本週一覽表的一列（2026-10-07 使用者裁，取代一張張專案卡）：名稱、負責人、三段進度條、本週 +N、本週工時、訊號、下一步；
    展開看 AI 寫的結果句與進行中的卡。Kick off 目標五段不在這一頁，總覽展開看。
    負責人是本週有產出的人（跟工時同一個口徑），本週沒人產出就寫 epic 負責人。
    訊號只陳述事實：停滯 N 天（metrics 的 stalled，2026-10-28），否則本週無完成。"""
    week = epic["week"]
    staffing = {s["id"]: s for s in metrics["staffing"]}
    people = staffing[epic["id"]]["week"]["people"] if epic["id"] in staffing else []
    owner = "、".join(people) if people else (epic["owner"] or UNASSIGNED_OWNER)
    plus = f'<b>+{week["completed_count"]}</b>' if week["completed_count"] else '<span class="muted">0</span>'
    signals = ""
    if epic["stalled"]:
        signals = f'<span class="sig">停滯 {epic["idle_days"]} 天</span>'
    elif epic["key"] and epic["status"] != "Done" and not week["completed_count"]:
        signals = f'<span class="sig">{NO_COMPLETION_SIGNAL}</span>'
    nxt = ""
    if epic["kickoff"] and epic["kickoff"]["sections"].get("next_milestone"):
        nxt = epic["kickoff"]["sections"]["next_milestone"].strip().splitlines()[0]
    token = f'<span class="num tok" data-label="Token">Token {esc(fmt_tokens(week["tokens"]))}</span>' if page.tokens else ""
    head = (f'<span class="name">{page.title(epic["key"], epic["summary"])} {_pill(epic["status"])}</span>'
            f'<span class="owner" data-label="負責人">{esc(owner)}</span>{_progress_cell(epic["progress"], week["completed_count"])}'
            f'<span class="plus" data-label="本週">{plus}</span><span class="num" data-label="本週工時">{esc(fmt_hours(week["hours"]))}</span>{token}'
            f'<span class="sigs" data-label="訊號">{signals}</span><span class="next" data-label="下一步">{esc(nxt) if nxt else DASH}</span>')
    if epic["active"]:
        result = f'<p class="result">{esc(narrative["epic_results"][epic["id"]])}</p>'
    else:
        result = '<p class="result muted">本週沒有完成項目</p>'
    doing_html = _doing([r for r in metrics["in_progress"] if r["group"] == epic["id"]], page, with_project=False)
    return f'<details class="proj"><summary class="proj-head">{head}</summary><div class="proj-body">{result}{doing_html}</div></details>'


def _week_table(metrics, narrative, page) -> str:
    """本週的專案依 label 分段，段的順序跟 label 表一樣，未歸屬 epic 放最後；段首帶本週工時（取 label 表，不另算）。"""
    epics = metrics["epics"]
    if not epics:
        return '<p class="muted">沒有進行中的專案。</p>'
    label_of = {e["id"]: e["label"] for e in metrics["cumulative"]["epics"]}
    sections = []
    for row in metrics["labels"]:
        members = [e for e in epics if e["key"] and label_of[e["id"]] == row["label"]]
        if not members:
            continue
        sections.append(f'<h4>{esc(row["label"])} <span class="muted">· {len(members)} 個專案 · 本週 {esc(fmt_hours(row["week"]["hours"]))}</span></h4>'
                        f'<div class="projs">{"".join(_week_row(e, narrative, metrics, page) for e in members)}</div>')
    loose = [e for e in epics if not e["key"]]
    if loose:
        sections.append(f'<h4>{esc(UNASSIGNED_EPIC_LABEL)}</h4><div class="projs">{"".join(_week_row(e, narrative, metrics, page) for e in loose)}</div>')
    return _week_table_head(page) + "".join(sections)


def _done_group(title: str, count: int, hours, body: str) -> str:
    """完成清單一個專案一組，預設收合（2026-10-07 使用者裁）：標題帶張數與工時，展開才看逐張。"""
    nums = f"{count} 張" + (f" · {fmt_hours(hours)}" if hours is not None else "")
    return f'<details class="done-group"><summary>{title} <span class="muted">· {esc(nums)}</span></summary>{body}</details>'


BREAKPOINT_LABEL = "改組前"  # 2026-09-30 使用者定名；metrics 的欄位叫 migrated
BREAKPOINT_NOTE = "以下是改組前在其他專案就已完成、之後才搬進來的卡，只列出，不算進完成卡片、實際工時與進度。"


def _month_day(moment: datetime) -> str:
    return moment.astimezone(TZ).strftime("%m/%d")


def _breakpoint(rows: list, page, with_week: bool) -> str:
    """「改組前」分隔線之後灰字列出搬入前已完成的卡：只給人對得上 Jira，不算任何數字、不列資料檢查（AC-16）。
    resolved 是 Jira 的時間格式（+0800），moved_in 是 snapshot 寫的 ISO，兩個解析方式不同。"""
    if not rows:
        return ""
    items = "".join(
        f'<li><span>{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {esc(r["project"])} · {esc(r["owner"])}</span></span>'
        f'<span class="nums">{esc(r["week"]) + " · " if with_week else ""}結案 {esc(_month_day(parse_jira_time(r["resolved"])))} · 搬入 {esc(_month_day(datetime.fromisoformat(r["moved_in"])))}</span></li>'
        for r in rows)
    return (f'<div class="breakpoint"><span>{BREAKPOINT_LABEL}</span></div>'
            f'<details class="migrated"><summary>{BREAKPOINT_LABEL}已完成 {len(rows)} 張 <span class="muted">· 不計入</span></summary>'
            f'<p class="note">{esc(BREAKPOINT_NOTE)}</p><ul class="list">{items}</ul></details>')


LABEL_LEGEND = ('<p class="legend">累計工時 <i class="sw-before"></i>之前 <i class="sw-week"></i>本週；'
                '各列加總等於工時 KPI，epic 數加總等於總覽專案列表的 epic 數。</p>')


def _labels(metrics) -> str:
    """依 label 橫條：累計工時，本週段深色，右側數字；總覽與本週頁各一組、同一份 metrics（2026-10-07 使用者裁，取代表格）。
    長條之間比的是「這個單位的案子累計填了多少小時」，仍受每人填法影響，所以數字一定印在旁邊。"""
    rows = metrics["labels"]
    if not rows:
        return '<p class="muted">還沒有任何專案</p>'
    top = max(r["cumulative"]["hours"] for r in rows)
    items = []
    for r in rows:
        cum, week = r["cumulative"]["hours"], r["week"]["hours"]
        before = round((cum - week) / top * 100, 1) if top else 0
        wk = round(week / top * 100, 1) if top else 0
        unfilled = f'<small>{r["cumulative"]["unestimated"]} 張{UNFILLED}</small>' if r["cumulative"]["unestimated"] else ""
        items.append(
            f'<div class="lrow"><span class="lname">{esc(r["label"])} <span class="muted">· {r["epics"]} 個 epic</span></span>'
            f'<div class="bar" role="img" aria-label="累計工時 {esc(fmt_hours(cum))}，本週 {esc(fmt_hours(week))}">'
            f'<span style="width:{before:g}%"></span>{f"<span class=wk style=width:{wk:g}%></span>" if week else ""}</div>'
            f'<span class="num">本週 {esc(fmt_hours(week))} · 累計 {esc(fmt_hours(cum))}{unfilled}</span></div>')
    return LABEL_LEGEND + f'<div class="labels">{"".join(items)}</div>'


TREND_WEEKS = 6          # 圖畫最近幾週
OLD_CARD_DAYS = 14       # 進行中超過幾天標警示色、列進原因清單
# 投入比例的 label 色：dataviz 參考色票的前 7 槽，順序就是色弱安全的機制，不重排；亮暗兩組都跑過 validate_palette.js
SERIES = 7


def _delta(now, prev, unit: str) -> str:
    """「上週 X，多／少 N」；上週沒有值（沒有上一期、上一期沒有這個欄、上週沒有完成卡）就不寫差值。百分比寫「個百分點」。"""
    if prev is None:
        return "沒有上週的值"
    if now is None or now == prev:
        return f"上週 {prev}{unit}" + ("，持平" if now == prev else "")
    step = " 個百分點" if unit == "%" else unit
    return f'上週 {prev}{unit}，{"多" if now > prev else "少"} {abs(now - prev)}{step}'


def _week_kpis(metrics) -> str:
    """三個數字（2026-10-28 鎖定 AC-2，同日補登拿掉）：停滯是主數字、唯一用警示色、連到下方清單；其他兩個用一般字色，寫明好壞方向。"""
    k = metrics["kpis"]

    def tile(cls, label, value, unit, delta, hint, warn=False):
        shown = DASH if value is None else f"{value}{unit}"
        return (f'<li class="{cls}"><span>{esc(label)}</span><strong{" class=alert" if warn else ""}>{esc(shown)}</strong>'
                f'<small>{esc(delta)}</small><small class="hint">{hint}</small></li>')

    stalled = k["stalled"]["now"]
    return ('<ul class="kpis wk-kpis" style="--kpis:3">'
            + tile("lead", "停滯專案", stalled, "", _delta(stalled, k["stalled"]["prev"], ""),
                   f'14 天沒有卡更新 · <a href="#step-in">看是哪幾個</a>', warn=bool(stalled))
            + tile("", "有完成的專案", k["projects"]["now"], "", _delta(k["projects"]["now"], k["projects"]["prev"], ""), "本週至少完成一張卡")
            + tile("", "照流程走", k["via_progress"]["now"], "%", _delta(k["via_progress"]["now"], k["via_progress"]["prev"], "%"),
                   "完成前到過「進行中」，越高越好")
            + "</ul>")


def _short_week(week: str) -> str:
    return "W" + week.split("-W")[1]


def _map_svg(metrics) -> str:
    """專案地圖：x 完成進度、y 幾天沒動；上方是停滯區。停滯的點標名字與負責人，標籤同一邊靠太近就往下錯開。
    沒有 JS：數字寫在 <title> 給滑鼠提示，停滯的專案在原因清單也列得出來。"""
    rows = [e for e in metrics["epics"] if e["key"] and e["status"] != "Done"]
    if not rows:
        return '<p class="muted">沒有未結案的專案。</p>'
    w, h, left, top, right, bottom = 600, 260, 36, 10, 12, 32
    maxd = max([30] + [e["idle_days"] for e in rows])

    def x(p):
        return left + p * (w - left - right)

    def y(d):
        return top + (1 - min(d, maxd) / maxd) * (h - top - bottom)

    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="專案地圖：{len(rows)} 個未結案專案的進度與幾天沒動">',
           f'<rect x="{left}" y="{top}" width="{w - left - right}" height="{y(14) - top:.1f}" class="zone"/>',
           f'<text x="{w - right - 4}" y="{top + 14}" text-anchor="end" class="t-warn">停滯區：14 天以上沒動</text>']
    for d in (0, 14, maxd):
        out.append(f'<text x="{left - 6}" y="{y(d) + 4:.1f}" text-anchor="end" class="t-axis">{d}天</text>')
    for p in (0, 0.5, 1):
        out.append(f'<text x="{x(p):.1f}" y="{h - 12}" text-anchor="middle" class="t-axis">{p:.0%}</text>')
    out.append(f'<text x="{(w + left) / 2:.0f}" y="{h - 1}" text-anchor="middle" class="t-axis">完成進度 →</text>')
    used = {True: [], False: []}
    for e in sorted(rows, key=lambda e: (e["week"]["completed_count"] > 0, e["key"])):
        prog = e["progress"]
        p = prog["done_cards"] / prog["total_cards"] if prog["total_cards"] else 0
        cls = "dot" + (" moving" if e["week"]["completed_count"] else "") + (" stalled" if e["stalled"] else "")
        out.append(f'<circle cx="{x(p):.1f}" cy="{y(e["idle_days"]):.1f}" r="6" class="{cls}"><title>{esc(e["summary"])}｜完成 {prog["done_cards"]}/{prog["total_cards"]} 張｜'
                   f'{e["idle_days"]} 天沒動｜本週 +{e["week"]["completed_count"]}</title></circle>')
        if e["stalled"]:
            right_side = p > 0.6
            ty = y(e["idle_days"]) + 4
            while any(abs(ty - u) < 14 for u in used[right_side]):
                ty += 14
            used[right_side].append(ty)
            name = e["summary"].split("]", 1)[-1] if e["summary"].startswith("[") else e["summary"]
            name = name[:16] + ("…" if len(name) > 16 else "")
            owner = (e["owner"] or UNASSIGNED_OWNER).split()[0]
            out.append(f'<text x="{x(p) + (-9 if right_side else 9):.1f}" y="{ty:.1f}" text-anchor="{"end" if right_side else "start"}" class="t-label">{esc(name)} · {esc(owner)}</text>')
    out.append("</svg>")
    legend = ('<p class="legend"><i class="dot-on"></i>本週有完成 <i class="dot-off"></i>本週沒完成 <i class="dot-stall"></i>停滯（標名字與負責人）</p>')
    return legend + "".join(out)


def _alloc_svg(metrics) -> str:
    """投入比例：每週一條 100% 橫條，每人每週先換成 100% 再平均（metrics 算）。7% 以上的段直接寫百分比，其餘看圖例與滑鼠提示。"""
    alloc = metrics["allocation"]
    weeks = alloc["weeks"][-TREND_WEEKS:]
    if not weeks:
        return '<p class="muted">還沒有完成的卡。</p>'
    names = alloc["labels"]
    w, rowh, gap, left, right = 600, 24, 8, 40, 8
    span = w - left - right
    out = [f'<svg viewBox="0 0 {w} {len(weeks) * (rowh + gap)}" role="img" aria-label="近 {len(weeks)} 週投入比例">']
    for i, row in enumerate(weeks):
        top = i * (rowh + gap)
        out.append(f'<text x="0" y="{top + 16}" class="t-axis">{_short_week(row["week"])}</text>')
        cursor = left
        for j, name in enumerate(names):
            share = row["shares"].get(name, 0)
            if share <= 0:
                continue
            series = f"s{j + 1}" if j < SERIES else "s-other"
            out.append(f'<rect x="{cursor:.1f}" y="{top}" width="{max(span * share - 2, 1):.1f}" height="{rowh}" rx="2" class="{series}">'
                       f'<title>{_short_week(row["week"])} {esc(name)} {share:.0%}（{row["people"]} 人）</title></rect>')
            if share >= 0.07:
                out.append(f'<text x="{cursor + 4:.1f}" y="{top + 16}" class="t-in">{share:.0%}</text>')
            cursor += span * share
    out.append("</svg>")
    legend = "".join(f'<span><i class="{f"s{j + 1}" if j < SERIES else "s-other"}"></i>{esc(n)}</span>' for j, n in enumerate(names))
    return f'<p class="legend swatches">{legend}</p>' + "".join(out)


def _output_svg(metrics) -> str:
    """產出：每週完成卡片張數，一根一週、數字寫在頂端。只畫一個量（張數），不疊第二個軸。"""
    rows = metrics["health"][-TREND_WEEKS:]
    if not rows:
        return '<p class="muted">還沒有完成的卡。</p>'
    w, h, base, bw = 600, 200, 172, 56
    top = max(r["completed"] for r in rows) or 1  # 近幾週全是 0 張時長條都是 0 高，不除以 0
    step = (w - 20) / len(rows)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="近 {len(rows)} 週完成卡片">']
    for i, r in enumerate(rows):
        n = r["completed"]
        x = 10 + i * step + (step - bw) / 2
        hn = n / top * 140
        out.append(f'<rect x="{x:.1f}" y="{base - hn:.1f}" width="{bw}" height="{hn:.1f}" rx="2" class="bar-wk"><title>{_short_week(r["week"])} 完成 {n} 張</title></rect>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base - hn - 6:.1f}" text-anchor="middle" class="t-val">{n}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 18}" text-anchor="middle" class="t-axis">{_short_week(r["week"])}</text>')
    out.append(f'<line x1="0" x2="{w}" y1="{base}" y2="{base}" class="axis"/></svg>')
    return "".join(out)


def _health_svg(metrics) -> str:
    """資料健康：照流程走逐週的比例，0～100%，數字寫在頂端；那週沒有完成卡寫「—」。"""
    rows = metrics["health"][-TREND_WEEKS:]
    if not rows:
        return '<p class="muted">還沒有完成的卡。</p>'
    w, h, base, top_h = 600, 200, 172, 140
    step = (w - 20) / len(rows)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="資料健康：照流程走的比例">',
           f'<line x1="0" x2="{w}" y1="{base - top_h}" y2="{base - top_h}" class="grid"/>',
           f'<text x="{w}" y="{base - top_h - 4}" text-anchor="end" class="t-axis">100%</text>']
    for i, r in enumerate(rows):
        v = r["via_progress"] / r["completed"] if r["completed"] else 0
        shown = f"{v:.0%}" if r["completed"] else "—"  # 那週沒有完成卡：比例不存在，不是 0%
        x = 10 + i * step
        out.append(f'<rect x="{x + 10:.1f}" y="{base - top_h * v:.1f}" width="{step - 20:.1f}" height="{top_h * v:.1f}" rx="2" class="bar-wk">'
                   f'<title>{_short_week(r["week"])} 照流程走 {r["via_progress"]}/{r["completed"]} 張</title></rect>')
        out.append(f'<text x="{x + step / 2:.1f}" y="{base - top_h * v - 6:.1f}" text-anchor="middle" class="t-val">{shown}</text>')
        out.append(f'<text x="{x + step / 2:.1f}" y="{base + 18}" text-anchor="middle" class="t-axis">{_short_week(r["week"])}</text>')
    out.append(f'<line x1="0" x2="{w}" y1="{base}" y2="{base}" class="axis"/></svg>')
    return "".join(out)


def _viz(metrics) -> str:
    open_count = sum(1 for e in metrics["epics"] if e["key"] and e["status"] != "Done")
    panels = (
        ("專案地圖", f'未結案 {open_count} 個：越上越久沒動、越右越接近完成，停滯的標名字。', _map_svg(metrics)),
        ("力氣花在哪", "全隊的力氣：每人每週先換成 100% 再平均，不比小時。", _alloc_svg(metrics)),
        ("產出", "全隊每週完成的卡片張數。", _output_svg(metrics)),
        ("資料健康", "照流程走：完成前到過「進行中」的比例。越高，週報上的「本週完成」越接近真的做完的時間。", _health_svg(metrics)),
    )
    return '<div class="viz">' + "".join(f'<div class="viz-panel"><h4>{esc(t)}</h4>{f"<p class=note>{esc(n)}</p>" if n else ""}{body}</div>' for t, n, body in panels) + "</div>"


def _causes(metrics, page) -> str:
    """這週要你出手的地方：三類原因列，有內容就展開（2026-10-28 鎖定 AC-3；同日補登那類拿掉）。每列先寫數字再寫原因，展開看是哪些。"""
    stalled = sorted((e for e in metrics["epics"] if e["stalled"]), key=lambda e: (-e["idle_days"], e["key"]))
    doing = {r["key"]: r for r in metrics["in_progress"]}
    old = sorted(((p["name"], k, d) for p in metrics["people"] for k, d in p["in_progress"]["ages"].items() if d >= OLD_CARD_DAYS),
                 key=lambda t: (-t[2], t[1]))
    decided = [e for e in metrics["epics"] if e["key"] and e["status"] != "Done" and e["kickoff"] and e["kickoff"]["sections"].get("decision")]
    blocks = (
        (len(stalled), "個專案停滯", "未結案、14 天以上沒有任何卡更新。找負責人問要收還是要推。",
         [f'{page.title(e["key"], e["summary"])} <span class="muted">· {esc(e["owner"] or UNASSIGNED_OWNER)} · {e["idle_days"]} 天沒動 · '
          f'完成 {e["progress"]["done_cards"]}/{e["progress"]["total_cards"]} 張</span>' for e in stalled]),
        (len(old), f"張卡放在進行中超過 {OLD_CARD_DAYS} 天", "從進入目前狀態算起。卡住、做完沒關，或拆太大。",
         [f'{page.issue(k)} {esc(doing[k]["summary"])} <span class="muted">· {esc(name)} · {d} 天</span>' for name, k, d in old]),
        (len(decided), "個專案在 Kick off 寫了要你決定的事", "Kick off 的「需要決定」段，有寫就列。",
         [f'{page.title(e["key"], e["summary"])}：<span class="muted">{esc(e["kickoff"]["sections"]["decision"])}</span>' for e in decided]),
    )
    out = []
    for i, (count, title, hint, items) in enumerate(blocks):
        body = "".join(f"<li>{x}</li>" for x in items) or '<li class="muted">無</li>'
        out.append(f'<details class="cause"{" open" if count else ""}><summary><b class="cnt{" alert" if count and i < 2 else ""}">{count}</b> {esc(title)}</summary>'
                   f'<p class="note">{esc(hint)}</p><ul>{body}</ul></details>')
    return "".join(out)


def _by_name(people: list) -> list:
    """人一律照名字排、未指派最後，不照任何數字排（2026-10-28 鎖定 AC-4、AC-5：不排名）。"""
    return sorted(people, key=lambda p: (p["name"] == UNASSIGNED_OWNER, p["name"].casefold()))


SPARK_SLOT = 28        # 小長條每週一格的寬度，放得下「W44」與兩位數
SPARK_DENSE_SLOT = 14  # 超過 SPARK_LABELED 週改窄格，週次隔幾根標一次、張數只標最新一週
SPARK_LABELED = 12


def _spark(trend: list) -> str:
    """一個人自己的逐週完成張數小長條，最新一週深色；只跟自己比，每個人的高度各自縮放。
    每根底下標週次、頂端標張數（2026-10-28 第二輪：只有長條看不出是哪幾週、幾張）；
    超過 SPARK_LABELED 週改窄格，週次隔幾根標一次、張數只標最新一週。寬度跟著週數長，不會算出負寬度（Codex 審 70a636d）。"""
    n = max(len(trend), 1)
    dense = len(trend) > SPARK_LABELED
    slot = SPARK_DENSE_SLOT if dense else SPARK_SLOT
    every = -(-len(trend) // SPARK_LABELED) if dense else 1
    top = max((t["completed"] for t in trend), default=0) or 1
    w, h, base, tall = n * slot, 50, 36, 22
    bw = slot - 4
    out = []
    for i, t in enumerate(trend):
        last = i == len(trend) - 1
        x = i * slot + 2
        hb = max(t["completed"] / top * tall, 0.5)
        out.append(f'<rect x="{x:.1f}" y="{base - hb:.1f}" width="{bw:.1f}" height="{hb:.1f}" rx="1.5" '
                   f'class="{"bar-wk" if last else "bar-before"}"><title>{_short_week(t["week"])} {t["completed"]} 張</title></rect>')
        if not dense or last:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{base - hb - 3:.1f}" text-anchor="middle" class="t-val">{t["completed"]}</text>')
        if last or (len(trend) - 1 - i) % every == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - 2}" text-anchor="middle">{_short_week(t["week"])}</text>')
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="近 {len(trend)} 週完成張數">{"".join(out)}</svg>')


def _doing_list(person: dict, doing: dict, page, limit) -> str:
    ages = person["in_progress"]["ages"]
    keys = sorted(person["in_progress"]["keys"], key=lambda k: (-ages[k], k))
    shown = keys if limit is None else keys[:limit]
    items = "".join(f'<li>{page.issue(k)} {esc(doing[k]["summary"])} <span class="{"alert" if ages[k] >= OLD_CARD_DAYS else "muted"}">{ages[k]} 天</span></li>' for k in shown)
    more = f'<li class="muted">另 {len(keys) - len(shown)} 張</li>' if len(keys) > len(shown) else ""
    return f'<ul class="list-doing">{items or "<li class=muted>沒有進行中的卡</li>"}{more}</ul>'


def _week_people(metrics, page) -> str:
    """本週頁人卡：負責的未結案專案、近 6 週完成小長條（只跟自己比）、近 4 週落在哪些 label、進行中的卡與天數。"""
    doing = {r["key"]: r for r in metrics["in_progress"]}
    owned = {}
    for e in metrics["epics"]:
        if e["key"] and e["status"] != "Done":
            owned.setdefault(e["owner"] or UNASSIGNED_OWNER, []).append(e["summary"])
    cards = []
    for p in _by_name(metrics["people"]):
        if not p["recent"]["completed"] and not p["in_progress"]["count"]:
            continue
        trend = p["trend"][-TREND_WEEKS:]
        this_week = next(t["completed"] for t in p["trend"] if t["week"] == metrics["period"]["week"])
        mine = owned.get(p["name"], [])
        epics = "、".join(esc(s) for s in mine[:3]) + (f" 等 {len(mine)} 個" if len(mine) > 3 else "") or "無"
        recent = p["recent"]
        labels = "、".join(f"{esc(l)} {n} 張" for l, n in recent["labels"]) or f'近 {recent["weeks"]} 週沒有完成'
        cards.append(f'<article class="pcard"><h4>{esc(p["name"])}</h4>'
                     f'<div class="trend"><span>近 {len(trend)} 週完成張數（只跟自己比，深色＝本週）· 本週 {this_week} 張</span>{_spark(trend)}</div>'
                     f'<div class="prow"><span>負責：{epics}</span></div>'
                     f'<div class="prow"><span>近 {recent["weeks"]} 週落在：{labels}</span></div>'
                     f'{_doing_list(p, doing, page, 4)}</article>')
    return f'<div class="pcards">{"".join(cards)}</div>' if cards else '<p class="muted">這週沒有人有完成或進行中的卡。</p>'


def _week_panel(metrics, narrative, page) -> str:
    """本週頁（2026-10-28 鎖定 AC-2、AC-3）：四個數字 → 四格圖 → 這週要你出手的地方 → 人卡 → 專案一覽（收合）。
    本週重點 highlights 照舊由 routine 產生與驗證，頁面不再獨立列；每個專案的結果句在專案一覽展開看。"""
    rows = len(metrics["epics"])
    return f"""
  {_week_kpis(metrics)}
  {_viz(metrics)}
  <h3 id="step-in">這週要你出手的地方</h3>
  {_causes(metrics, page)}
  <h3>人</h3>
  {_week_people(metrics, page)}
  <details class="proj-list"><summary>專案一覽（{rows} 列，點開）</summary>
  <p class="legend">進度條 <i class="sw-before"></i>之前完成 <i class="sw-week"></i>本週完成 <i class="sw-left"></i>未完成；點一列看本週結果與進行中的卡。</p>
  {_week_table(metrics, narrative, page)}
  {_other_doing(metrics, page)}
  </details>"""


def _overview_table_head(page) -> str:
    return ('<div class="proj head"><span class="name">專案</span><span class="prog">進度（深色＝本週）</span>'
            f'<span class="people">人</span><span class="num">累計工時</span>{TOKEN_HEAD if page.tokens else ""}</div>')


def _people_cell(scope: dict) -> str:
    """總覽「人」欄（2026-10-07 使用者裁，取代團隊頁的專案投入表）：累計有產出的人數與名字，未指派不算人、另外註明。"""
    names = esc("、".join(scope["people"]))
    note = "<small>另有未指派</small>" if scope["unassigned"] else ""
    return f'<span class="people" data-label="人">{scope["people_count"]}{f" <small>{names}</small>" if names else ""}{note}</span>'


def _overview_project(e, week_done: dict, staffing: dict, page) -> str:
    """總覽一個專案一列：名稱、狀態、三段進度條、人、累計工時；展開看 Kick off 目標五段，預設收合（2026-10-07 使用者裁）。
    用 <details>，沒有 JS 也能展開。Kick off 一段都沒填（含未歸屬 epic）就不給展開，點開只看到「未填」是空的承諾；
    缺 Kick off 卡仍列在資料檢查（2026-10-07 總覽 UI 盤點 U6）。
    本週段取本週頁同一個 epic 的完成數；先前已結案的 epic 本週沒有專案列，就是 0。"""
    token = f'<span class="num tok" data-label="Token">Token {esc(fmt_tokens(e["tokens"]))}</span>' if page.tokens else ""
    scope = staffing[e["id"]]["cumulative"] if e["id"] in staffing else {"people": [], "people_count": 0, "unassigned": False, "unestimated": 0}
    unfilled = f'<small>{scope["unestimated"]} 張{UNFILLED}</small>' if scope["unestimated"] else ""
    head = (f'<span class="name">{page.title(e["key"], e["summary"])} {_pill(e["status"])}</span>'
            f'{_progress_cell(e["progress"], week_done.get(e["id"], 0))}{_people_cell(scope)}'
            f'<span class="num" data-label="累計工時">{esc(fmt_hours(e["hours"]))}{unfilled}</span>{token}')
    body = _kickoff(e["kickoff"], BENEFIT_FIELDS) if e["key"] else ""
    if not body:
        return f'<div class="proj"><div class="proj-head">{head}</div></div>'
    return f'<details class="proj"><summary class="proj-head">{head}</summary><div class="proj-body">{body}</div></details>'


def _overview_projects(metrics, page) -> str:
    """專案依 label 分段，段的順序跟 label 表一樣；未歸屬 epic 放最後一段。段首的工時取 label 表，兩處不會各算一次。"""
    epics = metrics["cumulative"]["epics"]
    if not epics:
        return '<p class="muted">還沒有任何專案</p>'
    week_done = {e["id"]: e["week"]["completed_count"] for e in metrics["epics"]}
    staffing = {s["id"]: s for s in metrics["staffing"]}
    sections = [_overview_table_head(page)]
    for row in metrics["labels"]:
        members = [e for e in epics if e["key"] and e["label"] == row["label"]]
        if not members:
            continue
        sections.append(f'<h4>{esc(row["label"])} <span class="muted">· {len(members)} 個專案 · 累計工時 {esc(fmt_hours(row["cumulative"]["hours"]))}</span></h4>'
                        f'<div class="projs">{"".join(_overview_project(e, week_done, staffing, page) for e in members)}</div>')
    loose = [e for e in epics if not e["key"]]
    if loose:
        sections.append(f'<h4>未歸屬 epic <span class="muted">· 卡片照自己的 label 算進 label 表</span></h4>'
                        f'<div class="projs">{"".join(_overview_project(e, week_done, staffing, page) for e in loose)}</div>')
    return "".join(sections)


def _week_projects(cards: list, order: dict, page) -> str:
    """總覽一週的完成卡依專案分組（2026-10-07 總覽 UI 盤點 U7，跟本週頁的完成清單同一種兩層）：一個專案一列可展開，
    標題帶張數與工時。cumulative.completed 只有葉節點（metrics 的 done_leaves），張數與工時的口徑同週標題，加總對得上。
    專案的順序照 cumulative.epics（卡號序，跟本週〈完成〉一樣）；order 查不到 group 就讓它炸，不默默排到最後。"""
    groups: dict = {}
    for r in cards:
        groups.setdefault(r["group"], []).append(r)
    items = []
    for group_id in sorted(groups, key=lambda g: order[g]):
        rows = groups[group_id]
        hours = sum(r["hours"] or 0 for r in rows)
        sub = "".join(f'<li><span>{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {esc(r["owner"])}</span></span>'
                      f'<span class="nums">{esc(fmt_card_hours(r))}</span></li>' for r in rows)
        items.append(f'<li><details><summary><span>{esc(rows[0]["project"])}</span>'
                     f'<span class="nums">{len(rows)} 張 · {esc(fmt_hours(hours))}</span></summary><ul>{sub}</ul></details></li>')
    return f'<ul class="list">{"".join(items)}</ul>'


def _cumulative_panel(metrics, page) -> str:
    """區塊順序：資源 → 依 label → 專案列表 → 完成（2026-10-07 使用者要求，取代同日「跟本週頁一致」；
    2026-10-28 團隊表拿掉：每人累計工時與張數並排就是人與人比較，跟「不排名」衝突，人的累計看團隊頁）。
    資源多一格 epic 數，是專案列表裡的 epic 數，未歸屬 epic 不是一個 epic，不算。"""
    c = metrics["cumulative"]
    since = f'從 {esc(fmt_date(c["since"]))} 第一張完成卡起算，到本期結束。' if c["since"] else "到本期結束還沒有任何完成的卡。"
    order = {e["id"]: i for i, e in enumerate(c["epics"])}
    weeks = []
    for w in c["weeks"]:
        cards = [r for r in c["completed"] if r["week"] == w["week"]]
        weeks.append(_done_group(esc(w["week"]), w["completed"], w["hours"], _week_projects(cards, order, page)))
    return f"""
  <p class="note">{since}</p>
  <h3>資源</h3>
  {_resources(c["totals"], "累計", "累計工時", page, epics=sum(1 for e in c["epics"] if e["key"]))}
  <h3>依 label</h3>
  {_labels(metrics)}
  <h3>專案列表</h3>
  <p class="legend">進度條 <i class="sw-before"></i>之前完成 <i class="sw-week"></i>本週完成 <i class="sw-left"></i>未完成；點一列看 Kick off 的目標五段。</p>
  {_overview_projects(metrics, page)}
  <h3>完成</h3>
  {"".join(weeks) or '<p class="muted">還沒有任何完成的卡。</p>'}
  {_breakpoint(metrics["migrated"]["cumulative"], page, with_week=True)}"""


def _owned_list(epics: list, page) -> str:
    items = "".join(f'<li>{page.title(e["key"], e["summary"])}</li>' for e in epics)
    return f'<ul class="owned">{items}</ul>'


def _team_panel(metrics, page) -> str:
    """團隊頁：累計版人卡（2026-10-28 使用者裁）。每人自第一張完成卡起的逐週完成小長條、負責的專案、累計投入比例、
    他自己的累計工時與張數、進行中的卡與天數。照名字排；人與人並排的長條與表都不放（AC-5）。
    同日第二輪：負責的專案拆成「負責中」逐列列出、「已結案」收合，不再擠成一整段；桌機兩欄。"""
    doing = {r["key"]: r for r in metrics["in_progress"]}
    owned = {}
    for e in metrics["cumulative"]["epics"]:
        if e["key"]:
            owned.setdefault(e["owner"] or UNASSIGNED_OWNER, []).append(e)
    cards = []
    for p in _by_name(metrics["people"]):
        cum, prog = p["cumulative"], p["in_progress"]
        unassigned = p["name"] == UNASSIGNED_OWNER
        note = '<p class="warn">這些卡在 Jira 上沒有 assignee，工時算不到人。</p>' if unassigned else ""
        mine = owned.get(p["name"], [])
        open_ = [e for e in mine if e["status"] != "Done"]
        closed = [e for e in mine if e["status"] == "Done"]
        open_html = _owned_list(open_, page) if open_ else '<p class="prow">無</p>'
        closed_html = f'<details><summary>已結案 {len(closed)} 個</summary>{_owned_list(closed, page)}</details>' if closed else ""
        shares = "、".join(f"{esc(label)} {share:.0%}" for label, share in p["shares"][:4]) or "無"
        doing_html = ""
        if prog["count"]:
            doing_html = f'<details><summary>進行中 {prog["count"]} 張</summary>{_doing_list(p, doing, page, None)}</details>'
        cards.append(f"""
    <article class="card person{' warn-card' if unassigned else ''}">
      <div class="card-head"><h3>{esc(p["name"])}</h3><div class="figures"><span>進行中 <b>{prog["count"]}</b> 張</span></div></div>
      {note}
      <p class="prow">累計 {cum["completed"]} 張 · 累計工時 {esc(fmt_hours(cum["hours"]))}{_token_suffix(cum["tokens"], page)}</p>
      <div class="trend"><span>逐週完成張數（只跟自己比，深色＝本週）</span>{_spark(p["trend"])}</div>
      <p class="sub">負責中 {len(open_)} 個</p>
      {open_html}
      {closed_html}
      <p class="prow">累計投入比例：{shares}</p>
      {doing_html}
    </article>""")
    people = f'<div class="tcards">{"".join(cards)}</div>' if cards else '<p class="muted">還沒有任何人有完成或進行中的卡。</p>'
    return f"""
  <h3>人</h3>{people}"""


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


def _glossary(metrics) -> str:
    """每個詞的口徑。跟 metrics.py 的算法一一對應，改算法時同一次改這裡。"""
    period = metrics["period"]
    terms = (
        ("本週", f'{fmt_day(period["start"])} 00:00 到 {fmt_day(period["week_ending"])} 23:59（UTC+8）。'
                 "卡片的 Jira 完成時間落在這段期間內，才算本週完成；不是週一到週日。"),
        ("累計", f'從第一張完成卡起算，到本週結束（{fmt_date(period["week_ending"])}）為止。'),
        ("完成卡片", "完成的卡片張數，只數沒有子卡的卡。有子卡的父卡不算一張，它的張數與工時都計在子卡。"),
        ("實際工時", "Jira 的 Story point estimate 欄位，由執行人完工後回填花費的時數。"
                     "只加沒有子卡的卡；有子卡的父卡自己的數字不算，避免重複。"),
        ("累計工時", "到本週結束為止的實際工時加總，口徑同實際工時。"),
        (UNFILLED, "卡片完成了，Story point estimate 卻是空的；實際工時以 0 計。本週的逐張列在資料檢查，累計只計張數。"),
        ("人", "總覽專案列表的「人」欄：這個專案累計由幾個人產出，名字列在數字旁。人數是完成了沒有子卡的卡的執行人，同一人只算一次；"
               "沒有執行人的卡不算人，標「另有未指派」。本週一覽表的負責人是本週有產出的人，本週沒人產出就寫 epic 的負責人。"
               "人卡照名字排，小長條只跟自己的前幾週比，不跟別人比。"),
        ("進度", "專案底下沒有子卡的卡：到本週結束為止算進完成卡片的張數／全部張數；進度條深色那段是其中本週完成的。"
                 "不列入的卡與改組前的卡不算完成、仍算在全部張數裡，所以 Jira 上已全部完成的專案這裡可能不到 100%。"),
        ("label", "專案的 label 取 epic 在 Jira 上的 label；不在 epic 底下的卡用自己的 label。"
                  "沒有 label、或有兩個以上 label 的歸「未分類」，並列進資料檢查。epic 數是總覽專案列表裡掛這個 label 的 epic。"),
        ("Done／On Track", "Done 是 epic 在 Jira 已完成；On Track 只表示 epic 還沒完成，不代表進度評估。"),
        ("停滯", "epic 還沒完成，底下的卡 14 天沒有任何更新（只改 label 也算更新）；底下沒有卡就看 epic 自己。"),
        ("照流程走", "完成的卡在結案前待過「進行中」狀態（直接進審核中的不算）。比例越高，週報上的時間越準。"),
        ("投入比例", "每個人每週先把自己完成的卡換成 100%（用他自己填的實際工時當權重，沒填的卡補他這週的平均，"
                     "整週都沒填就用張數），再對當週有產出的人取平均。只在人內換算，不拿小時跨人比。"),
        ("不列入的卡", "config/excluded.json 列的卡不算完成卡片、實際工時，也不算進度裡的完成；列的 epic 連同底下所有卡整個不列入週報。"),
        (BREAKPOINT_LABEL, "卡片在 Jira 結案的時間早於或等於它搬進本專案的時間，是改組前在別的專案完成的歷史：本週與累計都不算，"
                           f"只在「完成」清單最後的「{BREAKPOINT_LABEL}」分隔線之後灰字列出。搬進來之後才結案的照算。"),
        ("資料檢查", "Jira 資料的缺漏與排除說明，包括 Kick off 鎖定後被修改、label 不一致；不影響發布。"),
    )
    items = "".join(f"<div><dt>{esc(term)}</dt><dd>{esc(text)}</dd></div>" for term, text in terms)
    return f"""
<section id="glossary">
  <details>
    <summary>名詞定義</summary>
    <dl class="glossary">{items}</dl>
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
    start = fmt_day(period["start"])
    end = fmt_day(period["week_ending"])
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
  <p class="generated" id="generated" data-generated="{esc(generated)}">資料截至 {esc(fmt_moment(generated))}（UTC+8）· 實際工時由執行人完工後回填，名詞定義在頁尾</p>
  <p class="warn" id="stale" hidden>這份報告超過 {STALE_DAYS} 天沒有更新，排程可能沒有成功執行。</p>
  {banner}
</header>
{_tabs(panels)}
{_glossary(metrics)}
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
--done-bg:#e3f4ea;--done-fg:#1b7a45;--bar:#4faf7b;--bar-week:#1b7a45;--track-bg:#e8eefc;--track-fg:#2f5bea;--warn-bg:#fff4e0;--warn-fg:#8a5300;
--alert:#b25e09;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;--s-other:#b5b4ad;}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--surface:#1c1c1f;--soft:#26262a;--text:#ececef;--muted:#9a9aa1;--line:#2e2e33;
--accent:#7c9cff;--done-bg:#15321f;--done-fg:#6fd39a;--bar:#2f7a52;--bar-week:#7fe0ad;--track-bg:#1b2544;--track-fg:#9fb6ff;--warn-bg:#3a2a0e;--warn-fg:#ffcf7a;
--alert:#ffb35c;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s-other:#5e5e64;}}
/* 進度與橫條兩階同色綠：dataviz validate_palette.js --ordinal 亮暗各過（2026-10-07）；兩段之間留 2px 底色縫 */
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Noto Sans TC","PingFang TC","Segoe UI",sans-serif}
main{max-width:1280px;margin:0 auto;padding:32px 24px 72px}
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
.highlights .epic{color:var(--accent)}
p.highlights{padding-left:16px}
.kpis{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(var(--kpis),minmax(0,1fr));gap:10px}
.kpis li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;min-width:0}
.kpis li.big strong{font-size:28px}
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
.bar{flex:1;height:8px;background:var(--soft);border-radius:4px;overflow:hidden;display:flex;min-width:60px}
.bar span{display:block;height:100%;background:var(--bar)}
.bar .wk{background:var(--bar-week);margin-left:2px}
.bar-empty{flex:1;color:var(--muted);font-size:12px}
.hbar{display:inline-block;width:64px;height:8px;background:var(--soft);border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:8px}
.hbar i{display:block;height:100%;background:var(--bar-week)}
.legend{color:var(--muted);font-size:12px;margin:-4px 0 8px}
.legend i{display:inline-block;width:12px;height:8px;border-radius:2px;vertical-align:middle;margin:0 3px 0 6px}
.sw-before{background:var(--bar)}.sw-week{background:var(--bar-week)}.sw-left{background:var(--soft);border:1px solid var(--line)}
.result{margin:6px 0}
.projs{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px}
.proj{border-bottom:1px solid var(--line)}.proj:last-child{border-bottom:0}
.proj-head,.proj.head{display:flex;align-items:center;gap:6px 12px;padding:9px 0;font-size:14px;list-style:none}
.proj.head{color:var(--muted);font-size:12px;padding:4px 14px 0}
details.proj>.proj-head{cursor:pointer}
.proj-head::-webkit-details-marker{display:none}
details.proj>.proj-head::before{content:"▸";color:var(--muted);margin-right:0}
details.proj[open]>.proj-head::before{content:"▾"}
div.proj>.proj-head,.proj.head{padding-left:16px}
.proj-head .name,.proj.head .name{flex:1;min-width:0}
.prog{display:flex;align-items:center;gap:8px;width:170px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
/* 數字欄用子選擇器：進度條旁的「88/140」也是 .num，不能被欄寬規則吃到 */
.prog .num{min-width:56px;text-align:right;white-space:nowrap}
.proj-head>.num,.proj.head>.num{flex:none;width:56px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
#cumulative .proj-head>.num,#cumulative .proj.head>.num{width:88px}
.proj-head>.tok,.proj.head>.tok{width:96px}
.proj-head>.num small,.people small{display:block;white-space:normal;color:var(--muted);font-size:11px}
.owner{width:90px;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.plus{width:40px;text-align:right;font-variant-numeric:tabular-nums;color:var(--bar-week)}
.sigs{width:96px}
.sig{display:inline-block;font-size:11px;padding:0 6px;border-radius:4px;background:var(--warn-bg);color:var(--warn-fg);white-space:nowrap}
.next{width:22%;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.people{width:150px;font-size:13px;font-variant-numeric:tabular-nums}
.proj-body{padding:0 0 10px 16px}
.labels{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px}
.lrow{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.lrow:last-child{border-bottom:0}
.lrow .lname{width:180px;flex:none}
.lrow .num{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}
.lrow .num small{display:block;white-space:normal}
.done-group{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin:0 0 8px}
.done-group>summary{font-size:14px;font-weight:600}
.done-group .list{margin-top:6px}
.kickoff{margin:8px 0;padding:8px 12px;background:var(--soft);border-radius:8px;font-size:14px}
.kickoff div{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;padding:2px 0}
.kickoff dt{color:var(--muted);font-size:13px}.kickoff dd{margin:0;min-width:0;overflow-wrap:anywhere}
/* 展開記號全頁一種：▸ 收合、▾ 展開（2026-10-07 總覽 UI 盤點 U7） */
details summary{cursor:pointer;list-style:none}
details summary::-webkit-details-marker{display:none}
details>summary::before{content:"▸";color:var(--muted);margin-right:6px}
details[open]>summary::before{content:"▾"}
.list summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:0 8px;list-style:none}
.list summary::-webkit-details-marker{display:none}
.list summary::before{content:"▸";color:var(--muted);margin-right:0}
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
.row>span small{display:block;color:var(--muted);font-size:12px;white-space:normal}
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
.breakpoint{display:flex;align-items:center;gap:10px;margin:22px 0 8px;color:var(--muted);font-size:12px}
.breakpoint::before,.breakpoint::after{content:"";flex:1;border-top:1px dashed var(--line)}
.migrated{color:var(--muted);font-size:14px}
.migrated summary{color:var(--muted)}
.migrated .list li{color:var(--muted)}
#glossary{margin-top:40px}
#glossary summary{font-weight:600}
.glossary{margin:8px 0 0;font-size:14px}
.glossary div{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;padding:4px 0;border-bottom:1px solid var(--line)}
.glossary div:last-child{border-bottom:0}
.glossary dt{color:var(--muted)}.glossary dd{margin:0}
#checks{margin-top:16px}
#checks summary{font-weight:600}
.checks{font-size:14px}
#archive{margin-top:32px;border-top:1px solid var(--line)}
.archive{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:8px 16px}
/* 本週頁：三個數字、四格圖、原因清單、人卡（2026-10-28 團隊績效評估；同日第二輪改桌機寬版） */
.wk-kpis{margin-top:20px}
.wk-kpis li.lead strong{font-size:36px}
.wk-kpis strong.alert,.alert{color:var(--alert)}
.wk-kpis small.hint{margin-top:2px}
.viz{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0 0}
.viz-panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.viz-panel h4{margin:0;font-size:15px}.viz-panel .note{margin:2px 0 8px}.viz-panel .legend{margin:0 0 6px}
.viz svg{display:block;width:100%;height:auto}
.viz text{font-size:12px;fill:var(--muted)}
.spark text{font-size:10px;fill:var(--muted)}
.viz .t-val,.spark .t-val{fill:var(--text)}.viz .t-label{fill:var(--text);font-size:11px}.viz .t-in{fill:#fff;font-weight:600}.viz .t-warn{fill:var(--alert)}
.viz .zone{fill:var(--warn-bg)}.viz .axis{stroke:var(--line)}.viz .grid{stroke:var(--line);stroke-dasharray:3 3}
.dot{fill:var(--surface);stroke:var(--bar-week);stroke-width:2}.dot.moving{fill:var(--bar-week)}.dot.stalled{stroke:var(--alert)}
.s1{fill:var(--s1);background:var(--s1)}.s2{fill:var(--s2);background:var(--s2)}.s3{fill:var(--s3);background:var(--s3)}.s4{fill:var(--s4);background:var(--s4)}
.s5{fill:var(--s5);background:var(--s5)}.s6{fill:var(--s6);background:var(--s6)}.s7{fill:var(--s7);background:var(--s7)}.s-other{fill:var(--s-other);background:var(--s-other)}
.bar-wk{fill:var(--bar-week)}.bar-before{fill:var(--bar)}.bar-warn{fill:var(--alert)}
.legend .dot-on,.legend .dot-off,.legend .dot-stall{width:10px;height:10px;border-radius:50%;border:2px solid var(--bar-week)}
.legend .dot-on{background:var(--bar-week)}.legend .dot-stall{border-color:var(--alert)}
.legend.swatches{display:flex;flex-wrap:wrap;gap:2px 10px;margin:0 0 6px}.legend.swatches i{margin-left:0}
.cause{border-top:1px solid var(--line)}
.cause>summary{padding:8px 0;font-size:14px}
.cause .cnt{display:inline-block;min-width:2.2em;text-align:right;font-variant-numeric:tabular-nums}
.cause .note{margin:0 0 4px 3em}.cause ul{margin:0 0 10px 3em;padding-left:16px;font-size:14px}
.pcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
.pcard{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.pcard h4{margin:0 0 4px}
.prow{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px;color:var(--muted);margin:2px 0}
.spark{flex:none;display:block;max-width:100%;height:auto;margin-top:2px}
.trend{font-size:12px;color:var(--muted);margin:4px 0 6px}
.tcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:12px}
.tcards .card{margin:0}
.card .prow{margin:4px 0}
.sub{font-size:12px;color:var(--muted);margin:8px 0 0}
.card ul.owned{margin:2px 0 0;padding-left:18px;font-size:13px}
.owned li{margin:1px 0}
.list-doing{margin:4px 0 0;padding-left:18px;font-size:13px}
.proj-list{margin-top:24px}
.proj-list>summary{font-weight:600;font-size:15px}
@media (max-width:640px){
  h1{font-size:22px}
  .viz,.pcards,.tcards{grid-template-columns:minmax(0,1fr)}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .split{grid-template-columns:1fr}
  .row{grid-template-columns:repeat(var(--cols),minmax(0,1fr));gap:4px 8px}
  .row .name{grid-column:1/-1}
  .row>span{text-align:left;font-size:13px}
  .row>span::before{content:attr(data-label);display:block;color:var(--muted);font-size:11px}
  .row.head{display:none}
  .kickoff div,.glossary div{grid-template-columns:minmax(0,1fr);gap:0}
  .list summary{grid-template-columns:auto minmax(0,1fr)}
  .proj.head{display:none}
  .proj-head{flex-wrap:wrap;gap:4px 10px}
  .proj-head>[data-label]::before{content:attr(data-label);display:block;color:var(--muted);font-size:11px}
  .proj-head>[data-label]:empty{display:none}
  .proj-head>.num,#cumulative .proj-head>.num,.proj-head>.tok{text-align:left;width:auto}
  .prog .num{text-align:left;min-width:0}
  .proj-head .name{flex-basis:calc(100% - 24px)}
  .prog{flex-basis:100%;width:auto}
  .owner,.next{width:auto;white-space:normal;flex-basis:100%}
  .people{width:auto;flex:1 1 0;min-width:0}  /* 人與累計工時同一行（2026-10-07 總覽 UI 盤點 U8） */
  .sigs{width:auto}
  .lrow{flex-wrap:wrap}.lrow .lname{width:100%}.lrow .bar{flex-basis:100%}
  .list summary .nums{grid-column:2;margin-left:0}
  .tabs button{flex:1;padding:10px 4px}
}
"""
