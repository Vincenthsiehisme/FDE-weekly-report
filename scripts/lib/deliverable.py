"""Deliverable 卡（epic 交付時的成效說明）：snapshot 存什麼、頁籤「成效說明」顯示什麼、哪些列資料檢查。

規則正本是 jira-ops `references/deliverable.md`，決策在 vault 的 jira-weekly-report · 成效說明頁籤（2026-10-29 鎖定）。
- 關鍵內容在 FDEPROJECT 的自訂欄位（FIELDS），id 只在這個 team-managed 專案有效；CS 沒有 Deliverable 類型。
  段落欄位 REST v3 回 ADF、短文字回字串，兩種都收；全部選填，沒填是 None。
- 描述的標籤：Acceptance、Code、Env 三行必有；入口、手冊兩行選填（2026-11-03 成效說明多讀者改版），
  接受的寫法比照 Kick off（行首標籤、全半形冒號、標題形式）。
- 畫面取附件：圖片（IMAGE_TYPES、不超過 MAX_IMAGE_BYTES）由 prepare 下載進 site/assets/deliverables/ 公開顯示，
  其他附件只列檔名加 Jira 連結（2026-11-03 使用者裁，推翻 10-29 不放圖；vault 的 jira-weekly-report · 成效說明顯示截圖）。
  個資守門在 Deliverable 規則：上傳前遮客人資料、轉完成前檢查。版本取修正版本（fixVersions）。
- 影片（VIDEO_TYPES、不超過 MAX_VIDEO_BYTES）由 prepare 上傳到 Cloudflare R2、頁面直接播（AC-5，2026-11-03 使用者裁
  「放 R2，限 100 MB」）；連線在 r2.py。
- 頁籤列已完成的 Deliverable，依支援單位分段；Jira 狀態類別 indeterminate（審核中）的另列「驗收中」，
  只解析不列格式檢查、不下載圖也不上傳影片（要轉完成、遮過個資才公開）。不鎖定，每週取當下內容。
- 頁面是公開網址：私有 IP 與 config/internal-hosts.json 列的主機，不管寫在哪一欄都換成 INTERNAL_MARK、列資料檢查；
  剩下的 https:// 網址才做成連結（vault 的 jira-weekly-report · 成效說明多讀者改版，2026-11-03 使用者裁「維持公開」）。
  scrub 也是整份公開頁的出口：render.esc 每個字都過它，工作卡、epic、Kick off 的資料檢查在 metrics 列
  （2026-11-04 公開頁內部位址與單位頁籤）。
- 格式問題與沒填的列資料檢查，不擋週報：一張卡寫錯不該讓整份週報出不來。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .kickoff import adf_to_lines
from .period import period_containing
from .snapshot import EPIC_LEVEL, issue_sort_key, parse_jira_time

# 自訂欄位 id → snapshot 裡的名稱（2026-10-29 createmeta 讀回）
FIELDS = {
    "customfield_10479": "delivered",   # 交付了什麼，短文字
    "customfield_10478": "features",    # 交付功能，段落，一行一項
    "customfield_10486": "not_done",    # 沒做的，段落，一行一項
    "customfield_10485": "metric",      # 成效指標，段落，只放主要指標一個
    "customfield_10484": "base",        # 成效基準值，短文字，可帶單位
    "customfield_10483": "base_date",   # 成效基準日，日期
    "customfield_10482": "now",         # 成效最新值，短文字
    "customfield_10481": "now_date",    # 成效最新日，日期
    "customfield_10480": "source",      # 成效資料來源，短文字
    "customfield_10487": "other",       # 其他成效，短文字，第二個以後的指標用「；」分隔
}
FETCH_FIELDS = ["description", "attachment", "fixVersions", *FIELDS]
# 已完成卻沒填就列資料檢查的欄位（2026-10-29：成效三欄與交付兩欄都改選填，Jira 不擋空白）
REQUIRED_WHEN_DONE = (("delivered", "交付了什麼"), ("features", "交付功能"), ("metric", "成效指標"),
                      ("base", "成效基準值"), ("now", "成效最新值"))
SECTIONS = (("acceptance", "Acceptance"), ("code", "Code"), ("env", "Env"), ("entry", "入口"), ("manual", "手冊"))
OPTIONAL_SECTIONS = {"entry", "manual"}  # 2026-11-03 加的兩行，舊卡沒有不算格式問題；已完成沒有入口另列 deliverable_no_entry
# 這天以後完成的 epic 要有完成的 Deliverable；之前已關閉的不補（2026-10-29 使用者裁）
REQUIRED_SINCE = date(2026, 10, 29)
RESULTS = ("通過", "未通過", "未驗")
# 會下載、公開顯示的圖片：mimeType → (副檔名, 檔頭)。SVG 不收：可以帶 script，直接打開網址會在週報的網域執行
IMAGE_TYPES = {
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)),
    "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/gif": (".gif", (b"GIF87a", b"GIF89a")),
}
# 單張上限（2026-11-03 裁定）：repo 只用標準函式庫、沒有縮圖；實查最大一張 1.1 MB。超過的只列檔名加連結
MAX_IMAGE_BYTES = 2 * 1024 * 1024
# 會上傳到 R2、頁面直接播的影片：mimeType → (副檔名, 檔頭檢查)。只收瀏覽器都能播的兩種；mov 等只列檔名連 Jira
# （AC-5，2026-11-03 使用者裁「放 R2，限 100 MB」；2026-11-10 實作裁定）
VIDEO_TYPES = {
    "video/mp4": (".mp4", lambda data: data[4:8] == b"ftyp"),
    "video/webm": (".webm", lambda data: data.startswith(b"\x1a\x45\xdf\xa3")),
}
MAX_VIDEO_BYTES = 100 * 1024 * 1024
ASSET_DIR = "assets/deliverables"  # 相對 site/
_LABELS = {label.lower(): key for key, label in SECTIONS}
_LINE = re.compile(r"^[\s#>*•\-]*\**\s*(?P<label>Acceptance|Code|Env|入口|手冊)\s*\**\s*(?:[:：]\s*(?P<rest>.*))?$", re.IGNORECASE)
# 成效最新日離本期結束超過這麼多天，列資料檢查、列上標「超過 8 週」
STALE_OUTCOME_DAYS = 56
INTERNAL_HOSTS_FILE = Path(__file__).resolve().parents[2] / "config" / "internal-hosts.json"
INTERNAL_MARK = "（內部位址，不公開）"
# 網址只收 ASCII：內文常把全形括號、逗號直接接在網址後面（1000 的「/convert/（部署於 …）」）
_URL = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")  # 句尾接半形句號也要認得
_IPV6 = re.compile(r"(?<![0-9A-Za-z:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![0-9A-Za-z:])")
_RESULT = re.compile(r"^(?P<cond>.+?)\s*(?:→|->)\s*(?P<result>通過|未通過|未驗)\s*(?:[，,]\s*(?P<where>.*))?$")


def _text(value):
    """短文字回字串、段落回 ADF：都轉成去頭尾空白的字串，空的回 None。"""
    if value is None:
        return None
    if isinstance(value, dict):
        text = "\n".join(adf_to_lines(value))
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError(f"Deliverable 欄位的值不是字串也不是 ADF：{type(value).__name__}")
    return text.strip() or None


def normalize(fields: dict) -> dict:
    """Jira 回來的 fields → snapshot 存的 deliverable。只留頁籤與資料檢查要的。"""
    out = {name: _text(fields.get(fid)) for fid, name in FIELDS.items()}
    out["description"] = adf_to_lines(fields.get("description"))
    out["attachments"] = [_attachment(a) for a in fields.get("attachment") or []]
    out["versions"] = [{"name": v["name"], "release_date": v.get("releaseDate")} for v in fields.get("fixVersions") or []]
    return out


def _attachment(raw: dict) -> dict:
    for name in ("id", "filename", "mimeType", "size"):
        if raw.get(name) is None:
            raise ValueError(f"Deliverable 附件少了 {name}：{raw.get('filename') or raw.get('id')}")
    return {"id": str(raw["id"]), "filename": raw["filename"], "mime": raw["mimeType"], "size": raw["size"]}


def _shown_attachment(a) -> dict:
    """頁面要的附件：image 是相對 site/ 的圖檔路徑，不顯示的是 None。
    snapshot 4 的附件只有檔名（字串）：沒有 id 就做不了連結也下載不了，照舊只列檔名。"""
    if isinstance(a, str):
        return {"id": None, "filename": a, "mime": None, "size": None, "image": None, "video": None}
    kind = IMAGE_TYPES.get(a["mime"])
    image = f"{ASSET_DIR}/{a['id']}{kind[0]}" if kind and a["size"] <= MAX_IMAGE_BYTES else None
    clip = VIDEO_TYPES.get(a["mime"])
    video = f"{a['id']}{clip[0]}" if clip and a["size"] <= MAX_VIDEO_BYTES else None  # R2 的物件鍵
    return dict(a, image=image, video=video)


def upload_videos(client, r2, cards: list) -> list:
    """把 cards（metrics 的 deliverables）要播的影片從 Jira 下載、上傳到 R2，回傳這次上傳的鍵。R2 已經有同一個鍵就不重傳：
    附件內容在 Jira 改不了，換影片就是新附件、新 id。位元數或檔頭對不上就失敗，不上傳。
    上傳回滾不了，所以 prepare 把這步放在寫任何檔之前：失敗時 site/ 與 data/ 都沒動，R2 多出來的同鍵物件下一班照用。"""
    uploaded = []
    for card in cards or []:
        for a in card["attachments"]:
            if not a["video"] or r2.exists(a["video"]):
                continue
            data = client.attachment(a["id"], MAX_VIDEO_BYTES)
            if len(data) != a["size"]:
                raise ValueError(f"{card['key']} 的附件 {a['filename']} 下載到 {len(data)} bytes，Jira 寫的是 {a['size']}")
            if not VIDEO_TYPES[a["mime"]][1](data):
                raise ValueError(f"{card['key']} 的附件 {a['filename']} 內容不是 {a['mime']}（檔頭對不上），不放上週報")
            r2.put(a["video"], data, a["mime"])
            uploaded.append(a["video"])
    return uploaded


def videos(cards: list) -> list:
    """頁面要播的影片鍵，照卡與附件順序。"""
    return [a["video"] for card in cards or [] for a in card["attachments"] if a["video"]]


def download_images(client, cards: list, site_dir: Path) -> list:
    """把 cards（metrics 的 deliverables）要顯示的圖存進 site_dir，回傳這次新寫的路徑。已經存過的同一個附件 id 不重抓：
    附件的內容在 Jira 改不了，換圖就是新附件、新 id。
    分兩段：先全部下載、驗位元數與檔頭（不碰磁碟），全部過了才寫；寫到一半失敗，這次寫進去的全部刪掉、暫存檔也刪，
    site_dir 回到呼叫前的樣子。site/ 是公開網址，routine 是 `git add data site`，半套留下來下一班就發布（Codex 審 e0c5b6c）。"""
    pending = []
    for card in cards or []:
        for a in card["attachments"]:
            if not a["image"] or (site_dir / a["image"]).is_file():
                continue
            data = client.attachment(a["id"], MAX_IMAGE_BYTES)
            if len(data) != a["size"]:
                raise ValueError(f"{card['key']} 的附件 {a['filename']} 下載到 {len(data)} bytes，Jira 寫的是 {a['size']}")
            if not data.startswith(IMAGE_TYPES[a["mime"]][1]):
                raise ValueError(f"{card['key']} 的附件 {a['filename']} 內容不是 {a['mime']}（檔頭對不上），不放上週報")
            pending.append((site_dir / a["image"], data))
    written = []
    try:
        for target, data in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.chmod(tmp, 0o644)  # mkstemp 開的是 600，公開的網站檔要誰都讀得到
                os.replace(tmp, target)
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            written.append(target)
    except BaseException:
        for target in written:
            target.unlink()
        raise
    return written


def missing_images(cards: list, site_dir: Path) -> list:
    """頁面要顯示、site_dir 裡卻沒有的圖（相對路徑）。finalize 用它擋：畫出來會是破圖。"""
    return [a["image"] for card in cards or [] for a in card["attachments"] if a["image"] and not (site_dir / a["image"]).is_file()]


def parse_description(lines: list) -> tuple:
    """回傳 (sections, problems)：描述 3 行標籤的內容，與格式問題。"""
    sections = {key: None for key, _ in SECTIONS}
    collected, problems, current = {}, [], None
    for line in lines:
        if "<" in line and ">" in line:
            problems.append("描述還留著 < > 提示，送出前要換成內容或刪掉")
        match = _LINE.match(line)
        if match:
            key = _LABELS[match.group("label").lower()]
            if key in collected:
                problems.append(f"「{match.group('label')}」出現兩次，只取第一次")
                current = None
                continue
            current = key
            collected[key] = []
            rest = (match.group("rest") or "").strip()
            if rest:
                collected[key].append(rest)
        elif current is not None:
            collected[current].append(line)
    for key, label in SECTIONS:
        if key not in collected and key not in OPTIONAL_SECTIONS:
            problems.append(f"描述少了「{label}：」這一行")
    for key, parts in collected.items():
        sections[key] = "；".join(p for p in parts if p).strip() or None
    return sections, list(dict.fromkeys(problems))


@lru_cache(maxsize=None)
def internal_hosts(path: Path = INTERNAL_HOSTS_FILE) -> frozenset:
    """config/internal-hosts.json：外網打不開、不該出現在公開頁的主機名稱。檔案不在或格式不對就丟錯，不當成空清單。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h.strip() for h in hosts):
        raise ValueError(f"{path} 要是 {{\"hosts\": [\"主機名稱\", …]}}")
    return frozenset(h.strip().lower() for h in hosts)


