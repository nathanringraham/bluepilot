from types import SimpleNamespace

from openpilot.selfdrive.ui.bp.onroad.cropped_dcam_geometry import (
  DEFAULT_WINDOW_CENTER_Y,
  Region,
  active_dcam_sides,
  adaptive_window_center_y,
  ease_visibility_alpha,
  panel_region,
  source_crop,
)


def test_active_dcam_sides_are_independent() -> None:
  state = SimpleNamespace(leftBlinker=True, rightBlinker=False, leftBlindspot=False, rightBlindspot=True)
  assert active_dcam_sides(state) == (True, True)

  state = SimpleNamespace(leftBlinker=False, rightBlinker=False, leftBlindspot=True, rightBlindspot=False)
  assert active_dcam_sides(state) == (True, False)


def test_panels_leave_center_model_corridor_clear() -> None:
  content = Region(30, 30, 2100, 1020)
  left = panel_region(content, "left", left_inset=175)
  right = panel_region(content, "right", right_inset=230)

  assert left.x + left.width < content.x + content.width * 0.42
  assert right.x > content.x + content.width * 0.58
  assert left.y > right.y  # left clears the speed-limit stack; right clears the top-right icon
  assert left.height == right.height
  assert left.width <= 420
  assert left.height <= 280


def test_comma_four_panels_scale_to_the_compact_layout() -> None:
  content = Region(0, 0, 536, 240)
  left = panel_region(content, "left")
  right = panel_region(content, "right")

  assert 110 <= left.width <= 125
  assert 74 <= left.height <= 84
  assert left.y == right.y
  assert left.x >= content.x
  assert right.x + right.width <= content.x + content.width


def test_visibility_easing_is_clamped_and_smooth() -> None:
  assert ease_visibility_alpha(-1.0) == 0.0
  assert ease_visibility_alpha(0.0) == 0.0
  assert ease_visibility_alpha(0.5) == 0.5
  assert ease_visibility_alpha(1.0) == 1.0
  assert ease_visibility_alpha(2.0) == 1.0


def test_physical_sides_select_opposite_raw_edges_for_mirroring() -> None:
  left = source_crop(1928, 1208, 540, 390, "left", (0.0, 0.0, 0.0))
  right = source_crop(1928, 1208, 540, 390, "right", (0.0, 0.0, 0.0))

  assert left.x > 1928 / 2
  assert right.x < 1928 / 2
  assert left.width == right.width
  assert left.height == right.height


def test_calibration_compensates_mount_pitch_yaw_and_roll() -> None:
  base_left = source_crop(1928, 1208, 540, 390, "left", (0.0, 0.0, 0.0))
  adjusted_left = source_crop(1928, 1208, 540, 390, "left", (0.05, 0.05, -0.05))
  base_right = source_crop(1928, 1208, 540, 390, "right", (0.0, 0.0, 0.0))
  rolled_right = source_crop(1928, 1208, 540, 390, "right", (0.05, 0.0, 0.0))

  assert adjusted_left.x < base_left.x  # negative yaw moves the vehicle-relative raw crop left
  assert adjusted_left.y != base_left.y
  assert rolled_right.y < base_right.y  # roll moves opposite sides in opposite directions


def test_face_landmark_adapts_and_clamps_window_height() -> None:
  assert adaptive_window_center_y(None, 0.0) == DEFAULT_WINDOW_CENTER_Y
  assert adaptive_window_center_y((0.0, -10.0), 1.0) == 0.48
  assert adaptive_window_center_y((0.0, 10.0), 1.0) == 0.64
