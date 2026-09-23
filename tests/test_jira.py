import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from helpers import *  # noqa: F401,F403
from lib.jira import CredentialError, JiraClient, load_credentials
from lib.period import TZ, period_ending
from lib.snapshot import fetch_snapshot, normalize

CREDS = {"JIRA_SITE": "example.atlassian.net", "JIRA_EMAIL": "a@b.c", "JIRA_API_TOKEN": "secret-value"}


def raw(key, type_="任務", level=0, **fields):
    base = {
        "project": {"key": key.split("-")[0]},
        "summary": f"{key} 摘要",
        "issuetype": {"name": type_, "hierarchyLevel": level},
        "status": {"name": "待辦事項", "statusCategory": {"key": "new"}},
        "updated": "2026-09-20T10:00:00.000+0800",
    }
    base.update(fields)
    return {"key": key, "fields": base}


class FakeClient:
    site = "example.atlassian.net"

    def __init__(self, all_issues, kickoffs):
        self.all_issues, self.kickoffs = all_issues, kickoffs

    def search(self, jql, fields):
        return self.kickoffs if "Kick off" in jql else self.all_issues


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
