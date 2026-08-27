"""BluePilot additions to comma 4's stock onroad alert renderer."""

import time

import pyray as rl

from openpilot.selfdrive.ui.bp.onroad.tesla_turn_signal import draw_tesla_turn_signal_texture
from openpilot.selfdrive.ui.mici.onroad.alert_renderer import (
  ALERT_MARGIN,
  TURN_SIGNAL_BLINK_PERIOD,
  Alert,
  AlertLayout,
  AlertRenderer,
  IconSide,
)
from openpilot.selfdrive.ui.ui_state import ui_state


class AlertRendererBP(AlertRenderer):
  """Keep MICI's built-in indicators, recoloring only Tesla-theme blinkers."""

  def __init__(self):
    super().__init__()
    self._tesla_style = False
    self._tesla_dark_fraction = 0.0

  def set_tesla_style(self, enabled: bool, dark_fraction: float) -> None:
    self._tesla_style = bool(enabled)
    self._tesla_dark_fraction = float(dark_fraction)

  def _is_turn_signal(self, texture) -> bool:
    return texture in (self._txt_turn_signal_left, self._txt_turn_signal_right)

  def _icon_helper(self, alert: Alert) -> AlertLayout:
    layout = super()._icon_helper(alert)
    if not (self._tesla_style and layout.icon is not None and
            self._is_turn_signal(layout.icon.texture) and not ui_state.turn_signals):
      return layout

    # The user-facing Show Turn Signals toggle continues to own whether Tesla's
    # recolored built-in blinker is shown. Restore the full text width when off.
    text_rect = rl.Rectangle(
      self._rect.x + ALERT_MARGIN,
      self._alert_y_filter.x,
      self._rect.width - ALERT_MARGIN,
      self._rect.height,
    )
    return AlertLayout(text_rect, None)

  def _draw_icons(self, alert_layout: AlertLayout) -> None:
    if not self._tesla_style:
      super()._draw_icons(alert_layout)
      return
    if alert_layout.icon is None:
      return

    if time.monotonic() - self._turn_signal_timer > TURN_SIGNAL_BLINK_PERIOD:
      self._turn_signal_timer = time.monotonic()
      self._turn_signal_alpha_filter.x = 255 * 2
    else:
      self._turn_signal_alpha_filter.update(255 * 0.2)

    if alert_layout.icon.side == IconSide.left:
      pos_x = int(self._rect.x + alert_layout.icon.margin_x)
    else:
      pos_x = int(self._rect.x + self._rect.width - alert_layout.icon.margin_x - alert_layout.icon.texture.width)
    position = rl.Vector2(pos_x, self._rect.y + alert_layout.icon.margin_y)

    if self._is_turn_signal(alert_layout.icon.texture):
      icon_alpha = int(min(self._turn_signal_alpha_filter.x, 255) * self._alpha_filter.x)
      draw_tesla_turn_signal_texture(
        alert_layout.icon.texture, position, self._tesla_dark_fraction, icon_alpha,
      )
    else:
      icon_alpha = int(alert_layout.icon.alpha * self._alpha_filter.x)
      rl.draw_texture_ex(alert_layout.icon.texture, position, 0.0, 1.0,
                         rl.Color(255, 255, 255, icon_alpha))
