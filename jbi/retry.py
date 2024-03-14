import asyncio
from datetime import datetime, timedelta
from os import getenv
from time import sleep

from jbi.configuration import ACTIONS
from jbi.queue import QueueItem, get_dl_queue
from jbi.runner import execute_action

CONSTANT_RETRY = getenv("CONSTANT_RETRY", "false") == "true"
RETRY_TIMEOUT_DAYS = getenv("RETRY_TIMEOUT_DAYS", 7)
QUEUE = get_dl_queue()

async def retry_failed():
  min_event_timestamp = datetime.now() - timedelta(days=int(RETRY_TIMEOUT_DAYS))

  print('retry_failed 1')

  # load items from DLQ
  items:list[QueueItem] = await QUEUE.retrieve()
  
  # track bugs that have failed
  failed_bugs = {}

  for item in items: 
    bug = item.payload.bug

    # skip and delete if we have exceeded max_timeout
    if item.timestamp < min_event_timestamp:
      print("removing expired event for bug " + bug.id)
      QUEUE.done(item)
      continue
    
    # skip if any previous retries for this bug have already failed
    if failed_bugs[bug.id]:
      print("skipping bug " + bug.id)
      continue

    try:
      execute_action(item.payload, ACTIONS)
      QUEUE.done(item)
    except ex:
      # write well formed log that could be alerted on
      failed_bugs[bug.id] = True
      print('bar')

async def main():
  while True:
    await retry_failed()

    if not CONSTANT_RETRY:
      return
    
    sleep(5)

asyncio.run(main())
