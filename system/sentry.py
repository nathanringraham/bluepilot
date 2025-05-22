"""Install exception handler for process crash."""

import os
import traceback
from datetime import datetime
import sentry_sdk
from enum import Enum
from sentry_sdk.integrations.threading import ThreadingIntegration

from openpilot.common.params import Params
from openpilot.system.athena.registration import UNREGISTERED_DONGLE_ID
from openpilot.system.hardware import HARDWARE
from openpilot.system.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog
from openpilot.system.version import get_build_metadata, get_version

from openpilot.sunnypilot.sunnylink.api import UNREGISTERED_SUNNYLINK_DONGLE_ID

CRASHES_DIR = Paths.crash_log_root()

# Check if profiling is available in this version of sentry-sdk
SENTRY_PROFILING_AVAILABLE = False
try:
  # Try to access the profiler module - will raise attribute error if not available
  if hasattr(sentry_sdk, 'profiler'):
    SENTRY_PROFILING_AVAILABLE = True
    cloudlog.info("Sentry profiling is available")
except Exception:
  cloudlog.info("Sentry profiling is not available")


class SentryProject(Enum):
  # python project
  # SELFDRIVE = "https://186a6736b7927e5ae9b92c869ba81b6b@o1138119.ingest.us.sentry.io/4508660076052480" # Native
  SELFDRIVE = "https://e4e23da828758b43877bff008866545f@o4509128983117824.ingest.us.sentry.io/4509129015885824"  # BP
  # native project
  SELFDRIVE_NATIVE = SELFDRIVE


def report_tombstone(fn: str, message: str, contents: str) -> None:
  cloudlog.error({'tombstone': message})

  with sentry_sdk.configure_scope() as scope:
    set_user()
    scope.set_extra("tombstone_fn", fn)
    scope.set_extra("tombstone", contents)
    sentry_sdk.capture_message(message=message)
    sentry_sdk.flush()


def capture_exception(*args, **kwargs) -> None:
  cloudlog.error("crash", exc_info=kwargs.get('exc_info', 1))

  try:
    save_exception(traceback.format_exc())

    set_user()
    sentry_sdk.capture_exception(*args, **kwargs)
    sentry_sdk.flush()  # https://github.com/getsentry/sentry-python/issues/291
  except Exception:
    cloudlog.exception("sentry exception")


def save_exception(content: str) -> None:
  try:
    if not os.path.exists(CRASHES_DIR):
      os.makedirs(CRASHES_DIR)

    files = [os.path.join(CRASHES_DIR, datetime.now().strftime("%Y-%m-%d--%H-%M-%S.log")), os.path.join(CRASHES_DIR, "error.log")]

    for fn in files:
      with open(fn, 'w') as f:
        if fn == "error.log":
          lines = content.splitlines()[-3:]
          f.write("\n".join(lines))
        else:
          f.write(content)

    cloudlog.error(f"logged crash to {files}")
  except Exception:
    cloudlog.exception("error when attempting to save exception")


def capture_fingerprint_mock() -> None:
  try:
    set_user()
    message = "car doesn't match any fingerprints"
    sentry_sdk.capture_message(message=message, level="error")
    sentry_sdk.flush()
  except Exception as e:
    cloudlog.exception(f"sentry fingerprint MOCK exception: {e}")


def capture_fingerprint(candidate: str, car_name: str) -> None:
  try:
    set_user()
    sentry_sdk.set_tag("carFingerprint", candidate)
    sentry_sdk.set_tag("carName", car_name)

    message = f"Fingerprinted {candidate}"
    sentry_sdk.capture_message(message=message, level="info")
    sentry_sdk.flush()
  except Exception as e:
    cloudlog.exception(f"sentry fingerprint exception: {e}")


def set_tag(key: str, value: str) -> None:
  sentry_sdk.set_tag(key, value)


def set_user() -> None:
  dongle_id, git_username, _ = get_properties()
  sentry_sdk.set_user({"id": dongle_id, "name": git_username})


def get_properties() -> tuple[str, str, str]:
  params = Params()
  hardware_serial: str = params.get("HardwareSerial", encoding='utf-8') or ""
  git_username: str = params.get("GithubUsername", encoding='utf-8') or ""
  dongle_id: str = params.get("DongleId", encoding='utf-8') or f"{UNREGISTERED_DONGLE_ID}-{hardware_serial}"
  sunnylink_dongle_id: str = params.get("SunnylinkDongleId", encoding='utf-8') or UNREGISTERED_SUNNYLINK_DONGLE_ID

  return dongle_id, git_username, sunnylink_dongle_id


def start_ui_monitoring(pid: int = None) -> None:
  """Start specific monitoring for the UI process to detect hangs."""
  if pid is None:
    return

  try:
    set_tag("ui_process_id", str(pid))

    # Start a transaction for monitoring the UI process
    with sentry_sdk.start_transaction(op="ui_monitoring", name="UI Process Monitoring") as transaction:
      transaction.set_tag("ui_pid", str(pid))

      # Explicitly start profiling for the UI process if available
      # Following the official recommendation for manual profiling
      if SENTRY_PROFILING_AVAILABLE:
        try:
          # Directly call the start_profiler as recommended by Sentry
          sentry_sdk.profiler.start_profiler()
          cloudlog.info(f"Started profiling UI process {pid} using recommended approach")
        except Exception as e:
          cloudlog.exception(f"Failed to start profiler: {e}")
  except Exception as e:
    cloudlog.exception(f"Failed to start UI monitoring: {e}")


