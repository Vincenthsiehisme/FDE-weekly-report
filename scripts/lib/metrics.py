"""從 snapshot 算出週報的所有數字。這裡是數字的唯一來源，AI 只能引用這裡算出來的值。

規則（決策正本在 vault 的 jira-weekly-report 專案 index）：
- 當週完成：狀態類別 done，且 resolutiondate 落在報告期間內。
- Hours = Story point estimate，只加葉節點。有子卡的卡自己的點數不算，epic、Kick off 與 Deliverable 卡不算。
  理由：父卡與子卡都有值，兩邊都加會灌水（CS-16 自己 5.0，子任務合計 7.5）。
  這個欄位由執行人完工後回填實際花費時數（2026-09-30 使用者確認），頁面叫「實際工時」。
- 完成卡片也只數葉節點，跟工時同一批卡：有子卡的父卡不算張數，張數與工時都計在子卡（2026-10-07 使用者裁，
  原本父卡與子卡各算一張，累計 185 張對 176 個交付，頁面上兩個數字對不上）。交付不再算（同日使用者裁）。
- 專案以 epic 分組，不在任何 epic 底下的卡歸「未歸屬 epic」。
- Kick off（epic 的目標）與 Deliverable（epic 的交付說明）是說明卡，不是工作卡：完成張數、工時、進度、人、label、
  照流程走、停滯、進行中都不算它們（2026-10-29 使用者裁：週報要排除 Deliverable）。
- label：專案的 label 取 epic 的 label；不在 epic 底下的卡用自己的 label；不是恰好一個 label 歸「未分類」。
  epic 不是恰好一個 label、或底下卡片的 label 跟 epic 不同，列資料檢查（2026-10-07 使用者裁：Jira 上已補齊，
  以 epic 為準；原本的 config/units.json 白名單退場）。
- 進度：累計完成的葉節點（跟完成卡片同一批，扣排除與改組前）／epic 底下全部葉節點（2026-10-07 使用者裁：
  分母不扣，Jira 上全部完成的專案可能不到 100%）。本週頁與總覽同一個數。
- Status 只有 Done（epic 已完成）與 On Track。
- 累計：snapshot 抓的是兩個專案的全部卡片，不限期間，所以累計直接從同一份 snapshot 重算到本期結束，
  不讀舊期的 metrics。
- 專案投入：每個專案由幾個人產出、花多少實際工時，人跟工時同一個口徑（完成的葉節點），未指派不算人。
- 排除：config/excluded.json 列的卡不算完成，本週與累計的張數、工時、進度、人、label 都不算
  （2026-09-27 使用者裁：Squad 8 移轉時補建的 8 張不算當週）。
- 整棵排除：config/excluded.json 的 epics 列的 epic 連同底下所有卡在算之前就移出，不列專案、不算數字、
  不做 Kick off 鎖定與資料檢查（2026-09-29 使用者裁：行政作業 epic 不放入成效統計）。
- 改組前：Jira 結案時間早於或等於搬進 FDEPROJECT／CS 的時間（snapshot 的 moved_in），是搬入前已完成的歷史，
  本週與累計都不算，也不列資料檢查，只給頁面在「改組前」分隔線之後灰字列出；搬入後才結案的照算；
  同一張卡也列在 config/excluded.json 的 cards 時以排除清單為準（照常列資料檢查，不進改組前清單）
  （2026-09-30 使用者裁：Squad 8 移轉的卡集中在陳勁佑）。
- 團隊績效評估（2026-10-28 使用者鎖定，plan 在 vault 的 jira-weekly-report · 團隊績效評估）：
  照流程走＝結案前待過「進行中」，只看累計完成卡片那批葉節點，逐週算。
  補登（建立後 10 分鐘內結案）2026-10-28 使用者裁不算（schema 8 拿掉），snapshot 仍存 created。
  停滯＝epic 未結案、底下的卡 14 天沒有任何更新（含只改 label 的更新）。
  投入比例＝每人每週先換成 100% 再平均，人內用實際工時當權重、沒填的卡補這人這週的平均，整週都沒填就用張數；
  只在人內換算，不跨人比小時（每人填工時的粒度不同）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import capacity as capacity_lib
from . import deliverable as deliverable_lib
from .kickoff import SECTIONS
from .period import TZ, period_containing, period_ending
from .snapshot import DELIVERABLE_TYPE, EPIC_LEVEL, KICKOFF_TYPE, issue_sort_key, parse_jira_time

SECTION_LABELS = dict(SECTIONS)
# 說明卡的類型：在 epic 底下，但不是工作，所有數字都不算（唯一例外：有填工時的 Kick off 就是探索人力占比的探索，見 _explore）
NOT_WORK_TYPES = (KICKOFF_TYPE, DELIVERABLE_TYPE)

UNASSIGNED_EPIC = "_unassigned"
UNASSIGNED_EPIC_LABEL = "未歸屬 epic"
UNASSIGNED_OWNER = "未指派"
UNLABELED = "未分類"
STALE_DAYS = 7
STALL_DAYS = 14
ALLOCATION_TOP = 7      # 投入比例畫幾個 label，其餘併「其他」（7 色過 dataviz validate_palette 亮暗）
ALLOCATION_WINDOW = 4   # 前幾名照最近幾週排，累計排的話近期主力會被併進「其他」（原型 W43「其他」65%）
OTHER_LABEL = "其他"
CAPACITY_CHECK_WEEKS = 6  # 工時超過可運用、不在名單，只查最近幾週（跟本週頁的圖同一段），不然資料檢查會一路變長
# 18：工作卡、epic 標題與 Kick off 有私有 IP 或 config/internal-hosts.json 的主機，一張卡列一條 internal_address 資料檢查；
#     頁面在 render.esc 換掉，metrics 照存原文（2026-11-04 公開頁內部位址與單位頁籤）；
# 17：多了 deliverables_reviewing（Jira 審核中的 Deliverable，頁籤「成效說明」收在最後；snapshot 3 是 null）；deliverables 與它每張多了
#     new、stale、entry、manual，文字欄的內部網址與私有 IP 換成「（內部位址，不公開）」（2026-11-03 成效說明多讀者改版）；
# 16：deliverables 每張卡的 attachments 從檔名字串改成 {id, filename, mime, size, image}，image 是要顯示的圖在 site/ 底下的路徑
#     （2026-11-03 成效說明顯示截圖）；
# 15：health 的 explore_share、explore_people 改算 Kick off：探索是這週完成、有填工時的 Kick off 卡，
#     完成工作卡（含 Spike）都算開發（2026-11-02 使用者裁，取代「這週第一次有完成卡的 epic」；epics_new 這些新 epic 同期數字照留，頁面不用）；
#     epics_done 只算本期結束前在 Jira Done 的 epic，卡都做完、epic 沒關的不再算；週次照舊取最後一張完成卡（同日使用者裁「Done 才算，算最後卡週」）；
# 14：health 的 epics_closed 改名 epics_done：卡全部做完或 epic 已結案就算完成，週次取最後一張完成卡，不看 epic 結案時間
#     （集中結案是行政動作）；拿掉資料檢查 epic_closed_outside_weeks（週次來自卡，一定在週序列裡）（2026-11-02 消化速度口徑修正）；
# 13：累計專案（cumulative.epics）多了 closed_week（結案那一期）、idle_days 與 stalled（未結案才有，跟週頁 epic 同一個時鐘），
#     總覽專案列表段內依狀態排要用；週頁 epics 用抓取當下的狀態篩，期末後才結案的 epic 累計算未結案、週頁卻沒有（2026-11-02 總覽優化）；
# 12：消化速率改以 epic 結案為完成單位（2026-11-02 使用者裁「改算 epic」）：health 逐週多了 epics_closed（這週結案、底下有完成工作卡的 epic 數）、
#     epics_per_person_week（÷ 可運用人週）；拿掉 cards_per_person_day、median_cards（頁面不再用，卡片速率要看就 completed ÷ person_days）；
# 11：health 逐週多了 explore_share（探索人力占比）、explore_people、roster、person_days、cards_per_person_day、median_cards（config/team.json × config/holidays.json 的可運用人日）
#     與 epics_active、epics_new、new_hours_share、new_same_day（探索同期陳述）；投入比例前幾名改本週有份額的 label 優先；
#     近 CAPACITY_CHECK_WEEKS 週的人週工時超過可運用、有完成卡卻不在名單的人列進資料檢查（2026-11-02 本週頁產值與探索）；
# 10：health 逐週多了 hours、hours_per_card（單卡平均工時）；進行中的卡多了 idle_days；週頁 epic 的 kickoff 多了 idle_days
#     （2026-11-02 本週頁一圖一訊息：出手區三個條件同一個時鐘）。via_progress、人的 in_progress.ages 留著，頁面不再用；
# 9：多了 deliverables（頁籤「成效說明」的已完成 Deliverable 卡；snapshot 3 沒抓是 null）；
# 8：拿掉補登（health、kpis、人的 recent 都少 backfill；2026-10-28 使用者裁不算）；
# 7：snapshot 3 的建立時間與狀態歷程算出 health（逐週補登、照流程走）、kpis（本週與上週）、allocation（投入比例）；
#    epic 多了 idle_days 與 stalled、totals 多了 stalled；人多了 trend、recent、shares 與進行中卡的 ages；
# 6：完成卡片只數葉節點、拿掉交付、units 換成 labels、累計專案帶 label 與 kickoff、進度只算完成卡片那批；
# 5：多了 staffing（每個專案幾個人產出、多少工時）；4：多了 token_source（data/tokens/ 有沒有任何檔）；3：多了 migrated（改組前）
SCHEMA = 18
SNAPSHOT_SCHEMAS = (3, 4, 5)  # 3 起有 moved_in、progress_seen、status_since；4 多了 Deliverable，3 照算、成效說明頁籤寫沒抓；
# 5 的附件帶 id 才下載得了圖，4 的附件只有檔名、照舊只列檔名


def _round(value: float) -> float:
    return round(value + 0.0, 2)


class _Tree:
    def __init__(self, issues: list, checks: list):
        self.by_key = {i["key"]: i for i in issues}
        self.checks = checks
        for issue in issues:
            if issue["parent"] and issue["parent"] not in self.by_key:
                checks.append(_check("parent_missing", issue["key"], f"父系 {issue['parent']} 不在 FDEPROJECT／CS 的查詢結果裡，當作沒有父系"))
            if issue["level"] > EPIC_LEVEL:
                checks.append(_check("unsupported_level", issue["key"], f"階層 {issue['level']} 高於 epic，不列入計算"))
        self.epics = [i for i in issues if i["level"] == EPIC_LEVEL]
        self.kickoffs = [i for i in issues if i["type"] == KICKOFF_TYPE]
        self.work = [i for i in issues if i["level"] < EPIC_LEVEL and i["type"] not in NOT_WORK_TYPES]
        work_keys = {i["key"] for i in self.work}
        self.children = {k: [] for k in work_keys}
        for item in self.work:
            if item["parent"] in work_keys:
                self.children[item["parent"]].append(item)
        for kids in self.children.values():
            kids.sort(key=lambda i: issue_sort_key(i["key"]))
        self.work_keys = work_keys

    def parent_of(self, issue: dict):
        return self.by_key.get(issue["parent"]) if issue["parent"] else None

    def epic_of(self, issue: dict):
        seen = set()
        current = issue
        while current is not None:
            if current["level"] == EPIC_LEVEL:
                return current["key"]
            if current["key"] in seen:
                raise ValueError(f"父系關係有循環：{issue['key']}")
            seen.add(current["key"])
            current = self.parent_of(current)
        return None

    def is_leaf(self, item: dict) -> bool:
        return not self.children[item["key"]]

    def root_of(self, item: dict) -> dict:
        """所屬的父卡：一般卡是自己；子任務歸到它的父卡（父卡不在範圍內就是子任務自己）。負責人歸屬與完成清單分組用。"""
        parent = self.parent_of(item)
        if item["level"] < 0 and parent is not None and parent["key"] in self.work_keys:
            return parent
        return item

    def owner_of(self, item: dict) -> str:
        """負責人：卡片自己的 assignee → 所屬父卡的 assignee → 未指派。
        子任務常常不指派人，歸給父卡的人。一般卡的父卡就是自己，不往上算給 epic 負責人：
        epic 負責人是專案負責人，不一定是做這張卡的人，算給他等於用猜的。"""
        if item["assignee"]:
            return item["assignee"]
        root = self.root_of(item)
        return root["assignee"] or UNASSIGNED_OWNER


def _check(kind: str, key, message: str) -> dict:
    return {"kind": kind, "key": key, "message": message}


def _internal_address_checks(issues: list, kickoff_states: dict) -> list:
    """標題與 Kick off 內容有私有 IP 或清單主機的，一張卡一條。頁面由 render.esc 換掉，這裡只讓人回 Jira 改。
    Deliverable 卡的欄位由 deliverable.views 列 deliverable_internal，這裡不重報。Kick off 的鎖定版與最新讀到的同一段
    常常一樣，相同的字只算一次。"""
    hosts = deliverable_lib.internal_hosts()
    mark = deliverable_lib.INTERNAL_MARK
    out = []
    for item in sorted(issues, key=lambda i: issue_sort_key(i["key"])):
        if item.get("deliverable") is not None:
            continue
        _, n = deliverable_lib.scrub(item["summary"], hosts)
        if n:
            out.append(_check("internal_address", item["key"], f"標題有 {n} 處內部網址或私有 IP，公開頁換成「{mark}」"))
    for state in sorted(kickoff_states.values(), key=lambda s: issue_sort_key(s["kickoff_key"])):
        texts = {v for part in ("sections", "last_seen") for v in (state.get(part) or {}).values() if isinstance(v, str)}
        n = sum(deliverable_lib.scrub(t, hosts)[1] for t in sorted(texts))
        if n:
            out.append(_check("internal_address", state["kickoff_key"], f"Kick off 有 {n} 處內部網址或私有 IP，公開頁換成「{mark}」"))
    return out


def _done(issue: dict) -> bool:
    return issue["status_category"] == "done"


def _token_sum(keys: list, tokens):
    if tokens is None:
        return None
    present = [tokens[k] for k in keys if k in tokens]
    return sum(present) if present else None


def _group_sort_key(group_id: str):
    return (group_id == UNASSIGNED_EPIC, issue_sort_key(group_id) if group_id != UNASSIGNED_EPIC else ("", 0))


def _progress(group_leaves: list, is_done) -> dict:
    """is_done 是「算進累計完成卡片」：本期結束前完成、扣排除與改組前。不看卡片在 Jira 現在的狀態，
    進度才會跟同一列的工時、張數是同一批卡，補算舊的一期也不會算進之後才完成的卡。分母是全部葉節點（2026-10-07 使用者裁）。"""
    total_points = sum(i["points"] for i in group_leaves if i["points"] is not None)
    done_points = sum(i["points"] for i in group_leaves if i["points"] is not None and is_done(i))
    done_cards = sum(1 for i in group_leaves if is_done(i))
    return {
        "done_cards": done_cards,
        "total_cards": len(group_leaves),
        "done_points": _round(done_points),
        "total_points": _round(total_points),
        # 跟頁面的進度條同一個比例：張數，不是工時（Codex 審 PR #17）
        "percent": round(done_cards / len(group_leaves) * 100) if group_leaves else None,
        "unestimated_cards": sum(1 for i in group_leaves if i["points"] is None),
    }


def drop_excluded_epics(issues: list, excluded_epics: dict, period, *, allow_missing: bool) -> tuple:
    """回傳 (留下的卡, checks)。excluded_epics 是 {epic 卡號: 理由}，epic 本身與底下所有卡（一般卡、子任務、Kick off、Deliverable）都移出。
    要在 Kick off 鎖定與 compute 之前呼叫：只在 compute 裡扣掉的話，Kick off 照樣會被鎖定、epic 照樣列進資料檢查。
    本期有一般卡完成的 epic 在資料檢查列一行帶理由，讓人知道有完成卡被拿掉；沒有就不列，不每週重複。
    allow_missing 只在 prepare 剛抓完 Jira 時為 False：那時找不到就是卡號打錯或 epic 不見了；
    重算與 finalize 為 True，epic 可能比那一期的 snapshot 晚建。"""
    if not excluded_epics:
        return list(issues), []
    by_key = {i["key"]: i for i in issues}
    # 列了非 epic 的卡號，整棵移出等於只移出那張卡跟它的子卡，看起來卻像排除了一個專案，所以直接失敗。
    not_epic = sorted((k for k in excluded_epics if k in by_key and by_key[k]["level"] != EPIC_LEVEL), key=issue_sort_key)
    if not_epic:
        raise ValueError(f"config/excluded.json 的 epics 只能列 epic，這些不是：{'、'.join(not_epic)}")
    missing = sorted((k for k in excluded_epics if k not in by_key), key=issue_sort_key)
    if missing and not allow_missing:
        raise ValueError(f"config/excluded.json 的 epics 在這一期的 snapshot 裡找不到：{'、'.join(missing)}（卡號打錯，或 epic 被刪除、搬到別的專案）")

    def excluded_root(issue: dict):
        seen = set()
        current = issue
        while current is not None:
            if current["key"] in excluded_epics:
                return current["key"]
            if current["key"] in seen:
                raise ValueError(f"父系關係有循環：{issue['key']}")
            seen.add(current["key"])
            parent = current["parent"]
            if parent and parent not in by_key:
                if parent in excluded_epics:
                    return parent  # 重算舊期時 epic 不在那一期的 snapshot，卡已經掛在它底下
                if current["level"] >= 0:
                    # 一般卡以上的父系是 epic 以上；不在 snapshot 的 epic 不是被排除的那個，交回 parent_missing 資料檢查
                    return None
                # 子任務的父卡一定同專案，找不到是抓取不一致，判斷不出它屬不屬於被排除的 epic；放行就可能算回去
                raise ValueError(f"{issue['key']} 的父系 {parent} 不在 snapshot 裡，無法判斷它是否屬於"
                                 f" config/excluded.json 排除的 epic（{'、'.join(sorted(excluded_epics, key=issue_sort_key))}）")
            current = by_key.get(current["parent"]) if current["parent"] else None
        return None

    kept, dropped = [], {}
    for item in issues:
        root = excluded_root(item)
        if root is None:
            kept.append(item)
        else:
            dropped.setdefault(root, []).append(item)
    checks = []
    for epic in sorted(dropped, key=issue_sort_key):
        done = [i for i in dropped[epic] if i["level"] < EPIC_LEVEL and i["type"] not in NOT_WORK_TYPES and _done(i)
                and i["resolved"] and period.contains(parse_jira_time(i["resolved"]))]
        if done:
            checks.append(_check("excluded_epic", epic, f"依 config/excluded.json 整個 epic 不列入週報，本週完成的 {len(done)} 張不算：{excluded_epics[epic]}"))
    return kept, checks

def kickoff_by_epic(issues: list) -> tuple:
    """回傳 ({epic: kickoff issue}, checks)。一個 epic 多張 Kick off 取卡號最小的一張並列進資料檢查。"""
    checks = []
    tree = _Tree(issues, [])
    found = {}
    for card in sorted(tree.kickoffs, key=lambda i: issue_sort_key(i["key"])):
        parent = tree.parent_of(card)
        if parent is None or parent["level"] != EPIC_LEVEL:
            checks.append(_check("kickoff_without_epic", card["key"], "Kick off 卡的父系不是 epic，無法對應專案"))
            continue
        if parent["key"] in found:
            checks.append(_check("kickoff_duplicate", card["key"], f"{parent['key']} 已經有 Kick off {found[parent['key']]['key']}，這張不採用"))
            continue
        found[parent["key"]] = card
    return found, checks


def _kickoff_view(state) -> dict:
    """頁面要的 Kick off 內容；state 是 None 代表這個 epic 沒有 Kick off。資料檢查由呼叫端列，這裡不列。"""
    if state is None:
        return None
    return {
        "key": state["kickoff_key"],
        "locked_at": state["locked_at"],
        "sections": state["sections"],
        "modified_at": [c["detected_at"] for c in state["changes"]],
    }


def _labels_of(tree, groups, checks, skip: set) -> tuple:
    """回傳 (group_label, card_label)：group_label 是 {epic: label}，card_label(item) 給未歸屬 epic 的卡用自己的 label。
    不是恰好一個 label 歸「未分類」；epic 本身、或 epic 底下卡片的 label 跟 epic 不同，各列一行資料檢查，讓人回 Jira 補。
    skip 是排除清單與改組前的卡：本來就不進報告，不要求人回 Jira 補它們的 label（改組前也不列資料檢查，Codex 審 PR #17）。"""
    group_label = {}
    for group_id, epic in groups:
        if epic is None:
            continue
        labels = epic["labels"]
        if len(labels) == 1:
            group_label[group_id] = labels[0]
            off = sorted((i["key"] for i in tree.work if i["key"] not in skip and tree.epic_of(i) == group_id and i["labels"] != labels),
                         key=issue_sort_key)
            if off:
                checks.append(_check("label_mismatch", group_id, f"底下 {len(off)} 張卡的 label 跟 epic 的「{labels[0]}」不同，頁面以 epic 為準：{'、'.join(off)}"))
        else:
            group_label[group_id] = UNLABELED
            what = "沒有 label" if not labels else f"有 {len(labels)} 個 label（{'、'.join(labels)}）"
            checks.append(_check("epic_label", group_id, f"epic {what}，歸{UNLABELED}"))

    def card_label(item: dict) -> str:
        labels = item["labels"]
        return labels[0] if len(labels) == 1 else UNLABELED

    for item in tree.work:
        if item["key"] not in skip and tree.epic_of(item) is None and len(item["labels"]) != 1:
            what = "沒有 label" if not item["labels"] else f"有 {len(item['labels'])} 個 label（{'、'.join(item['labels'])}）"
            checks.append(_check("card_label", item["key"], f"不在 epic 底下的卡{what}，歸{UNLABELED}"))
    return group_label, card_label


