import logging
import schedule
from os import getenv

def setup_retry_schedule():
  # note, this doesn't work if we're running more than 1 pod. we would
  # need to convert to a k8s job if we do so multiple pods aren't trying
  # to process the same data
  if getenv("FAST_RETRY", "false") == "true":
    schedule.every().seconds(5).do(retry_failed)
  else:
    schedule.every().day.at("02:00").do(retry_failed)
    schedule.every().day.at("14:00").do(retry_failed)


def retry_failed():
  # load items from DLQ
  # order items based on oldest events first, oldest events should be blocking
  # resend events to execute_action or directly to bugzilla_webhook
  print('retry failed events')
