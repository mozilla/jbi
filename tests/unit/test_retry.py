
from jbi.retry import retry_failed

# def test_setup_retry_schedule():
#   schedule.every = MagicMock()
  
#   # Verifying that if FAST_RETRY is set to "true", it's scheduled every 5 seconds
#   os.environ['FAST_RETRY'] = 'true'
#   setup_retry_schedule()
#   schedule.every().seconds.assert_called_once_with(5)
  
#   # Verifying if FAST_RETRY is not set to "true", it runs at 2:00 and 14:00
#   os.environ['FAST_RETRY'] = 'false'
#   setup_retry_schedule()
#   schedule.every().day.at.assert_has_calls([call("02:00"), call("14:00")], True)

def test_retry_failed():
  retry_failed()