def compute(snapshot: dict, period, tokens, kickoff_states: dict, extra_checks: list, *,
            cumulative_tokens, excluded: dict, token_source: bool, previous, team=None, calendar=None) -> dict:
    """tokens 是這一期的 Token 檔；cumulative_tokens 是週次不晚於這一期的 Token 檔逐卡相加。
    兩個都是 None 代表這一期之前沒有任何 Token 資料。這一期有 Token 但累計沒有，代表呼叫端沒把累計接上。
    token_source 是 data/tokens/ 有沒有任何檔（含比這一期晚的）：頁面靠它決定 Token 欄顯不顯示，
    不能從累計推，較晚的週才有檔時累計是 None 但來源已經接上（AC-4）。
    excluded 是 {卡號: 理由}，列的卡不算完成；沒有要排除的就傳 {}。
    previous 是上一期的 metrics；沒有上一期的檔就傳 None，上週值是 null、頁面不顯示差值。
    team、calendar 是 capacity.parse_team／parse_calendar 的結果；weekly.py 一定給，沒給（只有測試）時可運用相關欄位是 null。"""
    if tokens is not None and cumulative_tokens is None:
        raise ValueError("這一期有 Token 資料，累計 Token 卻是 None：累計應包含這一期")
    if cumulative_tokens is not None and not token_source:
        raise ValueError("累計有 Token 資料，token_source 卻是 False：呼叫端沒把 data/tokens/ 的狀態接上")
    if snapshot.get("schema") not in SNAPSHOT_SCHEMAS:
        raise ValueError(f"snapshot 的 schema 是 {snapshot.get('schema')}，需要 {'、'.join(map(str, SNAPSHOT_SCHEMAS))}（有搬入時間與狀態歷程才算得了改組前與照流程走）："
                         f"用 weekly.py prepare --week-ending {period.week_ending.isoformat()} 重抓一次 Jira")
    checks = list(extra_checks)
    tree = _Tree(snapshot["issues"], checks)
    checks += _internal_address_checks(snapshot["issues"], kickoff_states)
    deliverables = reviewing = None
    if snapshot["schema"] >= 4:
        deliverables, reviewing, deliverable_checks = deliverable_lib.views(snapshot["issues"], period)
        checks += deliverable_checks

    def completed_this_week(item: dict) -> bool:
        if not _done(item):
            return False
        if not item["resolved"]:
            checks.append(_check("done_without_resolution", item["key"], "狀態是完成但沒有完成時間（resolutiondate），不計入當週完成"))
            return False
        return period.contains(parse_jira_time(item["resolved"]))

    # 完成集合只從一般工作卡算：epic、Kick off、Deliverable 列進排除清單不會有任何作用，看起來卻像排除了，所以直接失敗。
    # 卡號不在 snapshot 放行：重算舊期時那張卡可能還不存在。
    not_work = sorted((k for k in excluded if k in tree.by_key and k not in tree.work_keys), key=issue_sort_key)
    if not_work:
        raise ValueError(f"config/excluded.json 只能列一般工作卡，這些是 epic、Kick off 或 Deliverable 卡：{'、'.join(not_work)}")
    raw_week_done = {i["key"] for i in tree.work if completed_this_week(i)}
    # 排除只在這裡接一次：本週與累計的完成集合都扣掉，下游的張數、工時、進度、人、label 都從這兩個集合算
    for key in sorted(raw_week_done & set(excluded), key=issue_sort_key):
        checks.append(_check("excluded", key, f"依 config/excluded.json 不算本週與累計完成：{excluded[key]}"))
    # 改組前：搬進來時就已完成的卡是別的專案的歷史，靜默不算，不列資料檢查。
    # 同時列在排除清單的以排除清單為準（2026-09-30 使用者裁）：照常列資料檢查、不進改組前清單
    migrated = {i["key"] for i in tree.work
                if _done(i) and i["resolved"] and i["moved_in"]
                and parse_jira_time(i["resolved"]) <= datetime.fromisoformat(i["moved_in"])} - set(excluded)
    week_done = raw_week_done - set(excluded) - migrated
    # 完成時間早於本期結束的都算累計；沒有 resolutiondate 的上面已經列進資料檢查
    done_by_end = {i["key"] for i in tree.work
                   if _done(i) and i["resolved"] and parse_jira_time(i["resolved"]) < period.end_exclusive}
    cumulative_done = done_by_end - set(excluded) - migrated
    group_of = {i["key"]: (tree.epic_of(i) or UNASSIGNED_EPIC) for i in tree.work}
    leaves = [i for i in tree.work if tree.is_leaf(i)]
    # 完成卡片、工時、進度、人、label 都只看這兩批葉節點：父卡的張數與工時計在子卡（2026-10-07 使用者裁）
    week_leaves = [i for i in leaves if i["key"] in week_done]
    cumulative_leaves = [i for i in leaves if i["key"] in cumulative_done]
    leaf_keys = {i["key"] for i in leaves}

    def counted(item: dict) -> bool:
        return item["key"] in cumulative_done

    for leaf in week_leaves:
        if leaf["points"] is None:
            checks.append(_check("unestimated", leaf["key"], "本週完成但 Story point estimate 沒填，實際工時以 0 計"))

    if tokens is not None:
        for key in sorted(tokens, key=issue_sort_key):
            if key not in tree.by_key:
                checks.append(_check("token_unknown_key", key, "Token 檔裡的卡號不在這期的 snapshot 裡，只計入總量"))
    if cumulative_tokens is not None:
        for key in sorted(cumulative_tokens, key=issue_sort_key):
            if key not in tree.by_key and (tokens is None or key not in tokens):
                checks.append(_check("token_unknown_key", key, "較早期的 Token 檔裡有這個卡號，但它不在這期的 snapshot 裡，只計入累計總量"))

    # 專案（epic）分組
    epic_rows = []
    groups = [(e["key"], e) for e in sorted(tree.epics, key=lambda i: issue_sort_key(i["key"]))]
    groups.append((UNASSIGNED_EPIC, None))
    group_label, card_label = _labels_of(tree, groups, checks, set(excluded) | migrated)

    def label_of(item: dict) -> str:
        group = group_of[item["key"]]
        return card_label(item) if group == UNASSIGNED_EPIC else group_label[group]

    for group_id, epic in groups:
        members = [i for i in tree.work if group_of[i["key"]] == group_id]
        group_leaves = [i for i in leaves if group_of[i["key"]] == group_id]
        group_week_leaves = [i for i in week_leaves if group_of[i["key"]] == group_id]
        completed_keys = sorted((i["key"] for i in group_week_leaves), key=issue_sort_key)
        epic_done_this_week = bool(epic and _done(epic) and epic["resolved"] and period.contains(parse_jira_time(epic["resolved"])))
        active = bool(completed_keys) or epic_done_this_week
        if epic is None and not active:
            continue
        if epic is not None and _done(epic) and not epic_done_this_week:
            continue  # 先前就已結算的專案不再列

        progress = _progress(group_leaves, counted)

        # 完成清單的分組：子任務歸到父卡下面，一般卡自成一組。只是頁面的分組，不是一個要算的數
        by_root = {}
        for leaf in group_week_leaves:
            by_root.setdefault(tree.root_of(leaf)["key"], []).append(leaf)
        roots = []
        for root_key in sorted(by_root, key=issue_sort_key):
            root = tree.by_key[root_key]
            kids = tree.children[root_key]
            if root_key in week_done:
                result = "完成"
            elif _done(root):
                result = "完成（先前已結）"
            else:
                done_kids = sum(1 for k in kids if _done(k))
                result = f"{root['status']}（子任務 {done_kids}/{len(kids)} 完成）" if kids else root["status"]
            roots.append({
                "key": root_key,
                "summary": root["summary"],
                "owner": root["assignee"] or UNASSIGNED_OWNER,
                "hours": _round(sum(l["points"] or 0 for l in by_root[root_key])),
                "tokens": _token_sum([root_key] + [k["key"] for k in kids], tokens),
                "result": result,
            })

        kickoff = None
        if epic is not None:
            state = kickoff_states.get(epic["key"])
            if state is None:
                checks.append(_check("kickoff_missing", epic["key"], "這個 epic 沒有 Kick off 卡，專案卡上不會有目標、Before／After 與下個里程碑"))
            else:
                kickoff = _kickoff_view(state)
                kickoff["idle_days"] = _kickoff_idle_days(tree, epic["key"], period)
                for problem in state.get("problems", []):
                    checks.append(_check("kickoff_format", state["kickoff_key"], problem))
                for change in state["changes"]:
                    what = "、".join(SECTION_LABELS[s] for s in change.get("sections", []))
                    if "kickoff_key" in change:
                        what = "、".join(filter(None, [what, f"改用另一張 Kick off {change['kickoff_key']}"]))
                    if change.get("kickoff_missing"):
                        what = "、".join(filter(None, [what, "Kick off 卡不見了（刪除、移出 epic 或改了類型）"]))
                    checks.append(_check("kickoff_modified", state["kickoff_key"], f"鎖定後被修改：{what}（偵測於 {change['detected_at']}），頁面的目標五段仍顯示鎖定版本"))

        epic_rows.append({
            "id": group_id,
            "key": epic["key"] if epic else None,
            "summary": epic["summary"] if epic else UNASSIGNED_EPIC_LABEL,
            "project": epic["project"] if epic else None,
            "owner": (epic["assignee"] or UNASSIGNED_OWNER) if epic else None,
            "status": ("Done" if _done(epic) else "On Track") if epic else None,
            "due": epic["due"] if epic else None,
            "active": active,
            "progress": progress,
            "week": {
                "completed_count": len(completed_keys),
                "completed_keys": completed_keys,
                "hours": _round(sum(l["points"] or 0 for l in group_week_leaves)),
                "tokens": _token_sum(([epic["key"]] if epic else []) + [i["key"] for i in members], tokens),
                "roots": roots,
            },
            "kickoff": kickoff,
        })

    # 依負責人
    owners = {}
    for leaf in week_leaves:
        row = owners.setdefault(tree.owner_of(leaf), {"hours": 0.0, "keys": []})
        row["hours"] += leaf["points"] or 0
        row["keys"].append(leaf["key"])
    owner_rows = [{
        "owner": name,
        "completed": len(row["keys"]),
        "hours": _round(row["hours"]),
        "tokens": _token_sum(row["keys"], tokens),
    } for name, row in owners.items()]
    owner_rows.sort(key=lambda r: (-r["hours"], r["owner"] == UNASSIGNED_OWNER, r["owner"]))

    # Jira Tracking
    def group_label_text(key: str) -> str:
        group = group_of[key]
        return UNASSIGNED_EPIC_LABEL if group == UNASSIGNED_EPIC else f"{group} {tree.by_key[group]['summary']}"

    def completed_row(key: str, tokens_map) -> dict:
        item = tree.by_key[key]
        leaf = tree.is_leaf(item)
        return {
            "key": key,
            "group": group_of[key],
            "root": tree.root_of(item)["key"],
            "project": group_label_text(key),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "leaf": leaf,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if leaf else None,
            "tokens": _token_sum([key], tokens_map),
        }

    completed = [completed_row(key, tokens) for key in sorted(week_done & leaf_keys, key=issue_sort_key)]

    def migrated_row(key: str) -> dict:
        item = tree.by_key[key]
        return dict(completed_row(key, None),
                    week=period_containing(parse_jira_time(item["resolved"])).week_id,
                    resolved=item["resolved"], moved_in=item["moved_in"])

    in_progress = []
    for item in sorted((i for i in tree.work if i["status_category"] == "indeterminate"), key=lambda i: issue_sort_key(i["key"])):
        kids = tree.children[item["key"]]
        state = item["status"]
        if kids:
            state += f"，子任務 {sum(1 for k in kids if _done(k))}/{len(kids)} 完成"
        idle = (period.end_exclusive - parse_jira_time(item["updated"])).days
        if idle >= STALE_DAYS:
            state += f"，{idle} 天未更新"
        upcoming = next((k for k in kids if not _done(k)), None)
        in_progress.append({
            "key": item["key"],
            # 最後一次更新到本期結束；抓取比期間結束晚幾分鐘，晚於期間結束的算 0 天（跟 epic 的 idle_days 同一個時鐘）
            "idle_days": max(0, idle),
            "group": group_of[item["key"]],
            "root": tree.root_of(item)["key"],
            "leaf": not kids,
            "project": group_label_text(item["key"]),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "current_state": state,
            "next": f"{upcoming['key']} {upcoming['summary']}" if upcoming else None,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if not kids else None,
            "tokens": _token_sum([item["key"]], tokens),
        })

    cumulative = _cumulative(tree, period, groups, group_of, group_label, leaves, cumulative_done, kickoff_states,
                             cumulative_tokens, completed_row)
    people = _people(tree, week_done, cumulative_done, in_progress, tokens, cumulative_tokens)
    staffing = _staffing(tree, group_of, week_done, cumulative_done)
    labels = _label_rows(cumulative["epics"], week_leaves, cumulative_leaves, label_of)

    week_of = {row["key"]: row["week"] for row in cumulative["completed"]}
    weeks = _week_span(week_of, period)
    health = _health(tree, cumulative_leaves, week_of, weeks)
    _explore(health, tree, cumulative_leaves, week_of, group_of)
    _epics_done(health, tree, cumulative_leaves, week_of, group_of, period, checks)
    _capacity(health, tree, cumulative_leaves, week_of, period, team, calendar, checks)
    allocation = _allocation(cumulative_leaves, week_of, weeks, tree.owner_of, label_of)
    _person_views(people, cumulative_leaves, week_of, weeks, tree, label_of, period)
    for row in epic_rows:
        row["idle_days"] = _idle_days(tree, group_of, row, period)
        row["stalled"] = row["key"] is not None and row["status"] != "Done" and row["idle_days"] >= STALL_DAYS

    active_rows = [r for r in epic_rows if r["active"]]
    stalled = sum(1 for r in epic_rows if r["stalled"])
    return {
        "schema": SCHEMA,
        "site": snapshot["site"],
        "token_source": token_source,
        # 算這份 metrics 時的排除清單：finalize 拿它跟現在的 config 比，排除了沒有葉節點出現在頁面上的父卡也擋得到
        "excluded": sorted(excluded, key=issue_sort_key),
        "period": period.as_dict(),
        "generated_at": snapshot["fetched_at"],
        "totals": {
            "projects": len(active_rows),
            "completed": len(completed),
            "hours": _round(sum(l["points"] or 0 for l in week_leaves)),
            "tokens": sum(tokens.values()) if tokens is not None else None,
            "unestimated_completed": sum(1 for l in week_leaves if l["points"] is None),
            "stalled": stalled,
        },
        "kpis": _kpis(health, period, len(active_rows), stalled, previous),
        "health": health,
        "allocation": allocation,
        "epics": epic_rows,
        "owners": owner_rows,
        "labels": labels,
        "completed": completed,
        "in_progress": in_progress,
        "cumulative": cumulative,
        "people": people,
        "staffing": staffing,
        "deliverables": deliverables,
        "deliverables_reviewing": reviewing,
        "migrated": {
            "week": [migrated_row(k) for k in sorted(raw_week_done & migrated, key=issue_sort_key)],
            "cumulative": [migrated_row(k) for k in sorted(done_by_end & migrated, key=issue_sort_key)],
        },
        "checks": _dedupe(checks),
    }