def _private_ip(text: str) -> bool:
    """單獨的 `::`（分隔符號）沒有任何位址數字，不算；補零的 IPv4（010.0.0.5）Python 3.9.5 起 ipaddress 不收，
    先去掉每段的前導零再判，不然會原樣上公開頁（2026-11-04 Sonnet 審 #41）。"""
    if ":" in text and not re.search(r"[0-9A-Fa-f]", text):
        return False
    parts = text.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        text = ".".join(str(int(p)) for p in parts)
    try:
        return ipaddress.ip_address(text).is_private
    except ValueError:
        return False


def _internal_url(url: str, hosts: frozenset) -> bool:
    """解析不了的網址（例 `https://[foo`）當內部處理：換掉、列資料檢查，不讓一張卡寫錯擋掉整份週報（Codex 審 225df02）。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return True
    return not url.startswith("https://") or _private_ip(host) or any(host == h or host.endswith("." + h) for h in hosts)


def scrub(text, hosts: frozenset) -> tuple:
    """回傳 (公開頁能放的字, 拿掉了幾處)。內部網址（含 http://）、私有 IP、清單上的主機名稱換成 INTERNAL_MARK；
    https:// 的公開網址留著，由頁面做成連結。None 照回 None。"""
    if text is None:
        return None, 0
    count = 0

    def url(match):
        nonlocal count
        found = match.group(0)
        tail = len(found) - len(found.rstrip(".,;:!?"))
        found, rest = found[:len(found) - tail], match.group(0)[len(found) - tail:]
        if _internal_url(found, hosts):
            count += 1
            return INTERNAL_MARK + rest
        return found + rest

    def ip(match):
        nonlocal count
        if _private_ip(match.group(0)):
            count += 1
            return INTERNAL_MARK
        return match.group(0)

    out = _IPV6.sub(ip, _IPV4.sub(ip, _URL.sub(url, text)))
    for pattern in _host_patterns(hosts):
        out, n = pattern.subn(INTERNAL_MARK, out)
        count += n
    return out, count


