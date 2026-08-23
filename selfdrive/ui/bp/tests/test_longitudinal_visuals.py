from types import SimpleNamespace

import pytest

from openpilot.selfdrive.ui.bp.lib.longitudinal_visuals import (
  advance_tesla_blue_phase,
  legacy_rainbow_cycle_rate,
  longitudinal_control_active,
  rainbow_cycle_rate,
  tesla_path_mode,
)


class FakeSubMaster:
  def __init__(self, *, long_active: bool = False, v_ego: float = 0.0,
               car_control_valid: bool = True, car_state_valid: bool = True):
    self.valid = {
      "carControl": car_control_valid,
      "carState": car_state_valid,
    }
    self.messages = {
      "carControl": SimpleNamespace(longActive=long_active),
      "carState": SimpleNamespace(vEgo=v_ego),
    }

  def __getitem__(self, key: str):
    return self.messages[key]


@pytest.mark.parametrize(("long_active", "status", "car_control_valid", "expected"), [
  (True, "disengaged", True, True),
  (False, "engaged", True, True),
  (False, "long_only", True, True),
  (False, "lat_only", True, False),
  (False, "disengaged", True, False),
  (False, "engaged", False, True),
  (False, "long_only", False, True),
  (False, "lat_only", False, False),
])
def test_longitudinal_visual_gate_supports_openpilot_and_oem_longitudinal(long_active, status,
                                                                         car_control_valid, expected):
  sm = FakeSubMaster(long_active=long_active, car_control_valid=car_control_valid)
  assert longitudinal_control_active(sm, status) is expected


@pytest.mark.parametrize(("v_ego", "expected"), [
  (-1.0, 0.0),
  (0.0, 0.0),
  (15.0, 0.5),
  (30.0, 1.0),
  (50.0, 35.0 / 30.0),
])
def test_rainbow_cycle_rate_tracks_actual_speed_and_clamps(v_ego, expected):
  assert rainbow_cycle_rate(FakeSubMaster(v_ego=v_ego)) == pytest.approx(expected)


def test_invalid_car_state_stops_rainbow_animation():
  assert rainbow_cycle_rate(FakeSubMaster(v_ego=30.0, car_state_valid=False)) == 0.0


def test_non_theme_rainbow_retains_bluepilot_animation_floor():
  assert legacy_rainbow_cycle_rate(FakeSubMaster(v_ego=0.0)) == pytest.approx(2.5 / 30.0)
  assert legacy_rainbow_cycle_rate(FakeSubMaster(v_ego=30.0)) == pytest.approx(1.0)


def test_tesla_blue_phase_is_speed_dependent_and_stops_at_standstill():
  assert advance_tesla_blue_phase(0.25, 0.0, 20.0) == pytest.approx(0.25)
  assert advance_tesla_blue_phase(0.25, 1.0, 20.0) == pytest.approx(0.2625)
  assert advance_tesla_blue_phase(0.99, 1.0, 20.0) == pytest.approx(0.0025)


@pytest.mark.parametrize(("rainbow", "long_active", "expected"), [
  (True, True, "rainbow"),
  (True, False, "rainbow"),
  (False, True, "blue_cycle"),
  (False, False, "blue_static"),
])
def test_tesla_rainbow_toggle_overrides_blue_path(rainbow, long_active, expected):
  assert tesla_path_mode(rainbow, long_active) == expected