def _cumulative(tree, period, groups, group_of, group_label, leaves, done, kickoff_states, tokens, completed_row) -> dict:
    """到本期結束為止的累計：專案（含已結案的 epic）、完成卡片（標上完成那一週）、總量。
    完成、進度與 Done 狀態都以本期結束為界，不看卡片現在的狀態。
    done 已扣掉排除清單與改組前；張數、工時、進度都只看 done 裡的葉節點，跟本週頁同一批卡。"""
    done_leaves = [i for i in leaves if i["key"] in done]

    def done_by_end(item) -> bool:
        return _done(item) and bool(item["resolved"]) and parse_jira_time(item["resolved"]) < period.end_exclusive

    epic_rows = []
    for group_id, epic in groups:
        members = [i for i in tree.work if group_of[i["key"]] == group_id]
        group_leaves = [i for i in leaves if group_of[i["key"]] == group_id]
        group_done_leaves = [i for i in group_leaves if i["key"] in done]
        if epic is None and not group_done_leaves:
            continue
        settled = bool(epic) and done_by_end(epic)
        # 未結案的 epic 才有幾天沒動；已結案的有結案週。未歸屬 epic 兩個都沒有
        idle = _idle_days(tree, group_of, {"id": group_id, "key": epic["key"]}, period) if epic and not settled else None
        epic_rows.append({
            "id": group_id,
            "key": epic["key"] if epic else None,
            "summary": epic["summary"] if epic else UNASSIGNED_EPIC_LABEL,
            "owner": (epic["assignee"] or UNASSIGNED_OWNER) if epic else None,
            "status": ("Done" if done_by_end(epic) else "On Track") if epic else None,
            "due": epic["due"] if epic else None,
            # 未歸屬 epic 沒有自己的 label：裡面的卡各自用自己的 label 算進 label 表，頁面放在最後一段
            "label": group_label[group_id] if epic else None,
            "progress": _progress(group_leaves, lambda i: i["key"] in done),
            "completed": len(group_done_leaves),
            "hours": _round(sum(i["points"] or 0 for i in group_done_leaves)),
            "tokens": _token_sum(([epic["key"]] if epic else []) + [i["key"] for i in members], tokens),
            "kickoff": _kickoff_view(kickoff_states.get(epic["key"])) if epic else None,
            "closed_week": period_containing(parse_jira_time(epic["resolved"])).week_id if settled else None,
            "idle_days": idle,
            "stalled": idle is not None and idle >= STALL_DAYS,
        })

    completed = []
    for item in sorted(done_leaves, key=lambda i: issue_sort_key(i["key"])):
        row = completed_row(item["key"], tokens)
        row["week"] = period_containing(parse_jira_time(item["resolved"])).week_id
        completed.append(row)
    weeks = {}
    for row in completed:
        w = weeks.setdefault(row["week"], {"week": row["week"], "completed": 0, "hours": 0.0})
        w["completed"] += 1
        w["hours"] += tree.by_key[row["key"]]["points"] or 0
    week_rows = [{"week": w["week"], "completed": w["completed"], "hours": _round(w["hours"])}
                 for w in sorted(weeks.values(), key=lambda w: w["week"], reverse=True)]
    since = min((parse_jira_time(i["resolved"]) for i in done_leaves), default=None)
    return {
        "since": since.date().isoformat() if since else None,
        "totals": {
            "projects": sum(1 for r in epic_rows if r["completed"]),
            "completed": len(completed),
            "hours": _round(sum(i["points"] or 0 for i in done_leaves)),
            "tokens": sum(tokens.values()) if tokens is not None else None,
            "unestimated_completed": sum(1 for i in done_leaves if i["points"] is None),
        },
        "epics": epic_rows,
        "weeks": week_rows,
        "completed": completed,
    }


