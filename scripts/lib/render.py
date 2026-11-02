"""把 metrics 與 narrative 排成靜態 HTML。

版面（2026-09-23 改版，決策在 vault 的 jira-weekly-report 專案 index）：讀者原本是主管（2026-10-28 改成團隊負責人本人，見下），
分四個頁籤：總覽（2026-10-07 由「持續累積」改名）、本週（預設）、團隊、成效說明（2026-10-29 加）。vault 的週報範本只當資訊清單，不照它的節次排。
總覽依序是資源（多一格 epic 數）、依 label、專案列表、完成；專案依 label 分段、每列可展開看 Kick off 目標五段（一段都沒填就不給展開）；
2026-11-02 總覽優化（plan 在 vault 的 jira-weekly-report · 總覽優化）：段內依狀態排（進行中依幾天沒動、「卡都做完，epic 沒關」、已結案收合標結案週），
epic 數下面併列三種狀態，名單人數變的週標分界，完成清單一週一列摘要；Kick off 鎖定後被修改只列在資料檢查，頁面不顯示提示（2026-10-07 使用者裁）。
Decision Needed 不顯示；Kick off 欄位有填才顯示；Token 一個檔都沒有時整欄不顯示、資源區只留一行說明，
有檔但沒記錄的卡顯示「—」，不顯示 0。目標日不顯示（2026-10-06 使用者裁：不是當前重點；metrics 仍帶 due）。
工時是實際花費時數（2026-09-30 使用者確認：Story point estimate 由執行人完工後回填），頁面一律叫「實際工時」；
頁尾的名詞定義是每個詞的口徑正本，改算法時同一次改那裡。
頁籤靠少量 JS 切換，沒有 JS 時四個頁籤全部攤開。
視覺化改版（2026-10-07 使用者裁，plan 在 vault 的 jira-weekly-report · 視覺化改版）：本週頁的專案是一張一覽表，
不是一張張卡；進度條分三段（之前完成｜本週完成｜未完成），總覽與本週同一條；依 label 是橫條；團隊表的完成卡片畫長條、
工時只給數字（每人填工時的粒度不同，畫成長度比的是填法）；完成清單預設收合；專案投入併成總覽的「人」欄；
Kick off 目標五段只在總覽展開看。圖都是 inline SVG 或純 CSS，沒有 JS 也顯示。
團隊績效評估（2026-10-28 使用者鎖定，plan 在 vault 的 jira-weekly-report · 團隊績效評估）：讀者改成團隊負責人本人管理用。
本週頁是數字、圖、這週要你出手的地方、人卡、收合的專案一覽（數字與圖的現況見下面 2026-11-02 那段）；本週重點不再獨立列。人一律照名字排、只跟自己比，三個頁籤都不放人與人並排的長條或表。
同日第二輪：補登不算（metrics schema 8 拿掉）；改桌機寬版（主欄 1280px，第一屏以 1440×900 量），圖放大、人卡多欄、
人卡的小長條每根標週次與張數，團隊頁「負責過」拆成負責中與已結案（收合）。
本週頁一圖一訊息（2026-11-02 使用者裁，plan 在 vault 的 jira-weekly-report · 本週頁一圖一訊息）：專案地圖、資料健康與「照流程走」數字拿掉，
數字剩停滯專案與有完成的專案；三張圖（完成張數、單卡平均工時、投入比例）各一則訊息，標題是程式照數字組的觀察句加上週比較；
出手區改成一專案一列，專案沒動、卡沒動、需要決定三個條件同一個時鐘（最後一次更新到本期結束），人卡的天數也是這個時鐘。

所有文字一律經過 html.escape；數字只從 metrics 來。
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta

from . import capacity as capacity_lib
from .metrics import ALLOCATION_TOP, ALLOCATION_WINDOW, CAPACITY_CHECK_WEEKS, OTHER_LABEL, SCHEMA, STALL_DAYS, UNASSIGNED_EPIC_LABEL, UNASSIGNED_OWNER, UNLABELED
from .period import TZ
from .snapshot import issue_sort_key, parse_jira_time

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
TABS = (("cumulative", "總覽"), ("week", "本週"), ("team", "團隊"), ("delivered", "成效說明"))  # 成效說明 2026-10-29 加
# 總覽專案展開後的效益說明：Kick off 鎖定的目標五段（2026-10-07 使用者裁），下個里程碑留在本週頁
BENEFIT_FIELDS = KICKOFF_FIELDS[:5]
DEFAULT_TAB = "week"
# 底下的卡全完成、epic 還沒結案（狀態代號 full）。2026-11-02 使用者裁改白話，取代「100% 待結案」；頁面上這個稱呼只從這裡拿
FULL_LABEL = "卡都做完，epic 沒關"


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
    epics 只有總覽給：專案列表裡的 epic，多一格 epic 數（2026-10-07 使用者要求），下面併列進行中、「卡都做完，epic 沒關」、已結案三個數
    （2026-11-02 總覽優化 AC-3：結案數不單獨出現）。"""
    unfilled = f'{totals["unestimated_completed"]} 張完成卡{UNFILLED}' if totals["unestimated_completed"] else None
    tiles = [(hours_label, fmt_hours(totals["hours"]), unfilled, True)]
    if page.tokens:
        tiles.append((f"{prefix} Token".strip(), fmt_tokens(totals["tokens"]), NO_TOKEN_SOURCE if totals["tokens"] is None else None, True))
    tiles.append((f"{prefix}完成卡片", str(totals["completed"]), None, False))
    if epics is not None:
        n = {k: sum(1 for e in epics if _overview_state(e) == k) for k in ("open", "full", "done")}
        tiles.append(("epic 數", str(len(epics)), f'進行中 {n["open"]} · {FULL_LABEL} {n["full"]} · 已結案 {n["done"]}', False))
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
IDLE_DAYS = STALL_DAYS   # 出手區三個條件同一個時鐘：最後一次更新到本期結束，幾天以上算沒動（跟停滯同一個數）
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


def _rolling_rate(rows: list, end: int):
    """到第 end 週為止（含）的過去四週平均消化速度：4 週結案 epic 加總 ÷ 4 週可運用人週加總（2026-11-02 使用者裁「改算 epic」）。
    不足 4 週（資料開頭那幾週）或沒有可運用人日（沒給名單）是 None：少於 4 週的值不能標成 4 週（Codex 審 ab4aff4）。"""
    if end < 3:
        return None
    window = rows[end - 3:end + 1]
    days = sum(r["person_days"] or 0 for r in window)
    return sum(r["epics_closed"] for r in window) / (days / capacity_lib.DAYS_PER_WEEK) if days else None


def _week_kpis(metrics) -> str:
    """三個數字（2026-11-02 使用者裁「照審查改」：人的時間是資源，目標是消化的速率提高）：
    過去四週平均消化速度（主數字，同日改算 epic 結案、標題直接寫四週）、探索人力占比、停滯專案（唯一用警示色、連到出手區）。
    消化速度比的是上週那時的四週值，兩個窗口重疊三週，所以差值小、不被單週集中結案拉動。"""
    rows = metrics["health"]
    stalled = metrics["kpis"]["stalled"]

    def tile(cls, label, shown, delta, hint, warn=False):
        return (f'<li class="{cls}"><span>{esc(label)}</span><strong{" class=alert" if warn else ""}>{esc(shown)}</strong>'
                f'<small>{esc(delta)}</small><small class="hint">{hint}</small></li>')

    now = _rolling_rate(rows, len(rows) - 1) if rows else None
    prev = _rolling_rate(rows, len(rows) - 2) if len(rows) > 1 else None
    label, hint = "過去四週平均消化速度（epic／人週）", "4 週結案 epic ÷ 4 週可運用人週，越高越好；epic 結案才算完成"
    if rows and rows[-1]["person_days"] is None:
        rate = tile("lead", label, DASH, "沒有成員名單，算不出可運用人日", "補 config/team.json")
    elif now is None:
        rate = tile("lead", label, DASH, f"資料還不到 4 週（目前 {len(rows)} 週）", hint)
    else:
        delta = "沒有上週的值" if prev is None else (
            f"上週 {prev:.2f}，" + ("持平" if round(now, 2) == round(prev, 2) else f'{"多" if now > prev else "少"} {abs(round(now, 2) - round(prev, 2)):.2f}'))
        rate = tile("lead", label, f"{now:.2f}", delta, hint)  # 單位放標題：大字只放數字，375 寬才不會擠成三行
    share = lambda r: None if r is None or r["explore_share"] is None else round(r["explore_share"] * 100)
    e_now, e_prev = share(rows[-1] if rows else None), share(rows[-2] if len(rows) > 1 else None)
    explore = tile("", "探索人力占比", DASH if e_now is None else f"{e_now}%", _delta(e_now, e_prev, "%"),
                   "有產出的人的時間，投到這週第一次有完成卡的 epic")
    stall = tile("", "停滯專案", str(stalled["now"]), _delta(stalled["now"], stalled["prev"], ""),
                 f'{IDLE_DAYS} 天沒有卡更新 · <a href="#step-in">看是哪幾個</a>', warn=bool(stalled["now"]))
    return f'<ul class="kpis wk-kpis" style="--kpis:3">{rate}{explore}{stall}</ul>'