@lru_cache(maxsize=None)
def _host_patterns(hosts: frozenset) -> tuple:
    """清單上每個主機一條 regex，長的先換。頁面每個字都經過 scrub（render.esc），每次重編會拖慢整份 finalize。
    只看 ASCII 邊界：中文卡片常把主機名稱直接接在中文字旁（「部署在travel-tech.liontravel.com上」）；子網域一起換"""
    # 後面接「.字」照換：分不出是別的網域（.com.tw）還是附件副檔名（.png），寧可多遮（2026-11-04 Sonnet 審 #41 那條不改）
    return tuple(re.compile(r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)*" + re.escape(host) + r"(?![A-Za-z0-9-])", re.IGNORECASE)
                 for host in sorted(hosts, key=lambda h: (-len(h), h)))


def _public_urls(values: list) -> list:
    """入口、手冊：只留 scrub 過還在的 https:// 網址（內部的已換成 INTERNAL_MARK、列過資料檢查）。"""
    values = [v.rstrip(".,;:!?") for v in values]
    return [v for v in values if v.startswith("https://") and _URL.fullmatch(v)]


def _items(text, sep: str) -> list:
    if not text:
        return []
    return [part.strip() for part in re.split(sep, text) if part.strip()]


def _acceptance(text) -> list:
    """「條件 → 通過，在哪驗、日期；…」→ [{condition, result, where}]。對不上格式的整條當條件、result None。"""
    out = []
    for part in _items(text, "[；;\n]"):
        match = _RESULT.match(part)
        if match:
            out.append({"condition": match.group("cond"), "result": match.group("result"), "where": match.group("where")})
        else:
            out.append({"condition": part, "result": None, "where": None})
    return out


