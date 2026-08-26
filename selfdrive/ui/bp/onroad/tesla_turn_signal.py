"""Tesla-inspired turn-signal indicators shared by comma 3X and comma 4."""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets import Widget


# Sampled from Tesla's official owner-manual turn-signal icon. Tesla does not
# publish this as a design token, so keep the measured source color documented.
TESLA_TURN_SIGNAL_GREEN = rl.Color(15, 102, 54, 255)  # #0F6636
TESLA_TURN_SIGNAL_GLOW = rl.Color(46, 211, 111, 255)
TESLA_TURN_SIGNAL_PERIOD_S = 0.75


@dataclass(frozen=True)
class TeslaTurnSignalLayout:
  left_x: float
  right_x: float
  center_y: float
  size: float


def tesla_turn_signal_state(sm, show_turn_signals: bool) -> tuple[bool, bool]:
  """Return the active vehicle blinkers while honoring SunnyPilot's UI toggle."""
  if not show_turn_signals:
    return False, False
  if not (sm.alive.get("carState", False) and sm.valid.get("carState", False)):
    return False, False

  car_state = sm["carState"]
  return bool(car_state.leftBlinker), bool(car_state.rightBlinker)


def tesla_turn_signal_alpha(elapsed: float) -> int:
  """Tesla-like soft pulse using BluePilot's established 80-BPM period."""
  phase = max(0.0, float(elapsed)) % TESLA_TURN_SIGNAL_PERIOD_S
  wave = 0.5 + 0.5 * math.cos(2.0 * math.pi * phase / TESLA_TURN_SIGNAL_PERIOD_S)
  return round(255.0 * (0.12 + 0.88 * wave * wave))


def tesla_turn_signal_layout(rect: rl.Rectangle, compact: bool) -> TeslaTurnSignalLayout:
  """Place arrows just outside the central speed readout on either display."""
  display_scale = max(0.45, float(rect.height) / 1080.0)
  size = 100.0 * display_scale
  center_x = rect.x + rect.width / 2.0
  gap_fraction = 0.10 if compact else 0.075
  center_gap = max(size * 1.35, rect.width * gap_fraction)
  center_y = rect.y + rect.height * (0.21 if compact else 0.22)
  return TeslaTurnSignalLayout(center_x - center_gap, center_x + center_gap, center_y, size)


class TeslaTurnSignalRenderer(Widget):
  def __init__(self, compact: bool = False):
    super().__init__()
    self._compact = compact
    self._enabled = False
    self._active = [False, False]
    self._active_since = [0.0, 0.0]

  def set_enabled(self, enabled: bool) -> None:
    self._enabled = bool(enabled)
    if not self._enabled:
      self._active = [False, False]

  @staticmethod
  def _draw_arrow(center_x: float, center_y: float, size: float,
                  points_left: bool, color: rl.Color) -> None:
    direction = -1.0 if points_left else 1.0
    tip_x = center_x + direction * size * 0.50
    base_x = center_x - direction * size * 0.02
    shaft_far_x = center_x - direction * size * 0.50
    half_head = size * 0.40
    half_shaft = size * 0.17

    triangle = [rl.Vector2(tip_x, center_y)]
    if points_left:
      triangle.extend((
        rl.Vector2(base_x, center_y - half_head),
        rl.Vector2(base_x, center_y + half_head),
      ))
    else:
      # raylib expects counter-clockwise triangle winding.
      triangle.extend((
        rl.Vector2(base_x, center_y + half_head),
        rl.Vector2(base_x, center_y - half_head),
      ))
    rl.draw_triangle(triangle[0], triangle[1], triangle[2], color)

    shaft_x = min(base_x - direction * size * 0.02, shaft_far_x)
    shaft_width = abs(shaft_far_x - (base_x - direction * size * 0.02))
    shaft_rect = rl.Rectangle(shaft_x, center_y - half_shaft, shaft_width, half_shaft * 2.0)
    rl.draw_rectangle_rounded(shaft_rect, 0.28, 6, color)

  def _draw_signal(self, center_x: float, center_y: float, size: float,
                   points_left: bool, alpha: int) -> None:
    glow_alpha = round(alpha * 0.28)
    self._draw_arrow(
      center_x, center_y, size * 1.16, points_left,
      rl.Color(TESLA_TURN_SIGNAL_GLOW.r, TESLA_TURN_SIGNAL_GLOW.g,
               TESLA_TURN_SIGNAL_GLOW.b, glow_alpha),
    )
    self._draw_arrow(
      center_x, center_y, size, points_left,
      rl.Color(TESLA_TURN_SIGNAL_GREEN.r, TESLA_TURN_SIGNAL_GREEN.g,
               TESLA_TURN_SIGNAL_GREEN.b, alpha),
    )

  def _render(self, rect: rl.Rectangle) -> None:
    active = tesla_turn_signal_state(
      ui_state.sm,
      self._enabled and ui_state.turn_signals,
    )
    now = time.monotonic()
    layout = tesla_turn_signal_layout(rect, self._compact)

    for index, is_active in enumerate(active):
      if is_active and not self._active[index]:
        self._active_since[index] = now
      self._active[index] = is_active
      if not is_active:
        continue

      center_x = layout.left_x if index == 0 else layout.right_x
      alpha = tesla_turn_signal_alpha(now - self._active_since[index])
      self._draw_signal(center_x, layout.center_y, layout.size, index == 0, alpha)
