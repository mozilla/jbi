"""This `queue` module stores Bugzilla webhook messages that we failed to sync
to Jira.

As Bugzilla sends us webhook messages, we want to eagerly accept them and
return a `200` response so that we don't prevent it from sending new messages.
But if we fail to sync a bug, we want to keep the message so we can retry it
later. We also want to store any messages that might be successfuly synced, but
were preceded by a message that wasn't synced.

Classes:
    - QueueItem: An entry in the dead letter queue, containing information
      about the payload, timestamp, and any associated errors when attempting
      to sync the bug.
    - PythonException: Information about any exception that occured when
      syncing a bug, stored along with the item.
    - DeadLetterQueue: Class representing the dead letter queue system, providing methods
      for adding, retrieving, and managing queue items. Supports pluggable backends.
    - QueueBackend: Abstract base class defining the interface for a DeadLetterQueue backend.
    - FileBackend: Implementation of a QueueBackend that stores messages in files.
    - InvalidQueueDSNError: Exception raised when an invalid queue DSN is provided.
    - QueueItemRetrievalError: Exception raised when the queue is unable to retreive a failed
      item and parse it as an item
"""

import logging
import tempfile
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from json import JSONDecodeError
from pathlib import Path
from typing import AsyncIterator, List, Optional
from urllib.parse import ParseResult, urlparse

import dockerflow.checks
from pydantic import BaseModel, FileUrl, ValidationError

from jbi import bugzilla
from jbi.environment import get_settings

logger = logging.getLogger(__name__)


class QueueItemRetrievalError(Exception):
    pass


class InvalidQueueDSNError(Exception):
    pass


class PythonException(BaseModel, frozen=True):
    type: str
    description: str
    details: str

    @classmethod
    def from_exc(cls, exc: Exception):
        return PythonException(
            type=exc.__class__.__name__,
            description=str(exc),
            details="".join(traceback.format_exception(exc)),
        )


class QueueItem(BaseModel, frozen=True):
    """Dead Letter Queue entry."""

    payload: bugzilla.WebhookRequest
    error: Optional[PythonException] = None

    @property
    def timestamp(self) -> datetime:
        return self.payload.event.time

    @property
    def identifier(self):
        return f"{self.payload.event.time}-{self.payload.bug.id}-{self.payload.event.action}-{"error" if self.error else "postponed"}"


@lru_cache(maxsize=1)
def get_dl_queue():
    settings = get_settings()
    return DeadLetterQueue(settings.dl_queue_dsn)


