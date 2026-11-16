"""Optional adapter for GitHub issues. It is off by default (`[github] enabled = false`).

The sender of an event is the issue's `user.login` from the API, never a name in the text.
Every HTTP or parse failure raises TrackerError, so a failed read never looks like "no issues".
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .inbox import Event, make_event
from .tracker import TrackerError

API = "https://api.github.com"


class GitHubTracker:
    source = "github"

    def __init__(self, repo: str, token: str, operator_logins: list[str], opener=urllib.request.urlopen):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            raise TrackerError(f"bad repository name {repo!r}")
        self.repo, self.token, self.operators, self.open = repo, token, set(operator_logins), opener

    def _call(self, method: str, path: str, body: object | None = None) -> tuple[object, dict]:
        url = path if path.startswith("https://") else f"{API}/repos/{self.repo}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with self.open(req) as resp:
                status = getattr(resp, "status", 200)
                raw = resp.read()
                headers = dict(resp.headers or {})
        except (urllib.error.URLError, OSError) as e:
            raise TrackerError(f"GitHub {method} {path}: {e}") from e
        if not 200 <= status < 300:
            raise TrackerError(f"GitHub {method} {path}: status {status}")
        try:
            return (json.loads(raw) if raw else None), headers
        except json.JSONDecodeError as e:
            raise TrackerError(f"GitHub {method} {path}: reply is not JSON") from e

    def _pages(self, path: str) -> list[dict]:
        # 100 is the largest page GitHub serves (its own limit). Every page is read.
        out: list[dict] = []
        url = path + ("&" if "?" in path else "?") + "per_page=100"
        while url:
            page, headers = self._call("GET", url)
            if not isinstance(page, list):
                raise TrackerError(f"GitHub {path}: reply is not a list")
            out += page
            match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            url = match.group(1) if match else ""
        return out

    @staticmethod
    def _number(issue_id: str) -> int:
        if not str(issue_id).isdigit():
            raise TrackerError(f"bad issue number {issue_id!r}")
        return int(issue_id)

    def ingest(self, now: float) -> tuple[list[Event], list[str]]:
        try:
            issues = [i for i in self._pages("/issues?state=open") if "pull_request" not in i]
        except TrackerError as e:
            return [], [str(e)]
        events = []
        for issue in issues:
            login = (issue.get("user") or {}).get("login", "")
            role = "operator" if login in self.operators else "reporter"
            snapshot = {"issue_id": str(issue["number"]), "title": issue.get("title", ""),
                        "body": issue.get("body") or ""}
            events.append(make_event("issue_opened", self.source, login, role, now, snapshot,
                                     key=("github", self.repo, "opened", issue["number"])))
        return events, []

    def get(self, issue_id: str) -> dict:
        issue, _ = self._call("GET", f"/issues/{self._number(issue_id)}")
        if not isinstance(issue, dict):
            raise TrackerError("GitHub issue is not an object")
        return {"id": issue_id, "title": issue.get("title", ""), "body": issue.get("body") or "",
                "labels": [lab["name"] for lab in issue.get("labels", [])],
                "comments": self.comments(issue_id)}

    def labels(self, issue_id: str) -> list[str]:
        return [lab["name"] for lab in self._pages(f"/issues/{self._number(issue_id)}/labels")]

    def comments(self, issue_id: str) -> list[dict]:
        pages = self._pages(f"/issues/{self._number(issue_id)}/comments")
        return [{"author": c["user"]["login"], "text": c["body"]} for c in pages]

    def set_labels(self, issue_id: str, labels: list[str]) -> None:
        self._call("PUT", f"/issues/{self._number(issue_id)}/labels", {"labels": labels})

    def add_comment(self, issue_id: str, comment: dict) -> None:
        self._call("POST", f"/issues/{self._number(issue_id)}/comments", {"body": comment["text"]})
