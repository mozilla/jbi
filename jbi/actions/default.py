"""
Default action is listed below.
`init` is required; and requires at minimum the `jira_project_key` parameter.
The `label_field` parameter configures which Jira field is used to store the
labels generated from the Bugzilla status whiteboard.

`init` should return a __call__able
"""
import logging
from typing import Any

from jbi import ActionResult, Operation
from jbi.environment import get_settings
from jbi.errors import ActionError
from jbi.models import (
    ActionLogContext,
    BugzillaBug,
    BugzillaWebhookComment,
    BugzillaWebhookEvent,
    JiraContext,
)
from jbi.services import bugzilla, jira

settings = get_settings()

logger = logging.getLogger(__name__)

JIRA_DESCRIPTION_CHAR_LIMIT = 32767
JIRA_REQUIRED_PERMISSIONS = {
    "ADD_COMMENTS",
    "CREATE_ISSUES",
    "DELETE_ISSUES",
    "EDIT_ISSUES",
}


def init(jira_project_key, sync_whiteboard_labels=True, **kwargs):
    """Function that takes required and optional params and returns a callable object"""
    return DefaultExecutor(
        jira_project_key=jira_project_key,
        sync_whiteboard_labels=sync_whiteboard_labels,
        **kwargs,
    )


class DefaultExecutor:
    """Callable class that encapsulates the default action."""

    def __init__(self, jira_project_key, **kwargs):
        """Initialize DefaultExecutor Object"""
        self.jira_project_key = jira_project_key
        self.sync_whiteboard_labels = kwargs.get("sync_whiteboard_labels", True)

    def __call__(  # pylint: disable=inconsistent-return-statements
        self,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
    ) -> ActionResult:
        """Called from BZ webhook when default action is used. All default-action webhook-events are processed here."""
        target = event.target
        if target == "comment":
            return self.comment_create_or_noop(bug=bug, event=event)
        if target == "bug":
            return self.bug_create_or_update(bug=bug, event=event)
        logger.debug(
            "Ignore event target %r",
            target,
            extra=ActionLogContext(
                bug=bug,
                event=event,
                operation=Operation.IGNORE,
            ).dict(),
        )
        return False, {}

    def comment_create_or_noop(
        self, bug: BugzillaBug, event: BugzillaWebhookEvent
    ) -> ActionResult:
        """Confirm issue is already linked, then apply comments; otherwise noop"""
        linked_issue_key = bug.extract_from_see_also()

        log_context = ActionLogContext(
            event=event,
            bug=bug,
            operation=Operation.COMMENT,
            jira=JiraContext(
                issue=linked_issue_key,
                project=self.jira_project_key,
            ),
        )
        if not linked_issue_key:
            logger.debug(
                "No Jira issue linked to Bug %s",
                bug.id,
                extra=log_context.dict(),
            )
            return False, {}

        if bug.comment is None:
            logger.debug(
                "No matching comment found in payload",
                extra=log_context.dict(),
            )
            return False, {}

        commenter = event.user.login if event.user else "unknown"
        jira_response = add_jira_comment(
            log_context, linked_issue_key, commenter, bug.comment
        )
        return True, {"jira_response": jira_response}

    def jira_comments_for_update(
        self,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
    ):
        """Returns the comments to post to Jira for a changed bug"""
        return bug.map_changes_as_comments(event)

    def update_issue(
        self,
        bug: BugzillaBug,
        event: BugzillaWebhookEvent,
        linked_issue_key: str,
        is_new: bool,
    ):
        """Allows sub-classes to modify the Jira issue in response to a bug event"""

    def bug_create_or_update(
        self, bug: BugzillaBug, event: BugzillaWebhookEvent
    ) -> ActionResult:
        """Create and link jira issue with bug, or update; rollback if multiple events fire"""
        linked_issue_key = bug.extract_from_see_also()
        if not linked_issue_key:
            return self.create_and_link_issue(bug=bug, event=event)

        log_context = ActionLogContext(
            event=event,
            bug=bug,
            operation=Operation.LINK,
            jira=JiraContext(
                issue=linked_issue_key,
                project=self.jira_project_key,
            ),
        )

        jira_response_update = update_jira_issue(
            log_context, bug, linked_issue_key, self.sync_whiteboard_labels
        )

        comments = self.jira_comments_for_update(bug=bug, event=event)
        jira_response_comments = []
        for i, comment in enumerate(comments):
            logger.debug(
                "Create comment #%s on Jira issue %s",
                i + 1,
                linked_issue_key,
                extra=log_context.update(operation=Operation.COMMENT).dict(),
            )
            jira_response_comments.append(
                jira.get_client().issue_add_comment(
                    issue_key=linked_issue_key, comment=comment
                )
            )

        self.update_issue(bug, event, linked_issue_key, is_new=False)

        return True, {"jira_responses": [jira_response_update, jira_response_comments]}

    def create_and_link_issue(
        self,
        bug,
        event,
    ) -> ActionResult:
        """create jira issue and establish link between bug and issue; rollback/delete if required"""
        log_context = ActionLogContext(
            event=event,
            bug=bug,
            operation=Operation.CREATE,
            jira=JiraContext(
                project=self.jira_project_key,
            ),
        )
        issue_key = create_jira_issue(
            log_context,
            bug,
            self.jira_project_key,
            sync_whiteboard_labels=self.sync_whiteboard_labels,
        )

        log_context.jira.issue = issue_key

        bug = bugzilla.get_client().get_bug(bug.id)
        jira_response_delete = delete_jira_issue_if_duplicate(
            log_context, bug, issue_key
        )
        if jira_response_delete:
            return True, {"jira_response": jira_response_delete}

        bugzilla_response = add_link_to_jira(log_context, bug, issue_key)

        jira_response = add_link_to_bugzilla(log_context, issue_key, bug)

        self.update_issue(bug=bug, event=event, linked_issue_key=issue_key, is_new=True)

        return True, {
            "bugzilla_response": bugzilla_response,
            "jira_response": jira_response,
        }