def _short_week(week: str) -> str:
    return "W" + week.split("-W")[1]


def _compare(now, prev, unit: str, fmt=str) -> str:
    """接在觀察句後面的上週比較：「，比上週多／少 N」；上週沒有值就不寫，不猜。"""
    if prev is None or now is None:
        return ""
    if now == prev:
        return "，跟上週一樣"
    return f'，比上週{"多" if now > prev else "少"} {fmt(abs(now - prev))}{unit}'


def _bars(rows: list, value, label, note, aria: str, dividers=None) -> str:
    """一週一根直條、從 0 起，最新一週深色、其餘淺色，值寫在頂端、週次寫在底下；note(row) 是週次下第二行小字（沒有就空字串）。
    值是 None 的週不畫條，頂端寫「—」：那週沒有可算的卡，不是 0。dividers 是 {索引: 字}，在那一根左邊畫虛線分界並寫字
    （名單人數變了的地方，跨過去比較要小心；fable 審 2026-11-02）。"""
    w, h, base, tall = 600, 210, 170, 124
    top = max((value(r) or 0) for r in rows) or 1  # 近幾週全是 0 時條都是 0 高，不除以 0
    step = (w - 20) / len(rows)
    bw = min(56, step - 16)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(aria)}">']
    for i, text in (dividers or {}).items():
        x = 10 + i * step
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="12" y2="{base}" class="divider"/>'
                   f'<text x="{x + 4:.1f}" y="22" class="t-axis">{esc(text)}</text>')
    for i, r in enumerate(rows):
        v = value(r)
        x = 10 + i * step + (step - bw) / 2
        hb = (v or 0) / top * tall
        cls = "bar-wk" if i == len(rows) - 1 else "bar-before"
        out.append(f'<rect x="{x:.1f}" y="{base - hb:.1f}" width="{bw:.1f}" height="{hb:.1f}" rx="2" class="{cls}">'
                   f'<title>{_short_week(r["week"])} {esc(label(r))}</title></rect>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base - hb - 6:.1f}" text-anchor="middle" class="t-val">{esc(label(r))}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 17}" text-anchor="middle" class="t-axis">{_short_week(r["week"])}</text>')
        if note(r):
            out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 33}" text-anchor="middle" class="t-axis">{esc(note(r))}</text>')
    out.append(f'<line x1="0" x2="{w}" y1="{base}" y2="{base}" class="axis"/></svg>')
    return "".join(out)


def _chart(title: str, sub: str, body: str, wide=False) -> str:
    """一圖一訊息：標題是這張圖的觀察（程式照數字組句，只陳述、不寫原因），副標寫指標、範圍、期間。"""
    return f'<div class="viz-panel{" wide" if wide else ""}"><h4>{esc(title)}</h4><p class="note">{esc(sub)}</p>{body}</div>'


def _roster_dividers(history: list, offset: int) -> dict:
    """畫面上的第 i 根（history[offset + i]）名單人數跟前一週不同就畫分界：「名單 3→7 人」。前一週在畫面外也比
    （Codex 審 84a886d：畫面第一根之前那週被切掉時，分界跟著不見，跨名單口徑的滾動值看起來可以直接比）。"""
    out = {}
    for i in range(len(history) - offset):
        j = offset + i
        if j and history[j]["roster"] is not None and history[j - 1]["roster"] != history[j]["roster"]:
            out[i] = f'名單 {history[j - 1]["roster"]}→{history[j]["roster"]} 人'
    return out


def _output_chart(history: list, full: int) -> str:
    """消化速度：每可運用人週結案幾個 epic（2026-11-02 使用者要求用每人每週可運用時間、一天 8 小時看可運用總額；同日裁以消化速率為主軸、
    改算 epic 結案）。單週一根柱，畫最近 TREND_WEEKS 週，柱下寫到那週為止的四週值；四週值用完整的 history 算，畫面第一根之前的週也算進去
    （Codex 審 70db523：只拿畫出來那 6 週算，前三根永遠沒有四週值）。可運用只當分母，不跟回填的實際工時比。
    full 是現在「卡都做完，epic 沒關」（100% 待結案）的 epic 數（跟出手區同一個判斷），不算完成，寫在副標。"""
    rows = history[-TREND_WEEKS:]
    now = rows[-1]
    span = f"近 {len(rows)} 週"
    if now["person_days"] is None:
        return _chart("沒有成員名單，算不出可運用人日", f"本週結案 {now['epics_closed']} 個 epic；補 config/team.json 與 config/holidays.json 後重算", "")
    offset = len(history) - len(rows)
    rolling = {r["week"]: _rolling_rate(history, offset + i) for i, r in enumerate(rows)}
    rate, r4 = now["epics_per_person_week"], rolling[now["week"]]
    title = (f"本週結案 {now['epics_closed']} 個 epic，{rate:.2f} 個／可運用人週" + (f"；過去四週平均 {r4:.2f}" if r4 is not None else "")
             if rate is not None else "本週沒有可運用人日")
    weeks = now["person_days"] / capacity_lib.DAYS_PER_WEEK
    sub = (f"結案 epic ÷ 可運用 {weeks:g} 人週（名單 {now['roster']} 人的工作日合計 {now['person_days']} 人日 ÷ {capacity_lib.DAYS_PER_WEEK}，"
           f"已扣國定假日、未扣請假）；另有 {full} 個 epic 底下的卡全完成、還沒結案，不算進來。"
           f"epic 大小不一，單週會被集中結案拉動，看四週值 · {span}")
    body = _bars(rows, lambda r: r["epics_per_person_week"], lambda r: DASH if r["epics_per_person_week"] is None else f'{r["epics_per_person_week"]:.2f}',
                 lambda r: f'4 週 {rolling[r["week"]]:.2f}' if rolling[r["week"]] is not None else "", f"{span}消化速度：每可運用人週結案 epic 數",
                 _roster_dividers(history, offset))
    return _chart(title, sub, body)


