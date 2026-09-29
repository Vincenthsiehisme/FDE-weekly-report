"""從 snapshot 算出週報的所有數字。這裡是數字的唯一來源，AI 只能引用這裡算出來的值。

規則（決策正本在 vault 的 jira-weekly-report 專案 index）：
- 當週完成：狀態類別 done，且 resolutiondate 落在報告期間內。
- Hours = Story point estimate，只加葉節點。有子卡的卡自己的點數不算，epic 與 Kick off 卡不算。
  理由：父卡與子卡都有值，兩邊都加會灌水（CS-16 自己 5.0，子任務合計 7.5）。
  這個欄位由執行人完工後回填實際花費時數（2026-09-30 使用者確認），頁面叫「實際工時」。
- 專案以 epic 分組，不在任何 epic 底下的卡歸「未歸屬 epic」。
- 支援單位以 label 分組，只認 config/units.json 白名單裡的 label。
- Status 只有 Done（epic 已完成）與 On Track。
- 累計：snapshot 抓的是兩個專案的全部卡片，不限期間，所以累計直接從同一份 snapshot 重算到本期結束，
  不讀舊期的 metrics。
- 排除：config/excluded.json 列的卡不算完成，本週與累計的張數、工時、交付、人、支援單位都不算；
  epic 進度照 Jira 狀態算完成（2026-09-27 使用者裁：Squad 8 移轉時補建的 8 張不算當週）。
- 整棵排除：config/excluded.json 的 epics 列的 epic 連同底下所有卡在算之前就移出，不列專案、不算數字、
  不做 Kick off 鎖定與資料檢查（2026-09-29 使用者裁：行政作業 epic 不放入成效統計）。
- 改組前：Jira 結案時間早於或等於搬進 FDEPROJECT／CS 的時間（snapshot 的 moved_in），是搬入前已完成的歷史，
  本週與累計都不算，也不列資料檢查，只給頁面在「改組前」分隔線之後灰字列出；搬入後才結案的照算；
  同一張卡也列在 config/excluded.json 的 cards 時以排除清單為準（照常列資料檢查，不進改組前清單）；
  epic 進度照 Jira 狀態（2026-09-30 使用者裁：Squad 8 移轉的卡集中在陳勁佑）。
"""
from __future__ import annotations

from datetime import datetime

from .kickoff import SECTIONS
from .period import period_containing
from .snapshot import EPIC_LEVEL, KICKOFF_TYPE, issue_sort_key, parse_jira_time

SECTION_LABELS = dict(SECTIONS)

UNASSIGNED_EPIC = "_unassigned"
UNASSIGNED_EPIC_LABEL = "未歸屬 epic"
UNASSIGNED_OWNER = "未指派"
UNIT_UNCLASSIFIED = "未分類"
UNIT_MULTIPLE = "多個單位（待釐清）"
STALE_DAYS = 7
SCHEMA = 3  # 3：多了 migrated（改組前）
SNAPSHOT_SCHEMA = 2  # snapshot 要有 moved_in 才判斷得了改組前


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
        self.work = [i for i in issues if i["level"] < EPIC_LEVEL and i["type"] != KICKOFF_TYPE]
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
        """交付單位：一般卡本身；子任務歸到它的父卡（父卡不在範圍內就以子任務本身為單位）。"""
        parent = self.parent_of(item)
        if item["level"] < 0 and parent is not None and parent["key"] in self.work_keys:
            return parent
        return item

    def owner_of(self, item: dict) -> str:
        """負責人：卡片自己的 assignee → 交付單位的 assignee → 未指派。
        子任務常常不指派人，歸給父卡的人。一般卡的交付單位就是自己，不往上算給 epic 負責人：
        epic 負責人是專案負責人，不一定是做這張卡的人，算給他等於用猜的。"""
        if item["assignee"]:
            return item["assignee"]
        root = self.root_of(item)
        return root["assignee"] or UNASSIGNED_OWNER


def _check(kind: str, key, message: str) -> dict:
    return {"kind": kind, "key": key, "message": message}


def _done(issue: dict) -> bool:
    return issue["status_category"] == "done"


def _token_sum(keys: list, tokens):
    if tokens is None:
        return None
    present = [tokens[k] for k in keys if k in tokens]
    return sum(present) if present else None


