"""AI 寫的文字段（narrative.json）的驗證。驗不過就不發布。

AI 只寫兩種東西：
  highlights：本週重點，本週有完成的專案一個一條，條數由程式定（2026-10-07 使用者要求「完成多少就寫多少」，取代 1 到 3 條）；
              本週沒有任何完成的卡時必須是空陣列
  epic_results：每個本週有完成的專案一句「結果」，key 是 metrics 裡 epic 的 id

擋兩種編造：
  1. 引用了 snapshot 裡不存在、或被 config/excluded.json 整棵排除的卡號
  2. 出現程式算好的本週數字以外的數字（卡號裡的數字不算）
任何數值都必須由程式算，不得由 AI 算或挑（2026-09-30 使用者裁）：只放行本週的數字（totals、本週有完成的
專案的 week、本週完成卡、人的本週、label 表的本週、負責人）。累計、進度、改組前的數字雖然也在 metrics 裡，
寫進本週文字就是挑錯欄位：W40 的「累計完成 61 張」其實是含改組前卡的 progress。
"""
from __future__ import annotations

import re

# 不用 \b：中文字在 Python 的 unicode 規則裡也算 \w，「完成CS-16」之間沒有 word boundary
ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*-\d+(?![0-9])")
# 千分位（1,000）要當成一個數字，拆開比對會變成 1 與 000，兩個都幾乎一定在 metrics 裡
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# 中文數字接量詞就是數量，驗證器比對不到，所以數量一律要寫阿拉伯數字。
# 只擋「數字＋量詞」：「第一階段」「進一步」「一句話」這類一般詞彙不受影響。
CHINESE_QUANTITY = re.compile(
    r"(?P<digits>[零〇一二兩三四五六七八九十百千萬]+)\s*(?:項|張|個|條|次|週|天|日|位|件|支|筆|成|倍|小時|%|％)")
MAX_LEN = 200


class NarrativeError(ValueError):
    pass


def chinese_quantities(text: str) -> list:
    """接量詞的中文數字。只放行兩種沒有宣稱數量的寫法（2026-09-27 使用者裁）：
    「第一」（第一次、第一週），以及「一次性」。其他序數照擋：「第九次嘗試」等於說試了 9 次，
    要寫就寫阿拉伯數字，讓它跟 metrics 比對。"""
    found = []
    for match in CHINESE_QUANTITY.finditer(text):
        start = match.start()
        first_ordinal = match.group("digits") == "一" and text[start - 1:start] == "第"
        one_off = text.startswith("一次性", start)
        if not (first_ordinal or one_off):
            found.append(match.group(0))
    return found


def _numbers_in(value, out: set):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 2))
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in ("period", "generated_at", "site"):
                _numbers_in(v, out)
    elif isinstance(value, list):
        for v in value:
            _numbers_in(v, out)


def allowed_numbers(metrics: dict) -> set:
    """程式算好的本週數字。cumulative、epics[].progress、migrated、in_progress、labels 的累計與 epic 數刻意不收。"""
    out = set()
    _numbers_in(metrics["totals"], out)
    for epic in metrics["epics"]:
        if epic["active"]:
            _numbers_in(epic["week"], out)
    for row in metrics["completed"]:
        _numbers_in({"hours": row["hours"], "tokens": row["tokens"]}, out)
    for person in metrics["people"]:
        _numbers_in(person["week"], out)
    for row in metrics["labels"]:
        _numbers_in(row["week"], out)
    _numbers_in(metrics["owners"], out)
    return out


def highlight_projects(metrics: dict) -> list:
    """本週重點要寫的專案與順序，(id, 專案名)：先是 epics 裡本週有完成的（照 epics 的順序），再來是先前已結案、
    底下的卡本週才完成的 epic（照完成清單第一次出現的順序，名稱取總覽的專案列）。「完成多少就寫多少」
    （2026-10-07 使用者要求）：已結案的 epic 不在 epics 裡，只看 active 會整案漏掉（Codex 審 PR D）。
    名稱查不到就讓它炸，不默默少寫一條。"""
    rows = [(e["id"], e["summary"]) for e in metrics["epics"] if e["active"]]
    shown = {e["id"] for e in metrics["epics"]}
    settled = {e["id"]: e["summary"] for e in metrics["cumulative"]["epics"]}
    for row in metrics["completed"]:
        if row["group"] not in shown and row["group"] not in {r[0] for r in rows}:
            rows.append((row["group"], settled[row["group"]]))
    return rows