def _time_chart(history: list) -> str:
    """時間怎麼分：每週一根 100% 柱，畫最近 TREND_WEEKS 週，下面深色是探索、上面淺色是開發，探索的百分比寫在柱頂，
    柱下是算占比的人數（不含未指派）；名單分界跟消化速率圖一樣看畫面外的前一週。
    只陳述同期數字，不寫成探索多所以速率低（fable 審 2026-11-02）。"""
    rows = history[-TREND_WEEKS:]
    offset = len(history) - len(rows)
    now, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
    span = f"近 {len(rows)} 週"
    pct = lambda r: None if r is None or r["explore_share"] is None else round(r["explore_share"] * 100)
    if pct(now) is None:
        title = "本週沒有人有完成卡，看不出時間怎麼分"
    else:
        title = f"本週有產出的人把 {pct(now)}% 的時間投到探索" + (_compare(pct(now), pct(prev), " 個百分點") if pct(prev) is not None else "")
    w, h, base, tall = 600, 210, 170, 124
    step = (w - 20) / len(rows)
    bw = min(56, step - 16)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{span}探索與開發的時間占比">']
    for i, text in _roster_dividers(history, offset).items():
        x = 10 + i * step
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="12" y2="{base}" class="divider"/><text x="{x + 4:.1f}" y="22" class="t-axis">{esc(text)}</text>')
    for i, r in enumerate(rows):
        x = 10 + i * step + (step - bw) / 2
        p = pct(r)
        if p is not None:
            he = r["explore_share"] * tall
            out.append(f'<rect x="{x:.1f}" y="{base - tall:.1f}" width="{bw:.1f}" height="{tall - he:.1f}" rx="2" class="bar-before"><title>{_short_week(r["week"])} 開發 {100 - p}%</title></rect>')
            out.append(f'<rect x="{x:.1f}" y="{base - he:.1f}" width="{bw:.1f}" height="{he:.1f}" rx="2" class="bar-wk"><title>{_short_week(r["week"])} 探索 {p}%</title></rect>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base - tall - 6:.1f}" text-anchor="middle" class="t-val">{DASH if p is None else f"{p}%"}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 17}" text-anchor="middle" class="t-axis">{_short_week(r["week"])}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 33}" text-anchor="middle" class="t-axis">{r["explore_people"]} 人</text>')
    out.append(f'<line x1="0" x2="{w}" y1="{base}" y2="{base}" class="axis"/></svg>')
    sub = ("每個有產出的人一週先換成 100%（用自己填的工時當權重），切成探索（這週第一次有完成卡的 epic）與開發（其餘，含未歸屬 epic），"
           f"再對有產出的人平均；柱頂是探索占比；只陳述同期數字 · {span}")
    legend = '<p class="legend"><i class="sw-week"></i>探索 <i class="sw-before"></i>開發</p>'
    return _chart(title, sub, legend + "".join(out))


def _epic_state(e) -> str:
    """未結案 epic 歸一類，依序判：底下的卡全完成（「卡都做完，epic 沒關」）→ 停滯 → 本週有完成卡（推進）→ 其他。"""
    p = e["progress"]
    if p["total_cards"] and p["done_cards"] == p["total_cards"]:
        return "full"
    if e["stalled"]:
        return "stalled"
    return "moving" if e["week"]["completed_count"] else "rest"


def _quality_line(metrics) -> str:
    """單卡工時退出主圖後的資料品質一行（fable 審 2026-11-02：單卡平均是人員組成效應，不是快慢）。"""
    now = metrics["health"][-1]
    over = sum(1 for c in metrics["checks"] if c["kind"] == "hours_over_capacity")
    strangers = sum(1 for c in metrics["checks"] if c["kind"] == "not_in_team")
    parts = [f'本週完成 {now["completed"]} 張，有填工時 {now["completed"] - now["unestimated"]} 張' + (f'、{UNFILLED} {now["unestimated"]} 張' if now["unestimated"] else "")]
    if over:
        parts.append(f'近 {CAPACITY_CHECK_WEEKS} 週有 {over} 個人週的實際工時超過當週可運用，可能填錯')
    if strangers:
        parts.append(f'{strangers} 人有完成卡卻不在成員名單')
    tail = '（見頁尾<a href="#checks">資料檢查</a>）' if over or strangers else ""
    return f'<p class="quality">資料品質：{esc("；".join(parts))}{tail}</p>'


def _viz(metrics, page) -> str:
    """兩張圖（2026-11-02 使用者裁「照審查改」，取代同日的四張）：消化速率、時間怎麼分；圖下資料品質一行。
    投入比例與未結案 epic 狀態圖移出本週頁（前者是「各單位分到幾個人」、一人全在某單位就是 1/人數，會誤導；
    後者的「卡都做完，epic 沒關」併進出手區）。plan：jira-weekly-report · 本週頁產值與探索。"""
    history = metrics["health"]
    if not history:
        return '<p class="muted">還沒有完成的卡。</p>'
    # 兩張圖都收完整歷史、自己切最後 TREND_WEEKS 週：滾動值與名單分界要看畫面外的週
    full = sum(1 for e in metrics["epics"] if e["key"] and e["status"] != "Done" and _epic_state(e) == "full")
    return f'<div class="viz">{_output_chart(history, full)}{_time_chart(history)}</div>' + _quality_line(metrics)


STEP_IN_RULE = (f"前三個條件用同一個時鐘：最後一次更新到本期結束，{IDLE_DAYS} 天為界。專案沒動＝底下的卡都沒更新；"
                "卡沒動＝進行中、本身沒更新；需要決定＝Kick off 的「需要決定」有寫就列，附 Kick off 卡寫了幾天。"
                f"第四個是「{FULL_LABEL}」＝底下算進進度的卡全部完成，epic 在 Jira 還沒結案。")


def _step_in(metrics, page) -> str:
    """這週要你出手的地方（2026-11-02 使用者裁甲，取代 2026-10-28 三類原因各列一區）：一個專案一列，
    幾天沒動、沒動的卡、需要決定三欄並排，三個條件同一個時鐘；依幾天沒動排，久的在上。
    沒動的卡所屬的專案不在未結案專案裡（未歸屬 epic、epic 已結案）也自成一列，不會漏掉。"""
    epics = {e["id"]: e for e in metrics["epics"] if e["key"] and e["status"] != "Done"}
    idle_cards = {}
    for r in metrics["in_progress"]:
        if r["leaf"] and r["idle_days"] >= IDLE_DAYS:
            idle_cards.setdefault(r["group"], []).append(r)
    rows = []
    for gid in set(epics) | set(idle_cards):
        e = epics.get(gid)
        cards = sorted(idle_cards.get(gid, []), key=lambda r: (-r["idle_days"], r["key"]))
        decision = (e["kickoff"] or {}).get("sections", {}).get("decision") if e else None
        if e is not None and not (e["stalled"] or cards or decision or _epic_state(e) == "full"):
            continue
        idle = e["idle_days"] if e else None
        sort_idle = idle if idle is not None else max(r["idle_days"] for r in cards)
        rows.append((-sort_idle, e["key"] if e else cards[0]["project"], gid, e, cards, decision))
    if not rows:
        return f'<h3 id="step-in">這週要你出手的地方</h3><p class="muted">沒有專案或卡 {IDLE_DAYS} 天沒動、沒有卡都做完卻沒關的 epic，Kick off 也沒寫需要決定。</p>'
    rows.sort(key=lambda t: (t[0], t[3] is None, issue_sort_key(t[1]) if t[3] else (t[1],)))
    head = ('<div class="si head"><span>專案</span><span>負責人</span><span class="num">幾天沒動</span>'
            f'<span>進行中、{IDLE_DAYS} 天沒更新的卡</span><span>Kick off 寫的需要決定</span></div>')
    body = []
    for _, _, gid, e, cards, decision in rows:
        if e is not None:
            name = page.title(e["key"], e["summary"]) + (FULL_TAG if _epic_state(e) == "full" else "")
            owner = esc(e["owner"] or UNASSIGNED_OWNER)
            idle = f'<b class="alert">{e["idle_days"]} 天</b>' if e["stalled"] else f'{e["idle_days"]} 天'
        else:
            name, owner, idle = esc(cards[0]["project"]), DASH, DASH
        if cards:
            items = "".join(f'<li>{page.issue(r["key"])} {esc(r["summary"])} <span class="muted">· {esc(r["owner"])} · {r["idle_days"]} 天</span></li>' for r in cards)
            cards_html = f'<details><summary>{len(cards)} 張</summary><ul>{items}</ul></details>'
        else:
            cards_html = f'<span class="muted">{DASH}</span>'
        if decision:
            written = e["kickoff"].get("idle_days")
            decision_html = esc(decision) + (f' <span class="muted">· 寫於 {written} 天前</span>' if written is not None else "")
        else:
            decision_html = f'<span class="muted">{DASH}</span>'
        body.append(f'<div class="si"><span class="name">{name}</span><span class="owner" data-label="負責人">{owner}</span>'
                    f'<span class="num" data-label="幾天沒動">{idle}</span><span data-label="沒動的卡">{cards_html}</span>'
                    f'<span class="dec" data-label="需要決定">{decision_html}</span></div>')
    stalled = sum(1 for t in rows if t[3] is not None and t[3]["stalled"])
    ncards = sum(len(t[4]) for t in rows)
    ndec = sum(1 for t in rows if t[5])
    nfull = sum(1 for t in rows if t[3] is not None and _epic_state(t[3]) == "full")
    return (f'<h3 id="step-in">這週要你出手的地方：{len(rows)} 個專案</h3>'
            f'<p class="note">其中專案沒動 {stalled} 個、卡沒動 {ncards} 張、需要決定 {ndec} 個、「{FULL_LABEL}」{nfull} 個；依幾天沒動排，久的在上。</p>'
            f'<p class="note">{esc(STEP_IN_RULE)}</p><div class="sis">{head}{"".join(body)}</div>')


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
    pad = 4  # 頭尾各留 4px：第一根的週次、最後一根的三位數張數置中時不被裁掉（fable 審 26350ed）
    w, h, base, tall = n * slot + 2 * pad, 50, 36, 22
    bw = slot - 4
    out = []
    for i, t in enumerate(trend):
        last = i == len(trend) - 1
        x = pad + i * slot + 2
        hb = max(t["completed"] / top * tall, 0.5)
        out.append(f'<rect x="{x:.1f}" y="{base - hb:.1f}" width="{bw:.1f}" height="{hb:.1f}" rx="1.5" '
                   f'class="{"bar-wk" if last else "bar-before"}"><title>{_short_week(t["week"])} {t["completed"]} 張</title></rect>')
        if not dense or last:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{base - hb - 3:.1f}" text-anchor="middle" class="t-val">{t["completed"]}</text>')
        if (len(trend) - 1 - i) % every == 0:  # 從最新一週往回數，最新一週一定標
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - 2}" text-anchor="middle">{_short_week(t["week"])}</text>')
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="近 {len(trend)} 週完成張數">{"".join(out)}</svg>')