def profile_ui_process(pid: int, duration: int = 60) -> None:
  """Explicitly profile the UI process for a specific duration following Sentry's recommended approach.

  Args:
      pid: Process ID of the UI process
      duration: Duration in seconds to collect profile data (default: 60s)
  """
  if not SENTRY_PROFILING_AVAILABLE:
    cloudlog.warning("Cannot profile UI process: profiling not available in this version of sentry-sdk")
    return

  try:
    set_tag("profiled_ui_pid", str(pid))
    set_tag("profiled_duration", str(duration))

    # Start a transaction for the profiling session
    with sentry_sdk.start_transaction(op="ui_profile", name="UI Process Profiling") as transaction:
      # Use the recommended approach to start profiling
      sentry_sdk.profiler.start_profiler()

      # Log the start of profiling
      cloudlog.info(f"Started profiling UI process {pid} for {duration} seconds using recommended approach")

      # Sleep for the specified duration
      import time

      time.sleep(duration)

      # Stop the profiler as recommended by Sentry
      sentry_sdk.profiler.stop_profiler()

      # Log the completion of profiling
      cloudlog.info(f"Completed profiling UI process {pid}")

      # Ensure the transaction gets the proper tags
      transaction.set_tag("profiling_completed", "true")
      transaction.set_data("profile_duration", duration)
  except Exception as e:
    cloudlog.exception(f"Failed to profile UI process: {e}")
    # Make sure to stop the profiler even if there was an error
    if SENTRY_PROFILING_AVAILABLE:
      try:
        sentry_sdk.profiler.stop_profiler()
      except:
        pass


def ui_watchdog_breadcrumb(last_watchdog_time: float, current_time: float, max_dt: float) -> None:
  """Add a breadcrumb for UI watchdog activity to help diagnose hangs."""
  try:
    dt = current_time - last_watchdog_time
    sentry_sdk.add_breadcrumb(
      category="ui_watchdog",
      message=f"UI watchdog check",
      data={"last_watchdog_time": last_watchdog_time, "current_time": current_time, "dt": dt, "max_dt": max_dt, "is_hanging": dt > max_dt},
      level="info" if dt <= max_dt else "warning",
    )
  except Exception as e:
    cloudlog.exception(f"Failed to add UI watchdog breadcrumb: {e}")


def init(project: SentryProject) -> bool:
  build_metadata = get_build_metadata()

  env = build_metadata.channel_type
  dongle_id, git_username, sunnylink_dongle_id = get_properties()

  integrations = []
  if project == SentryProject.SELFDRIVE:
    # Add threading integration to monitor threads
    integrations.append(ThreadingIntegration(propagate_hub=True))

  # Initialize Sentry with enhanced features
  init_options = {
    "default_integrations": False,
    "release": get_version(),
    "integrations": integrations,
    "traces_sample_rate": 1.0,  # Enable tracing for all transactions
    "max_value_length": 8192,
    "environment": env,
  }

  # Add profiling options if available - using the official recommended approach
  if SENTRY_PROFILING_AVAILABLE:
    # Set profile_session_sample_rate to 1.0 to profile 100% of sessions
    init_options["profile_session_sample_rate"] = 1.0

  sentry_sdk.init(project.value, **init_options)

  # Set important tags
  sentry_sdk.set_user({"id": dongle_id, "name": git_username})
  sentry_sdk.set_tag("dirty", build_metadata.openpilot.is_dirty)
  sentry_sdk.set_tag("origin", build_metadata.openpilot.git_origin)
  sentry_sdk.set_tag("branch", build_metadata.channel)
  sentry_sdk.set_tag("commit", build_metadata.openpilot.git_commit)
  sentry_sdk.set_tag("device", HARDWARE.get_device_type())
  sentry_sdk.set_tag("sunnylink_dongle_id", sunnylink_dongle_id)

  # Add a tag to indicate if profiling is available
  sentry_sdk.set_tag("profiling_available", str(SENTRY_PROFILING_AVAILABLE))

  return True


def capture_process_lifecycle(process_name: str, event_type: str, details: dict = None) -> None:
  """Capture process lifecycle events like start/stop/restart."""
  try:
    if details is None:
      details = {}

    sentry_sdk.add_breadcrumb(category="process_lifecycle", message=f"Process {process_name} {event_type}", data=details, level="info")

    # For critical events, capture as transactions
    if event_type in ("crash", "restart", "watchdog_timeout"):
      with sentry_sdk.start_transaction(op="process_event", name=f"{process_name} {event_type}") as transaction:
        for key, value in details.items():
          transaction.set_tag(key, str(value))
  except Exception as e:
    cloudlog.exception(f"Failed to capture process lifecycle event: {e}")