def _label_rows(cumulative_epics, week_leaves, cumulative_leaves, label_of) -> list:
    """依 label：每個 label 本週與累計的實際工時、底下幾個 epic。總覽與本週頁共用這一份（2026-10-07 使用者裁）。
    epic 數只數總覽專案列表裡的 epic，未歸屬 epic 不算 epic，它的卡照自己的 label 算工時。
    各列工時加總等於本週與累計的工時總數，epic 數加總等於總覽專案列表的 epic 數；工時加總後才捨入一次。"""
    def row():
        return {"epics": 0, "week": {"hours": 0.0, "unestimated": 0}, "cumulative": {"hours": 0.0, "unestimated": 0}}

    acc = {}
    for epic in cumulative_epics:
        if epic["key"]:
            acc.setdefault(epic["label"], row())["epics"] += 1
    for scope, done_leaves in (("week", week_leaves), ("cumulative", cumulative_leaves)):
        for leaf in done_leaves:
            s = acc.setdefault(label_of(leaf), row())[scope]
            s["hours"] += leaf["points"] or 0
            s["unestimated"] += leaf["points"] is None
    rows = []
    for label, r in acc.items():
        for scope in ("week", "cumulative"):
            r[scope]["hours"] = _round(r[scope]["hours"])
        rows.append(dict(label=label, **r))
    rows.sort(key=lambda r: (r["label"] == UNLABELED, -r["cumulative"]["hours"], -r["week"]["hours"], r["label"]))
    return rows


