import base64
import http.client
import io
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from helpers import *  # noqa: F401,F403
from lib.jira import CredentialError, JiraClient, JiraError, load_credentials
from lib.period import TZ, period_ending
from lib.snapshot import fetch_snapshot, normalize

CREDS = {"JIRA_SITE": "example.atlassian.net", "JIRA_EMAIL": "a@b.c", "JIRA_API_TOKEN": "secret-value"}


PROJECT_IDS = {"FDEPROJECT": "10215", "CS": "10216"}
OUTSIDE = "10042"  # Squad 8


def raw(key, type_="任務", level=0, **fields):
    base = {
        "project": {"key": key.split("-")[0], "id": PROJECT_IDS[key.split("-")[0]]},
        "summary": f"{key} 摘要",
        "issuetype": {"name": type_, "hierarchyLevel": level},
        "status": {"name": "待辦事項", "statusCategory": {"key": "new"}},
        "updated": "2026-09-20T10:00:00.000+0800",
    }
    base.update(fields)
    return {"key": key, "id": issue_id(key), "fields": base}


def issue_id(key):
    return str(30000 + int(key.split("-")[1]) + (1000 if key.startswith("CS") else 0))


DONE = {"status": {"name": "完成", "statusCategory": {"key": "done"}}, "resolutiondate": "2026-09-18T10:00:00.000+0800"}


def move(at_ms, from_id, to_id):
    return {"created": at_ms, "items": [{"field": "project", "fieldId": "project", "from": from_id, "to": to_id}]}


class FakeClient:
    site = "example.atlassian.net"

    def __init__(self, all_issues, kickoffs, verify_error=None, histories=None):
        self.all_issues, self.kickoffs, self.verify_error = all_issues, kickoffs, verify_error
        self.histories = histories or {}
        self.searched = False
        self.changelog_ids = None

    def project_ids(self, keys):
        return {k: PROJECT_IDS[k] for k in keys}

    def changelogs(self, issue_ids, field_ids):
        self.changelog_ids = list(issue_ids)
        return {i: self.histories[i] for i in issue_ids if i in self.histories}

    def verify(self):
        if self.verify_error:
            raise self.verify_error

    def search(self, jql, fields):
        self.searched = True
        return self.kickoffs if "Kick off" in jql else self.all_issues


def http_error(code, headers=None):
    message = http.client.HTTPMessage()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError("https://example.atlassian.net/rest/api/3/myself", code, "err", message, io.BytesIO(b"{}"))


class CredentialTest(unittest.TestCase):
    def test_environment_wins(self):
        self.assertEqual(load_credentials(CREDS, Path("/nonexistent")), CREDS)

    def test_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env"
            path.write_text('# c\nJIRA_SITE=x.atlassian.net\nJIRA_EMAIL="a@b.c"\nJIRA_API_TOKEN=t\n', encoding="utf-8")
            self.assertEqual(load_credentials({}, path)["JIRA_EMAIL"], "a@b.c")

    def test_missing_names_listed_without_values(self):
        with self.assertRaises(CredentialError) as ctx:
            load_credentials({"JIRA_SITE": "x", "JIRA_API_TOKEN": "secret-value"}, Path("/nonexistent"))
        self.assertIn("JIRA_EMAIL", str(ctx.exception))
        self.assertNotIn("secret-value", str(ctx.exception))


class PaginationTest(unittest.TestCase):
    def test_follows_next_page_token(self):
        client = JiraClient(CREDS)
        pages = [{"issues": [1, 2], "nextPageToken": "p2", "isLast": False}, {"issues": [3], "isLast": True}]
        sent = []
        client._post = lambda path, body: (sent.append(body), pages.pop(0))[1]
        self.assertEqual(client.search("project = CS", ["summary"]), [1, 2, 3])
        self.assertEqual(sent[1]["nextPageToken"], "p2")