def _doing_list(person: dict, doing: dict, page, limit) -> str:
    """人手上進行中的卡與幾天沒更新，久的在上；跟出手區同一個時鐘（2026-11-02 起，取代進入目前狀態幾天）。"""
    idle = {k: doing[k]["idle_days"] for k in person["in_progress"]["keys"]}
    keys = sorted(idle, key=lambda k: (-idle[k], k))
    shown = keys if limit is None else keys[:limit]
    items = "".join(f'<li>{page.issue(k)} {esc(doing[k]["summary"])} <span class="{"alert" if idle[k] >= IDLE_DAYS else "muted"}">{idle[k]} 天沒更新</span></li>' for k in shown)
    more = f'<li class="muted">另 {len(keys) - len(shown)} 張</li>' if len(keys) > len(shown) else ""
    return f'<ul class="list-doing">{items or "<li class=muted>沒有進行中的卡</li>"}{more}</ul>'


def _owned_states(epics: list) -> str:
    """負責的未結案專案狀態小標籤：「卡都做完，epic 沒關」、停滯、需要決定，只寫有的（fable 審 2026-11-02：只列清單做不了判斷）。"""
    full = sum(1 for e in epics if _epic_state(e) == "full")
    stalled = sum(1 for e in epics if e["stalled"])
    decision = sum(1 for e in epics if e["kickoff"] and e["kickoff"]["sections"].get("decision"))
    tags = [(full, FULL_LABEL), (stalled, f"{IDLE_DAYS} 天以上沒動"), (decision, "需要決定")]
    return "".join(f'<span class="tag warn">{label} {n}</span>' for n, label in tags if n)


def _recent_mix(person: dict, colors: dict) -> str:
    """近 4 週落在哪些 label：一條 100% 細條，顏色跟投入比例圖同一個 label 同一色，不在圖上的 label 用「其他」的灰；
    下面寫名字與張數（前三名，其餘併「其他」）。"""
    r = person["recent"]
    if not r["completed"]:
        return f'<p class="muted small">近 {r["weeks"]} 週沒有完成</p>'
    segs = "".join(f'<i class="{colors.get(l, "s-other")}" style="width:{n / r["completed"] * 100:.1f}%" title="{esc(l)} {n} 張"></i>' for l, n in r["labels"])
    chips = "".join(f'<span class="chip"><i class="{colors.get(l, "s-other")}"></i>{esc(l)} <b>{n}</b></span>' for l, n in r["labels"])
    rest = r["completed"] - sum(n for _, n in r["labels"])
    if rest:
        segs += f'<i class="s-other" style="width:{rest / r["completed"] * 100:.1f}%" title="{OTHER_LABEL} {rest} 張"></i>'
        chips += f'<span class="chip"><i class="s-other"></i>{OTHER_LABEL} <b>{rest}</b></span>'
    return f'<div class="mix">{segs}</div><div class="chips">{chips}</div>'


def _doing_folded(person: dict, doing: dict, page) -> str:
    """進行中的卡收合，摘要寫張數與幾張 IDLE_DAYS 天以上沒更新；展開是 _doing_list 的全部清單。"""
    keys = person["in_progress"]["keys"]
    if not keys:
        return '<p class="muted small doing-none">沒有進行中的卡</p>'
    old = sum(1 for k in keys if doing[k]["idle_days"] >= IDLE_DAYS)
    flag = f'，<span class="alert">{old} 張 {IDLE_DAYS} 天以上沒更新</span>' if old else ""
    return f'<details class="doing-fold"><summary>進行中 {len(keys)} 張{flag}</summary>{_doing_list(person, doing, page, None)}</details>'


def _week_people(metrics, page) -> str:
    """本週頁人卡（2026-11-02 使用者裁版型甲）：頂端名字、本週張數與近 6 週小長條（只跟自己比）；
    下面左欄「負責」（狀態小標籤，清單超過 3 個收合）、右欄「近 4 週落在」（細條加張數）；進行中的卡收合。
    「未指派」不出人卡，那些卡在出手區與專案一覽看得到。"""
    doing = {r["key"]: r for r in metrics["in_progress"]}
    names = metrics["allocation"]["labels"]
    colors = {n: (f"s{j + 1}" if j < SERIES else "s-other") for j, n in enumerate(names)}
    owned = {}
    for e in metrics["epics"]:
        if e["key"] and e["status"] != "Done":
            owned.setdefault(e["owner"] or UNASSIGNED_OWNER, []).append(e)
    cards = []
    for p in _by_name(metrics["people"]):
        if p["name"] == UNASSIGNED_OWNER or (not p["recent"]["completed"] and not p["in_progress"]["count"]):
            continue
        trend = p["trend"][-TREND_WEEKS:]
        this_week = next(t["completed"] for t in p["trend"] if t["week"] == metrics["period"]["week"])
        mine = owned.get(p["name"], [])
        items = "".join(f'<li>{page.title(e["key"], e["summary"])}</li>' for e in mine)
        if len(mine) > 3:
            first = "".join(f'<li>{page.title(e["key"], e["summary"])}</li>' for e in mine[:3])
            rest = "".join(f'<li>{page.title(e["key"], e["summary"])}</li>' for e in mine[3:])
            listing = f'<ul class="owned">{first}</ul><details class="more"><summary>另 {len(mine) - 3} 個</summary><ul class="owned">{rest}</ul></details>'
        else:
            listing = f'<ul class="owned">{items}</ul>' if mine else '<p class="muted small">沒有負責中的專案</p>'
        cards.append(f'<article class="pcard"><div class="phead"><h4>{esc(p["name"])}</h4>'
                     f'<span class="muted small">本週 {this_week} 張 · 近 {len(trend)} 週只跟自己比，深色＝本週</span></div>{_spark(trend)}'
                     f'<div class="pgrid"><div class="pblock"><h5>負責 <span class="muted">{len(mine)} 個未結案</span></h5>'
                     f'<div class="tags">{_owned_states(mine)}</div>{listing}</div>'
                     f'<div class="pblock"><h5>近 {p["recent"]["weeks"]} 週落在 <span class="muted">{p["recent"]["completed"]} 張</span></h5>{_recent_mix(p, colors)}</div></div>'
                     f'{_doing_folded(p, doing, page)}</article>')
    return f'<div class="pcards">{"".join(cards)}</div>' if cards else '<p class="muted">這週沒有人有完成或進行中的卡。</p>'