def views(issues: list, period, hosts: frozenset = None) -> tuple:
    """回傳 (已完成的卡, 驗收中的卡, checks)。issues 是排除過整棵 epic 之後的 snapshot 卡片，period 是這一期
    （本期新交付、成效多久沒更新都對它算，不看今天）。一個 epic 多張只取卡號最小的那張，其餘列資料檢查。
    已完成的卡才下載圖（download_images 只收第一個值）；驗收中的只解析、不列格式與沒填的檢查。"""
    hosts = internal_hosts() if hosts is None else hosts
    by_key = {i["key"]: i for i in issues}
    checks, cards, reviewing, seen = [], [], [], {}
    for item in sorted((i for i in issues if i.get("deliverable") is not None), key=lambda i: issue_sort_key(i["key"])):
        parent = by_key.get(item["parent"]) if item["parent"] else None
        if parent is None or parent["level"] != EPIC_LEVEL:
            checks.append(_check("deliverable_without_epic", item["key"], "Deliverable 卡的父系不是 epic，對不到專案"))
            continue
        if parent["key"] in seen:
            checks.append(_check("deliverable_duplicate", item["key"], f"{parent['key']} 已經有 Deliverable {seen[parent['key']]}，這張不採用"))
            continue
        seen[parent["key"]] = item["key"]
        done = item["status_category"] == "done"
        if not done and item["status_category"] != "indeterminate":
            continue
        d = item["deliverable"]
        sections, problems = parse_description(d["description"])
        if done:
            for problem in problems:
                checks.append(_check("deliverable_format", item["key"], problem))
            missing = [label for key, label in REQUIRED_WHEN_DONE if not d[key]]
            if missing:
                checks.append(_check("deliverable_missing", item["key"], f"Deliverable 已完成，沒填：{'、'.join(missing)}"))
            if not item["resolved"]:
                checks.append(_check("deliverable_no_resolution", item["key"], "Deliverable 是完成但沒有完成時間，不知道放哪一週"))
                continue
        scrubbed = 0

        def clean(text):
            nonlocal scrubbed
            out, n = scrub(text, hosts)
            scrubbed += n
            return out

        fields = {key: clean(d[key]) for key in ("delivered", "features", "not_done", "metric", "base", "now", "source", "other")}
        # 頁面上每個從 Jira 來的字都過同一道：epic 標題、label、附件檔名、版本名也會印出來（Codex 審 225df02）
        title = clean(parent["summary"])
        label = clean(parent["labels"][0]) if len(parent["labels"]) == 1 else None
        sections = {key: clean(value) for key, value in sections.items()}
        acceptance = _acceptance(sections["acceptance"])
        entry, manual = _public_urls(_items(sections["entry"], "[；;]")), _public_urls(_items(sections["manual"], "[；;]"))
        attachments = [dict(a, filename=clean(a["filename"])) for a in (_shown_attachment(a) for a in d["attachments"])]
        versions = [dict(v, name=clean(v["name"])) for v in d["versions"]]
        if scrubbed:
            checks.append(_check("deliverable_internal", item["key"], f"有 {scrubbed} 處內部網址或私有 IP，頁面換成「{INTERNAL_MARK}」；"
                                                                      "Deliverable 只放對外網址"))
        if not done:
            attachments = [dict(a, image=None, video=None) for a in attachments]
        week = period_containing(parse_jira_time(item["resolved"])).week_id if done else None
        stale = done and bool(d["now_date"]) and (period.week_ending - date.fromisoformat(d["now_date"])) > timedelta(days=STALE_OUTCOME_DAYS)
        if done:
            if not entry:
                checks.append(_check("deliverable_no_entry", item["key"], "Deliverable 已完成，描述沒有「入口：」的對外網址，使用的人不知道去哪用"))
            if stale:
                checks.append(_check("deliverable_stale", item["key"], f"成效最新日 {d['now_date']} 離本期結束超過 8 週，成效最新值要更新"))
            if not any(a["mime"] and a["mime"].split("/")[0] in ("image", "video") for a in attachments):
                checks.append(_check("deliverable_no_media", item["key"], "Deliverable 已完成，沒有附畫面或影片"))
        (cards if done else reviewing).append({
            "key": item["key"],
            "epic": parent["key"],
            "epic_summary": title,
            "label": label,
            "done": item["resolved"] if done else None,
            "week": week,
            "new": week == period.week_id,
            "delivered": fields["delivered"],
            "features": _items(fields["features"], "\n"),
            "not_done": _items(fields["not_done"], "\n"),
            "metric": fields["metric"],
            "base": fields["base"],
            "base_date": d["base_date"],
            "now": fields["now"],
            "now_date": d["now_date"],
            "stale": stale,
            "source": fields["source"],
            "other": _items(fields["other"], "[；;]"),
            "acceptance": acceptance,
            "passed": sum(1 for a in acceptance if a["result"] == "通過"),
            "failed": sum(1 for a in acceptance if a["result"] == "未通過"),
            "code": _items(sections["code"], "[；;]"),
            "env": _items(sections["env"], "[；;]"),
            "entry": entry,
            "manual": manual,
            "attachments": attachments,
            "versions": versions,
        })
    for epic in sorted((i for i in issues if i["level"] == EPIC_LEVEL and i["status_category"] == "done" and i["resolved"]),
                       key=lambda i: issue_sort_key(i["key"])):
        finished = parse_jira_time(epic["resolved"]).date()
        has_done = any(c["epic"] == epic["key"] for c in cards)
        if finished >= REQUIRED_SINCE and not has_done:
            checks.append(_check("deliverable_absent", epic["key"], f"epic {finished.isoformat()} 完成，底下沒有完成的 Deliverable"))
    cards.sort(key=lambda c: (c["done"], c["key"]), reverse=True)
    reviewing.sort(key=lambda c: issue_sort_key(c["key"]))
    return cards, reviewing, checks


def _check(kind: str, key, message: str) -> dict:
    return {"kind": kind, "key": key, "message": message}
