import math

import pyray as rl
from openpilot.common.params import Params
from opendbc.car.structs import ControllerStateBP
from openpilot.bluepilot.ui.lib.bp_shaders import draw_shader_circle_gradient
from openpilot.selfdrive.ui.onroad.hud_renderer import UI_CONFIG, FONT_SIZES, COLORS, CRUISE_DISABLED_CHAR
from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP, SLA_ACTIVE_COLOR
from openpilot.selfdrive.ui.bp.onroad.exp_button_bp import ExpButtonBP
from openpilot.selfdrive.ui.bp.lib import theme_pack
from openpilot.selfdrive.ui.bp.lib.longitudinal_visuals import longitudinal_control_active
from openpilot.selfdrive.ui.bp.lib.tesla_palette import palette_for_variant
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.bp.lib.ui_debug_logger import bp_ui_log

LateralMode = ControllerStateBP.LateralMode

# BluePilot: Y center for speed display (matching upstream hardcoded values)
SPEED_CENTER_Y = 180
SPEED_UNIT_CENTER_Y = 290

TESLA_SET_SPEED_SIZE = 100
TESLA_MAX_LABEL_SIZE = 34
TESLA_LEAD_TEXT_SIZE = 38
TESLA_TEXT_SHADOW = rl.Color(0, 0, 0, 105)
TESLA_LEAD_FASTER_COLOR = rl.Color(80, 216, 112, 255)
TESLA_LEAD_SLOW_YELLOW = rl.Color(255, 211, 30, 255)
TESLA_LEAD_SLOW_RED = rl.Color(235, 62, 52, 255)
TESLA_LEAD_FULL_RED_DELTA_MPS = 15.0 / 2.2369362920544


def tesla_lead_speed_state(sm) -> tuple[float, float] | None:
  """Return fused lead/ego speeds in m/s while a valid primary lead exists."""
  if not (sm.alive.get("radarState", False) and sm.valid.get("radarState", False) and
          sm.alive.get("carState", False) and sm.valid.get("carState", False)):
    return None

  lead = sm["radarState"].leadOne
  if lead is None or not bool(getattr(lead, "status", False)):
    return None

  d_rel = float(lead.dRel)
  # Prefer radard's Kalman-filtered absolute speed so the readout does not
  # flicker with individual radar samples. Vision-only leads publish the same
  # value through vLeadK.
  lead_speed = float(getattr(lead, "vLeadK", lead.vLead))
  ego_speed = float(sm["carState"].vEgo)
  if not all(math.isfinite(value) for value in (d_rel, lead_speed, ego_speed)) or d_rel <= 0.0:
    return None
  return max(0.0, lead_speed), max(0.0, ego_speed)


def tesla_lead_speed_color(lead_speed: float, ego_speed: float) -> rl.Color:
  """Green for non-slower leads; yellow through red as closing speed grows."""
  if lead_speed >= ego_speed:
    return TESLA_LEAD_FASTER_COLOR

  speed_delta = ego_speed - lead_speed
  if speed_delta >= TESLA_LEAD_FULL_RED_DELTA_MPS:
    return TESLA_LEAD_SLOW_RED

  severity = max(0.0, speed_delta / TESLA_LEAD_FULL_RED_DELTA_MPS)
  return rl.Color(
    round(TESLA_LEAD_SLOW_YELLOW.r + (TESLA_LEAD_SLOW_RED.r - TESLA_LEAD_SLOW_YELLOW.r) * severity),
    round(TESLA_LEAD_SLOW_YELLOW.g + (TESLA_LEAD_SLOW_RED.g - TESLA_LEAD_SLOW_YELLOW.g) * severity),
    round(TESLA_LEAD_SLOW_YELLOW.b + (TESLA_LEAD_SLOW_RED.b - TESLA_LEAD_SLOW_YELLOW.b) * severity),
    255,
  )