def _week_panel(metrics, narrative, page) -> str:
    """本週頁（2026-10-28 鎖定 AC-2、AC-3，2026-11-02 改一圖一訊息）：兩個數字 → 三張圖 → 這週要你出手的地方 → 人卡 → 專案一覽（收合）。
    本週重點 highlights 照舊由 routine 產生與驗證，頁面不再獨立列；每個專案的結果句在專案一覽展開看。"""
    rows = len(metrics["epics"])
    return f"""
  {_week_kpis(metrics)}
  {_viz(metrics, page)}
  {_step_in(metrics, page)}
  <h3>人</h3>
  {_week_people(metrics, page)}
  <details class="proj-list"><summary>專案一覽（{rows} 列，點開）</summary>
  <p class="legend">進度條 <i class="sw-before"></i>之前完成 <i class="sw-week"></i>本週完成 <i class="sw-left"></i>未完成；點一列看本週結果與進行中的卡。</p>
  {_week_table(metrics, narrative, page)}
  {_other_doing(metrics, page)}
  </details>"""


def _overview_table_head(page) -> str:
    return ('<div class="proj head"><span class="name">專案</span><span class="prog">進度（深色＝本週）</span>'
            f'<span class="idle">幾天沒動</span><span class="people">人</span><span class="num">累計工時</span>{TOKEN_HEAD if page.tokens else ""}</div>')


def _people_cell(scope: dict) -> str:
    """總覽「人」欄（2026-10-07 使用者裁，取代團隊頁的專案投入表）：累計有產出的人數與名字，未指派不算人、另外註明。"""
    names = esc("、".join(scope["people"]))
    note = "<small>另有未指派</small>" if scope["unassigned"] else ""
    return f'<span class="people" data-label="人">{scope["people_count"]}{f" <small>{names}</small>" if names else ""}{note}</span>'


def _overview_state(e) -> str:
    """總覽專案的狀態：已結案 → done；底下算進進度的卡全完成 → full（「卡都做完，epic 沒關」，同 _epic_state）；其餘 → open。"""
    if e["status"] == "Done":
        return "done"
    p = e["progress"]
    return "full" if p["total_cards"] and p["done_cards"] == p["total_cards"] else "open"


def _idle_cell(e) -> str:
    """幾天沒動：跟本週頁出手區同一個時鐘（metrics 的 idle_days），停滯（IDLE_DAYS 天以上）標警示色。
    未歸屬 epic 沒有這個值，留空（窄螢幕空格不顯示）；有卡號的缺值在 _by_idle 就擋下來。"""
    if not e["key"]:
        return '<span class="idle" data-label="幾天沒動"></span>'
    days = f'<b class="alert">{e["idle_days"]} 天</b>' if e["stalled"] else f'{e["idle_days"]} 天'
    return f'<span class="idle" data-label="幾天沒動">{days}</span>'


FULL_TAG = f' <span class="tag warn">{FULL_LABEL}</span>'


def _overview_project(e, week_done: dict, staffing: dict, page, tag: str = "") -> str:
    """總覽一個未結案專案（或未歸屬 epic）一列：名稱、三段進度條、幾天沒動、人、累計工時；展開看 Kick off 目標五段，預設收合（2026-10-07 使用者裁）。
    用 <details>，沒有 JS 也能展開。Kick off 一段都沒填（含未歸屬 epic）就不給展開，點開只看到「未填」是空的承諾；
    缺 Kick off 卡仍列在資料檢查（2026-10-07 總覽 UI 盤點 U6）。
    本週段取本週頁同一個 epic 的完成數；本週沒有專案列的 epic 就是 0。"""
    token = f'<span class="num tok" data-label="Token">Token {esc(fmt_tokens(e["tokens"]))}</span>' if page.tokens else ""
    scope = staffing[e["id"]]["cumulative"] if e["id"] in staffing else {"people": [], "people_count": 0, "unassigned": False, "unestimated": 0}
    unfilled = f'<small>{scope["unestimated"]} 張{UNFILLED}</small>' if scope["unestimated"] else ""
    head = (f'<span class="name">{page.title(e["key"], e["summary"])}{tag}</span>'
            f'{_progress_cell(e["progress"], week_done.get(e["id"], 0))}{_idle_cell(e)}{_people_cell(scope)}'
            f'<span class="num" data-label="累計工時">{esc(fmt_hours(e["hours"]))}{unfilled}</span>{token}')
    body = _kickoff(e["kickoff"], BENEFIT_FIELDS) if e["key"] else ""
    if not body:
        return f'<div class="proj"><div class="proj-head">{head}</div></div>'
    return f'<details class="proj"><summary class="proj-head">{head}</summary><div class="proj-body">{body}</div></details>'


def _overview_closed(e, delivered: set, page) -> str:
    """已結案一列（段內沒有自己的表頭，結案週寫成「W42 結案」自己看得懂）：名稱、結案週、成效說明連結（有已完成的 Deliverable 才有，點了切到成效說明頁籤）、累計工時。"""
    token = f'<span class="num tok" data-label="Token">Token {esc(fmt_tokens(e["tokens"]))}</span>' if page.tokens else ""
    link = '<a href="#delivered">成效說明 ›</a>' if e["key"] in delivered else ""
    return (f'<div class="proj"><div class="proj-head"><span class="name">{page.title(e["key"], e["summary"])}</span>'
            f'<span class="cw" data-label="結案週">{esc(_short_week(e["closed_week"]))} 結案</span>'
            f'<span class="dv" data-label="成效說明">{link}</span>'
            f'<span class="num" data-label="累計工時">{esc(fmt_hours(e["hours"]))}</span>{token}</div></div>')


def _by_idle(epics: list) -> list:
    """未結案的依幾天沒動排、久的在上；有卡號的未結案 epic 少了 idle_days 就讓它炸，不排到最後裝沒事。"""
    for e in epics:
        if e["idle_days"] is None:
            raise ValueError(f'{e["key"]} 未結案卻沒有 idle_days：metrics 要用 schema {SCHEMA} 重算')
    return sorted(epics, key=lambda e: (-e["idle_days"], issue_sort_key(e["key"])))


def _by_closed(epics: list) -> list:
    """已結案的依結案週排、新的在上；少了 closed_week 就讓它炸。"""
    for e in epics:
        if not e["closed_week"]:
            raise ValueError(f'{e["key"]} 已結案卻沒有 closed_week：metrics 要用 schema {SCHEMA} 重算')
    return sorted(epics, key=lambda e: (e["closed_week"], issue_sort_key(e["key"])), reverse=True)