def _group_info(tree, group_id) -> dict:
    if group_id == UNASSIGNED_EPIC:
        return {"id": group_id, "key": None, "summary": UNASSIGNED_EPIC_LABEL}
    return {"id": group_id, "key": group_id, "summary": tree.by_key[group_id]["summary"]}


def _people(tree, week_done, cumulative_done, in_progress, tokens, cumulative_tokens) -> list:
    """執行人視角：每個人本週與累計的完成卡片、工時、Token，加上手上進行中的葉節點卡。
    完成卡片、工時與進行中都只算葉節點（跟總數同一個口徑）；Token 算他名下所有完成卡（含父卡），
    Token 是用量不是工時，父卡與子卡各自記錄不會重複。"""
    def bucket():
        return {"hours": 0.0, "cards": [], "token_cards": []}

    acc = {}

    def person(name):
        return acc.setdefault(name, {"week": bucket(), "cumulative": bucket(), "in_progress": {"keys": [], "hours": 0.0, "unestimated": 0}})

    for item in tree.work:
        key = item["key"]
        for scope, done in (("week", week_done), ("cumulative", cumulative_done)):
            if key not in done:
                continue
            b = person(tree.owner_of(item))[scope]
            b["token_cards"].append(key)
            if tree.is_leaf(item):
                b["cards"].append(key)
                b["hours"] += item["points"] or 0
    for row in in_progress:
        if not row["leaf"]:
            continue
        points = tree.by_key[row["key"]]["points"]
        p = person(row["owner"])["in_progress"]
        p["keys"].append(row["key"])
        p["hours"] += points or 0
        p["unestimated"] += points is None

    def out(b, tokens_map):
        return {
            "hours": _round(b["hours"]),
            "tokens": _token_sum(b["token_cards"], tokens_map),
            "completed": len(b["cards"]),
            "completed_keys": sorted(b["cards"], key=issue_sort_key),
        }

    rows = [{
        "name": name,
        "week": out(p["week"], tokens),
        "cumulative": out(p["cumulative"], cumulative_tokens),
        "in_progress": {
            "count": len(p["in_progress"]["keys"]),
            "hours": _round(p["in_progress"]["hours"]),
            "unestimated": p["in_progress"]["unestimated"],
            "keys": sorted(p["in_progress"]["keys"], key=issue_sort_key),
        },
    } for name, p in acc.items()]
    rows.sort(key=lambda r: (r["name"] == UNASSIGNED_OWNER, -r["cumulative"]["hours"], -r["week"]["hours"], r["name"]))
    return rows


