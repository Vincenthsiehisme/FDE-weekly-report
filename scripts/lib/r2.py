"""Cloudflare R2：成效說明的影片放這裡，頁面用公開網址直接播（vault 的 jira-weekly-report · 成效說明多讀者改版 AC-5，
2026-11-03 使用者裁「放 R2，限 100 MB」）。

- bucket、帳號、公開網址在 config/r2.json（不是秘密）；寫入用的 key 只從環境變數或本機 ~/.config/fde-weekly-report/r2.env 讀，
  兩處都缺就直接失敗。錯誤訊息只列缺的變數名，不印值。
- 走 S3 相容 API，簽章是 AWS Signature V4（region auto），只用標準函式庫。只做兩件事：HEAD 看物件在不在、PUT 上傳。
- 公開網址（r2.dev）給頁面播放，也給 finalize 確認影片真的在（HEAD 不帶憑證）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .jira import CredentialError, _parse_env_file

CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "r2.json"
REQUIRED = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
ENV_FILE = Path.home() / ".config" / "fde-weekly-report" / "r2.env"
REGION = "auto"
SERVICE = "s3"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# r2.dev 對 Python 預設的 User-Agent（Python-urllib/3.x）回 403，curl 與自訂名稱回 200（2026-11-10 實測 14437.mp4）
USER_AGENT = "fde-weekly-report"


class R2Error(RuntimeError):
    pass


def load_config(path: Path = CONFIG_FILE) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for name in ("account_id", "bucket", "public_base"):
        if not isinstance(config.get(name), str) or not config[name]:
            raise ValueError(f"{path} 少了 {name}")
    if not config["public_base"].startswith("https://") or config["public_base"].endswith("/"):
        raise ValueError(f"{path} 的 public_base 要是 https:// 開頭、結尾不帶 /")
    return config


def public_url(key: str, config: dict = None) -> str:
    config = load_config() if config is None else config
    return f"{config['public_base']}/{quote(key)}"


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
    raise CredentialError(f"找不到 R2 憑證：環境變數缺少 {', '.join(missing)}，也沒有 {env_file}")


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, headers: dict, payload_sha256: str, when: datetime,
         key_id: str, secret: str, region: str = REGION) -> dict:
    """回傳要加上的 headers（含 Authorization）。headers 是除了 host、x-amz-date、x-amz-content-sha256 以外要簽的。
    path 要已經照 S3 的規則編碼過（每段 quote，保留 /）。沒有 query string。"""
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    signed = {k.lower(): str(v).strip() for k, v in headers.items()}
    signed.update({"host": host, "x-amz-date": stamp, "x-amz-content-sha256": payload_sha256})
    names = sorted(signed)
    canonical = "\n".join([method, path, "", "".join(f"{n}:{signed[n]}\n" for n in names), ";".join(names), payload_sha256])
    scope = f"{day}/{region}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}".encode("utf-8"), day), region), SERVICE), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k: v for k, v in signed.items() if k != "host"}
    out["authorization"] = f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    return out


class R2Client:
    def __init__(self, config: dict, creds: dict, now=lambda: datetime.now(timezone.utc)):
        self.host = f"{config['account_id']}.r2.cloudflarestorage.com"
        self.bucket = config["bucket"]
        self._key_id, self._secret = creds["R2_ACCESS_KEY_ID"], creds["R2_SECRET_ACCESS_KEY"]
        self._now = now

    def _request(self, method: str, key: str, data: bytes = None, headers: dict = None):
        path = f"/{quote(self.bucket)}/{quote(key)}"
        payload = hashlib.sha256(data).hexdigest() if data is not None else EMPTY_SHA256
        signed = sign(method, self.host, path, headers or {}, payload, self._now(), self._key_id, self._secret)
        request = urllib.request.Request(f"https://{self.host}{path}", data=data, method=method, headers=signed)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.status
        except urllib.error.HTTPError as err:
            if method == "HEAD" and err.code == 404:
                return 404
            # 錯誤內文是 R2 的 XML，不含我們的 key；只留前 300 字定位用
            detail = err.read().decode(errors="replace")[:300] if method != "HEAD" else ""
            raise R2Error(f"R2 {method} {self.bucket}/{key} 回 HTTP {err.code} {detail}") from None
        except urllib.error.URLError as err:
            raise R2Error(f"連不到 R2（{self.host}）：{err.reason}") from None

    def exists(self, key: str) -> bool:
        return self._request("HEAD", key) == 200

    def put(self, key: str, data: bytes, content_type: str) -> None:
        status = self._request("PUT", key, data, {"content-type": content_type, "content-length": str(len(data))})
        if status != 200:
            raise R2Error(f"R2 PUT {self.bucket}/{key} 回 HTTP {status}，不是 200")


def public_exists(url: str) -> bool:
    """公開網址 HEAD：200 是在，404 是不在，其他一律失敗（r2.dev 被限速或擋住時不能當成不在）。"""
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return False
        raise R2Error(f"HEAD {url} 回 HTTP {err.code}") from None
    except urllib.error.URLError as err:
        raise R2Error(f"連不到 {url}：{err.reason}") from None