def _overview_projects(metrics, page) -> str:
    """專案依 label 分段，段的順序跟 label 表一樣；未歸屬 epic 放最後一段。段首的工時取 label 表，兩處不會各算一次。
    段內依狀態排（2026-11-02 使用者裁版型乙，plan：jira-weekly-report · 總覽優化）：進行中依幾天沒動排、久的在上，
    再列「卡都做完，epic 沒關」（帶標籤，也依幾天沒動排），已結案收在段尾、新結案的在上。label 分段與區塊順序是 2026-10-07 鎖定的，不動。"""
    epics = metrics["cumulative"]["epics"]
    if not epics:
        return '<p class="muted">還沒有任何專案</p>'
    week_done = {e["id"]: e["week"]["completed_count"] for e in metrics["epics"]}
    staffing = {s["id"]: s for s in metrics["staffing"]}
    delivered = {c["epic"] for c in metrics["deliverables"] or []}
    keyed = [e for e in epics if e["key"]]
    full_all = [e for e in keyed if _overview_state(e) == "full"]
    idle_open = sum(1 for e in keyed if _overview_state(e) == "open" and e["stalled"])
    sections = [f'<p class="note">每段先列進行中（久沒動的在上），再列「{FULL_LABEL}」，已結案收在段尾。全部「{FULL_LABEL}」{len(full_all)} 個；'
                f'進行中 {IDLE_DAYS} 天以上沒動 {idle_open} 個，「{FULL_LABEL}」裡也 {IDLE_DAYS} 天以上沒動的 {sum(1 for e in full_all if e["stalled"])} 個。</p>',
                _overview_table_head(page)]
    for row in metrics["labels"]:
        members = [e for e in keyed if e["label"] == row["label"]]
        if not members:
            continue
        open_ = _by_idle([e for e in members if _overview_state(e) == "open"])
        full = _by_idle([e for e in members if _overview_state(e) == "full"])
        done = _by_closed([e for e in members if _overview_state(e) == "done"])
        def tally(name, rows):  # 各狀態個數，後面接其中幾個久沒動（AC-2：段標題寫各狀態個數與久沒動個數）
            idle_n = sum(1 for e in rows if e["stalled"])
            return (f"{name} {len(rows)}" + (f"（{idle_n} 個久沒動）" if idle_n else "")) if rows else ""
        counts = " · ".join(filter(None, [   # 狀態名本身有逗號，用「·」分隔才不混
            tally("進行中", open_), tally(FULL_LABEL, full),
            f"已結案 {len(done)}" if done else ""]))
        body = ("".join(_overview_project(e, week_done, staffing, page) for e in open_)
                + "".join(_overview_project(e, week_done, staffing, page, FULL_TAG) for e in full))
        if done:
            body += (f'<details class="closed"><summary>已結案 {len(done)} 個</summary>'
                     f'{"".join(_overview_closed(e, delivered, page) for e in done)}</details>')
        sections.append(f'<h4>{esc(row["label"])} <span class="muted">· {esc(counts)} · 累計工時 {esc(fmt_hours(row["cumulative"]["hours"]))}</span></h4>'
                        f'<div class="projs">{body}</div>')
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


def _roster_changes(history: list) -> list:
    """名單人數變的週：[(週, 前一週人數, 這週人數)]，從 health 的 roster 推，不寫死哪一週（W41 團隊搬入是 3→7）。
    名單沒資料（None，沒有 config/team.json 的舊資料或測試）的週不比。"""
    return [(history[i]["week"], history[i - 1]["roster"], history[i]["roster"]) for i in range(1, len(history))
            if None not in (history[i - 1]["roster"], history[i]["roster"]) and history[i - 1]["roster"] != history[i]["roster"]]


def _since_note(metrics) -> str:
    """起算說明。名單人數變過就接一句分界：跨期的累計數字含人少的那幾週，讀的人要知道。"""
    c, history = metrics["cumulative"], metrics["health"]
    if not c["since"]:
        return '<p class="note">到本期結束還沒有任何完成的卡。</p>'
    base = f'從 {esc(fmt_date(c["since"]))} 第一張完成卡起算，到本期結束。'
    changes = _roster_changes(history)
    if not changes:
        return f'<p class="note">{base}</p>'
    first, before, _ = changes[0]
    early = [r for r in history if r["week"] < first]
    people = sorted({r["explore_people"] for r in early})
    who = f"{people[0]}～{people[-1]}" if len(people) > 1 else str(people[0])
    span = _short_week(early[0]["week"]) + (f'～{_short_week(early[-1]["week"])}' if len(early) > 1 else "")
    marks = "、".join(f"{_short_week(w)} 起名單 {b}→{a} 人" for w, b, a in changes)
    return (f'<p class="note">{base}<b class="era">{esc(marks)}</b>：{esc(span)} 名單 {before} 人、有完成卡的 {esc(who)} 人，'
            f'累計數字含這 {len(early)} 週。</p>')


def _done_weeks(metrics, page) -> str:
    """完成：一週一列摘要（週、名單人數、完成張數、實際工時），點開才看依專案分組的逐卡，新的週在上（2026-11-02 總覽優化 AC-5）。
    名單人數變的地方在兩週之間畫分界線，上下兩段的張數不能直接比。分界照週次位置畫，不看那週有沒有列：
    名單變動那週沒有完成卡（cumulative.weeks 沒有那一列）時，線畫在它前後兩列之間（fable 審 2026-11-02）。"""
    c = metrics["cumulative"]
    if not c["weeks"]:
        return '<p class="muted">還沒有任何完成的卡。</p>'
    roster = {h["week"]: h["roster"] for h in metrics["health"]}
    changes = _roster_changes(metrics["health"])
    order = {e["id"]: i for i, e in enumerate(c["epics"])}
    out = ['<div class="wk-sum"><div class="wrow head"><span>週</span><span>名單</span><span>完成張數</span><span>實際工時</span></div>']
    newer = None
    for w in c["weeks"]:  # 新的在上：這一列上方的線是變動週落在（這週, 上一列那週] 的
        for week, b, a in reversed(changes):
            if w["week"] < week and (newer is None or week <= newer):
                out.append(f'<div class="era-line"><span>名單 {b}→{a} 人（{esc(_short_week(week))} 起）</span></div>')
        newer = w["week"]
        cards = [r for r in c["completed"] if r["week"] == w["week"]]
        size = roster.get(w["week"])
        out.append(f'<details class="wrow"><summary><span>{esc(w["week"])}</span><span>{f"{size} 人" if size is not None else DASH}</span>'
                   f'<span>{w["completed"]} 張</span><span>{esc(fmt_hours(w["hours"]))}</span></summary>'
                   f'{_week_projects(cards, order, page)}</details>')
    return "".join(out) + "</div>"


def _cumulative_panel(metrics, page) -> str:
    """區塊順序：資源 → 依 label → 專案列表 → 完成（2026-10-07 使用者要求，取代同日「跟本週頁一致」；
    2026-10-28 團隊表拿掉：每人累計工時與張數並排就是人與人比較，跟「不排名」衝突，人的累計看團隊頁）。
    資源的 epic 數是專案列表裡的 epic 數，未歸屬 epic 不是一個 epic，不算；下面一定併列進行中、「卡都做完，epic 沒關」、已結案三個數
    （fable 審 2026-11-02：累計結案多是小題，單看結案數會誤讀）。名單人數變的週在起算說明、依 label 與完成三處標出來。"""
    c = metrics["cumulative"]
    changes = _roster_changes(metrics["health"])
    marks = "、".join(f"{_short_week(w)} 起 {b}→{a} 人" for w, b, a in changes)
    era = f'<p class="legend">累計跨過名單人數變動（{esc(marks)}），前後的週人數不同。</p>' if changes else ""
    return f"""
  {_since_note(metrics)}
  <h3>資源</h3>
  {_resources(c["totals"], "累計", "累計工時", page, epics=[e for e in c["epics"] if e["key"]])}
  <h3>依 label</h3>
  {era}{_labels(metrics)}
  <h3>專案列表</h3>
  <p class="legend">進度條 <i class="sw-before"></i>之前完成 <i class="sw-week"></i>本週完成 <i class="sw-left"></i>未完成；幾天沒動＝底下的卡最後一次更新到本期結束（還沒有卡的看 epic 自己），{IDLE_DAYS} 天以上標色；點一列看 Kick off 的目標五段。</p>
  {_overview_projects(metrics, page)}
  <h3>完成</h3>
  {_done_weeks(metrics, page)}
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



def _links(values: list) -> str:
    """Code、Env：只把 https:// 開頭的做成連結，其餘照原文。"""
    return "；".join(f'<a href="{esc(v)}">{esc(v[len("https://"):])}</a>' if v.startswith("https://") else esc(v) for v in values)


def _dv_outcome(card: dict) -> str:
    if not (card["metric"] and card["base"] and card["now"]):
        return '<div class="dv-outcome missing">成效未填</div>'
    more = f' <span class="dv-more">另 {len(card["other"])} 項，見完整說明</span>' if card["other"] else ""
    return (f'<div class="dv-outcome"><small>{esc(card["metric"].replace(chr(10), " "))}</small>'
            f'<strong>{esc(card["base"])} → {esc(card["now"])}</strong>{more}</div>')