def _staffing(tree, group_of, week_done, cumulative_done) -> list:
    """專案投入：每個專案本週與累計各由幾個人產出、花多少實際工時（2026-10-06 使用者：團隊要看單一 epic 由多少人產出、花多久時間）。
    跟人的工時同一個口徑：只算完成的葉節點，父卡的執行人沒有自己完成葉節點就不算人。
    工時從葉節點原值加總、最後捨入一次，跟專案列表一致；不從 people 已捨入的各專案時數反推（Codex 審 P2）。
    未指派不算人，工時照加並標 unassigned；未填工時的張數另計，工時 0 不代表沒花時間。人名照他在這個專案的工時排，多的在前。"""
    def scope():
        return {"by_person": {}, "hours": 0.0, "unestimated": 0, "unassigned": False}

    acc = {}
    for item in tree.work:
        if not tree.is_leaf(item):
            continue
        for name, done in (("week", week_done), ("cumulative", cumulative_done)):
            if item["key"] not in done:
                continue
            group_id = group_of[item["key"]]
            row = acc.setdefault(group_id, dict(_group_info(tree, group_id), week=scope(), cumulative=scope()))
            s = row[name]
            owner = tree.owner_of(item)
            if owner == UNASSIGNED_OWNER:
                s["unassigned"] = True
            else:
                s["by_person"][owner] = s["by_person"].get(owner, 0.0) + (item["points"] or 0)
            s["hours"] += item["points"] or 0
            s["unestimated"] += item["points"] is None
    rows = []
    for group_id in sorted(acc, key=_group_sort_key):
        row = acc[group_id]
        for name in ("week", "cumulative"):
            s = row[name]
            people = sorted(s.pop("by_person").items(), key=lambda kv: (-kv[1], kv[0]))
            s["people"] = [n for n, _ in people]
            s["people_count"] = len(people)
            s["hours"] = _round(s["hours"])
        rows.append(row)
    return rows


def _week_span(week_of: dict, period) -> list:
    """從第一張完成卡那週到本期，一週不漏（舊到新）：全隊沒有完成卡的週也要有一列、數字是 0，
    不然「本週」會被當成上一個有完成的週，「近 6 週」也會跨過更長的時間（Codex 審 d442a48）。
    一張完成卡都沒有時只有本期這一週。"""
    first = min(week_of.values(), default=period.week_id)
    weeks, current = [], period
    while True:
        weeks.append(current.week_id)
        if current.week_id <= first:
            return weeks[::-1]
        current = period_ending(current.week_ending - timedelta(days=7))


def _health(tree, done_leaves, week_of, weeks) -> list:
    """逐週（舊到新）：完成卡片、照流程走幾張、沒填工時幾張、有填的工時加總與單卡平均工時。週序列連續，沒有完成卡的週是 0。
    單卡平均只除有填工時的張數，沒填的不當 0h 拉低平均；整週都沒填（或沒有完成卡）是 None，不是 0h。"""
    rows = {w: {"week": w, "completed": 0, "via_progress": 0, "unestimated": 0, "hours": 0.0} for w in weeks}
    for leaf in done_leaves:
        row = rows[week_of[leaf["key"]]]
        row["completed"] += 1
        if leaf["progress_seen"] is None:
            raise ValueError(f"{leaf['key']} 是完成的工作卡，snapshot 卻沒有狀態歷程（progress_seen）：snapshot 抓取不完整，重抓一次")
        row["via_progress"] += leaf["progress_seen"]
        row["unestimated"] += leaf["points"] is None
        row["hours"] += leaf["points"] or 0
    for row in rows.values():
        filled = row["completed"] - row["unestimated"]
        row["hours"] = _round(row["hours"])
        row["hours_per_card"] = round(row["hours"] / filled, 1) if filled else None
    return [rows[w] for w in weeks]


def _week_periods(weeks: list, period) -> dict:
    """{週次: Period}，從本期往回一週一週推（同 _week_span）。"""
    out, current = {}, period
    for week in reversed(weeks):
        if current.week_id != week:
            raise ValueError(f"週序列不連續：預期 {current.week_id}，拿到 {week}")
        out[week] = current
        current = period_ending(current.week_ending - timedelta(days=7))
    return out