class VerifyTest(unittest.TestCase):
    """憑證錯時 Jira 的查詢回 200 和 0 張卡，只有 /myself 會回 401；verify 要把它講成憑證問題。"""

    def test_401_is_a_credential_error_with_login_reason(self):
        err = http_error(401, {"X-Seraph-LoginReason": "AUTHENTICATED_FAILED"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(CredentialError) as ctx:
                JiraClient(CREDS).verify()
        self.assertIn("不接受這組憑證", str(ctx.exception))
        self.assertIn("AUTHENTICATED_FAILED", str(ctx.exception))
        self.assertNotIn("secret-value", str(ctx.exception))

    def test_verify_asks_myself_with_the_credentials(self):
        seen = []

        def answer(request, timeout):
            seen.append(request)
            return mock.MagicMock(**{"__enter__.return_value.read.return_value": b"{}"})

        with mock.patch("urllib.request.urlopen", side_effect=answer):
            JiraClient(CREDS).verify()
        request = seen[0]
        self.assertEqual(request.full_url, "https://example.atlassian.net/rest/api/3/myself")
        self.assertEqual(request.get_method(), "GET")
        expected = "Basic " + base64.b64encode(f"{CREDS['JIRA_EMAIL']}:{CREDS['JIRA_API_TOKEN']}".encode()).decode()
        self.assertEqual(request.get_header("Authorization"), expected)

    def test_401_without_headers_is_still_a_credential_error(self):
        err = urllib.error.HTTPError("https://example.atlassian.net/rest/api/3/myself", 401, "err", None, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesRegex(CredentialError, "X-Seraph-LoginReason：無"):
                JiraClient(CREDS).verify()

    def test_other_http_errors_stay_jira_errors(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(503)):
            with self.assertRaisesRegex(JiraError, "HTTP 503"):
                JiraClient(CREDS).verify()

    def test_snapshot_stops_before_searching_when_credentials_are_rejected(self):
        client = FakeClient([raw("CS-1")], [], verify_error=CredentialError("Jira 不接受這組憑證"))
        with self.assertRaises(CredentialError):
            fetch_snapshot(client, period_ending(date(2026, 9, 22)), datetime(2026, 9, 22, 23, 59, tzinfo=TZ))
        self.assertFalse(client.searched)


class ChangelogTest(unittest.TestCase):
    def test_follows_next_page_token(self):
        client = JiraClient(CREDS)
        pages = [{"issueChangeLogs": [{"issueId": 1, "changeHistories": [{"created": 1}]}], "nextPageToken": "p2"},
                 {"issueChangeLogs": [{"issueId": 1, "changeHistories": [{"created": 2}]}, {"issueId": 2, "changeHistories": []}]}]
        sent = []
        client._post = lambda path, body: (sent.append(body), pages.pop(0))[1]
        self.assertEqual(client.changelogs(["1", "2"], ["project"]), {"1": [{"created": 1}, {"created": 2}], "2": []})
        self.assertEqual(sent[1]["nextPageToken"], "p2")
        self.assertEqual(sent[0]["fieldIds"], ["project"])

    def test_more_than_1000_ids_go_in_batches(self):
        client = JiraClient(CREDS)
        sent = []
        client._post = lambda path, body: (sent.append(body), {"issueChangeLogs": [{"issueId": i, "changeHistories": []} for i in body["issueIdsOrKeys"]]})[1]
        ids = [str(i) for i in range(1001)]
        self.assertEqual(sorted(client.changelogs(ids, ["project"])), sorted(ids))
        self.assertEqual([len(b["issueIdsOrKeys"]) for b in sent], [1000, 1])


class MovedInTest(unittest.TestCase):
    """改組前的原始資料：完成的工作卡從其他專案搬進來的時間。"""

    def snap(self, issues, histories):
        client = FakeClient(issues, [], histories=histories)
        return client, fetch_snapshot(client, period_ending(date(2026, 9, 22)), datetime(2026, 9, 22, 23, 59, tzinfo=TZ))

    def test_moved_in_only_counts_moves_from_outside_projects(self):
        issues = [raw("CS-1", "大型工作", 1, **DONE), raw("CS-2", **DONE), raw("CS-3", **DONE), raw("CS-4", **DONE), raw("CS-5")]
        histories = {
            issue_id("CS-2"): [move(1_758_000_000_000, OUTSIDE, "10216"), move(1_758_100_000_000, OUTSIDE, "10216")],
            issue_id("CS-3"): [move(1_758_000_000_000, "10215", "10216")],  # FDEPROJECT 搬到 CS：一直在範圍內
        }
        client, snap = self.snap(issues, histories)
        moved = {i["key"]: i["moved_in"] for i in snap["issues"]}
        self.assertEqual(moved["CS-2"], datetime.fromtimestamp(1_758_100_000, timezone.utc).isoformat())  # 取最後一次
        self.assertIsNone(moved["CS-3"])
        self.assertIsNone(moved["CS-4"])
        # 只查完成的工作卡：epic 與未完成的卡不查
        self.assertEqual(sorted(client.changelog_ids), sorted(issue_id(k) for k in ("CS-2", "CS-3", "CS-4")))
        self.assertEqual(snap["schema"], 2)

    def test_move_from_a_project_with_no_cards_is_still_internal(self):
        # 查到的卡全是 CS，FDEPROJECT 一張都沒有；從 FDEPROJECT 搬過來仍不算外部搬入
        _, snap = self.snap([raw("CS-3", **DONE)], {issue_id("CS-3"): [move(1_758_000_000_000, "10215", "10216")]})
        self.assertIsNone(snap["issues"][0]["moved_in"])


class SnapshotTest(unittest.TestCase):
    def test_normalize_requires_hierarchy_level(self):
        broken = raw("CS-1")
        del broken["fields"]["issuetype"]["hierarchyLevel"]
        with self.assertRaisesRegex(ValueError, "hierarchyLevel"):
            normalize(broken)

    def test_kickoff_description_is_merged(self):
        issues = [raw("CS-1", "大型工作", 1), raw("CS-2", "Kick off", 0, parent={"key": "CS-1"})]
        kick = [raw("CS-2", "Kick off", 0, description={"type": "doc", "content": []})]
        snap = fetch_snapshot(FakeClient(issues, kick), period_ending(date(2026, 9, 22)), datetime(2026, 9, 22, 23, 59, tzinfo=TZ))
        self.assertEqual(snap["issues"][1]["description"], {"type": "doc", "content": []})
        self.assertEqual(snap["counts"], {"FDEPROJECT": 0, "CS": 2})

    def test_zero_issues_in_both_projects_fails(self):
        with self.assertRaisesRegex(ValueError, "0 張卡"):
            fetch_snapshot(FakeClient([], []), period_ending(date(2026, 9, 22)), datetime(2026, 9, 22, 23, 59, tzinfo=TZ))


if __name__ == "__main__":
    unittest.main()