def _dv_card(card: dict, page) -> str:
    """成效說明一張卡：版型照原型 B（vault 的 jira-weekly-report · 成效說明原型B.html，2026-10-29 鎖定）。"""
    items = []

    def add(label, body):
        items.append(f"<div><dt>{label}</dt><dd>{body}</dd></div>")

    def bullets(values):
        return "<ul>" + "".join(f"<li>{esc(v)}</li>" for v in values) + "</ul>"

    if card["features"]:
        add("交付功能", bullets(card["features"]))
    if card["metric"] and card["base"] and card["now"]:
        main = (f'{esc(card["metric"].replace(chr(10), " "))}：{esc(card["base"])}（{esc(fmt_date(card["base_date"]) if card["base_date"] else "日期未填")}）'
                f' → {esc(card["now"])}（{esc(fmt_date(card["now_date"]) if card["now_date"] else "日期未填")}）')
        source = f'<br><span class="muted">來源：{esc(card["source"])}</span>' if card["source"] else ""
        add("成效", main + source + "".join(f"<br>{esc(o)}" for o in card["other"]))
    if card["acceptance"]:
        add("驗收", "<ul>" + "".join(
            f'<li>{esc(a["condition"])}'
            + (f' → <b class="{"bad" if a["result"] == "未通過" else ""}">{esc(a["result"])}</b>' if a["result"] else "")
            + (f'<span class="muted">，{esc(a["where"])}</span>' if a["where"] else "") + "</li>"
            for a in card["acceptance"]) + "</ul>")
    if card["not_done"]:
        add("沒做的", bullets(card["not_done"]))
    if card["attachments"]:
        add("畫面", f'{esc("、".join(card["attachments"]))}<br><span class="muted">圖在 Jira 卡 {page.issue(card["key"])} 的附件</span>')
    if card["code"]:
        add("程式碼", _links(card["code"]))
    if card["env"]:
        add("開發環境", _links(card["env"]))
    if card["versions"]:
        add("版本", "、".join(esc(v["name"]) + (f'（{esc(fmt_date(v["release_date"]))}）' if v["release_date"] else "") for v in card["versions"]))
    if card["acceptance"]:
        acc = f'驗收 {card["passed"]}/{len(card["acceptance"])} 通過' + (f'，<span class="bad">{card["failed"]} 未通過</span>' if card["failed"] else "")
    else:
        acc = "驗收未填"
    delivered = esc(card["delivered"]) if card["delivered"] else '<span class="muted">交付了什麼未填</span>'
    details = f'<details><summary>完整說明</summary><dl>{"".join(items)}</dl></details>' if items else ""
    return (f'<article class="dv-card"><div class="dv-top"><span class="dv-title"><b>{page.issue(card["epic"])}</b> {esc(card["epic_summary"])}</span>'
            f'<span class="dv-label">{esc(card["label"] or UNLABELED)}</span></div>'
            f'<p class="dv-delivered">{delivered}</p>{_dv_outcome(card)}'
            f'<p class="dv-meta">{acc}，{esc(fmt_date(card["done"]))} 交付 · Deliverable {page.issue(card["key"])}</p>{details}</article>')


