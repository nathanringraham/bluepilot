from types import SimpleNamespace

import pyray as rl

from cereal import log
from openpilot.selfdrive.ui.bp.lib.lane_change_visuals import tesla_lane_change_lane_active
from openpilot.selfdrive.ui.bp.onroad.tesla_turn_signal import (
  TESLA_TURN_SIGNAL_GREEN,
  TESLA_TURN_SIGNAL_PERIOD_S,
  TESLA_TURN_SIGNAL_SOURCE_GREEN,
  tesla_turn_signal_alpha,
  tesla_turn_signal_layout,
  tesla_turn_signal_state,
)


class FakeSubMaster:
  def __init__(self, *, left_blinker: bool = False, right_blinker: bool = False,
               car_alive: bool = True, car_valid: bool = True,
               lane_change_state=log.LaneChangeState.off,
               lane_change_direction=log.LaneChangeDirection.none,
               model_alive: bool = True, model_valid: bool = True):
    self.alive = {"carState": car_alive, "modelV2": model_alive}
    self.valid = {"carState": car_valid, "modelV2": model_valid}
    self.messages = {
      "carState": SimpleNamespace(leftBlinker=left_blinker, rightBlinker=right_blinker),
      "modelV2": SimpleNamespace(meta=SimpleNamespace(
        laneChangeState=lane_change_state,
        laneChangeDirection=lane_change_direction,
      )),
    }

  def __getitem__(self, service):
    return self.messages[service]


def test_tesla_turn_signals_honor_existing_show_toggle() -> None:
  sm = FakeSubMaster(left_blinker=True)

  assert tesla_turn_signal_state(sm, True) == (True, False)
  assert tesla_turn_signal_state(sm, False) == (False, False)


def test_tesla_turn_signals_reject_invalid_car_state() -> None:
  assert tesla_turn_signal_state(FakeSubMaster(left_blinker=True, car_alive=False), True) == (False, False)
  assert tesla_turn_signal_state(FakeSubMaster(right_blinker=True, car_valid=False), True) == (False, False)


def test_tesla_turn_signal_uses_sampled_official_green_and_soft_pulse() -> None:
  assert TESLA_TURN_SIGNAL_SOURCE_GREEN == (15, 102, 54)
  assert TESLA_TURN_SIGNAL_GREEN.g > TESLA_TURN_SIGNAL_SOURCE_GREEN[1]
  assert tesla_turn_signal_alpha(0.0) == 255
  assert tesla_turn_signal_alpha(TESLA_TURN_SIGNAL_PERIOD_S / 2.0) < 40
  assert tesla_turn_signal_alpha(TESLA_TURN_SIGNAL_PERIOD_S) == 255


def test_tesla_turn_signal_layout_is_symmetric_and_scales_for_c4() -> None:
  tici = tesla_turn_signal_layout(rl.Rectangle(0, 0, 2160, 1080), compact=False)
  mici = tesla_turn_signal_layout(rl.Rectangle(0, 0, 1080, 540), compact=True)

  assert tici.left_x + tici.right_x == 2160
  assert mici.left_x + mici.right_x == 1080
  assert tici.size == 170
  assert mici.size == 50
  assert tici.center_y > 300


def test_lane_change_highlight_targets_only_destination_inner_lane() -> None:
  left = FakeSubMaster(
    lane_change_state=log.LaneChangeState.laneChangeStarting,
    lane_change_direction=log.LaneChangeDirection.left,
  )
  right = FakeSubMaster(
    lane_change_state=log.LaneChangeState.laneChangeFinishing,
    lane_change_direction=log.LaneChangeDirection.right,
  )

  assert {i for i in range(4) if tesla_lane_change_lane_active(left, i)} == {1}
  assert {i for i in range(4) if tesla_lane_change_lane_active(right, i)} == {2}


def test_lane_change_highlight_stays_off_while_only_preparing_or_invalid() -> None:
  pre = FakeSubMaster(
    lane_change_state=log.LaneChangeState.preLaneChange,
    lane_change_direction=log.LaneChangeDirection.left,
  )
  invalid = FakeSubMaster(
    lane_change_state=log.LaneChangeState.laneChangeStarting,
    lane_change_direction=log.LaneChangeDirection.left,
    model_valid=False,
  )

  assert not any(tesla_lane_change_lane_active(pre, i) for i in range(4))
  assert not any(tesla_lane_change_lane_active(invalid, i) for i in range(4))
