"""Pure geometry helpers for BluePilot's cropped driver-camera view."""

from dataclasses import dataclass
from math import isfinite, tan
from typing import Literal


Side = Literal["left", "right"]
DcamTrigger = Literal["blind_spot", "turn_signal"]

PANEL_WIDTH_RATIO = 0.23
PANEL_ASPECT_RATIO = 1.5
PANEL_MIN_WIDTH = 110.0
PANEL_MAX_WIDTH = 420.0
PANEL_MIN_HEIGHT = 74.0
PANEL_MAX_HEIGHT = 280.0
COMPACT_CONTENT_MAX_WIDTH = 1000.0
COMPACT_PANEL_TOP_RATIO = 0.27
PANEL_TOP_RATIO = 0.27
LEFT_PANEL_SCC_TOP_RATIO = 0.43

# Zoom into the outer third of the mirrored dcam image. Keeping the destination
# card unchanged while narrowing this source region prioritizes the side window
# over the occupants and seats on the supported fisheye cameras.
CROP_WIDTH_RATIO = 0.34
LEFT_RAW_CENTER_X = 0.83
RIGHT_RAW_CENTER_X = 0.17
DEFAULT_WINDOW_CENTER_Y = 0.46
WINDOW_CENTER_FACE_OFFSET_Y = 0.08
WINDOW_CENTER_MIN_Y = 0.40
WINDOW_CENTER_MAX_Y = 0.56

# ui_state.light_sensor is 100 in bright light and approaches zero in darkness.
LOW_LIGHT_ENHANCEMENT_START = 70.0
LOW_LIGHT_ENHANCEMENT_FULL = 20.0

TRIGGER_BADGE_DIAMETER_RATIO = 0.25
TRIGGER_BADGE_MIN_DIAMETER = 24.0
TRIGGER_BADGE_MAX_DIAMETER = 68.0
TRIGGER_BADGE_MARGIN_RATIO = 0.035


@dataclass(frozen=True)
class Region:
  x: float
  y: float
  width: float
  height: float


def active_dcam_sides(car_state) -> tuple[bool, bool]:
  """Return left/right activation without coupling to either warning toggle."""
  left_trigger, right_trigger = active_dcam_triggers(car_state)
  return left_trigger is not None, right_trigger is not None


def active_dcam_triggers(car_state) -> tuple[DcamTrigger | None, DcamTrigger | None]:
  """Resolve the reason for each crop, prioritizing the safety-critical BLIS alert."""
  left_trigger: DcamTrigger | None = (
    "blind_spot" if car_state.leftBlindspot else "turn_signal" if car_state.leftBlinker else None
  )
  right_trigger: DcamTrigger | None = (
    "blind_spot" if car_state.rightBlindspot else "turn_signal" if car_state.rightBlinker else None
  )
  return left_trigger, right_trigger


def ease_visibility_alpha(alpha: float) -> float:
  """Clamp and smooth visibility for an OEM-like camera-card transition."""
  alpha = min(1.0, max(0.0, alpha))
  return alpha * alpha * (3.0 - 2.0 * alpha)


def panel_region(content: Region, side: Side, left_inset: float = 0.0,
                 right_inset: float = 0.0, left_scc_stack: bool = False) -> Region:
  """Place a crop at the edge while reserving the center for model overlays."""
  width = min(PANEL_MAX_WIDTH, max(PANEL_MIN_WIDTH, content.width * PANEL_WIDTH_RATIO))
  height = min(PANEL_MAX_HEIGHT, max(PANEL_MIN_HEIGHT, width / PANEL_ASPECT_RATIO))
  if content.width <= COMPACT_CONTENT_MAX_WIDTH:
    top_ratio = COMPACT_PANEL_TOP_RATIO
  else:
    top_ratio = LEFT_PANEL_SCC_TOP_RATIO if side == "left" and left_scc_stack else PANEL_TOP_RATIO
  top = content.y + content.height * top_ratio
  margin = max(18.0, content.width * 0.012)

  if side == "left":
    x = content.x + margin + left_inset
  else:
    x = content.x + content.width - margin - right_inset - width

  return Region(x, top, width, height)


