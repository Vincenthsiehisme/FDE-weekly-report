"""Jira REST v3 連線與抓取。只讀，不寫任何卡片。

憑證順序：環境變數（雲端 routine）→ ~/.config/jira-ops/env（本機，與 jira-ops skill 共用）。
兩處都缺就直接失敗，不拿空字串去打 API。錯誤訊息只列缺的變數名，不印值。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

REQUIRED = ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN")
ENV_FILE = Path.home() / ".config" / "jira-ops" / "env"
MAX_PAGES = 200
BULK_IDS = 1000  # changelog/bulkfetch 一次最多 1000 張卡


class CredentialError(RuntimeError):
    pass


class JiraError(RuntimeError):
    pass


def _parse_env_file(path: Path) -> dict:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_credentials(environ=None, env_file: Path = ENV_FILE) -> dict:
    environ = os.environ if environ is None else environ
    if all(environ.get(k) for k in REQUIRED):
        return {k: environ[k] for k in REQUIRED}
    if env_file.is_file():
        values = _parse_env_file(env_file)
        missing = [k for k in REQUIRED if not values.get(k)]
        if missing:
            raise CredentialError(f"{env_file} 缺少 {', '.join(missing)}")
        return {k: values[k] for k in REQUIRED}
    missing = [k for k in REQUIRED if not environ.get(k)]
    raise CredentialError(f"找不到 Jira 憑證：環境變數缺少 {', '.join(missing)}，也沒有 {env_file}")


class JiraClient:
    def __init__(self, creds: dict):
        self.site = creds["JIRA_SITE"]
        token = base64.b64encode(f"{creds['JIRA_EMAIL']}:{creds['JIRA_API_TOKEN']}".encode()).decode()
        self._auth = f"Basic {token}"

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"https://{self.site}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": self._auth, "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            raise JiraError(f"Jira API HTTP {err.code}：POST {path} — {detail}") from err
        except urllib.error.URLError as err:
            raise JiraError(f"連不到 Jira（{self.site}）：{err.reason}") from err

    def verify(self) -> None:
        """抓資料前確認 Jira 認得這組憑證。email 或 token 錯時 search 不回 401，而是當成匿名回 200 和 0 張卡，
        錯誤只說得出「查到 0 張卡」（2026-09-29 雲端第一班）；/myself 不接受匿名，憑證錯就回 401。"""
        request = urllib.request.Request(
            f"https://{self.site}/rest/api/3/myself",
            method="GET",
            headers={"Authorization": self._auth, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read()
        except urllib.error.HTTPError as err:
            if err.code == 401:
                reason = err.headers.get("X-Seraph-LoginReason") or "無"
                raise CredentialError(
                    f"Jira 不接受這組憑證（GET /rest/api/3/myself 回 401，X-Seraph-LoginReason：{reason}）："
                    "檢查 JIRA_EMAIL 與 JIRA_API_TOKEN，token 是否完整、過期或被撤銷") from err
            detail = err.read().decode(errors="replace")[:500]
            raise JiraError(f"Jira API HTTP {err.code}：GET /rest/api/3/myself — {detail}") from err
        except urllib.error.URLError as err:
            raise JiraError(f"連不到 Jira（{self.site}）：{err.reason}") from err

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(
            f"https://{self.site}{path}", method="GET",
            headers={"Authorization": self._auth, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            raise JiraError(f"Jira API HTTP {err.code}：GET {path} — {detail}") from err
        except urllib.error.URLError as err:
            raise JiraError(f"連不到 Jira（{self.site}）：{err.reason}") from err

    def project_ids(self, keys) -> dict:
        """{專案 key: 專案 id}。changelog 的專案異動只記 id。"""
        return {key: str(self._get(f"/rest/api/3/project/{key}")["id"]) for key in keys}

    def changelogs(self, issue_ids: list, field_ids: list) -> dict:
        """/rest/api/3/changelog/bulkfetch 以 nextPageToken 翻頁，只取 field_ids 的異動。回傳 {issue id: [changeHistory]}。"""
        found = {}
        for start in range(0, len(issue_ids), BULK_IDS):
            token = None
            for _ in range(MAX_PAGES):
                body = {"issueIdsOrKeys": issue_ids[start:start + BULK_IDS], "fieldIds": field_ids, "maxResults": BULK_IDS}
                if token:
                    body["nextPageToken"] = token
                page = self._post("/rest/api/3/changelog/bulkfetch", body)
                if "issueChangeLogs" not in page:
                    raise JiraError(f"changelog 回應沒有 issueChangeLogs 欄位：{sorted(page)}")
                for log in page["issueChangeLogs"]:
                    found.setdefault(str(log["issueId"]), []).extend(log["changeHistories"])
                token = page.get("nextPageToken")
                if not token:
                    break
            else:
                raise JiraError(f"changelog 翻頁超過 {MAX_PAGES} 頁仍未結束")
        return found

    def search(self, jql: str, fields: list) -> list:
        """/rest/api/3/search/jql 以 nextPageToken 翻頁，回傳全部 issue。"""
        issues, token = [], None
        for _ in range(MAX_PAGES):
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            page = self._post("/rest/api/3/search/jql", body)
            if "issues" not in page:
                raise JiraError(f"search 回應沒有 issues 欄位：{sorted(page)}")
            issues.extend(page["issues"])
            token = page.get("nextPageToken")
            if page.get("isLast", token is None) or not token:
                return issues
        raise JiraError(f"翻頁超過 {MAX_PAGES} 頁仍未結束：{jql}")
