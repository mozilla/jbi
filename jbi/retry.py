import asyncio
from datetime import datetime, timedelta
from os import getenv
from time import sleep
import logging
import jbi.runner as runner
from jbi.configuration import ACTIONS
from jbi.queue import QueueItem, get_dl_queue

CONSTANT_RETRY = getenv("CONSTANT_RETRY", "false") == "true"
RETRY_TIMEOUT_DAYS = getenv("RETRY_TIMEOUT_DAYS", 7)

queue = get_dl_queue()
logger = logging.getLogger(__name__)

async def retry_failed():
  min_event_timestamp = datetime.now() - timedelta(days=int(RETRY_TIMEOUT_DAYS))

  # load items from DLQ
  items:list[QueueItem] = await queue.retrieve()

  # track bugs that have failed
  failed_bugs = dict()

  for item in items:
    bug = item.payload.bug

    # skip and delete if we have exceeded max_timeout
    if item.timestamp < min_event_timestamp:
      logger.warn("removing expired event %d", item.identifier)
      await queue.done(item)
      continue

    # skip if any previous retries for this bug have already failed
    if bug.id in failed_bugs:
      logger.info("skipping event %d - previous items have failed", item.identifier)
      continue

    try:
      runner.execute_action(item.payload, ACTIONS)
      await queue.done(item)
    except Exception as ex:
      # write well formed log that could be alerted on
      failed_bugs[bug.id] = True
      logger.error("failed to reprocess event %d. error: %d", item.identifier, ex)


async def main():
  while True:
    await retry_failed()

    if not CONSTANT_RETRY:
      return

    sleep(5)

if __name__ == '__main__':
  asyncio.run(main())
