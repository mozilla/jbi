import logging
from time import sleep
from os import getenv


def retry_failed():
  
  # load items from DLQ
  # order items based on oldest events first, oldest events should be blocking
  # resend events to execute_action or directly to bugzilla_webhook
  print('retry failed events')

retry_failed()

if getenv("CONSTANT_RETRY", "false") == "true":
  while True:
    sleep(5)
    retry_failed()
