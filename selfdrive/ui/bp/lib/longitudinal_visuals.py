"""Shared engagement and animation state for BluePilot longitudinal visuals."""

from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np


RAINBOW_SPEED_CAP_MS = 35.0
RAINBOW_RATE_DIVISOR = 30.0
TESLA_BLUE_CYCLES_PER_SECOND = 0.25
TESLA_GEOMETRY_MIN_SPEED_MPS = 2.0
TESLA_GEOMETRY_MIN_DEPTH_M = 38.0
TESLA_PATH_NEAR_LENGTH_M = 16.0
TESLA_PATH_NEAR_POINT_COUNT = 9
TESLA_PATH_LOW_CONFIDENCE_ALPHA = 0.28
TESLA_PATH_CONFIDENCE_LOW = 0.20
TESLA_PATH_CONFIDENCE_FULL = 0.55
TESLA_PATH_CONFIDENCE_FILTER_SECONDS = 0.45
TESLA_PATH_NEAR_TRACK_SECONDS = 0.45
TESLA_PATH_FULL_TRACK_SECONDS = 0.18
TESLA_PATH_STANDSTILL_FREEZE_MPS = 0.5
TESLA_PATH_FRAME_JUMP_M = 0.9
TESLA_PATH_PENDING_MATCH_M = 0.45
TESLA_PATH_UNSTABLE_DWELL_SECONDS = 0.15
TESLA_PATH_STABLE_DWELL_SECONDS = 0.20
TESLA_PATH_FULL_FADE_OUT_SECONDS = 0.20
TESLA_PATH_FULL_FADE_IN_SECONDS = 0.32
TESLA_PATH_MISSING_HOLD_SECONDS = 0.25
TESLA_PATH_MISSING_FADE_SECONDS = 0.35
TESLA_PATH_MAX_UPDATE_GAP_SECONDS = 0.25
TESLA_PATH_PROJECTED_CHAIN_POINTS = 33


def longitudinal_control_active(sm, status) -> bool:
  """True while longitudinal control is represented as active in the UI.

  carControl.longActive covers openpilot longitudinal. BluePilot's ENGAGED and
  LONG_ONLY states also cover platforms using stock/OEM longitudinal control,
  where carControl is valid but longActive intentionally remains false.
  """
  if sm.valid.get("carControl", False) and sm["carControl"].longActive:
    return True

  status_value = getattr(status, "value", status)
  return status_value in ("engaged", "long_only")


def rainbow_cycle_rate(sm) -> float:
  """Scale rainbow animation rate with actual ego speed, stopping at standstill."""
  if not sm.valid.get("carState", False):
    return 0.0

  v_ego = max(0.0, min(float(sm["carState"].vEgo), RAINBOW_SPEED_CAP_MS))
  return v_ego / RAINBOW_RATE_DIVISOR


def legacy_rainbow_cycle_rate(sm) -> float:
  """Preserve BluePilot's pre-theme rainbow animation floor outside Tesla themes."""
  if not sm.valid.get("carState", False):
    return 1.0

  v_ego = max(2.5, min(float(sm["carState"].vEgo), RAINBOW_SPEED_CAP_MS))
  return v_ego / RAINBOW_RATE_DIVISOR


def advance_tesla_blue_phase(phase: float, speed_rate: float, fps: float) -> float:
  """Advance the Tesla-blue shade cycle, stopping naturally at zero speed."""
  if fps <= 0.0:
    return phase % 1.0
  return (phase + max(0.0, speed_rate) * TESLA_BLUE_CYCLES_PER_SECOND / fps) % 1.0


def tesla_path_mode(rainbow_enabled: bool, longitudinal_active: bool) -> str:
  if rainbow_enabled:
    return "rainbow"
  return "blue_cycle" if longitudinal_active else "blue_static"