def _delivered_panel(metrics, page) -> str:
    """成效說明：已完成的 Deliverable，依完成那一期分段、新的在上，一排兩張卡（2026-10-29 鎖定 AC-3）。"""
    cards = metrics["deliverables"]
    if cards is None:
        return '<p class="note">這一期的資料沒有抓 Deliverable，成效說明從之後重抓的週報開始有。</p>'
    if not cards:
        return '<p class="dv-empty">還沒有已交付的專案。epic 完成、Deliverable 卡填好後，下一期週報會列在這裡。</p>'
    weeks = {}
    for card in cards:
        weeks.setdefault(card["week"], []).append(card)
    sections = "".join(
        f'<h3>{esc(week)} 交付 <span class="muted">{len(group)}</span></h3>'
        f'<div class="dv-cards">{"".join(_dv_card(c, page) for c in group)}</div>'
        for week, group in sorted(weeks.items(), reverse=True))
    return f'<p class="note">依交付週排，新的在上；內容取自各 epic 的 Deliverable 卡，共 {len(cards)} 個專案。</p>{sections}'


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
        ("累計", f'從第一張完成卡起算，到本週結束（{fmt_date(period["week_ending"])}）為止。'
                 "成員名單（config/team.json）人數變過的週，在總覽起算說明、依 label 與完成清單標出來，前後兩段的數字不能直接比。"),
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
        ("Done／On Track", "Done 是 epic 在 Jira 已完成；On Track 只表示 epic 還沒完成，不代表進度評估。"
                           "總覽專案列表的已結案收在每段最後，標 epic 在 Jira 完成的那一週（結案週）；"
                           "資源的已結案數含底下沒有完成工作卡的 epic，消化速度不算這種，所以兩個數可能差幾個（差的列在資料檢查）。"),
        ("沒動", f"最後一次更新到本週結束過了幾天（只改 label 也算更新）。專案看底下的卡（不算 Kick off），卡看它自己；"
                 f"{IDLE_DAYS} 天以上列進「這週要你出手的地方」，人卡與總覽專案列表的幾天沒動標警示色。Kick off 的「需要決定」沒有沒動這回事，有寫就列，附 Kick off 卡寫了幾天。"),
        ("停滯", f"epic 還沒完成，專案沒動 {IDLE_DAYS} 天以上；底下沒有卡就看 epic 自己。"),
        ("可運用人日", f"config/team.json 成員名單裡，每人在職期間的工作日加總；工作日是週一到週五扣掉 config/holidays.json 的國定假日"
                      f"（人事行政總處辦公日曆表），加上補班日，一天 {capacity_lib.HOURS_PER_DAY} 小時。請假還沒有資料來源，沒扣。"
                      "只拿來當產出的分母，不跟實際工時比：實際工時是完工後回填，一人一週超過可運用時數的列進資料檢查，可能填錯。"),
        ("新 epic", "這週第一次有完成卡的 epic（不含未歸屬 epic）。epic 建立當天就有完成卡的另外數出來，可能是事後一次開卡結卡；只陳述同期數字，不代表原因。"),
        (FULL_LABEL, "epic 底下算進進度的卡都完成了，但 epic 本身在 Jira 還沒結案（以前叫 100% 待結案）。"
                       "可能是做完忘了關（去 Jira 關掉 epic、補 Deliverable 卡），也可能還有工作沒開卡；資料上分不出來，要人看。"
                       "消化速度只算結案的 epic，這種不算。列進「這週要你出手的地方」，人卡與總覽專案列表也會標出來。"),
        ("消化速度", f"結案 epic 數 ÷ 可運用人週（可運用人日 ÷ {capacity_lib.DAYS_PER_WEEK}）。epic 卡本身結案才算完成，週次取結案時間；"
                     f"底下沒有任何完成工作卡的 epic 不算（列在資料檢查），「{FULL_LABEL}」的也不算。本週頁的主數字是過去四週平均（4 週結案 epic 加總 ÷ 4 週可運用人週加總），"
                     "跟上週的四週值比；上週值用這一期的資料重算，epic 重開再結案時會跟上週頁面寫的不同。單週值會被集中結案拉動。"
                     "epic 大小不一，一個 epic 可能一天也可能兩個月。"),
        ("探索人力占比", "每個有產出的人一週先把自己完成的卡換成 100%（用自己填的實際工時當權重，沒填的卡補他這週的平均，整週都沒填就用張數），"
                       "切成探索（這週第一次有完成卡的 epic）與開發（其餘，含未歸屬 epic），再對有產出的人平均。只在人內換算，不拿小時跨人比。"),
        ("投入比例", "團隊頁人卡的累計投入比例：這個人自己完成的卡，用自己填的實際工時當權重換成 100%，看落在哪些 label。"
                     f"人卡上 label 的顏色固定：畫出顏色的 label 最多 {ALLOCATION_TOP} 個，本週有份額的先排，再照近 {ALLOCATION_WINDOW} 週補滿，其餘是「其他」的灰。"),
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
        "delivered": _delivered_panel(metrics, page),
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
.idle{flex:none;width:72px;text-align:right;font-variant-numeric:tabular-nums;font-size:13px}.proj.head .idle{font-size:12px}
.cw{flex:none;width:88px;color:var(--muted);font-size:13px;text-align:right}
.dv{flex:none;width:88px;text-align:right;font-size:13px}
details.closed{border-top:1px solid var(--line);padding:6px 0}
details.closed>summary{font-size:13px;color:var(--muted)}
.proj-body{padding:0 0 10px 16px}
.labels{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px}
.lrow{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.lrow:last-child{border-bottom:0}
.lrow .lname{width:180px;flex:none}
.lrow .num{flex:none;width:200px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px}
.lrow .num small{display:block;white-space:normal}
/* 總覽完成：一週一列摘要，名單人數變的地方畫分界（2026-11-02 總覽優化） */
.wk-sum{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px}
.wrow{border-bottom:1px solid var(--line)}.wrow:last-child{border-bottom:0}
.wrow>summary,.wrow.head{display:grid;gap:12px;padding:8px 0;font-size:14px;font-variant-numeric:tabular-nums}
.wrow>summary{grid-template-columns:12px minmax(0,1fr) 80px 90px 90px}.wrow>summary::before{margin:0}
.wrow.head{grid-template-columns:minmax(0,1fr) 80px 90px 90px;color:var(--muted);font-size:12px;padding-left:24px}
.wrow>summary>span:not(:first-child),.wrow.head>span:not(:first-child){text-align:right}
.wrow .list{margin:0 0 8px 18px}
.era{color:var(--text);font-weight:600;margin-left:4px}
.era-line{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:12px;padding:4px 0;border-bottom:1px solid var(--line)}
.era-line::before,.era-line::after{content:"";flex:1;border-top:1px dashed var(--muted)}
.dv-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:stretch}
.dv-card:has(details[open]){grid-column:1/-1}
.dv-card{display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.dv-top{display:flex;align-items:baseline;gap:8px;font-size:14px}
.dv-title{flex:1;min-width:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dv-label{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap;background:var(--track-bg);color:var(--track-fg)}
.dv-delivered{margin:6px 0 10px;font-size:15px}
.dv-outcome{margin:0 0 8px;min-height:62px}
.dv-outcome small{display:block;color:var(--muted);font-size:12px}
.dv-outcome strong{font-size:22px;font-variant-numeric:tabular-nums;line-height:1.3}
.dv-more{color:var(--muted);font-size:12px}
.dv-outcome.missing{display:flex;align-items:center;padding:0 12px;border:1px dashed var(--warn-fg);border-radius:8px;background:var(--warn-bg);color:var(--warn-fg);font-weight:600}
.dv-meta{font-size:13px;color:var(--muted);margin:auto 0 0}
.dv-card details{margin-top:8px;font-size:14px}
.dv-card details summary{color:var(--muted)}
.dv-card dl{margin:6px 0 0;border-top:1px solid var(--line);padding-top:6px}
.dv-card dl div{display:grid;grid-template-columns:96px minmax(0,1fr);gap:8px;padding:2px 0}
.dv-card dt{color:var(--muted);font-size:13px}.dv-card dd{margin:0;min-width:0;overflow-wrap:anywhere}
.dv-card ul{margin:0;padding-left:18px}
.dv-card .bad{color:var(--warn-fg);font-weight:600}
.dv-empty{margin:24px 0;padding:20px;background:var(--surface);border:1px solid var(--line);border-radius:10px;color:var(--muted)}
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
/* 本週頁：兩個數字、三張圖、出手區、人卡（2026-10-28 團隊績效評估、同日第二輪改桌機寬版；2026-11-02 一圖一訊息） */
.wk-kpis{margin-top:20px}
.wk-kpis li.lead strong{font-size:36px}
.wk-kpis strong.alert,.alert{color:var(--alert)}
.wk-kpis small.hint{margin-top:2px}
.viz{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0 0}
.viz-panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.viz-panel.wide{grid-column:1/-1}
.viz-panel h4{margin:0;font-size:16px}.viz-panel .note{margin:2px 0 8px}.viz-panel .legend{margin:0 0 6px}
.viz svg{display:block;width:100%;height:auto}
.viz text{font-size:12px;fill:var(--muted)}
.spark text{font-size:10px;fill:var(--muted)}
.viz .t-val,.spark .t-val{fill:var(--text)}.viz .t-in{fill:#fff;font-weight:600}.viz .axis{stroke:var(--line)}
.viz .divider{stroke:var(--muted);stroke-dasharray:4 3}
.quality{margin:8px 0 0;font-size:13px;color:var(--muted)}
.s1{fill:var(--s1);background:var(--s1)}.s2{fill:var(--s2);background:var(--s2)}.s3{fill:var(--s3);background:var(--s3)}.s4{fill:var(--s4);background:var(--s4)}
.s5{fill:var(--s5);background:var(--s5)}.s6{fill:var(--s6);background:var(--s6)}.s7{fill:var(--s7);background:var(--s7)}.s-other{fill:var(--s-other);background:var(--s-other)}
.bar-wk{fill:var(--bar-week)}.bar-before{fill:var(--bar)}.bar-warn{fill:var(--alert)}
.legend.swatches{display:flex;flex-wrap:wrap;gap:2px 10px;margin:0 0 6px}.legend.swatches i{margin-left:0}
.sis{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:0 14px;margin-top:8px}
.si{display:grid;grid-template-columns:minmax(0,1.3fr) 110px 72px minmax(0,1fr) minmax(0,1.4fr);gap:12px;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px;align-items:baseline}
.si:last-child{border-bottom:0}
.si.head{font-size:12px;color:var(--muted);padding:8px 0}
.si .owner{width:auto}
.si .num{text-align:right;font-variant-numeric:tabular-nums}
.si .dec{font-size:13px;overflow-wrap:anywhere}
.si ul{margin:4px 0 0;padding-left:16px;font-size:13px}
.pcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:12px}
.pcard{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.pcard h4{margin:0 0 4px}
.small{font-size:12px}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.pcard h5{margin:10px 0 4px;font-size:12px;font-weight:600}.pcard h5 .muted{font-weight:400}
.pgrid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:16px;border-top:1px solid var(--line);margin-top:6px}
.pcard ul.owned{margin:4px 0 0;padding-left:16px;font-size:13px}.pcard .owned li{margin:2px 0;overflow-wrap:anywhere}
.more{font-size:12px;margin:2px 0 0}.more summary{color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:4px}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:4px;background:var(--soft);color:var(--muted);white-space:nowrap}
.tag.warn{background:var(--warn-bg);color:var(--warn-fg)}
.mix{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--soft);gap:2px}.mix i{display:block;height:100%}
.chips{display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:6px;font-size:12px}
.chip{white-space:nowrap}.chip i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:middle}.chip b{font-variant-numeric:tabular-nums}
.doing-fold{margin-top:10px;border-top:1px solid var(--line);padding-top:6px;font-size:13px}.doing-fold summary{color:var(--muted)}
.doing-none{margin:10px 0 0;border-top:1px solid var(--line);padding-top:6px}
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
  .dv-cards{grid-template-columns:minmax(0,1fr)}
  h1{font-size:22px}
  .viz,.pcards,.tcards{grid-template-columns:minmax(0,1fr)}
  .pgrid{grid-template-columns:minmax(0,1fr);gap:0}
  .si{grid-template-columns:minmax(0,1fr) auto;gap:2px 10px}.si.head{display:none}
  .si>[data-label]:nth-child(n+4){grid-column:1/-1}
  .si>[data-label]::before{content:attr(data-label) "：";color:var(--muted);font-size:12px}
  .si .name{grid-column:1/-1}.si .owner{grid-column:1}.si .num{grid-column:2;grid-row:2}
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
  .lrow{flex-wrap:wrap}.lrow .lname{width:100%}.lrow .bar{flex-basis:100%}.lrow .num{width:auto;text-align:left}
  .idle,.cw,.dv{width:auto;text-align:left}
  .wrow>summary{grid-template-columns:12px minmax(0,1fr) auto auto}.wrow.head{grid-template-columns:minmax(0,1fr) auto auto}
  .wrow>summary>span:nth-child(2),.wrow.head>span:nth-child(2){display:none}
  .list summary .nums{grid-column:2;margin-left:0}
  .tabs button{flex:1;padding:10px 4px}
}
"""