def validate(narrative: dict, metrics: dict, snapshot_keys: set) -> None:
    errors = []
    if set(narrative) != {"highlights", "epic_results"}:
        raise NarrativeError(f"narrative.json 的欄位必須剛好是 highlights、epic_results，實際是 {sorted(narrative)}")
    highlights, results = narrative["highlights"], narrative["epic_results"]
    if not isinstance(highlights, list) or not all(isinstance(h, str) for h in highlights):
        raise NarrativeError("highlights 必須是字串陣列")
    if not isinstance(results, dict) or not all(isinstance(v, str) for v in results.values()):
        raise NarrativeError("epic_results 必須是 {epic id: 字串}")

    # 比對過的「[專案名]」開頭是 Jira 上的名稱、不是 AI 寫的，下面驗卡號與數字時拿掉：
    # 專案名帶版本號或卡號格式（「Phase 999 升級」）時，AI 改不掉它，不拿掉就永遠發布不了（Codex 審 PR D 第二輪）
    written = list(highlights)
    if metrics["totals"]["completed"] == 0:
        if highlights:
            errors.append("本週沒有完成的卡，highlights 必須是空陣列")
    else:
        # 一個專案一條、照順序、以「[專案名]」開頭：只比條數的話，同一個專案寫五遍也會過（Codex 審 PR D）
        projects = highlight_projects(metrics)
        if len(highlights) != len(projects):
            errors.append(f"highlights 要本週有完成的專案一個一條：{len(projects)} 個專案，實際 {len(highlights)} 條")
        for i, ((_, name), text) in enumerate(zip(projects, highlights)):
            if not text.startswith(f"[{name}]"):
                errors.append(f"highlights[{i}] 要以「[{name}]」開頭（順序照本週有完成的專案）")
            else:
                written[i] = text[len(name) + 2:]

    expected = {e["id"] for e in metrics["epics"] if e["active"]}
    if set(results) != expected:
        missing, extra = sorted(expected - set(results)), sorted(set(results) - expected)
        errors.append(f"epic_results 要剛好涵蓋本週有完成的專案：缺 {missing}，多 {extra}")

    allowed = allowed_numbers(metrics)
    # (位置, 全文, AI 寫的部分)：空字串與長度看全文，卡號、中文數量、數字只驗 AI 寫的部分
    texts = [(f"highlights[{i}]", h, w) for i, (h, w) in enumerate(zip(highlights, written))] + \
            [(f"epic_results[{k}]", v, v) for k, v in sorted(results.items())]
    for where, full, text in texts:
        if not text.strip():
            errors.append(f"{where} 是空字串" if not full.strip() else f"{where} 只有專案名，沒有內容")
            continue
        if len(full) > MAX_LEN:
            errors.append(f"{where} 超過 {MAX_LEN} 字（{len(full)}）")
        for key in ISSUE_KEY.findall(text):
            if key not in snapshot_keys:
                errors.append(f"{where} 引用了不存在或已排除的卡號 {key}")
        for quantity in chinese_quantities(text):
            errors.append(f"{where} 用了中文數字「{quantity}」，數量一律寫阿拉伯數字")
        for number in NUMBER.findall(ISSUE_KEY.sub(" ", text)):
            if round(float(number.replace(",", "")), 2) not in allowed:
                errors.append(f"{where} 出現程式算好的本週數字以外的數字 {number}（累計、進度、改組前的數字不能寫）")
    if errors:
        raise NarrativeError("narrative.json 驗證失敗：\n- " + "\n- ".join(errors))