def _group_sort_key(group_id: str):
    return (group_id == UNASSIGNED_EPIC, issue_sort_key(group_id) if group_id != UNASSIGNED_EPIC else ("", 0))


def _progress(group_leaves: list, is_done=_done) -> dict:
    """is_done 預設看卡片現在的狀態；累計要改用「本期結束前完成」，補算舊的一期才不會算進之後才完成的卡。"""
    total_points = sum(i["points"] for i in group_leaves if i["points"] is not None)
    done_points = sum(i["points"] for i in group_leaves if i["points"] is not None and is_done(i))
    return {
        "done_cards": sum(1 for i in group_leaves if is_done(i)),
        "total_cards": len(group_leaves),
        "done_points": _round(done_points),
        "total_points": _round(total_points),
        "percent": round(done_points / total_points * 100) if total_points else None,
        "unestimated_cards": sum(1 for i in group_leaves if i["points"] is None),
    }


def drop_excluded_epics(issues: list, excluded_epics: dict, period, *, allow_missing: bool) -> tuple:
    """回傳 (留下的卡, checks)。excluded_epics 是 {epic 卡號: 理由}，epic 本身與底下所有卡（一般卡、子任務、Kick off）都移出。
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
        done = [i for i in dropped[epic] if i["level"] < EPIC_LEVEL and i["type"] != KICKOFF_TYPE and _done(i)
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


def compute(snapshot: dict, period, units: list, tokens, kickoff_states: dict, extra_checks: list, *,
            cumulative_tokens, excluded: dict) -> dict:
    """tokens 是這一期的 Token 檔；cumulative_tokens 是週次不晚於這一期的 Token 檔逐卡相加。
    兩個都是 None 代表沒有任何 Token 資料。這一期有 Token 但累計沒有，代表呼叫端沒把累計接上。
    excluded 是 {卡號: 理由}，列的卡不算完成；沒有要排除的就傳 {}。"""
    if tokens is not None and cumulative_tokens is None:
        raise ValueError("這一期有 Token 資料，累計 Token 卻是 None：累計應包含這一期")
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(f"snapshot 的 schema 是 {snapshot.get('schema')}，需要 {SNAPSHOT_SCHEMA}（有搬入時間才判斷得了改組前）：重跑 prepare 抓一次 Jira")
    checks = list(extra_checks)
    tree = _Tree(snapshot["issues"], checks)
    unit_set = set(units)

    def completed_this_week(item: dict) -> bool:
        if not _done(item):
            return False
        if not item["resolved"]:
            checks.append(_check("done_without_resolution", item["key"], "狀態是完成但沒有完成時間（resolutiondate），不計入當週完成"))
            return False
        return period.contains(parse_jira_time(item["resolved"]))

    # 完成集合只從一般工作卡算：epic、Kick off 列進排除清單不會有任何作用，看起來卻像排除了，所以直接失敗。
    # 卡號不在 snapshot 放行：重算舊期時那張卡可能還不存在。
    not_work = sorted((k for k in excluded if k in tree.by_key and k not in tree.work_keys), key=issue_sort_key)
    if not_work:
        raise ValueError(f"config/excluded.json 只能列一般工作卡，這些是 epic 或 Kick off 卡：{'、'.join(not_work)}")
    raw_week_done = {i["key"] for i in tree.work if completed_this_week(i)}
    # 排除只在這裡接一次：本週與累計的完成集合都扣掉，下游的張數、工時、交付、人、支援單位都從這兩個集合算
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
    week_leaves = [i for i in leaves if i["key"] in week_done]

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
    for group_id, epic in groups:
        members = [i for i in tree.work if group_of[i["key"]] == group_id]
        group_leaves = [i for i in leaves if group_of[i["key"]] == group_id]
        group_week_leaves = [i for i in week_leaves if group_of[i["key"]] == group_id]
        completed_keys = sorted((i["key"] for i in members if i["key"] in week_done), key=issue_sort_key)
        epic_done_this_week = bool(epic and _done(epic) and epic["resolved"] and period.contains(parse_jira_time(epic["resolved"])))
        active = bool(completed_keys) or epic_done_this_week
        if epic is None and not active:
            continue
        if epic is not None and _done(epic) and not epic_done_this_week:
            continue  # 先前就已結算的專案不再列

        progress = _progress(group_leaves)

        roots = {}
        for leaf in group_week_leaves:
            roots.setdefault(tree.root_of(leaf)["key"], []).append(leaf)
        deliveries = []
        for root_key in sorted(roots, key=issue_sort_key):
            root = tree.by_key[root_key]
            kids = tree.children[root_key]
            if root_key in week_done:
                result = "完成"
            elif _done(root):
                result = "完成（先前已結）"
            else:
                done_kids = sum(1 for k in kids if _done(k))
                result = f"{root['status']}（子任務 {done_kids}/{len(kids)} 完成）" if kids else root["status"]
            deliveries.append({
                "key": root_key,
                "summary": root["summary"],
                "owner": root["assignee"] or UNASSIGNED_OWNER,
                "hours": _round(sum(l["points"] or 0 for l in roots[root_key])),
                "tokens": _token_sum([root_key] + [k["key"] for k in kids], tokens),
                "result": result,
            })

        kickoff = None
        if epic is not None:
            state = kickoff_states.get(epic["key"])
            if state is None:
                checks.append(_check("kickoff_missing", epic["key"], "這個 epic 沒有 Kick off 卡，專案卡上不會有目標、Before／After 與下個里程碑"))
            else:
                kickoff = {
                    "key": state["kickoff_key"],
                    "locked_at": state["locked_at"],
                    "sections": state["sections"],
                    "modified_at": [c["detected_at"] for c in state["changes"]],
                }
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
                "deliveries": deliveries,
            },
            "kickoff": kickoff,
        })

    # 依負責人
    owners = {}
    for leaf in week_leaves:
        row = owners.setdefault(tree.owner_of(leaf), {"roots": set(), "hours": 0.0, "keys": []})
        row["roots"].add(tree.root_of(leaf)["key"])
        row["hours"] += leaf["points"] or 0
        row["keys"].append(leaf["key"])
    owner_rows = [{
        "owner": name,
        "deliveries": [tree.by_key[k]["summary"] for k in sorted(row["roots"], key=issue_sort_key)],
        "deliverables": len(row["roots"]),
        "hours": _round(row["hours"]),
        "tokens": _token_sum(row["keys"], tokens),
    } for name, row in owners.items()]
    owner_rows.sort(key=lambda r: (-r["hours"], r["owner"] == UNASSIGNED_OWNER, r["owner"]))

    # 依支援單位
    unknown_labels = set()
    units_acc = {}
    for leaf in week_leaves:
        matched = [l for l in leaf["labels"] if l in unit_set]
        unknown_labels.update(l for l in leaf["labels"] if l not in unit_set)
        unit = matched[0] if len(matched) == 1 else (UNIT_UNCLASSIFIED if not matched else UNIT_MULTIPLE)
        if len(matched) > 1:
            checks.append(_check("unit_multiple", leaf["key"], f"同時有多個支援單位 label：{'、'.join(matched)}"))
        row = units_acc.setdefault(unit, {"roots": set(), "hours": 0.0})
        row["roots"].add(tree.root_of(leaf)["key"])
        row["hours"] += leaf["points"] or 0
    if not units:
        checks.append(_check("units_unconfigured", None, f"config/units.json 白名單是空的，支援單位全部歸{UNIT_UNCLASSIFIED}"))
    for label in sorted(unknown_labels):
        checks.append(_check("unit_unknown_label", None, f"label「{label}」不在 config/units.json 白名單，歸為{UNIT_UNCLASSIFIED}"))
    unit_rows = [{"unit": u, "deliverables": len(r["roots"]), "hours": _round(r["hours"])} for u, r in units_acc.items()]
    special = {UNIT_UNCLASSIFIED: 1, UNIT_MULTIPLE: 2}
    unit_rows.sort(key=lambda r: (special.get(r["unit"], 0), -r["hours"], r["unit"]))

    # Jira Tracking
    def group_label(key: str) -> str:
        group = group_of[key]
        return UNASSIGNED_EPIC_LABEL if group == UNASSIGNED_EPIC else f"{group} {tree.by_key[group]['summary']}"

    def completed_row(key: str, tokens_map) -> dict:
        item = tree.by_key[key]
        leaf = tree.is_leaf(item)
        return {
            "key": key,
            "group": group_of[key],
            "root": tree.root_of(item)["key"],
            "project": group_label(key),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "leaf": leaf,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if leaf else None,
            "tokens": _token_sum([key], tokens_map),
        }

    completed = [completed_row(key, tokens) for key in sorted(week_done, key=issue_sort_key)]

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
            "group": group_of[item["key"]],
            "root": tree.root_of(item)["key"],
            "leaf": not kids,
            "project": group_label(item["key"]),
            "owner": tree.owner_of(item),
            "summary": item["summary"],
            "current_state": state,
            "next": f"{upcoming['key']} {upcoming['summary']}" if upcoming else None,
            "hours": (_round(item["points"]) if item["points"] is not None else None) if not kids else None,
            "tokens": _token_sum([item["key"]], tokens),
        })

    cumulative = _cumulative(tree, period, groups, group_of, leaves, cumulative_done, done_by_end, cumulative_tokens, completed_row)
    people = _people(tree, group_of, week_done, cumulative_done, in_progress, tokens, cumulative_tokens)

    active_rows = [r for r in epic_rows if r["active"]]
    return {
        "schema": SCHEMA,
        "site": snapshot["site"],
        "period": period.as_dict(),
        "generated_at": snapshot["fetched_at"],
        "totals": {
            "projects": len(active_rows),
            # 跟人、支援單位、累計同一個口徑：本週完成葉節點的交付單位。只加專案列的話，
            # 所屬 epic 先前已結案的卡不算，同一頁的「交付」就有兩種數字（2026-09-30 Fable 審 PR #8）
            "deliverables": len({tree.root_of(l)["key"] for l in week_leaves}),
            "completed": len(completed),
            "hours": _round(sum(l["points"] or 0 for l in week_leaves)),
            "tokens": sum(tokens.values()) if tokens is not None else None,
            "unestimated_completed": sum(1 for l in week_leaves if l["points"] is None),
        },
        "epics": epic_rows,
        "owners": owner_rows,
        "units": unit_rows,
        "completed": completed,
        "in_progress": in_progress,
        "cumulative": cumulative,
        "people": people,
        "migrated": {
            "week": [migrated_row(k) for k in sorted(raw_week_done & migrated, key=issue_sort_key)],
            "cumulative": [migrated_row(k) for k in sorted(done_by_end & migrated, key=issue_sort_key)],
        },
        "checks": _dedupe(checks),
    }


def _cumulative(tree, period, groups, group_of, leaves, done, done_in_jira, tokens, completed_row) -> dict:
    """到本期結束為止的累計：專案（含已結案的 epic）、完成卡片（標上完成那一週）、總量。
    完成、進度與 Done 狀態都以本期結束為界，不看卡片現在的狀態。
    done 已扣掉排除清單，用來算張數、工時、交付；進度用 done_in_jira，跟本週頁一樣照 Jira 狀態算。"""
    done_leaves = [i for i in leaves if i["key"] in done]

    def done_by_end(item) -> bool:
        return _done(item) and bool(item["resolved"]) and parse_jira_time(item["resolved"]) < period.end_exclusive

    epic_rows = []
    for group_id, epic in groups:
        members = [i for i in tree.work if group_of[i["key"]] == group_id]
        group_leaves = [i for i in leaves if group_of[i["key"]] == group_id]
        group_done_leaves = [i for i in group_leaves if i["key"] in done]
        completed = sum(1 for i in members if i["key"] in done)
        if epic is None and not completed:
            continue
        epic_rows.append({
            "id": group_id,
            "key": epic["key"] if epic else None,
            "summary": epic["summary"] if epic else UNASSIGNED_EPIC_LABEL,
            "owner": (epic["assignee"] or UNASSIGNED_OWNER) if epic else None,
            "status": ("Done" if done_by_end(epic) else "On Track") if epic else None,
            "due": epic["due"] if epic else None,
            "progress": _progress(group_leaves, lambda i: i["key"] in done_in_jira),
            "completed": completed,
            "deliverables": len({tree.root_of(i)["key"] for i in group_done_leaves}),
            "hours": _round(sum(i["points"] or 0 for i in group_done_leaves)),
            "tokens": _token_sum(([epic["key"]] if epic else []) + [i["key"] for i in members], tokens),
        })

    completed = []
    for key in sorted(done, key=issue_sort_key):
        row = completed_row(key, tokens)
        row["week"] = period_containing(parse_jira_time(tree.by_key[key]["resolved"])).week_id
        completed.append(row)
    weeks = {}
    for row in completed:
        w = weeks.setdefault(row["week"], {"week": row["week"], "completed": 0, "hours": 0.0, "roots": set()})
        w["completed"] += 1
        if row["leaf"]:
            w["hours"] += tree.by_key[row["key"]]["points"] or 0
            w["roots"].add(row["root"])
    week_rows = [{"week": w["week"], "completed": w["completed"], "hours": _round(w["hours"]), "deliverables": len(w["roots"])}
                 for w in sorted(weeks.values(), key=lambda w: w["week"], reverse=True)]
    since = min((parse_jira_time(tree.by_key[k]["resolved"]) for k in done), default=None)
    return {
        "since": since.date().isoformat() if since else None,
        "totals": {
            "projects": sum(1 for r in epic_rows if r["completed"]),
            "deliverables": len({tree.root_of(i)["key"] for i in done_leaves}),
            "completed": len(completed),
            "hours": _round(sum(i["points"] or 0 for i in done_leaves)),
            "tokens": sum(tokens.values()) if tokens is not None else None,
            "unestimated_completed": sum(1 for i in done_leaves if i["points"] is None),
        },
        "epics": epic_rows,
        "weeks": week_rows,
        "completed": completed,
    }


def _people(tree, group_of, week_done, cumulative_done, in_progress, tokens, cumulative_tokens) -> list:
    """執行人視角：每個人本週與累計的完成、工時、Token、各專案時數，加上手上進行中的葉節點卡。
    工時與進行中都只算葉節點（跟 Hours 同一個口徑）；Token 算他名下所有完成卡，Token 是用量不是工時，
    父卡與子卡各自記錄不會重複。"""
    def bucket():
        return {"roots": set(), "hours": 0.0, "cards": [], "groups": {}}

    acc = {}

    def person(name):
        return acc.setdefault(name, {"week": bucket(), "cumulative": bucket(), "in_progress": {"keys": [], "hours": 0.0, "unestimated": 0}})

    for item in tree.work:
        key = item["key"]
        for scope, done in (("week", week_done), ("cumulative", cumulative_done)):
            if key not in done:
                continue
            b = person(tree.owner_of(item))[scope]
            b["cards"].append(key)
            if tree.is_leaf(item):
                b["hours"] += item["points"] or 0
                b["roots"].add(tree.root_of(item)["key"])
                g = b["groups"].setdefault(group_of[key], {"hours": 0.0, "unestimated": 0})
                g["hours"] += item["points"] or 0
                g["unestimated"] += item["points"] is None
    for row in in_progress:
        if not row["leaf"]:
            continue
        points = tree.by_key[row["key"]]["points"]
        p = person(row["owner"])["in_progress"]
        p["keys"].append(row["key"])
        p["hours"] += points or 0
        p["unestimated"] += points is None

    def group_info(group_id):
        if group_id == UNASSIGNED_EPIC:
            return {"id": group_id, "key": None, "summary": UNASSIGNED_EPIC_LABEL}
        return {"id": group_id, "key": group_id, "summary": tree.by_key[group_id]["summary"]}

    def out(b, tokens_map):
        return {
            "hours": _round(b["hours"]),
            "tokens": _token_sum(b["cards"], tokens_map),
            "deliverables": len(b["roots"]),
            "completed": len(b["cards"]),
            "completed_keys": sorted(b["cards"], key=issue_sort_key),
            "by_project": [dict(group_info(g), hours=_round(v["hours"]), unestimated=v["unestimated"])
                           for g, v in sorted(b["groups"].items(), key=lambda kv: _group_sort_key(kv[0]))],
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


def _dedupe(checks: list) -> list:
    seen, out = set(), []
    for c in checks:
        marker = (c["kind"], c["key"], c["message"])
        if marker not in seen:
            seen.add(marker)
            out.append(c)
    return out
