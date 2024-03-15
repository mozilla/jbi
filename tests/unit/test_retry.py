from jbi.retry import retry_failed
from unittest.mock import AsyncMock, MagicMock
import asyncio
from jbi.queue import QueueItem, get_dl_queue
import pytest
import logging
import jbi.runner
from datetime import datetime
from jbi.bugzilla.models import Bug, WebhookRequest, WebhookEvent

@pytest.fixture()
def logger():
    log_info = MagicMock()
    log_warn = MagicMock()
    log_error = MagicMock()
    logger = logging.getLogger(__name__)
    logger.info = log_info
    logger.warn = log_warn
    logger.error = log_error
    return logger


@pytest.fixture()
def execute_action():
    _execute_action = MagicMock()
    jbi.runner.execute_action = _execute_action
    return _execute_action


@pytest.mark.asyncio
async def test_retry_empty_list(
    logger
):
    retrieve = AsyncMock(return_value=[])
    get_dl_queue().retrieve = retrieve

    await retry_failed()
    retrieve.assert_called_once()
    logger.info.assert_not_called()
    logger.warn.assert_not_called()
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_retry_success(
    logger,
    execute_action
):
    queue = get_dl_queue()
    queue.retrieve = AsyncMock(
        return_value=[
            QueueItem(
                timestamp=datetime.now(),
                payload=WebhookRequest(
                    webhook_id = 1,
                    webhook_name = "test",
                    bug=Bug(id=1), 
                    event=WebhookEvent(action="test")
                ),
            )
        ]
    )
    queue.done = AsyncMock()
    
    await retry_failed()
    queue.retrieve.assert_called_once()
    queue.done.assert_called_once()
    logger.info.assert_not_called()
    logger.warn.assert_not_called()
    logger.error.assert_not_called()
    execute_action.assert_called_once()


# @pytest.mark.asyncio
# async def test_retry_fail_and_skip():


# @pytest.mark.asyncio
# async def test_retry_remove_expired():

