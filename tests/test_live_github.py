"""Lesson 14: a mock measures the mock. This contract test reads a real repository.

It is read-only, and it is skipped with a visible reason when no credentials are set.
A green offline run is not a green live run.
"""

import os

import pytest

from steward.github import GitHubTracker

TOKEN = os.environ.get("STEWARD_GITHUB_TOKEN")
REPO = os.environ.get("STEWARD_GITHUB_REPO")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (TOKEN and REPO), reason="live contract test: set STEWARD_GITHUB_TOKEN and STEWARD_GITHUB_REPO"),
]


def test_the_real_api_lists_issues_and_reads_labels():
    tracker = GitHubTracker(REPO, TOKEN, [])
    events, failures = tracker.ingest(0.0)
    assert failures == []
    if events:
        assert isinstance(tracker.labels(events[0].data["issue_id"]), list)