def tesla_geometry_reliable(path_points, sm) -> bool:
  """Require useful forward reach before presenting model-backed Tesla geometry."""
  if not (sm.valid.get("carState", False) and sm.valid.get("modelV2", False)):
    return False
  if hasattr(sm, "alive") and not (
    sm.alive.get("carState", False) and sm.alive.get("modelV2", False)
  ):
    return False

  speed = float(sm["carState"].vEgo)
  points = np.asarray(path_points, dtype=np.float64)
  if not np.isfinite(speed) or speed < TESLA_GEOMETRY_MIN_SPEED_MPS:
    return False
  if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] < 3:
    return False
  return bool(np.all(np.isfinite(points[:, :3])) and np.max(points[:, 0]) >= TESLA_GEOMETRY_MIN_DEPTH_M)


def _safe_disengage_probability(values) -> float:
  probabilities = np.asarray(values, dtype=np.float64)
  if probabilities.size == 0 or not np.all(np.isfinite(probabilities)):
    return 1.0
  return float(np.clip(np.max(probabilities), 0.0, 1.0))


def tesla_model_confidence(sm, status) -> float:
  """Match the confidence ball's model signals without depending on its toggle."""
  valid = getattr(sm, "valid", {})
  alive = getattr(sm, "alive", None)
  if not valid.get("modelV2", False) or (alive is not None and not alive.get("modelV2", False)):
    return 0.0

  try:
    predictions = sm["modelV2"].meta.disengagePredictions
    brake = _safe_disengage_probability(predictions.brakeDisengageProbs)
    steer = _safe_disengage_probability(predictions.steerOverrideProbs)
  except (AttributeError, KeyError, TypeError):
    return 0.0

  status_value = getattr(status, "value", status)
  if status_value == "lat_only":
    return 1.0 - steer
  if status_value == "long_only":
    return 1.0 - brake
  if status_value == "engaged":
    return (1.0 - brake) * (1.0 - steer)
  # Confidence is not a driver-facing engagement metric while disengaged or
  # overridden. Preserve normal path visibility, while geometry reliability
  # still invokes the low-speed/short-path treatment below.
  return 1.0


def _resample_path_chain(points: np.ndarray, count: int) -> np.ndarray | None:
  if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] != 2 or not np.all(np.isfinite(points)):
    return None
  lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
  cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
  keep = np.concatenate(([True], np.diff(cumulative) > 1e-3))
  cumulative = cumulative[keep]
  points = points[keep]
  if len(points) < 2:
    return None
  if float(cumulative[-1]) < 1e-3:
    return None
  samples = np.linspace(0.0, float(cumulative[-1]), count)
  return np.column_stack((
    np.interp(samples, cumulative, points[:, 0]),
    np.interp(samples, cumulative, points[:, 1]),
  )).astype(np.float32)


def _normalized_projected_path_polygon(points) -> np.ndarray | None:
  """Normalize a projected ribbon to fixed paired chains for brief filtering."""
  polygon = np.asarray(points, dtype=np.float32)
  if (polygon.ndim != 2 or polygon.shape[1] != 2 or len(polygon) < 4 or
      len(polygon) % 2 != 0 or not np.all(np.isfinite(polygon))):
    return None
  half = len(polygon) // 2
  left = _resample_path_chain(polygon[:half], TESLA_PATH_PROJECTED_CHAIN_POINTS)
  right = _resample_path_chain(polygon[half:][::-1], TESLA_PATH_PROJECTED_CHAIN_POINTS)
  if left is None or right is None:
    return None
  return np.concatenate((left, right[::-1])).astype(np.float32)


def tesla_near_path_points(path_points, length_m: float = TESLA_PATH_NEAR_LENGTH_M) -> np.ndarray | None:
  """Return a fixed model-derived XYZ centerline over the requested near distance.

  Distance is measured along the model's XY curve, so a sharp turn stays curved.
  No centered or straight geometry is ever synthesized.
  """
  points = np.asarray(path_points, dtype=np.float64)
  length_m = float(length_m)
  if (not np.isfinite(length_m) or length_m <= 0.0 or points.ndim != 2 or
      points.shape[0] < 2 or points.shape[1] < 3 or not np.all(np.isfinite(points[:, :3]))):
    return None
  if float(np.linalg.norm(points[0, :2])) > 1.0:
    return None

  segment_lengths = np.linalg.norm(np.diff(points[:, :2], axis=0), axis=1)
  cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))
  keep = np.concatenate(([True], np.diff(cumulative) > 1e-4))
  cumulative = cumulative[keep]
  points = points[keep]
  if len(points) < 2 or float(cumulative[-1]) + 1e-6 < length_m:
    return None

  samples = np.linspace(0.0, length_m, TESLA_PATH_NEAR_POINT_COUNT)
  result = np.column_stack([
    np.interp(samples, cumulative, points[:, axis])
    for axis in range(3)
  ])
  return result.astype(np.float32)