class HudRendererBP(HudRendererSP):
  """BluePilot HudRenderer with brake status display.

  Note: Torque bar is rendered by TorqueBarRendererBP in AugmentedRoadViewBP,
  not here. This keeps the torque bar above gauges in draw order and allows
  repositioning above the battery/power flow gauges.
  """

  def __init__(self):
    super().__init__()
    # BluePilot: Restore the animated C3X wheel without modifying the upstream ExpButton.
    self._exp_button = ExpButtonBP(UI_CONFIG.button_size, UI_CONFIG.wheel_icon_size)
    self._bp_params = Params()
    self._brakes_on = False
    self.speed_right = 0
    self._gradient_rect = None  # BluePilot: Full-width rect for header gradient

    # BluePilot: Cache params to avoid per-frame disk I/O (refresh every ~60 frames)
    self._param_counter = 0
    self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
    self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
    self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")
    self._tesla_theme_variant = theme_pack.tesla_variant(self._bp_params)
    # BluePilot: actual mode from controllerStateBP (None = not published, e.g. non-Ford)
    self._lateral_mode = None

  def set_gradient_rect(self, rect: rl.Rectangle):
    """Set full-width rect for header gradient (when HUD renders offset for confidence ball)."""
    self._gradient_rect = rect

  def get_speed_right(self) -> int:
    return self.speed_right

  def _update_state(self) -> None:
    super()._update_state()

    # BluePilot: Refresh cached params periodically (~1s at 20fps)
    self._param_counter += 1
    if self._param_counter >= 60:
      self._param_counter = 0
      self._show_brake_status = self._bp_params.get_bool("ShowBrakeStatus")
      self._hide_v_ego_ui = self._bp_params.get_bool("HideVEgoUI")
      self._show_lateral_control = self._bp_params.get_bool("BpShowLateralControl")
      self._tesla_theme_variant = theme_pack.tesla_variant(self._bp_params)

    if self._show_lateral_control:
      sm = ui_state.sm
      self._lateral_mode = sm['controllerStateBP'].activeLateralMode if sm.alive['controllerStateBP'] else None

    # Check brake status if enabled
    if self._show_brake_status:
      sm = ui_state.sm
      if sm.valid['carStateBP']:
        try:
          car_state_bp = sm['carStateBP']
          brake_light_status = car_state_bp.brakeLightStatus
          self._brakes_on = brake_light_status.dataAvailable and brake_light_status.brakeLightsOn
        except (KeyError, AttributeError):
          self._brakes_on = False
      else:
        self._brakes_on = False
    else:
      self._brakes_on = False

    bp_ui_log.state("HudRendererBP", "brakes_on", self._brakes_on)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    if self._tesla_theme_variant is None:
      super()._draw_set_speed(rect)
      return

    # Tesla presents set speed as an unboxed number over a compact MAX label.
    # Only MAX changes state color; the centered current-speed renderer is untouched.
    self._get_icbm_status()
    palette = palette_for_variant(self._tesla_theme_variant)

    set_speed_width = UI_CONFIG.set_speed_width_metric if ui_state.is_metric else UI_CONFIG.set_speed_width_imperial
    x = rect.x + 60 + (UI_CONFIG.set_speed_width_imperial - set_speed_width) // 2
    y = rect.y + 45
    longitudinal_active = self.is_cruise_set and longitudinal_control_active(ui_state.sm, ui_state.status)
    value_color = palette.set_speed
    max_color = palette.max_active if longitudinal_active else palette.max_inactive

    # Preserve SunnyPilot's speed-limit-assist feedback without using engagement
    # color on the commanded value itself. Tesla's blue/gray state belongs to MAX.
    long_plan_sp = ui_state.sm["longitudinalPlanSP"]
    long_override = ui_state.sm["carControl"].cruiseControl.override
    if self.is_cruise_set and long_plan_sp.speedLimit.assist.active:
      value_color = SLA_ACTIVE_COLOR if long_override else rl.Color(0, 255, 0, 255)

    if not self.is_cruise_set:
      set_speed_text = CRUISE_DISABLED_CHAR
    else:
      set_speed_text = str(round(self.set_speed))

    speed_text_width = measure_text_cached(self._font_medium, set_speed_text, TESLA_SET_SPEED_SIZE).x
    speed_pos = rl.Vector2(x + (set_speed_width - speed_text_width) / 2, y - 4)
    rl.draw_text_ex(
      self._font_medium, set_speed_text,
      rl.Vector2(speed_pos.x + 2, speed_pos.y + 2),
      TESLA_SET_SPEED_SIZE,
      0,
      TESLA_TEXT_SHADOW,
    )
    rl.draw_text_ex(
      self._font_medium, set_speed_text, speed_pos, TESLA_SET_SPEED_SIZE, 0, value_color,
    )

    # ICBM replaces the label with cluster speed, like the stock SunnyPilot HUD,
    # while retaining the commanded set speed as the main Tesla-style value.
    max_text = str(round(self.speed_cluster)) if self.show_icbm_status else tr("MAX")
    max_size = 34 if self.show_icbm_status else TESLA_MAX_LABEL_SIZE
    max_spacing = 2.0
    max_text_width = measure_text_cached(self._font_semi_bold, max_text, max_size, max_spacing).x
    max_pos = rl.Vector2(x + (set_speed_width - max_text_width) / 2, y + 112)
    rl.draw_text_ex(
      self._font_semi_bold, max_text,
      rl.Vector2(max_pos.x + 1, max_pos.y + 1),
      max_size, max_spacing, TESLA_TEXT_SHADOW,
    )
    rl.draw_text_ex(
      self._font_semi_bold, max_text, max_pos,
      max_size, max_spacing, max_color,
    )

    lead_state = tesla_lead_speed_state(ui_state.sm)
    if lead_state is not None:
      lead_speed, ego_speed = lead_state
      lead_label = tr("LEAD:")
      lead_value = str(round(lead_speed * self.speed_conv))
      lead_color = tesla_lead_speed_color(lead_speed, ego_speed)

      for text, text_y, color in (
        (lead_label, y + 168, palette.max_inactive),
        (lead_value, y + 215, lead_color),
      ):
        text_width = measure_text_cached(
          self._font_semi_bold, text, TESLA_LEAD_TEXT_SIZE, max_spacing,
        ).x
        text_pos = rl.Vector2(x + (set_speed_width - text_width) / 2, text_y)
        rl.draw_text_ex(
          self._font_semi_bold, text,
          rl.Vector2(text_pos.x + 1, text_pos.y + 1),
          TESLA_LEAD_TEXT_SIZE, max_spacing, TESLA_TEXT_SHADOW,
        )
        rl.draw_text_ex(
          self._font_semi_bold, text, text_pos,
          TESLA_LEAD_TEXT_SIZE, max_spacing, color,
        )

  def _render(self, rect: rl.Rectangle) -> None:
    # BluePilot: Draw header gradient at full content width (not offset by confidence ball)
    gradient_rect = self._gradient_rect if self._gradient_rect else rect
    rl.draw_rectangle_gradient_v(
      int(gradient_rect.x), int(gradient_rect.y), int(gradient_rect.width),
      UI_CONFIG.header_height,
      COLORS.HEADER_GRADIENT_START, COLORS.HEADER_GRADIENT_END,
    )

    # HUD elements use the (possibly offset) rect for positioning
    if self.is_cruise_available:
      self._draw_set_speed(rect)
    self._draw_current_speed(rect)

    button_x = rect.x + rect.width - UI_CONFIG.border_size - UI_CONFIG.button_size
    button_y = rect.y + UI_CONFIG.border_size
    self._exp_button.render(rl.Rectangle(button_x, button_y, UI_CONFIG.button_size, UI_CONFIG.button_size))
    self._draw_lateral_control_overlay(
      button_x + UI_CONFIG.button_size / 2,
      button_y + UI_CONFIG.button_size / 2,
      UI_CONFIG.button_size,
    )

    # SP additions (dev UI, road name, speed limit, SCC, turn signals, circular alerts, rocket fuel)
    self.developer_ui.render(rect)
    self.road_name_renderer.render(rect)
    self.speed_limit_renderer.render(rect)
    self.smart_cruise_control_renderer.render(rect)
    self.turn_signal_controller.render(rect)
    self.circular_alerts_renderer.render(rect)
    self.rocket_fuel.render(rect, ui_state.sm)

  def _draw_lateral_control_overlay(self, center_x: float, center_y: float, wheel_size: int) -> None:
    """Draw the current lateral control mode over the steering wheel icon."""
    if not self._show_lateral_control or self._lateral_mode is None:
      return

    text_size = int(wheel_size * 0.4)
    if self._lateral_mode == LateralMode.angle:
      letter, color = "A", rl.Color(50, 100, 255, 220)
    elif self._lateral_mode == LateralMode.curvature:
      letter, color = "C", rl.Color(255, 165, 0, 220)
    else:
      letter, color = "OP", rl.Color(100, 100, 100, 220)

    text_dims = measure_text_cached(self._font_bold, letter, text_size)
    text_pos = rl.Vector2(center_x - text_dims.x / 2, center_y - text_dims.y / 2)

    top = rl.Color(250, 250, 250, 200)
    bottom = rl.Color(200, 200, 200, 200)
    draw_shader_circle_gradient(center_x, center_y, text_size / 2, top, bottom)
    rl.draw_text_ex(self._font_bold, letter, text_pos, text_size, 0, color)

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Override to add brake status red coloring and track speed_right."""
    # BluePilot: Respect "Speedometer: Hide from Onroad Screen" (HideVEgoUI) from Visuals.
    if self._hide_v_ego_ui:
      self.speed_right = 0
      return
    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, FONT_SIZES.current_speed)
    speed_pos = rl.Vector2(
      rect.x + rect.width / 2 - speed_text_size.x / 2,
      SPEED_CENTER_Y - speed_text_size.y / 2
    )
    self.speed_right = speed_pos.x + speed_text_size.x

    # BluePilot: Show red when braking if brake status is enabled
    speed_color = rl.Color(255, 60, 60, 255) if self._brakes_on else COLORS.WHITE
    rl.draw_text_ex(self._font_bold, speed_text, speed_pos, FONT_SIZES.current_speed, 0, speed_color)

    unit_text = "km/h" if ui_state.is_metric else "mph"
    unit_text_size = measure_text_cached(self._font_medium, unit_text, FONT_SIZES.speed_unit)
    unit_pos = rl.Vector2(rect.x + rect.width / 2 - unit_text_size.x / 2, SPEED_UNIT_CENTER_Y - unit_text_size.y / 2)
    # Draw drop shadow for readability over camera feed
    shadow_offset = 2
    shadow_pos = rl.Vector2(unit_pos.x + shadow_offset, unit_pos.y + shadow_offset)
    rl.draw_text_ex(self._font_medium, unit_text, shadow_pos, FONT_SIZES.speed_unit, 0, rl.Color(0, 0, 0, 150))
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, FONT_SIZES.speed_unit, 0, COLORS.WHITE_TRANSLUCENT)
