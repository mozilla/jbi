"""
Module for testing src/jbi/configuration.py functionality
"""
# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.jbi import bugzilla
from src.jbi.errors import ActionNotFoundError


@pytest.mark.parametrize(
    "whiteboard,expected",
    [
        ("", ["bugzilla"]),
        ("[test whiteboard]", ["bugzilla", "test.whiteboard", "[test.whiteboard]"]),
        ("[test-whiteboard]", ["bugzilla", "test-whiteboard", "[test-whiteboard]"]),
        (
            "[test whiteboard][test-no-space][test-both space-and-not",
            [
                "bugzilla",
                "test.whiteboard",
                "test-no-space",
                "test-both.space-and-not",
                "[test.whiteboard]",
                "[test-no-space]",
                "[test-both.space-and-not]",
            ],
        ),
    ],
)
def test_jira_labels(whiteboard, expected):
    bug = bugzilla.BugzillaBug(id=0, whiteboard=whiteboard)
    assert bug.get_jira_labels() == expected


@pytest.mark.parametrize(
    "see_also,expected",
    [
        ([], None),
        (["foo"], None),
        (["fail:/format"], None),
        (["foo", "http://jira.net/123"], "123"),
        (["http://bugzilla.org/123"], None),
        (["http://jira.com"], None),
        (["http://mozilla.jira.com/"], None),
        (["http://mozilla.jira.com/123"], "123"),
        (["http://mozilla.jira.com/123/"], "123"),
        (["http://mozilla.jira.com/ticket/123"], "123"),
        (["http://atlassian.com/ticket/123"], "123"),
        (["http://mozilla.jira.com/123", "http://mozilla.jira.com/456"], "123"),
    ],
)
def test_extract_see_also(see_also, expected):
    test_bug = bugzilla.BugzillaBug(id=0, see_also=see_also)
    assert test_bug.extract_from_see_also() == expected


def test_lookup_action(actions_example):
    bug = bugzilla.BugzillaBug.parse_obj(
        {"id": 1234, "whiteboard": "[example][DevTest]"}
    )
    tag, action = bug.lookup_action(actions_example)
    assert tag == "devtest"
    assert "Mocked config" in action.description


def test_lookup_action_missing(actions_example):
    bug = bugzilla.BugzillaBug.parse_obj({"id": 1234, "whiteboard": "example DevTest"})
    with pytest.raises(ActionNotFoundError) as exc_info:
        bug.lookup_action(actions_example)
    assert str(exc_info.value) == "example devtest"
