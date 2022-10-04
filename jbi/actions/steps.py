"""
Collection of reusable action steps.

Each step takes an `ActionContext` and a list of arbitrary parameters.
"""

import logging

from jbi import Operation
from jbi.models import ActionContext
from jbi.services import bugzilla, jira

logger = logging.getLogger(__name__)


def create_comment(context: ActionContext, **parameters):
    """Create a Jira comment using `context.bug.comment`"""
    bug = context.bug

    if bug.comment is None:
        logger.debug(
            "No matching comment found in payload",
            extra=context.dict(),
        )
        return context, ()

    jira_response = jira.add_jira_comment(context)
    return context, (jira_response,)


def create_issue(context: ActionContext, **parameters):
    """Create the Jira issue with the first comment as the description."""
    sync_whiteboard_labels: bool = parameters.get("sync_whiteboard_labels", True)
    bug = context.bug

    # In the payload of a bug creation, the `comment` field is `null`.
    # We fetch the list of comments to use the first one as the Jira issue description.
    comment_list = bugzilla.get_client().get_comments(bug.id)
    description = comment_list[0].text if comment_list else ""

    jira_create_response = jira.create_jira_issue(
        context,
        description,
        sync_whiteboard_labels=sync_whiteboard_labels,
    )
    issue_key = jira_create_response.get("key")

    context = context.update(jira=context.jira.update(issue=issue_key))
    return context, (jira_create_response,)


def add_link_to_jira(context: ActionContext, **parameters):
    """Add the URL to the Jira issue in the `see_also` field on the Bugzilla ticket"""
    bugzilla_response = bugzilla.add_link_to_jira(context)
    return context, (bugzilla_response,)


def add_link_to_bugzilla(context: ActionContext, **parameters):
    """Add the URL of the Bugzilla ticket to the links of the Jira issue"""
    jira_response = jira.add_link_to_bugzilla(context)
    return context, (jira_response,)


def maybe_delete_duplicate(context: ActionContext, **parameters):
    """
    In the time taken to create the Jira issue the bug may have been updated so
    re-retrieve it to ensure we have the latest data, and delete any duplicate
    if two Jira issues were created for the same Bugzilla ticket.
    """
    latest_bug = bugzilla.get_client().get_bug(context.bug.id)
    jira_response_delete = jira.delete_jira_issue_if_duplicate(context, latest_bug)
    if jira_response_delete:
        return context, (jira_response_delete,)
    return context, ()


def update_issue(context: ActionContext, **parameters):
    """Update the Jira issue's summary and labels if the linked bug is modified."""
    sync_whiteboard_labels: bool = parameters.get("sync_whiteboard_labels", True)

    resp = jira.update_jira_issue(context, sync_whiteboard_labels)

    return context, (resp,)


def add_jira_comments_for_changes(context: ActionContext, **parameters):
    """Add a Jira comment for each field (assignee, status, resolution) change on
    the Bugzilla ticket."""
    comments_responses = jira.add_jira_comments_for_changes(context)

    return context, tuple(comments_responses)


def maybe_assign_jira_user(context: ActionContext, **parameters):
    """Assign the user on the Jira issue, based on the Bugzilla assignee email.

    It will attempt to assign the Jira issue the same person as the bug is assigned to. This relies on
    the user using the same email address in both Bugzilla and Jira. If the user does not exist in Jira
    then the assignee is cleared from the Jira issue. The Jira account that JBI uses requires the "Browse
    users and groups" global permission in order to set the assignee.
    """
    event = context.event
    bug = context.bug

    if context.operation == Operation.CREATE:
        if not bug.is_assigned():
            return context, ()

        try:
            resp = jira.assign_jira_user(context, bug.assigned_to)  # type: ignore
            return context, (resp,)
        except ValueError as exc:
            logger.debug(str(exc), extra=context.dict())

    if context.operation == Operation.UPDATE:
        changed_fields = event.changed_fields() or []

        if "assigned_to" not in changed_fields:
            return context, ()

        if not bug.is_assigned():
            resp = jira.clear_assignee(context)
        else:
            try:
                resp = jira.assign_jira_user(context, bug.assigned_to)  # type: ignore
            except ValueError as exc:
                logger.debug(str(exc), extra=context.dict())
                # If that failed then just fall back to clearing the assignee.
                resp = jira.clear_assignee(context)
        return context, (resp,)

    # This happens when exceptions are raised an ignored.
    return context, ()


def maybe_update_issue_resolution(
    context: ActionContext,
    **parameters,
):
    """
    Update the Jira issue status
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    resolution_map: dict[str, str] = parameters.get("resolution_map", {})
    jira_resolution = resolution_map.get(context.bug.resolution or "")
    if jira_resolution is None:
        logger.debug(
            "Bug resolution was not in the resolution map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        return context, ()

    if context.operation == Operation.CREATE:
        resp = jira.update_issue_resolution(context, jira_resolution)
        return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = context.event.changed_fields() or []

        if "resolution" in changed_fields:
            resp = jira.update_issue_resolution(context, jira_resolution)
            return context, (resp,)

    return context, ()


def maybe_update_issue_status(context: ActionContext, **parameters):
    """
    Update the Jira issue resolution
    https://support.atlassian.com/jira-cloud-administration/docs/what-are-issue-statuses-priorities-and-resolutions/
    """
    resolution_map: dict[str, str] = parameters.get("status_map", {})
    bz_status = context.bug.resolution or context.bug.status
    jira_status = resolution_map.get(bz_status or "")

    if jira_status is None:
        logger.debug(
            "Bug status was not in the status map.",
            extra=context.update(
                operation=Operation.IGNORE,
            ).dict(),
        )
        return context, ()

    if context.operation == Operation.CREATE:
        resp = jira.update_issue_status(context, jira_status)
        return context, (resp,)

    if context.operation == Operation.UPDATE:
        changed_fields = context.event.changed_fields() or []

        if "status" in changed_fields or "resolution" in changed_fields:
            resp = jira.update_issue_status(context, jira_status)
            return context, (resp,)

    return context, ()