def _explore(health, tree, done_leaves, week_of, group_of) -> None:
    """探索的同期數字，加在 health 每一列：epics_active 這週有完成卡的 epic 數（不含未歸屬 epic）、epics_new 其中第一次有完成卡的、
    new_hours_share 新 epic 的工時占這週有填工時的比例（沒有填工時是 None）、explore_share 探索人力占比（Kick off 工時，這週沒人產出是 None）、explore_people 算占比的人數（不含未指派）、
    new_same_day 新 epic 裡建立當天就有完成卡的
    （量到的可能是開卡補登，不是探索，頁面寫出來讓讀者自己判斷；fable 審 2026-11-02）。
    epic 的第一張完成卡取結案時間最早的那張，週次用它在 week_of 的週，跟 health 同一個來源。"""
    first = {}
    for leaf in done_leaves:
        gid = group_of[leaf["key"]]
        if gid == UNASSIGNED_EPIC:
            continue
        moment = parse_jira_time(leaf["resolved"])
        if gid not in first or moment < first[gid][0]:
            first[gid] = (moment, week_of[leaf["key"]])
    for h in health:
        h.update(epics_active=0, epics_new=0, new_same_day=0, explore_share=None, explore_people=0)
    by_week = {h["week"]: h for h in health}
    # 探索人力占比（2026-11-02 使用者裁「照審查改」：人的時間是資源，開發跟探索都是投入）：每個有產出的人一週先換成 100%
    # （人內用自己填的工時當權重，同投入比例），切成探索與開發，再對有產出的人平均。
    # 探索＝這週完成、有填工時的 Kick off 卡，完成工作卡（含 Spike）都算開發（同日使用者先選「Kick off＋Spike」，看過副標後裁只算 Kick off；
    # 取代「新 epic 第一週」：小 epic 一週做完、事後補開的 epic 整個算成探索）。Kick off 沒填工時不算：沒填的卡會補成這人這週的平均，
    # 51 張 Kick off 只有 3 張有填，補了等於憑空生出探索。Kick off 工時只進這裡，完成張數與實際工時照舊不算說明卡。
    # 未指派不是一個人：它的卡不進探索人力占比，也不算進 explore_people（Codex 審 ab4aff4）
    cells = {}
    for leaf in done_leaves:
        week, owner = week_of[leaf["key"]], tree.owner_of(leaf)
        if owner == UNASSIGNED_OWNER:
            continue
        cells.setdefault((week, owner), []).append({"label": "開發", "points": leaf["points"]})
    for card in tree.kickoffs:
        if not _done(card) or card["points"] is None or not card["resolved"]:
            continue
        week, owner = period_containing(parse_jira_time(card["resolved"])).week_id, tree.owner_of(card)
        if week not in by_week or owner == UNASSIGNED_OWNER:  # 週序列外（第一張工作卡之前、期末之後）不算
            continue
        cells.setdefault((week, owner), []).append({"label": "探索", "points": card["points"]})
    per_week = {}
    for (week, _), leaves in cells.items():
        per_week.setdefault(week, []).append(_shares(leaves).get("探索", 0.0))
    for week, shares in per_week.items():
        by_week[week].update(explore_share=round(sum(shares) / len(shares), 4), explore_people=len(shares))
    active, new_hours, filled = {}, {}, {}
    for leaf in done_leaves:
        week, gid = week_of[leaf["key"]], group_of[leaf["key"]]
        filled[week] = filled.get(week, 0.0) + (leaf["points"] or 0)
        if gid == UNASSIGNED_EPIC:
            continue
        active.setdefault(week, set()).add(gid)
        if first[gid][1] == week:
            new_hours[week] = new_hours.get(week, 0.0) + (leaf["points"] or 0)
    for week, groups in active.items():
        new = [g for g in groups if first[g][1] == week]
        by_week[week].update(
            epics_active=len(groups), epics_new=len(new),
            new_same_day=sum(1 for g in new if first[g][0].astimezone(TZ).date() == parse_jira_time(tree.by_key[g]["created"]).astimezone(TZ).date()))
    for h in health:
        total = filled.get(h["week"], 0.0)
        h["new_hours_share"] = round(new_hours.get(h["week"], 0.0) / total, 4) if total else None


def _epics_done(health, tree, done_leaves, week_of, group_of, period, checks) -> None:
    """epics_done 加在 health 每一列：這週完成的 epic 數（2026-11-02 使用者裁「改算 epic」，同日改看最後一張卡：
    jira-weekly-report · 消化速度口徑修正；同日再裁「Done 才算，算最後卡週」：jira-weekly-report · 探索改算 Kick off 與 Spike）。
    epic 在本期結束前已在 Jira 結案（Done），而且底下至少一張算進累計的完成工作卡，就算完成；卡都做完、epic 沒關的不算
    （W44 有 7 個沒關的 epic 期末後又補開卡，卡做完就算會把還在做的專案算成完成）。週次一律取底下最後一張完成卡的那一週，
    不看 epic 的結案時間：W41、W42、W44 都有好幾個 epic 在同一兩分鐘內被結案，結案時間是去 Jira 關 epic 的行政動作，不是做完的時間。
    代價：epic 晚幾週才關，回頭看當初那週的數字會變多。結案但卡沒全做完的也算（改組前、排除的卡留在進度分母，例 FDEPROJECT-71）。
    排除的 epic 在 compute 之前已經整棵移掉。
    算不進去、要人知道的兩種列進資料檢查：完成卻沒有結案時間；
    結案在近 CAPACITY_CHECK_WEEKS 週、底下沒有完成工作卡（沒有工作被消化，可能是改組或取消，W42 FDEPROJECT-245）。"""
    by_group = {}
    for leaf in done_leaves:
        by_group.setdefault(group_of[leaf["key"]], []).append(leaf)
    by_week = {h["week"]: h for h in health}
    recent = {h["week"] for h in health[-CAPACITY_CHECK_WEEKS:]}
    for h in health:
        h["epics_done"] = 0
    for epic in sorted(tree.epics, key=lambda e: issue_sort_key(e["key"])):
        closed = False
        if _done(epic):
            if not epic["resolved"]:
                checks.append(_check("epic_done_without_resolution", epic["key"],
                                     "epic 狀態是完成但沒有完成時間（resolutiondate），消化速度算不到它"))
            else:
                closed = parse_jira_time(epic["resolved"]) < period.end_exclusive
        mine = by_group.get(epic["key"], [])
        if not mine:
            if closed:
                week = period_containing(parse_jira_time(epic["resolved"])).week_id
                if week in recent:
                    checks.append(_check("epic_closed_without_work", epic["key"],
                                         f"epic 在 {week} 結案，底下沒有算進累計的完成工作卡（沒有卡，或只剩改組前、排除的卡），消化速度不算它"))
            continue
        if not closed:
            continue
        last = max(mine, key=lambda leaf: (parse_jira_time(leaf["resolved"]), issue_sort_key(leaf["key"])))
        by_week[week_of[last["key"]]]["epics_done"] += 1


def _capacity(health, tree, done_leaves, week_of, period, team, calendar, checks) -> None:
    """可運用人日加在 health 每一列：roster 這週在職的人數、person_days 可運用人日（在職工作日加總，一天 8 小時）、
    epics_per_person_week 完成 epic 數 ÷ 可運用人週（人日 ÷ capacity.DAYS_PER_WEEK；每人日約 0.1 個，換成人週才分得出週間差異）。
    沒給名單（測試）時三欄都是 None。近 CAPACITY_CHECK_WEEKS 週裡，一人一週的實際工時超過他當週可運用時數的，
    以及有完成卡卻不在名單上的人，列進資料檢查（2026-11-02 使用者裁：超過代表填錯）。"""
    for h in health:
        h.update(roster=None, person_days=None, epics_per_person_week=None)
    if team is None or calendar is None:
        return
    weeks = [h["week"] for h in health]
    periods = _week_periods(weeks, period)
    names = {m["name"] for m in team}
    cards, hours = {}, {}
    for leaf in done_leaves:
        cell = (week_of[leaf["key"]], tree.owner_of(leaf))
        cards[cell] = cards.get(cell, 0) + 1
        hours[cell] = hours.get(cell, 0.0) + (leaf["points"] or 0)
    recent = set(weeks[-CAPACITY_CHECK_WEEKS:])
    for h in health:
        p = periods[h["week"]]
        days = capacity_lib.workdays(p.start.date(), p.week_ending, calendar)
        per_member = {m["name"]: capacity_lib.member_days(m, days) for m in team}
        on = [n for n, d in per_member.items() if d]
        h["roster"] = len(on)
        h["person_days"] = sum(per_member.values())
        h["epics_per_person_week"] = (round(h["epics_done"] / (h["person_days"] / capacity_lib.DAYS_PER_WEEK), 2)
                                      if h["person_days"] else None)
        if h["week"] not in recent:
            continue
        for (week, owner), logged in sorted(hours.items()):
            if week != h["week"] or owner not in per_member:
                continue
            limit = per_member[owner] * capacity_lib.HOURS_PER_DAY
            if logged > limit:
                checks.append(_check("hours_over_capacity", None,
                                     f"{week} {owner} 的實際工時 {_round(logged):g}h，超過當週可運用 {limit}h"
                                     f"（{per_member[owner]} 個工作日 × {capacity_lib.HOURS_PER_DAY}h），可能填錯"))
    strangers = sorted({owner for (week, owner) in cards if week in recent and owner not in names and owner != UNASSIGNED_OWNER})
    for owner in strangers:
        checks.append(_check("not_in_team", None, f"{owner} 近 {CAPACITY_CHECK_WEEKS} 週有完成卡，卻不在 config/team.json：可運用人日沒算到他"))


