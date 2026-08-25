"""Pure geometry helpers for BluePilot's cropped driver-camera view."""

from dataclasses import dataclass
from math import isfinite, tan
from typing import Literal


Side = Literal["left", "right"]

PANEL_WIDTH_RATIO = 0.23
PANEL_ASPECT_RATIO = 1.5
PANEL_MIN_WIDTH = 110.0
PANEL_MAX_WIDTH = 420.0
PANEL_MIN_HEIGHT = 74.0
PANEL_MAX_HEIGHT = 280.0
COMPACT_CONTENT_MAX_WIDTH = 1000.0
COMPACT_PANEL_TOP_RATIO = 0.27
LEFT_PANEL_TOP_RATIO = 0.43
RIGHT_PANEL_TOP_RATIO = 0.27

# The outer 42% of the mirrored dcam image contains the applicable side
# window and mirror on the supported fisheye cameras. A broad crop is
# intentional: it tolerates different cabin widths and camera placements.
CROP_WIDTH_RATIO = 0.42
LEFT_RAW_CENTER_X = 0.79
RIGHT_RAW_CENTER_X = 0.21
DEFAULT_WINDOW_CENTER_Y = 0.55


@dataclass(frozen=True)
class Region:
  x: float
  y: float
  width: float
  height: float


def active_dcam_sides(car_state) -> tuple[bool, bool]:
  """Return left/right activation without coupling to either warning toggle."""
  left_active = bool(car_state.leftBlinker or car_state.leftBlindspot)
  right_active = bool(car_state.rightBlinker or car_state.rightBlindspot)
  return left_active, right_active


def ease_visibility_alpha(alpha: float) -> float:
  """Clamp and smooth visibility for an OEM-like camera-card transition."""
  alpha = min(1.0, max(0.0, alpha))
  return alpha * alpha * (3.0 - 2.0 * alpha)


def panel_region(content: Region, side: Side, left_inset: float = 0.0,
                 right_inset: float = 0.0) -> Region:
  """Place a crop at the edge while reserving the center for model overlays."""
  width = min(PANEL_MAX_WIDTH, max(PANEL_MIN_WIDTH, content.width * PANEL_WIDTH_RATIO))
  height = min(PANEL_MAX_HEIGHT, max(PANEL_MIN_HEIGHT, width / PANEL_ASPECT_RATIO))
  if content.width <= COMPACT_CONTENT_MAX_WIDTH:
    top_ratio = COMPACT_PANEL_TOP_RATIO
  else:
    top_ratio = LEFT_PANEL_TOP_RATIO if side == "left" else RIGHT_PANEL_TOP_RATIO
  top = content.y + content.height * top_ratio
  margin = max(18.0, content.width * 0.012)

  if side == "left":
    x = content.x + margin + left_inset
  else:
    x = content.x + content.width - margin - right_inset - width

  return Region(x, top, width, height)


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
  return min(0.64, max(0.48, DEFAULT_WINDOW_CENTER_Y + 0.35 * (face_y_norm - 0.42)))