class QueueBackend(ABC):
    """An interface for dead letter queues."""

    @abstractmethod
    def ping(self) -> bool:
        """Report if the queue backend is available and ready to be written to"""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Remove all bugs and their items from the queue"""
        pass

    @abstractmethod
    async def put(self, item: QueueItem) -> None:
        """Insert item into queued items for a bug, maintaining sorted order by
        payload event time ascending
        """
        pass

    @abstractmethod
    async def remove(self, bug_id: int, identifier: str) -> None:
        """Remove an item from the target bug's queue. If the item is the last
        one for the bug, remove the bug from the queue entirely.
        """
        pass

    @abstractmethod
    def get(self, bug_id: int) -> AsyncIterator[QueueItem]:
        """Retrieve all of the queue items for a specific bug, sorted in
        ascending order by the timestamp of the payload event.
        """
        pass

    @abstractmethod
    async def list(self, bug_id: int) -> List[str]:
        """Report a summary of all of the items in the queue for a bug

        Returns:
            a dict bug id, list of item identifier
        """
        pass

    @abstractmethod
    async def list_all(self) -> dict[int, List[str]]:
        """Report a summary of all of the items in the queue

        Returns:
            a dict bug id, list of item identifiers
        """
        pass

    @abstractmethod
    async def get_all(self) -> dict[int, AsyncIterator[QueueItem]]:
        """Retrieve all items in the queue, grouped by bug

        Returns:
            dict[int, List[QueueItem]]: Returns a dict of
            {bug_id: list of events}. Each list of events sorted in ascending
            order by the timestamp of the payload event.
        """
        pass

    @abstractmethod
    async def size(self, bug_id: Optional[int] = None) -> int:
        """Report the number of items in the queue, optionally filtered by bug id"""
        pass


class FileBackend(QueueBackend):
    def __init__(self, location):
        self.location = Path(location)
        self.location.mkdir(parents=True, exist_ok=True)

    def ping(self):
        try:
            with tempfile.TemporaryDirectory(dir=self.location) as temp_dir:
                with tempfile.TemporaryFile(dir=temp_dir) as f:
                    f.write(b"")
            return True
        except Exception:
            logger.exception("Could not write to file backed queue")
            return False

    async def clear(self):
        for root, dirs, files in self.location.walk(top_down=False):
            for name in files:
                (root / name).unlink()
            for name in dirs:
                (root / name).rmdir()

    async def put(self, item: QueueItem):
        folder = self.location / f"{item.payload.bug.id}"
        folder.mkdir(exist_ok=True)
        path = folder / (item.identifier + ".json")
        path.write_text(item.model_dump_json())
        logger.debug(
            "Wrote item %s for bug %s to path %s",
            item.identifier,
            item.payload.bug.id,
            path,
        )
        logger.debug("%d items in dead letter queue", await self.size())

    async def remove(self, bug_id: int, identifier: str):
        bug_dir = self.location / f"{bug_id}"
        item_path = bug_dir / (identifier + ".json")
        item_path.unlink(missing_ok=True)
        logger.debug("Removed %s from queue for bug %s", identifier, bug_id)
        if not any(bug_dir.iterdir()):
            bug_dir.rmdir()
            logger.debug("Removed directory for bug %s", bug_id)

    async def list(self, bug_id: int) -> List[str]:
        bug_dir = self.location / str(bug_id)
        return [path.stem for path in sorted(bug_dir.glob("*.json"))]

    async def list_all(self) -> dict[int, List[str]]:
        item_data: dict[int, List[str]] = {}
        for filesystem_object in self.location.iterdir():
            if filesystem_object.is_dir():
                bug_id = int(filesystem_object.name)
                item_ids = await self.list(bug_id=bug_id)
                item_data[bug_id] = item_ids
        return item_data

    async def get(self, bug_id: int) -> AsyncIterator[QueueItem]:
        folder = self.location / str(bug_id)
        if not folder.is_dir():
            return
            yield
        for path in sorted(folder.iterdir()):
            try:
                yield QueueItem.parse_file(path)
            except (JSONDecodeError, ValidationError) as e:
                raise QueueItemRetrievalError(
                    "Unable to load item at path %s from queue", str(path)
                ) from e

    async def get_all(self) -> dict[int, AsyncIterator[QueueItem]]:
        all_items: dict[int, AsyncIterator[QueueItem]] = {}
        for filesystem_object in self.location.iterdir():
            if filesystem_object.is_dir():
                all_items[int(filesystem_object.name)] = self.get(filesystem_object)
        return all_items

    async def size(self, bug_id=None) -> int:
        location = self.location / str(bug_id) if bug_id else self.location
        return sum(1 for _ in location.rglob("*.json"))


class DeadLetterQueue:
    backend: QueueBackend

    def __init__(self, dsn: FileUrl | str | ParseResult):
        dsn = urlparse(url=dsn) if isinstance(dsn, str) else dsn

        if dsn.scheme != "file":
            raise InvalidQueueDSNError(f"{dsn.scheme} is not supported")
        self.backend = FileBackend(dsn.path)

    def ready(self) -> list[dockerflow.checks.CheckMessage]:
        """Heartbeat check to assert we can write items to queue

        TODO: Convert to an async method when Dockerflow's FastAPI integration
        can run check asynchronously
        """

        ping_result = self.backend.ping()
        if ping_result is False:
            return [
                dockerflow.checks.Error(
                    f"queue with f{str(self.backend)} backend unavailable"
                )
            ]
        return []

    async def postpone(self, payload: bugzilla.WebhookRequest) -> None:
        """
        Postpone the specified request for later.
        """
        item = QueueItem(payload=payload)
        await self.backend.put(item)

    async def track_failed(
        self, payload: bugzilla.WebhookRequest, exc: Exception
    ) -> None:
        """
        Store the specified payload and exception information into the queue.
        """
        item = QueueItem(
            payload=payload,
            error=PythonException.from_exc(exc),
        )
        await self.backend.put(item)

    async def is_blocked(self, payload: bugzilla.WebhookRequest) -> bool:
        """
        Return `True` if the specified `payload` is blocked and should be
        queued instead of being processed.
        """
        existing = await self.backend.size(payload.bug.id)
        return existing > 0

    async def retrieve(self) -> dict[int, AsyncIterator[QueueItem]]:
        """
        Returns the whole queue -- a dict of bug_id and a generator for the
        items for that bug
        """
        return await self.backend.get_all()

    async def list(self, bug_id: int) -> List[str]:
        return await self.backend.list(bug_id=bug_id)

    async def list_all(self) -> dict[int, List[str]]:
        return await self.backend.list_all()

    async def size(self, bug_id=None):
        return await self.backend.size(bug_id=bug_id)

    async def done(self, item: QueueItem) -> None:
        """
        Mark item as done, remove from queue.
        """
        return await self.backend.remove(item.payload.bug.id, item.identifier)