def _percent(part: int, whole: int):
    """整數百分比；分母 0 是「這週沒有完成卡」，回 None，不是 0%。"""
    return round(part / whole * 100) if whole else None


def _kpis(health, period, projects: int, stalled: int, previous) -> dict:
    """本週頁三個數字的本週與上週值（2026-10-28 鎖定 AC-2；同日補登拿掉，四個變三個）。
    照流程走的上週值取同一份 health 序列的上一週；有完成的專案與停滯讀上一期 metrics。
    上一期沒有檔是 None；上一期是 schema 7 以前算的，沒有 stalled 欄，停滯的上週值也是 None。"""
    by_week = {h["week"]: h for h in health}
    prev_week = period_containing(period.start - timedelta(seconds=1)).week_id

    def ratio(field, week):
        h = by_week.get(week)
        return _percent(h[field], h["completed"]) if h else None

    prev_totals = previous["totals"] if previous is not None else None
    return {
        "stalled": {"now": stalled, "prev": prev_totals.get("stalled") if prev_totals is not None else None},
        "projects": {"now": projects, "prev": prev_totals["projects"] if prev_totals is not None else None},
        "via_progress": {"now": ratio("via_progress", period.week_id), "prev": ratio("via_progress", prev_week)},
    }


def _weights(leaves: list) -> dict:
    """一個人一段時間的 {label: 權重}。人內用實際工時；沒填的卡補這批有填的平均，全都沒填就每張算 1（用張數）。"""
    known = [l["points"] for l in leaves if l["points"] is not None]
    fill = sum(known) / len(known) if known else 1.0
    by_label = {}
    for leaf in leaves:
        by_label[leaf["label"]] = by_label.get(leaf["label"], 0.0) + (leaf["points"] if leaf["points"] is not None else fill)
    return by_label


def _shares(leaves: list) -> dict:
    """{label: 占比}，加總 1；工時全是 0 時改用張數，不除以 0。"""
    weights = _weights(leaves)
    total = sum(weights.values())
    if not total:
        weights = {}
        for leaf in leaves:
            weights[leaf["label"]] = weights.get(leaf["label"], 0.0) + 1
        total = len(leaves)
    return {label: value / total for label, value in weights.items()}


def _allocation(done_leaves, week_of, weeks, owner_of, label_of) -> dict:
    """投入比例：每人每週先換成 100%，再對當週有產出的人取平均（Swarmia 的做法），看比例不看小時。
    前幾名 label 照最近 ALLOCATION_WINDOW 週的占比總和排，其餘併「其他」。"""
    cells = {}
    for leaf in done_leaves:
        cells.setdefault((week_of[leaf["key"]], owner_of(leaf)), []).append({"label": label_of(leaf), "points": leaf["points"]})
    per_week = {w: [] for w in weeks}
    for (week, _), leaves in sorted(cells.items()):
        per_week[week].append(_shares(leaves))
    recent = {}
    for week in weeks[-ALLOCATION_WINDOW:]:
        for shares in per_week[week]:
            for label, value in shares.items():
                recent[label] = recent.get(label, 0.0) + value
    # 本週有份額的 label 先排（照本週占比），再照最近幾週補滿：只照近幾週排的話，本週的主力可能被併進「其他」、
    # 本週 0 張的 label 反而佔著有色格（fable 審 2026-11-02：W44「其他」33% 裡有雄獅 14 張）
    now = {}
    for shares in per_week[weeks[-1]]:
        for label, value in shares.items():
            now[label] = now.get(label, 0.0) + value
    ranked = sorted(recent, key=lambda l: (-now.get(l, 0.0), -recent[l], l))
    top = ranked[:ALLOCATION_TOP]
    rows, has_other = [], False
    for week in weeks:
        people = per_week[week]
        acc = {}
        for shares in people:
            for label, value in shares.items():
                key = label if label in top else OTHER_LABEL
                acc[key] = acc.get(key, 0.0) + value / len(people)
        has_other = has_other or OTHER_LABEL in acc
        rows.append({"week": week, "people": len(people), "shares": {k: round(v, 4) for k, v in sorted(acc.items())}})  # 沒人產出的週 shares 是 {}
    return {"labels": top + ([OTHER_LABEL] if has_other else []), "weeks": rows}


def _person_views(people, done_leaves, week_of, weeks, tree, label_of, period) -> None:
    """人卡要的數字，直接加在 people 每一列（2026-10-28 鎖定 AC-4）：trend 逐週完成數（只跟自己比）；
    recent 最近 ALLOCATION_WINDOW 週的完成數與前三個 label；shares 累計投入比例；in_progress.ages 進行中的卡進入目前狀態幾天。"""
    recent_weeks = set(weeks[-ALLOCATION_WINDOW:])
    mine = {}
    for leaf in done_leaves:
        mine.setdefault(tree.owner_of(leaf), []).append(leaf)
    for person in people:
        leaves = mine.get(person["name"], [])
        person["trend"] = [{"week": w, "completed": sum(1 for l in leaves if week_of[l["key"]] == w)} for w in weeks]
        recent = [l for l in leaves if week_of[l["key"]] in recent_weeks]
        counts = {}
        for leaf in recent:
            counts[label_of(leaf)] = counts.get(label_of(leaf), 0) + 1
        person["recent"] = {
            "weeks": len(recent_weeks),
            "completed": len(recent),
            "labels": [[label, n] for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]],
        }
        shares = _shares([{"label": label_of(l), "points": l["points"]} for l in leaves]) if leaves else {}
        person["shares"] = [[label, round(v, 4)] for label, v in sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))]
        ages = {}
        for key in person["in_progress"]["keys"]:
            since = tree.by_key[key]["status_since"]
            if since is None:
                raise ValueError(f"{key} 是進行中的卡，snapshot 卻沒有進入目前狀態的時間（status_since）：snapshot 抓取不完整，重抓一次")
            # 期間結束後才進入目前狀態的卡（抓取比期間結束晚幾分鐘），算 0 天
            ages[key] = max(0, (period.end_exclusive - datetime.fromisoformat(since)).days)
        person["in_progress"]["ages"] = ages


def _kickoff_idle_days(tree, epic_key: str, period):
    """這一期 epic 底下那張 Kick off 卡（跟 kickoff_by_epic 一樣取卡號最小的）最後一次更新到本期結束幾天：
    出手區「需要決定」附寫了幾天，需要決定段就是從這張卡讀的。這一期找不到卡（被刪或移出 epic）是 None。"""
    card = min((k for k in tree.kickoffs if k["parent"] == epic_key), key=lambda i: issue_sort_key(i["key"]), default=None)
    if card is None:
        return None
    return max(0, (period.end_exclusive - parse_jira_time(card["updated"])).days)


def _idle_days(tree, group_of, row, period) -> int:
    """專案底下的卡（不含 epic 自己、Kick off 與 Deliverable）最後一次更新到本期結束幾天；底下沒有卡就看 epic 自己。
    updated 是抓取當下的值，抓取比期間結束晚幾分鐘，晚於期間結束的算 0 天。"""
    members = [i for i in tree.work if group_of[i["key"]] == row["id"]]
    if members:
        last = max(parse_jira_time(i["updated"]) for i in members)
    else:
        last = parse_jira_time(tree.by_key[row["key"]]["updated"])
    return max(0, (period.end_exclusive - last).days)


def _dedupe(checks: list) -> list:
    seen, out = set(), []
    for c in checks:
        marker = (c["kind"], c["key"], c["message"])
        if marker not in seen:
            seen.add(marker)
            out.append(c)
    return out
