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