def _near_path_delta(first: np.ndarray, second: np.ndarray) -> float:
  if first.shape != second.shape:
    return float("inf")
  return float(np.max(np.linalg.norm(first[:, :2] - second[:, :2], axis=1)))


def _smoothstep(value: float) -> float:
  amount = float(np.clip(value, 0.0, 1.0))
  return amount * amount * (3.0 - 2.0 * amount)


@dataclass(frozen=True)
class TeslaPathLayers:
  """Two complementary path layers returned to either device renderer."""
  full_points: np.ndarray | None
  full_opacity: float
  near_raw_points: np.ndarray | None
  near_opacity: float


class TeslaPathPresentationState:
  """Keep a calm, bounded path during brief model uncertainty.

  The long ribbon is only retained briefly. The fallback is always the model's
  own first 16 metres, frozen at standstill and filtered in car space so it
  cannot become a fabricated directional cue.
  """

  def __init__(self) -> None:
    self.reset()

  def reset(self) -> None:
    self._near_points: np.ndarray | None = None
    self._near_target: np.ndarray | None = None
    self._pending_near: np.ndarray | None = None
    self._full_points: np.ndarray | None = None
    self._confidence = 1.0
    self._confidence_initialized = False
    self._full_visibility = 0.0
    self._full_visibility_at_dropout = 0.0
    self._last_update: float | None = None
    self._last_near_seen: float | None = None
    self._stable_since: float | None = None
    self._unstable_since: float | None = None

  def _update_near(self, candidate: np.ndarray | None, speed_mps: float,
                   dt: float, now: float) -> float:
    if candidate is not None:
      self._last_near_seen = now
      if self._near_points is None:
        self._near_points = candidate.copy()
        self._near_target = candidate.copy()
        self._pending_near = None
      elif speed_mps >= TESLA_PATH_STANDSTILL_FREEZE_MPS:
        target = self._near_target if self._near_target is not None else self._near_points
        if _near_path_delta(candidate, target) <= TESLA_PATH_FRAME_JUMP_M:
          self._near_target = candidate.copy()
          self._pending_near = None
        elif (self._pending_near is not None and
              _near_path_delta(candidate, self._pending_near) <= TESLA_PATH_PENDING_MATCH_M):
          # A new curve that persists for a second frame is real; reacquire it
          # through the filter instead of snapping to it.
          self._near_target = candidate.copy()
          self._pending_near = None
        else:
          self._pending_near = candidate.copy()

        if self._near_target is not None and dt > 0.0:
          alpha = 1.0 - float(np.exp(-dt / TESLA_PATH_NEAR_TRACK_SECONDS))
          self._near_points += (self._near_target - self._near_points) * alpha
    else:
      self._pending_near = None

    if self._near_points is None or self._last_near_seen is None:
      return 0.0
    missing_age = max(0.0, now - self._last_near_seen)
    if missing_age <= TESLA_PATH_MISSING_HOLD_SECONDS:
      return 1.0
    availability = 1.0 - (
      (missing_age - TESLA_PATH_MISSING_HOLD_SECONDS) / TESLA_PATH_MISSING_FADE_SECONDS
    )
    if availability <= 0.0:
      self._near_points = None
      self._near_target = None
      self._pending_near = None
      self._last_near_seen = None
      return 0.0
    return float(np.clip(availability, 0.0, 1.0))

  def _update_full(self, candidate: np.ndarray | None, stable: bool,
                   dt: float, now: float, update_gap: bool) -> None:
    if update_gap:
      self._full_points = None
      self._full_visibility = 0.0
      self._stable_since = None
      self._unstable_since = now

    if stable and candidate is not None:
      self._unstable_since = None
      if self._stable_since is None:
        self._stable_since = now
      stable_age = now - self._stable_since
      if stable_age >= TESLA_PATH_STABLE_DWELL_SECONDS:
        if self._full_points is None or self._full_points.shape != candidate.shape:
          self._full_points = candidate.copy()
        elif dt > 0.0:
          alpha = 1.0 - float(np.exp(-dt / TESLA_PATH_FULL_TRACK_SECONDS))
          self._full_points += (candidate - self._full_points) * alpha
        visibility_alpha = 1.0 - float(np.exp(-dt / TESLA_PATH_FULL_FADE_IN_SECONDS)) if dt > 0.0 else 0.0
        self._full_visibility += (1.0 - self._full_visibility) * visibility_alpha
      return

    self._stable_since = None
    if self._full_points is None:
      self._full_visibility = 0.0
      return
    if self._unstable_since is None:
      self._unstable_since = now
      self._full_visibility_at_dropout = self._full_visibility
    unstable_age = now - self._unstable_since
    if unstable_age <= TESLA_PATH_UNSTABLE_DWELL_SECONDS:
      return
    fade_age = unstable_age - TESLA_PATH_UNSTABLE_DWELL_SECONDS
    fade = 1.0 - fade_age / TESLA_PATH_FULL_FADE_OUT_SECONDS
    self._full_visibility = self._full_visibility_at_dropout * float(np.clip(fade, 0.0, 1.0))
    if fade <= 0.0:
      self._full_points = None
      self._full_visibility = 0.0

  def update(self, raw_points, projected_points, *, confidence: float,
             reliable: bool, speed_mps: float,
             now: float | None = None) -> TeslaPathLayers:
    now = time.monotonic() if now is None else float(now)
    elapsed = 0.0 if self._last_update is None else max(0.0, now - self._last_update)
    update_gap = self._last_update is not None and elapsed > TESLA_PATH_MAX_UPDATE_GAP_SECONDS
    dt = min(elapsed, TESLA_PATH_MAX_UPDATE_GAP_SECONDS)
    self._last_update = now

    raw_confidence = float(np.clip(confidence, 0.0, 1.0)) if np.isfinite(confidence) else 0.0
    if not self._confidence_initialized:
      self._confidence = raw_confidence
      self._confidence_initialized = True
    elif dt > 0.0:
      alpha = 1.0 - float(np.exp(-dt / TESLA_PATH_CONFIDENCE_FILTER_SECONDS))
      self._confidence += (raw_confidence - self._confidence) * alpha

    near_candidate = tesla_near_path_points(raw_points)
    near_availability = self._update_near(near_candidate, max(0.0, float(speed_mps)), dt, now)
    full_candidate = _normalized_projected_path_polygon(projected_points)
    aligned = (
      near_candidate is not None and self._near_points is not None and
      _near_path_delta(near_candidate, self._near_points) <= TESLA_PATH_FRAME_JUMP_M
    )
    self._update_full(full_candidate, bool(reliable and aligned), dt, now, update_gap)

    # The confidence-ball signal controls presentation only. Geometry validity
    # remains independently bounded by live model data, reach, jump rejection,
    # dwell, and hard expiry above.
    confidence_weight = _smoothstep(
      (self._confidence - TESLA_PATH_CONFIDENCE_LOW) /
      (TESLA_PATH_CONFIDENCE_FULL - TESLA_PATH_CONFIDENCE_LOW)
    )
    full_opacity = float(np.clip(self._full_visibility * confidence_weight, 0.0, 1.0))
    near_opacity = float(np.clip(
      TESLA_PATH_LOW_CONFIDENCE_ALPHA * near_availability * (1.0 - full_opacity),
      0.0,
      TESLA_PATH_LOW_CONFIDENCE_ALPHA,
    ))
    return TeslaPathLayers(
      None if self._full_points is None else self._full_points.copy(),
      full_opacity,
      None if self._near_points is None else self._near_points.copy(),
      near_opacity,
    )
