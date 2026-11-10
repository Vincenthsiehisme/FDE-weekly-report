"""R2：簽章、設定、憑證、HEAD／PUT 的判讀（vault 的 jira-weekly-report · 成效說明多讀者改版 AC-5）。"""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from helpers import *  # noqa: F401,F403  (把 scripts 放進 sys.path)
from lib import r2
from lib.jira import CredentialError

# AWS 文件 Signature V4 的 S3 範例（GET Object、PUT Object）：key、日期、region 都是文件給的，簽章寫死文件上的值
KEY_ID, SECRET = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
WHEN = datetime(2013, 5, 24, tzinfo=timezone.utc)
CONFIG = {"account_id": "acct", "bucket": "videos", "public_base": "https://pub-x.r2.dev"}
CREDS = {"R2_ACCESS_KEY_ID": "id-example", "R2_SECRET_ACCESS_KEY": "secret-example"}


class SignTest(unittest.TestCase):
    def test_get_object_example(self):
        h = r2.sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"}, r2.EMPTY_SHA256, WHEN,
                    KEY_ID, SECRET, region="us-east-1")
        self.assertEqual(h["authorization"],
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                         "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                         "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
        self.assertEqual(h["x-amz-date"], "20130524T000000Z")
        self.assertNotIn("host", h)  # urllib 自己帶 Host，重複帶會送兩個

    def test_put_object_example(self):
        body = b"Welcome to Amazon S3."
        import hashlib
        h = r2.sign("PUT", "examplebucket.s3.amazonaws.com", "/test%24file.text",
                    {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                    hashlib.sha256(body).hexdigest(), WHEN, KEY_ID, SECRET, region="us-east-1")
        self.assertTrue(h["authorization"].endswith(
            "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
            "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"), h["authorization"])


class ConfigTest(unittest.TestCase):
    def write(self, value):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "r2.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_repo_config_loads(self):
        config = r2.load_config()
        self.assertTrue(config["public_base"].startswith("https://"))

    def test_missing_field_fails(self):
        with self.assertRaisesRegex(ValueError, "少了 bucket"):
            r2.load_config(self.write({"account_id": "a", "public_base": "https://x"}))

    def test_public_base_must_be_https_without_trailing_slash(self):
        with self.assertRaisesRegex(ValueError, "public_base"):
            r2.load_config(self.write(dict(CONFIG, public_base="https://pub-x.r2.dev/")))
        with self.assertRaisesRegex(ValueError, "public_base"):
            r2.load_config(self.write(dict(CONFIG, public_base="http://pub-x.r2.dev")))

    def test_public_url_quotes_the_key(self):
        self.assertEqual(r2.public_url("502 a.mp4", CONFIG), "https://pub-x.r2.dev/502%20a.mp4")


class CredentialsTest(unittest.TestCase):
    def test_environment_first(self):
        self.assertEqual(r2.load_credentials(dict(CREDS), Path("/nonexistent")), CREDS)

    def test_env_file_when_environment_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "r2.env"
            path.write_text("R2_ACCESS_KEY_ID=id-example\nR2_SECRET_ACCESS_KEY='secret-example'\n", encoding="utf-8")
            self.assertEqual(r2.load_credentials({}, path), CREDS)

    def test_missing_everywhere_fails_and_names_only_the_variables(self):
        with self.assertRaises(CredentialError) as ctx:
            r2.load_credentials({"R2_ACCESS_KEY_ID": "id-example"}, Path("/nonexistent"))
        self.assertIn("R2_SECRET_ACCESS_KEY", str(ctx.exception))
        self.assertNotIn("id-example", str(ctx.exception))


class FakeResponse(io.BytesIO):
    def __init__(self, status):
        super().__init__(b"")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


class ClientTest(unittest.TestCase):
    def client(self):
        return r2.R2Client(CONFIG, CREDS, now=lambda: WHEN)

    def test_head_200_is_there_404_is_not(self):
        with mock.patch.object(r2.urllib.request, "urlopen", return_value=FakeResponse(200)) as opened:
            self.assertTrue(self.client().exists("502.mp4"))
        request = opened.call_args[0][0]
        self.assertEqual((request.get_method(), request.full_url), ("HEAD", "https://acct.r2.cloudflarestorage.com/videos/502.mp4"))
        self.assertIn("Credential=id-example/20130524/auto/s3/aws4_request", request.get_header("Authorization"))
        with mock.patch.object(r2.urllib.request, "urlopen", side_effect=http_error(404)):
            self.assertFalse(self.client().exists("502.mp4"))

    def test_head_other_errors_fail_instead_of_meaning_missing(self):
        # 403（key 錯、權限不夠）不能當成不在：當成不在就會每班重傳、而且一樣失敗
        with mock.patch.object(r2.urllib.request, "urlopen", side_effect=http_error(403)):
            with self.assertRaisesRegex(r2.R2Error, "HTTP 403"):
                self.client().exists("502.mp4")

    def test_put_signs_the_body_and_sets_type_and_length(self):
        import hashlib
        with mock.patch.object(r2.urllib.request, "urlopen", return_value=FakeResponse(200)) as opened:
            self.client().put("502.mp4", b"data", "video/mp4")
        request = opened.call_args[0][0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.data, b"data")
        self.assertEqual(request.get_header("X-amz-content-sha256"), hashlib.sha256(b"data").hexdigest())
        self.assertEqual(request.get_header("Content-type"), "video/mp4")
        self.assertEqual(request.get_header("Content-length"), "4")
        self.assertIn("SignedHeaders=content-length;content-type;host;x-amz-content-sha256;x-amz-date", request.get_header("Authorization"))

    def test_put_2xx_other_than_200_fails(self):
        with mock.patch.object(r2.urllib.request, "urlopen", return_value=FakeResponse(204)):
            with self.assertRaisesRegex(r2.R2Error, "不是 200"):
                self.client().put("502.mp4", b"data", "video/mp4")

    def test_put_error_names_the_object_not_the_secret(self):
        with mock.patch.object(r2.urllib.request, "urlopen", side_effect=http_error(403, b"<Error>AccessDenied</Error>")):
            with self.assertRaises(r2.R2Error) as ctx:
                self.client().put("502.mp4", b"data", "video/mp4")
        self.assertIn("videos/502.mp4", str(ctx.exception))
        self.assertNotIn("secret-example", str(ctx.exception))

    def test_public_head(self):
        with mock.patch.object(r2.urllib.request, "urlopen", return_value=FakeResponse(200)) as opened:
            self.assertTrue(r2.public_exists("https://pub-x.r2.dev/502.mp4"))
        # r2.dev 擋 Python 預設的 User-Agent（回 403），要帶自己的名字
        self.assertEqual(opened.call_args[0][0].get_header("User-agent"), "fde-weekly-report")
        with mock.patch.object(r2.urllib.request, "urlopen", side_effect=http_error(404)):
            self.assertFalse(r2.public_exists("https://pub-x.r2.dev/502.mp4"))
        with mock.patch.object(r2.urllib.request, "urlopen", return_value=FakeResponse(204)):
            with self.assertRaisesRegex(r2.R2Error, "不是 200"):
                r2.public_exists("https://pub-x.r2.dev/502.mp4")
        with mock.patch.object(r2.urllib.request, "urlopen", side_effect=http_error(429)):
            with self.assertRaisesRegex(r2.R2Error, "429"):
                r2.public_exists("https://pub-x.r2.dev/502.mp4")


if __name__ == "__main__":
    unittest.main()