def create_jira_issue(context, bug, jira_project_key, sync_whiteboard_labels) -> str:
    """Create a Jira issue with the specified fields and return its key."""
    logger.debug(
        "Create new Jira issue for Bug %s",
        bug.id,
        extra=context.dict(),
    )
    comment_list = bugzilla.get_client().get_comments(bug.id)
    description = comment_list[0].text[:JIRA_DESCRIPTION_CHAR_LIMIT]
    fields: dict[str, Any] = {
        "summary": bug.summary,
        "issuetype": {"name": bug.issue_type()},
        "description": description,
        "project": {"key": jira_project_key},
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_create = jira.get_client().create_issue(fields=fields)

    # Jira response can be of the form: List or Dictionary
    if isinstance(jira_response_create, list):
        # if a list is returned, get the first item
        jira_response_create = jira_response_create[0]

    if isinstance(jira_response_create, dict):
        # if a dict is returned or the first item in a list, confirm there are no errors
        if any(
            element in ["errors", "errorMessages"] and jira_response_create[element]
            for element in jira_response_create.keys()
        ):
            raise ActionError(f"response contains error: {jira_response_create}")

    issue_key: str = jira_response_create.get("key")
    return issue_key


def update_jira_issue(context, bug, issue_key, sync_whiteboard_labels):
    """Update the fields of an existing Jira issue"""
    logger.debug(
        "Update fields of Jira issue %s for Bug %s",
        issue_key,
        bug.id,
        extra=context.dict(),
    )
    fields = {
        "summary": bug.summary,
    }
    if sync_whiteboard_labels:
        fields["labels"] = bug.get_jira_labels()

    jira_response_update = jira.get_client().update_issue_field(
        key=issue_key, fields=fields
    )
    return jira_response_update


def add_jira_comment(
    context, issue_key, commenter: str, comment: BugzillaWebhookComment
):
    """Publish a comment on the specified Jira issue"""
    formatted_comment = f"*({commenter})* commented: \n{{quote}}{comment.body}{{quote}}"
    jira_response = jira.get_client().issue_add_comment(
        issue_key=issue_key,
        comment=formatted_comment,
    )
    logger.debug(
        "Comment added to Jira issue %s",
        issue_key,
        extra=context.dict(),
    )
    return jira_response


def delete_jira_issue_if_duplicate(context, bug, issue_key):
    """Rollback the Jira issue creation if there is already a linked Jira issue
    on the Bugzilla ticket"""
    # In the time taken to create the Jira issue the bug may have been updated so
    # re-retrieve it to ensure we have the latest data.
    jira_key_in_bugzilla = bug.extract_from_see_also()
    _duplicate_creation_event = (
        jira_key_in_bugzilla is not None and issue_key != jira_key_in_bugzilla
    )
    if not _duplicate_creation_event:
        return None

    logger.warning(
        "Delete duplicated Jira issue %s from Bug %s",
        issue_key,
        bug.id,
        extra=context.update(operation=Operation.DELETE).dict(),
    )
    jira_response_delete = jira.get_client().delete_issue(issue_id_or_key=issue_key)
    return jira_response_delete


def add_link_to_jira(context, bug, issue_key):
    """Add link to Jira in Bugzilla ticket"""
    jira_url = f"{settings.jira_base_url}browse/{issue_key}"
    logger.debug(
        "Link %r on Bug %s",
        jira_url,
        bug.id,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    return bugzilla.get_client().update_bug(bug.id, see_also_add=jira_url)


def add_link_to_bugzilla(context, issue_key, bug):
    """Add link to Bugzilla ticket in Jira issue"""
    bugzilla_url = f"{settings.bugzilla_base_url}/show_bug.cgi?id={bug.id}"
    logger.debug(
        "Link %r on Jira issue %s",
        bugzilla_url,
        issue_key,
        extra=context.update(operation=Operation.LINK).dict(),
    )
    icon_url = f"{settings.bugzilla_base_url}/favicon.ico"
    return jira.get_client().create_or_update_issue_remote_links(
        issue_key=issue_key,
        link_url=bugzilla_url,
        title=bugzilla_url,
        icon_url=icon_url,
        icon_title=icon_url,
    )
