from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.ui.bp.lib.longitudinal_visuals import (
  TESLA_PATH_LOW_CONFIDENCE_ALPHA,
  TESLA_PATH_NEAR_POINT_COUNT,
  TeslaPathPresentationState,
  advance_tesla_blue_phase,
  legacy_rainbow_cycle_rate,
  longitudinal_control_active,
  rainbow_cycle_rate,
  tesla_geometry_reliable,
  tesla_model_confidence,
  tesla_near_path_points,
  tesla_path_mode,
)


class FakeSubMaster:
  def __init__(self, *, long_active: bool = False, v_ego: float = 0.0,
               car_control_valid: bool = True, car_state_valid: bool = True,
               model_valid: bool = True, car_state_alive: bool = True,
               model_alive: bool = True):
    self.valid = {
      "carControl": car_control_valid,
      "carState": car_state_valid,
      "modelV2": model_valid,
    }
    self.alive = {
      "carState": car_state_alive,
      "modelV2": model_alive,
    }
    self.messages = {
      "carControl": SimpleNamespace(longActive=long_active),
      "carState": SimpleNamespace(vEgo=v_ego),
      "modelV2": SimpleNamespace(),
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


def test_tesla_geometry_requires_live_valid_finite_model_geometry():
  long_path = np.asarray([[0.0, 0.0, 0.0], [45.0, 1.0, 0.0]])
  short_path = np.asarray([[0.0, 0.0, 0.0], [22.0, 8.0, 0.0]])
  nonfinite_path = np.asarray([[0.0, 0.0, 0.0], [45.0, np.nan, 0.0]])

  assert not tesla_geometry_reliable(long_path, FakeSubMaster(v_ego=0.0))
  assert not tesla_geometry_reliable(short_path, FakeSubMaster(v_ego=4.0))
  assert not tesla_geometry_reliable(nonfinite_path, FakeSubMaster(v_ego=4.0))
  assert not tesla_geometry_reliable(long_path, FakeSubMaster(v_ego=4.0, model_valid=False))
  assert not tesla_geometry_reliable(long_path, FakeSubMaster(v_ego=4.0, model_alive=False))
  assert not tesla_geometry_reliable(long_path, FakeSubMaster(v_ego=4.0, car_state_alive=False))
  assert tesla_geometry_reliable(long_path, FakeSubMaster(v_ego=4.0))


def test_tesla_path_presentation_confidence_matches_active_control_axes():
  sm = FakeSubMaster()
  sm.messages["modelV2"] = SimpleNamespace(meta=SimpleNamespace(disengagePredictions=SimpleNamespace(
    brakeDisengageProbs=[0.10, 0.20],
    steerOverrideProbs=[0.25, 0.40],
  )))

  assert tesla_model_confidence(sm, "lat_only") == pytest.approx(0.60)
  assert tesla_model_confidence(sm, "long_only") == pytest.approx(0.80)
  assert tesla_model_confidence(sm, "engaged") == pytest.approx(0.48)
  assert tesla_model_confidence(sm, "disengaged") == 1.0
  sm.valid["modelV2"] = False
  assert tesla_model_confidence(sm, "engaged") == 0.0


def _raw_path(y_values=None):
  x_values = np.linspace(0.0, 50.0, 26)
  if y_values is None:
    y_values = np.zeros_like(x_values)
  return np.column_stack((x_values, y_values, np.zeros_like(x_values))).astype(np.float32)


def _projected_path(x_offset: float = 0.0):
  longitudinal = np.linspace(0.0, 50.0, 12)
  left = np.column_stack((longitudinal + x_offset, np.full_like(longitudinal, -1.0)))
  right = np.column_stack((longitudinal + x_offset, np.full_like(longitudinal, 1.0)))
  return np.concatenate((left, right[::-1])).astype(np.float32)


def _settle_full_path(state, raw, projected, *, confidence: float = 1.0):
  layers = None
  for now in np.arange(0.0, 0.81, 0.1):
    layers = state.update(raw, projected, confidence=confidence, reliable=True,
                          speed_mps=10.0, now=float(now))
  assert layers is not None
  return layers


def test_tesla_near_path_resamples_exactly_sixteen_metres_along_curve():
  # Four right-angle legs make interpolation distances exact at every 2 m sample.
  curved = np.asarray([
    [0.0, 0.0, 0.0],
    [4.0, 0.0, 1.0],
    [4.0, 4.0, 2.0],
    [8.0, 4.0, 3.0],
    [8.0, 8.0, 4.0],
  ])
  expected = np.asarray([
    [0.0, 0.0, 0.0],
    [2.0, 0.0, 0.5],
    [4.0, 0.0, 1.0],
    [4.0, 2.0, 1.5],
    [4.0, 4.0, 2.0],
    [6.0, 4.0, 2.5],
    [8.0, 4.0, 3.0],
    [8.0, 6.0, 3.5],
    [8.0, 8.0, 4.0],
  ], dtype=np.float32)

  near = tesla_near_path_points(curved)

  assert near is not None
  assert near.shape == (TESLA_PATH_NEAR_POINT_COUNT, 3)
  np.testing.assert_allclose(near, expected)


def test_single_frame_near_path_jump_is_rejected():
  state = TeslaPathPresentationState()
  baseline = _raw_path()
  curved = _raw_path(4.0 * np.sin(np.linspace(0.0, np.pi / 2.0, 26)))
  first = state.update(baseline, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.0)
  jumped = state.update(curved, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.1)
  recovered = state.update(baseline, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.2)

  np.testing.assert_allclose(jumped.near_raw_points, first.near_raw_points)
  np.testing.assert_allclose(recovered.near_raw_points, first.near_raw_points)


def test_persistent_near_path_jump_reacquires_without_snapping():
  state = TeslaPathPresentationState()
  baseline = _raw_path()
  curved = _raw_path(4.0 * np.sin(np.linspace(0.0, np.pi / 2.0, 26)))
  initial = state.update(baseline, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.0)
  state.update(curved, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.1)
  reacquired = state.update(curved, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.2)
  target = tesla_near_path_points(curved)

  assert target is not None
  assert np.max(reacquired.near_raw_points[:, 1]) > np.max(initial.near_raw_points[:, 1])
  assert np.max(reacquired.near_raw_points[:, 1]) < np.max(target[:, 1])


def test_near_path_freezes_at_standstill():
  state = TeslaPathPresentationState()
  baseline = _raw_path()
  curved = _raw_path(4.0 * np.sin(np.linspace(0.0, np.pi / 2.0, 26)))
  initial = state.update(baseline, None, confidence=1.0, reliable=False, speed_mps=10.0, now=0.0)
  frozen = state.update(curved, None, confidence=1.0, reliable=False, speed_mps=0.0, now=0.1)
  frozen_again = state.update(curved, None, confidence=1.0, reliable=False, speed_mps=0.0, now=0.2)

  np.testing.assert_allclose(frozen.near_raw_points, initial.near_raw_points)
  np.testing.assert_allclose(frozen_again.near_raw_points, initial.near_raw_points)


def test_full_path_holds_briefly_then_fades_on_geometry_dropout():
  state = TeslaPathPresentationState()
  raw = _raw_path()
  projected = _projected_path()
  settled = _settle_full_path(state, raw, projected)
  dropout = state.update(raw, projected, confidence=1.0, reliable=False, speed_mps=10.0, now=0.9)
  held = state.update(raw, projected, confidence=1.0, reliable=False, speed_mps=10.0, now=1.05)
  fading = state.update(raw, projected, confidence=1.0, reliable=False, speed_mps=10.0, now=1.15)
  expired = state.update(raw, projected, confidence=1.0, reliable=False, speed_mps=10.0, now=1.26)

  assert dropout.full_opacity == pytest.approx(settled.full_opacity)
  assert held.full_opacity == pytest.approx(settled.full_opacity)
  assert 0.0 < fading.full_opacity < held.full_opacity
  assert expired.full_points is None
  assert expired.full_opacity == 0.0


def test_missing_near_path_holds_then_expires_by_deadline():
  state = TeslaPathPresentationState()
  visible = state.update(_raw_path(), None, confidence=0.0, reliable=False, speed_mps=10.0, now=0.0)
  held = state.update(None, None, confidence=0.0, reliable=False, speed_mps=10.0, now=0.25)
  fading = state.update(None, None, confidence=0.0, reliable=False, speed_mps=10.0, now=0.4)
  expired = state.update(None, None, confidence=0.0, reliable=False, speed_mps=10.0, now=0.61)

  assert held.near_opacity == pytest.approx(visible.near_opacity)
  assert 0.0 < fading.near_opacity < held.near_opacity
  assert expired.near_raw_points is None
  assert expired.near_opacity == 0.0


def test_long_update_gap_purges_full_path_before_reacquisition():
  state = TeslaPathPresentationState()
  raw = _raw_path()
  projected = _projected_path()
  settled = _settle_full_path(state, raw, projected)
  after_gap = state.update(raw, projected, confidence=1.0, reliable=True,
                           speed_mps=10.0, now=1.2)

  assert settled.full_points is not None
  assert after_gap.full_points is None
  assert after_gap.full_opacity == 0.0
  assert after_gap.near_raw_points is not None


def test_model_confidence_crossfades_full_and_bounded_near_layers():
  raw = _raw_path()
  projected = _projected_path()
  high = _settle_full_path(TeslaPathPresentationState(), raw, projected, confidence=1.0)
  low = _settle_full_path(TeslaPathPresentationState(), raw, projected, confidence=0.0)

  assert high.full_points is not None
  assert high.full_opacity > 0.0
  assert 0.0 <= high.near_opacity < TESLA_PATH_LOW_CONFIDENCE_ALPHA
  assert low.full_points is not None
  assert low.full_opacity == 0.0
  assert low.near_opacity == pytest.approx(TESLA_PATH_LOW_CONFIDENCE_ALPHA)


def test_path_presentation_reset_discards_cached_geometry_and_confidence():
  state = TeslaPathPresentationState()
  raw = _raw_path()
  projected = _projected_path()
  settled = _settle_full_path(state, raw, projected)
  assert settled.full_points is not None

  state.reset()
  reset_layers = state.update(raw, projected, confidence=0.0, reliable=True,
                              speed_mps=10.0, now=5.0)

  assert reset_layers.full_points is None
  assert reset_layers.full_opacity == 0.0
  assert reset_layers.near_raw_points is not None
  assert reset_layers.near_opacity == pytest.approx(TESLA_PATH_LOW_CONFIDENCE_ALPHA)
