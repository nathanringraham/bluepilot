from types import SimpleNamespace

import pyray as rl

from openpilot.selfdrive.ui.bp.onroad.hud_renderer_bp import (
  TESLA_LEAD_FASTER_COLOR,
  TESLA_LEAD_FULL_RED_DELTA_MPS,
  TESLA_LEAD_SLOW_RED,
  TESLA_LEAD_SLOW_YELLOW,
  TESLA_LEAD_TEXT_SIZE,
  TESLA_MAX_LABEL_SIZE,
  TESLA_SET_SPEED_SIZE,
  HudRendererBP,
  tesla_lead_speed_color,
  tesla_lead_speed_state,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP


def test_non_tesla_theme_delegates_to_unchanged_set_speed_renderer(monkeypatch):
  renderer = object.__new__(HudRendererBP)
  renderer._tesla_theme_variant = None
  rect = rl.Rectangle(0, 0, 2160, 1080)
  calls = []

  def stock_draw(instance, draw_rect):
    calls.append((instance, draw_rect))

  monkeypatch.setattr(HudRendererSP, "_draw_set_speed", stock_draw)
  renderer._draw_set_speed(rect)

  assert calls == [(renderer, rect)]


class FakeSubMaster:
  def __init__(self, *, lead_status=True, radar_alive=True, radar_valid=True,
               car_alive=True, car_valid=True, d_rel=30.0, v_lead=20.0,
               v_lead_k=None, v_ego=18.0):
    v_lead_k = v_lead if v_lead_k is None else v_lead_k
    self.alive = {"radarState": radar_alive, "carState": car_alive}
    self.valid = {"radarState": radar_valid, "carState": car_valid}
    self.messages = {
      "radarState": SimpleNamespace(leadOne=SimpleNamespace(
        status=lead_status, dRel=d_rel, vLead=v_lead, vLeadK=v_lead_k,
      )),
      "carState": SimpleNamespace(vEgo=v_ego),
    }

  def __getitem__(self, service):
    return self.messages[service]


def test_tesla_max_and_lead_typography_is_enlarged() -> None:
  assert TESLA_SET_SPEED_SIZE == 100
  assert TESLA_MAX_LABEL_SIZE == 34
  assert TESLA_LEAD_TEXT_SIZE == 38


def test_tesla_lead_speed_uses_fused_primary_lead() -> None:
  assert tesla_lead_speed_state(FakeSubMaster(v_lead=20.0, v_ego=18.0)) == (20.0, 18.0)
  assert tesla_lead_speed_state(FakeSubMaster(v_lead=21.0, v_lead_k=20.0)) == (20.0, 18.0)
  assert tesla_lead_speed_state(FakeSubMaster(lead_status=False)) is None
  assert tesla_lead_speed_state(FakeSubMaster(radar_alive=False)) is None
  assert tesla_lead_speed_state(FakeSubMaster(car_valid=False)) is None
  assert tesla_lead_speed_state(FakeSubMaster(d_rel=float("nan"))) is None


def test_tesla_lead_speed_clamps_negative_speed_to_zero() -> None:
  assert tesla_lead_speed_state(FakeSubMaster(v_lead=-1.0, v_ego=2.0)) == (0.0, 2.0)


def test_tesla_lead_speed_color_rules() -> None:
  assert tesla_lead_speed_color(20.0, 20.0) == TESLA_LEAD_FASTER_COLOR
  assert tesla_lead_speed_color(21.0, 20.0) == TESLA_LEAD_FASTER_COLOR
  assert tesla_lead_speed_color(20.0, 20.001).r == TESLA_LEAD_SLOW_YELLOW.r
  assert tesla_lead_speed_color(10.0, 10.0 + TESLA_LEAD_FULL_RED_DELTA_MPS) == TESLA_LEAD_SLOW_RED
  assert tesla_lead_speed_color(0.0, 30.0) == TESLA_LEAD_SLOW_RED

  midpoint = tesla_lead_speed_color(10.0, 10.0 + TESLA_LEAD_FULL_RED_DELTA_MPS / 2.0)
  assert midpoint.r == round((TESLA_LEAD_SLOW_YELLOW.r + TESLA_LEAD_SLOW_RED.r) / 2.0)
  assert midpoint.g == round((TESLA_LEAD_SLOW_YELLOW.g + TESLA_LEAD_SLOW_RED.g) / 2.0)
  assert midpoint.b == round((TESLA_LEAD_SLOW_YELLOW.b + TESLA_LEAD_SLOW_RED.b) / 2.0)
