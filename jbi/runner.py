"""
Execute actions from Webhook requests
"""
import logging
from typing import Optional

from statsd.defaults.env import statsd

from jbi import Operation
from jbi.environment import Settings
from jbi.errors import ActionNotFoundError, IgnoreInvalidRequestError
from jbi.models import (
    ActionContext,
    Actions,
    BugzillaWebhookRequest,
    JiraContext,
    RunnerContext,
)
from jbi.services import bugzilla

logger = logging.getLogger(__name__)


@statsd.timer("jbi.action.execution.timer")
def execute_action(
    request: BugzillaWebhookRequest,
    actions: Actions,
    settings: Settings,
):
    """Execute the configured action for the specified `request`.

    This will raise an `IgnoreInvalidRequestError` error if the request
    does not contain bug data or does not match any action.

    The value returned by the action call is returned.
    """
    bug, event = request.bug, request.event
    runner_context = RunnerContext(
        bug=bug,
        event=event,
        operation=Operation.HANDLE,
    )
    try:
        logger.debug(
            "Handling incoming request",
            extra=runner_context.dict(),
        )
        try:
            if bug.is_private:
                bug = bugzilla.get_client().get_bug(bug.id)
        except Exception as err:
            logger.exception("Failed to get bug: %s", err, extra=runner_context.dict())
            raise IgnoreInvalidRequestError(
                "bug not accessible or bugzilla down"
            ) from err

        runner_context = runner_context.update(bug=bug)
        try:
            action = bug.lookup_action(actions)
        except ActionNotFoundError as err:
            raise IgnoreInvalidRequestError(
                f"no action matching bug whiteboard tags: {err}"
            ) from err
        runner_context = runner_context.update(action=action)

        if bug.is_private and not action.allow_private:
            raise IgnoreInvalidRequestError(
                f"private bugs are not valid for action {action.whiteboard_tag!r}"
            )

        linked_issue_key: Optional[str] = bug.extract_from_see_also()

        action_context = ActionContext(
            bug=bug,
            event=event,
            operation=Operation.IGNORE,
            jira=JiraContext(project=action.jira_project_key, issue=linked_issue_key),
            extra={k: str(v) for k, v in action.parameters.items()},
        )

        if action_context.jira.issue is None:
            if event.target == "bug":
                action_context = action_context.update(operation=Operation.CREATE)

        else:
            if event.target == "bug":
                changed_fields = event.changed_fields() or []
                action_context = action_context.update(
                    operation=Operation.UPDATE,
                    extra={
                        "changed_fields": ", ".join(changed_fields),
                        **action_context.extra,
                    },
                )

            elif event.target == "comment":
                action_context = action_context.update(operation=Operation.COMMENT)

        if action_context.operation == Operation.IGNORE:
            raise IgnoreInvalidRequestError(
                f"ignore event target {action_context.event.target!r}"
            )

        logger.info(
            "Execute action '%s:%s' for Bug %s",
            action.whiteboard_tag,
            action.module,
            bug.id,
            extra=runner_context.update(operation=Operation.EXECUTE).dict(),
        )

        handled, details = action.caller(context=action_context)

        logger.info(
            "Action %r executed successfully for Bug %s",
            action.whiteboard_tag,
            bug.id,
            extra=runner_context.update(
                operation=Operation.SUCCESS if handled else Operation.IGNORE
            ).dict(),
        )
        statsd.incr("jbi.bugzilla.processed.count")
        return details
    except IgnoreInvalidRequestError as exception:
        logger.debug(
            "Ignore incoming request: %s",
            exception,
            extra=runner_context.update(operation=Operation.IGNORE).dict(),
        )
        statsd.incr("jbi.bugzilla.ignored.count")
        raise