def trigger_badge_region(panel: Region) -> Region:
  """Scale and place a compact trigger badge inside a popup's lower-right corner."""
  diameter = min(
    TRIGGER_BADGE_MAX_DIAMETER,
    max(TRIGGER_BADGE_MIN_DIAMETER, panel.height * TRIGGER_BADGE_DIAMETER_RATIO),
  )
  margin = max(5.0, panel.height * TRIGGER_BADGE_MARGIN_RATIO)
  return Region(
    panel.x + panel.width - margin - diameter,
    panel.y + panel.height - margin - diameter,
    diameter,
    diameter,
  )


def source_crop(frame_width: float, frame_height: float, destination_width: float,
                destination_height: float, side: Side,
                calibration_rpy: tuple[float, float, float],
                window_center_y: float = DEFAULT_WINDOW_CENTER_Y,
                focal_length: float = 567.0) -> Region:
  """Calculate a calibration-aware raw dcam crop.

  The driver stream is mirrored when drawn. The physical left side is therefore
  sourced from the raw image's right edge and vice versa. liveCalibration keeps
  the crop vehicle-relative across device roll/pitch/yaw, while the driver-model
  window anchor supplied by the caller adapts vertically to the installation.
  """
  if frame_width <= 0 or frame_height <= 0 or destination_width <= 0 or destination_height <= 0:
    return Region(0.0, 0.0, max(frame_width, 0.0), max(frame_height, 0.0))

  roll, pitch, yaw = calibration_rpy
  roll = roll if isfinite(roll) else 0.0
  pitch = pitch if isfinite(pitch) else 0.0
  yaw = yaw if isfinite(yaw) else 0.0

  crop_width = frame_width * CROP_WIDTH_RATIO
  crop_height = crop_width / (destination_width / destination_height)
  crop_height = min(frame_height * 0.70, crop_height)

  raw_center_x = LEFT_RAW_CENTER_X if side == "left" else RIGHT_RAW_CENTER_X
  raw_center_x += focal_length * tan(yaw) / frame_width

  # Project mount pitch and roll into the raw dcam image. Roll moves the two
  # sides in opposite vertical directions around the optical center.
  center_y = window_center_y - focal_length * tan(pitch) / frame_height
  center_y += (raw_center_x - 0.5) * (frame_width / frame_height) * tan(roll)

  center_x_px = raw_center_x * frame_width
  center_y_px = center_y * frame_height
  x = min(max(center_x_px - crop_width / 2, 0.0), frame_width - crop_width)
  y = min(max(center_y_px - crop_height / 2, 0.0), frame_height - crop_height)
  return Region(x, y, crop_width, crop_height)


def adaptive_window_center_y(face_position: tuple[float, float] | list[float] | None,
                             face_prob: float) -> float:
  """Use the driver's face as a stable per-installation vertical landmark."""
  if face_prob <= 0.5 or face_position is None or len(face_position) != 2:
    return DEFAULT_WINDOW_CENTER_Y

  face_x, face_y = face_position
  # Same inexpensive raw-dcam approximation used by DriverCameraDialog.
  face_y_px = -135.0 + (504.0 + abs(face_x) * 112.0) + (1205.0 - abs(face_x) * 724.0) * face_y
  face_y_norm = face_y_px / 1208.0
  return min(WINDOW_CENTER_MAX_Y, max(WINDOW_CENTER_MIN_Y, face_y_norm + WINDOW_CENTER_FACE_OFFSET_Y))


def low_light_enhancement(light_sensor: float) -> float:
  """Return a smooth 0..1 shadow-lift strength from the existing UI light estimate."""
  if not isfinite(light_sensor) or light_sensor < 0:
    return 0.0

  span = LOW_LIGHT_ENHANCEMENT_START - LOW_LIGHT_ENHANCEMENT_FULL
  strength = (LOW_LIGHT_ENHANCEMENT_START - light_sensor) / span
  return ease_visibility_alpha(strength)
