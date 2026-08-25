"""Tesla-inspired environment layer for BluePilot's onroad view.

This renderer replaces only the camera/model scene.  AugmentedRoadViewBP keeps
the existing HUD, alerts, driver monitoring, gauges, confidence indicators,
and safety blind-spot edges above it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.bp.lib.tesla_palette import palette_for_variant
from openpilot.selfdrive.ui.bp.lib.longitudinal_visuals import tesla_geometry_reliable
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.shader_polygon import draw_polygon, Gradient

MAX_LEAD_DISTANCE_M = 140.0
NOMINAL_LANE_WIDTH_M = 3.7
LEAD_LANE_WIDTH_FRACTION = 0.62
LEAD_HEIGHT_TO_WIDTH = 0.74
LEAD_FULL_SCALE_DISTANCE_M = 8.0
LEAD_FAR_SCALE_DISTANCE_M = 55.0
LEAD_FAR_DISTANCE_SCALE = 0.70
LEAD_FADE_SECONDS = 0.25
ROAD_CHAIN_POINT_COUNT = 9
ROAD_NEUTRAL_FAR_Y_FRACTION = 0.60
ROAD_NEUTRAL_HORIZON_Y_FRACTION = 0.44
ROAD_TRANSITION_SECONDS = 0.42
ROAD_TRACKING_SECONDS = 0.24

SEDAN_BODY_POINTS = (
  (-0.17, -1.00), (0.17, -1.00), (0.25, -0.96), (0.32, -0.84),
  (0.36, -0.67), (0.40, -0.48), (0.45, -0.24), (0.46, -0.10),
  (0.35, 0.00), (-0.35, 0.00), (-0.46, -0.10), (-0.45, -0.24),
  (-0.40, -0.48), (-0.36, -0.67), (-0.32, -0.84), (-0.25, -0.96),
)
SEDAN_HOOD_POINTS = ((-0.14, -0.96), (0.14, -0.96), (0.23, -0.82), (-0.23, -0.82))
SEDAN_WINDSHIELD_POINTS = ((-0.23, -0.80), (0.23, -0.80), (0.28, -0.64), (-0.28, -0.64))
SEDAN_ROOF_POINTS = ((-0.27, -0.62), (0.27, -0.62), (0.30, -0.45), (-0.30, -0.45))
SEDAN_REAR_GLASS_POINTS = ((-0.30, -0.43), (0.30, -0.43), (0.33, -0.29), (-0.33, -0.29))
SEDAN_TRUNK_POINTS = ((-0.35, -0.27), (0.35, -0.27), (0.40, -0.13), (-0.40, -0.13))
SEDAN_REAR_FASCIA_POINTS = ((-0.41, -0.12), (0.41, -0.12), (0.37, -0.02), (-0.37, -0.02))
SEDAN_LEFT_FRONT_FACET = ((-0.25, -0.96), (-0.32, -0.84), (-0.40, -0.48), (-0.30, -0.45))
SEDAN_LEFT_REAR_FACET = ((-0.30, -0.43), (-0.40, -0.48), (-0.46, -0.10), (-0.37, -0.02))
SEDAN_WHEEL_CENTER_X = 0.455
SEDAN_WHEEL_CENTER_Y = -0.23
SEDAN_WHEEL_RADIUS_X = 0.065
SEDAN_WHEEL_RADIUS_Y = 0.12

LeadValues = tuple[float, float, float]


@dataclass
class LeadFadeState:
  """Fade between lead identities without interpolating the actor across cars."""
  displayed_values: LeadValues | None = None
  displayed_generation: int | None = None
  pending_values: LeadValues | None = None
  pending_generation: int | None = None
  opacity: float = 0.0
  phase: str = "hidden"

  def reset(self) -> None:
    self.displayed_values = None
    self.displayed_generation = None
    self.pending_values = None
    self.pending_generation = None
    self.opacity = 0.0
    self.phase = "hidden"

  def update(self, candidate: LeadValues | None, generation: int | None,
             opacity_step: float) -> tuple[LeadValues | None, float]:
    """Advance fade-out-before-fade-in state and return actor data to draw."""
    step = float(np.clip(opacity_step, 0.0, 1.0))

    if self.phase == "hidden":
      if candidate is None:
        return None, 0.0
      self.displayed_values = candidate
      self.displayed_generation = generation
      self.phase = "fading_in"

    elif self.phase in ("visible", "fading_in"):
      if candidate is None or generation != self.displayed_generation:
        self.pending_values = candidate
        self.pending_generation = generation if candidate is not None else None
        self.phase = "fading_out"
      else:
        self.displayed_values = candidate

    elif self.phase == "fading_out":
      if candidate is not None and generation == self.displayed_generation:
        # A one-frame dropout recovered as the same lead; reverse the fade.
        self.displayed_values = candidate
        self.pending_values = None
        self.pending_generation = None
        self.phase = "fading_in"
      else:
        # Follow the newest replacement while the old actor continues fading.
        self.pending_values = candidate
        self.pending_generation = generation if candidate is not None else None

    if self.phase == "fading_out":
      values_to_draw = self.displayed_values
      self.opacity = max(0.0, self.opacity - step)
      if self.opacity <= 1e-6:
        self.opacity = 0.0
        if self.pending_values is None:
          self.reset()
        else:
          self.displayed_values = self.pending_values
          self.displayed_generation = self.pending_generation
          self.pending_values = None
          self.pending_generation = None
          self.phase = "fading_in"
      return values_to_draw, self.opacity

    self.opacity = min(1.0, self.opacity + step)
    if self.opacity >= 1.0:
      self.phase = "visible"
    return self.displayed_values, self.opacity


@dataclass
class RoadGeometryState:
  """Ease between model geometry and a calm, non-predictive near-field apron."""
  points: np.ndarray | None = None
  neutral_amount: float = 1.0
  neutral: bool = True

  def reset(self) -> None:
    self.points = None
    self.neutral_amount = 1.0
    self.neutral = True

  def update(self, target: np.ndarray, neutral: bool, fps: float) -> np.ndarray:
    target = np.asarray(target, dtype=np.float32)
    if self.points is None or self.points.shape != target.shape:
      self.points = target.copy()
      self.neutral_amount = 1.0 if neutral else 0.0
      self.neutral = neutral
      return self.points.copy()

    changed_mode = neutral != self.neutral
    tau = ROAD_TRANSITION_SECONDS if changed_mode or neutral else ROAD_TRACKING_SECONDS
    dt = 1.0 / max(1.0, float(fps))
    alpha = 1.0 - float(np.exp(-dt / tau))
    self.points += (target - self.points) * alpha
    neutral_target = 1.0 if neutral else 0.0
    self.neutral_amount += (neutral_target - self.neutral_amount) * alpha
    self.neutral = neutral
    return self.points.copy()


def color_with_opacity(color: rl.Color, opacity: float) -> rl.Color:
  return rl.Color(color.r, color.g, color.b, round(color.a * float(np.clip(opacity, 0.0, 1.0))))


def _bottom_road_anchor(edge: list[tuple[float, float]], rect: rl.Rectangle,
                        fallback_x_fraction: float, outward: int) -> tuple[float, float]:
  """Extend one projected road edge to the bottom of the viewport.

  The model-backed road begins at 6 m, which is often halfway up the display.
  Extrapolating its nearest segment avoids closing the road ribbon across that
  point and exposing a horizontal strip of ground beneath it.
  """
  bottom_y = float(rect.y + rect.height + 1.0)
  fallback_x = float(rect.x + rect.width * fallback_x_fraction)
  if len(edge) < 2:
    return fallback_x, bottom_y

  near = np.asarray(edge[0], dtype=np.float64)
  next_near = np.asarray(edge[1], dtype=np.float64)
  if not (np.all(np.isfinite(near)) and np.all(np.isfinite(next_near))):
    return fallback_x, bottom_y

  dy = float(near[1] - next_near[1])
  if abs(dy) < 1.0:
    return fallback_x, bottom_y

  x = float(near[0] + (bottom_y - near[1]) * (near[0] - next_near[0]) / dy)
  if not np.isfinite(x):
    return fallback_x, bottom_y

  # Road edges should widen toward the viewer, never fold back across their
  # nearest model sample on a sharp curve or unusual hill profile.
  if near[1] < bottom_y:
    x = min(x, float(near[0])) if outward < 0 else max(x, float(near[0]))
  x = float(np.clip(x, rect.x, rect.x + rect.width))
  return x, bottom_y


def extend_road_edges_to_bottom(left: list[tuple[float, float]], right: list[tuple[float, float]],
                                rect: rl.Rectangle) -> tuple[list[tuple[float, float]],
                                                             list[tuple[float, float]]]:
  """Return paired road-edge chains whose near closure is below the viewport."""
  if not left or not right:
    return left, right

  bottom_y = float(rect.y + rect.height)
  if left[0][1] >= bottom_y and right[0][1] >= bottom_y:
    return left, right

  left_anchor = _bottom_road_anchor(left, rect, 0.05, -1)
  right_anchor = _bottom_road_anchor(right, rect, 0.95, 1)
  if left_anchor[0] >= right_anchor[0]:
    left_anchor = (float(rect.x + rect.width * 0.05), left_anchor[1])
    right_anchor = (float(rect.x + rect.width * 0.95), right_anchor[1])
  return [left_anchor, *left], [right_anchor, *right]


def _resample_road_edge(edge: list[tuple[float, float]], count: int) -> np.ndarray | None:
  points = np.asarray(edge, dtype=np.float32)
  if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] != 2 or not np.all(np.isfinite(points)):
    return None

  lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
  cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
  total = float(cumulative[-1])
  if total < 1e-3:
    return None

  samples = np.linspace(0.0, total, count)
  return np.column_stack((
    np.interp(samples, cumulative, points[:, 0]),
    np.interp(samples, cumulative, points[:, 1]),
  )).astype(np.float32)


def normalized_road_polygon(left: list[tuple[float, float]], right: list[tuple[float, float]],
                            rect: rl.Rectangle) -> np.ndarray | None:
  """Create a fixed-size two-chain polygon suitable for temporal interpolation."""
  left, right = extend_road_edges_to_bottom(left, right, rect)
  left_points = _resample_road_edge(left, ROAD_CHAIN_POINT_COUNT)
  right_points = _resample_road_edge(right, ROAD_CHAIN_POINT_COUNT)
  if left_points is None or right_points is None:
    return None
  if np.any(left_points[:, 0] >= right_points[:, 0]):
    return None
  return np.concatenate((left_points, right_points[::-1])).astype(np.float32)


def neutral_road_polygon(rect: rl.Rectangle) -> np.ndarray:
  """Short road apron used when the model has no stable, useful road geometry.

  It deliberately ends below the horizon and carries no invented lane lines.
  The renderer fades its far edge into the ground, leaving only a subtle pair
  of near-field shoulder guides through parking-lot turns and standstill noise.
  """
  center_x = float(rect.x + rect.width * 0.5)
  bottom_y = float(rect.y + rect.height + 1.0)
  far_y = float(rect.y + rect.height * ROAD_NEUTRAL_FAR_Y_FRACTION)
  progress = np.linspace(0.0, 1.0, ROAD_CHAIN_POINT_COUNT, dtype=np.float32)
  perspective = np.power(progress, 0.72)
  y = bottom_y + (far_y - bottom_y) * perspective
  half_width = rect.width * (0.46 + (0.065 - 0.46) * perspective)
  left = np.column_stack((center_x - half_width, y))
  right = np.column_stack((center_x + half_width, y))
  return np.concatenate((left, right[::-1])).astype(np.float32)


def valid_primary_lead_values(lead) -> tuple[float, float, float] | None:
  """Validate the fused primary lead without inventing a raw-radar fallback."""
  if lead is None or not bool(getattr(lead, "status", False)):
    return None
  values = (float(lead.dRel), float(lead.yRel), float(lead.vRel))
  if not all(np.isfinite(value) for value in values):
    return None
  if not 0.0 < values[0] <= MAX_LEAD_DISTANCE_M:
    return None
  return values


def lead_actor_width(lane_width_px: float, rect_height: float, d_rel: float) -> float:
  """Size the lead from lane perspective with extra attenuation in the distance."""
  display_scale = max(0.45, float(rect_height) / 1080.0)
  distance_scale = float(np.interp(
    d_rel,
    (LEAD_FULL_SCALE_DISTANCE_M, LEAD_FAR_SCALE_DISTANCE_M),
    (1.0, LEAD_FAR_DISTANCE_SCALE),
  ))
  min_width = 30.0 * display_scale
  perspective_width = float(np.clip(lane_width_px * LEAD_LANE_WIDTH_FRACTION,
                                    min_width, 180.0 * display_scale))
  return max(min_width, perspective_width * distance_scale)


def lead_actor_base_y(projected_y: float, width: float, rect: rl.Rectangle) -> float:
  """Keep a close lead fully visible when its ground point projects offscreen."""
  height = width * LEAD_HEIGHT_TO_WIDTH
  display_scale = max(0.45, float(rect.height) / 1080.0)
  bottom_margin = max(24.0 * display_scale, height * 0.14)
  return min(float(projected_y), float(rect.y + rect.height - bottom_margin))


def project_car_space_unclipped(transform: np.ndarray, in_x: float,
                                in_y: float, in_z: float) -> tuple[float, float] | None:
  """Project traffic geometry without the model renderer's offscreen clip guard."""
  point = np.asarray(transform, dtype=np.float64) @ np.asarray((in_x, in_y, in_z), dtype=np.float64)
  if point.shape != (3,) or not np.all(np.isfinite(point)) or abs(float(point[2])) < 1e-6:
    return None
  projected = (float(point[0] / point[2]), float(point[1] / point[2]))
  return projected if all(np.isfinite(value) for value in projected) else None


class TeslaStyleRendererBP:
  def __init__(self, relative_projection: bool = False, show_lead_vehicle: bool = True,
               theme_variant: str = "light"):
    self.relative_projection = relative_projection
    self.show_lead_vehicle = show_lead_vehicle
    self._theme_variant = theme_variant
    self._lead_fade = LeadFadeState()
    self._road_geometry = RoadGeometryState()

  def set_theme_variant(self, variant: str | None) -> None:
    new_variant = variant or "light"
    if variant is None or new_variant != self._theme_variant:
      self._lead_fade.reset()
      self._road_geometry.reset()
    self._theme_variant = new_variant

  def _to_screen(self, point, rect: rl.Rectangle):
    if point is None:
      return None
    x, y = float(point[0]), float(point[1])
    if self.relative_projection:
      x += rect.x
      y += rect.y
    return x, y

  def _project(self, model_renderer, rect: rl.Rectangle, d_rel: float, y_rel: float):
    path = model_renderer._path.raw_points
    z = 0.0
    if path.size:
      idx = model_renderer._get_path_length_idx(path[:, 0], d_rel)
      if idx < len(path):
        z = float(path[idx, 2])
    # ModelRenderer clips ordinary model geometry outside a generous screen
    # margin. A very close lead's road-contact point can legitimately project
    # beyond that margin, so retain it here and clamp the actor after sizing.
    point = project_car_space_unclipped(
      model_renderer._car_space_transform,
      d_rel,
      -y_rel + float(getattr(model_renderer, "_camera_offset", 0.0)),
      z + float(getattr(model_renderer, "_path_offset_z", 0.0)),
    )
    return self._to_screen(point, rect)

  def _projected_road_polygon(self, rect: rl.Rectangle, model_renderer) -> np.ndarray | None:
    path = model_renderer._path.raw_points
    if path.size and path.ndim == 2 and path.shape[1] >= 3 and np.all(np.isfinite(path)):
      max_forward_distance = float(np.max(path[:, 0]))
      left: list[tuple[float, float]] = []
      right: list[tuple[float, float]] = []
      for distance in (6.0, 10.0, 16.0, 25.0, 38.0, 55.0, 78.0, 100.0):
        if distance > max_forward_distance:
          continue
        idx = model_renderer._get_path_length_idx(path[:, 0], distance)
        if idx >= len(path):
          continue
        center_y = float(path[idx, 1])
        z = float(path[idx, 2]) + float(getattr(model_renderer, "_path_offset_z", 0.0))
        left_pt = self._to_screen(model_renderer._map_to_screen(distance, center_y - 5.2, z), rect)
        right_pt = self._to_screen(model_renderer._map_to_screen(distance, center_y + 5.2, z), rect)
        if left_pt is not None and right_pt is not None:
          left.append(left_pt)
          right.append(right_pt)
      if len(left) >= 3 and len(right) >= 3:
        return normalized_road_polygon(left, right, rect)
    return None

  def _road_polygon(self, rect: rl.Rectangle, model_renderer) -> np.ndarray:
    projected = self._projected_road_polygon(rect, model_renderer)
    use_neutral = not tesla_geometry_reliable(model_renderer._path.raw_points, ui_state.sm) or projected is None
    target = neutral_road_polygon(rect) if use_neutral else projected
    return self._road_geometry.update(target, use_neutral, gui_app.target_fps)

  def render_background(self, rect: rl.Rectangle, model_renderer) -> None:
    palette = palette_for_variant(self._theme_variant)
    model_renderer.prepare_projection(rect)
    road = self._road_polygon(rect, model_renderer)
    far_points = road[np.argsort(road[:, 1])[:2]] if len(road) >= 2 else np.empty((0, 2))
    if len(far_points):
      horizon_y = float(np.mean(far_points[:, 1]))
    else:
      horizon_y = rect.y + rect.height * 0.34
    neutral_horizon_y = rect.y + rect.height * ROAD_NEUTRAL_HORIZON_Y_FRACTION
    horizon_y += (neutral_horizon_y - horizon_y) * self._road_geometry.neutral_amount
    horizon_y = float(np.clip(horizon_y, rect.y + rect.height * 0.20, rect.y + rect.height * 0.55))

    sky_h = max(1, int(horizon_y - rect.y))
    ground_h = max(1, int(rect.y + rect.height - horizon_y))
    rl.draw_rectangle_gradient_v(int(rect.x), int(rect.y), int(rect.width), sky_h,
                                 palette.sky_top, palette.sky_horizon)
    rl.draw_rectangle_gradient_v(int(rect.x), int(horizon_y), int(rect.width), ground_h,
                                 palette.ground_horizon, palette.ground_near)
    neutral_amount = float(np.clip(self._road_geometry.neutral_amount, 0.0, 1.0))
    far_alpha = round(palette.road_surface.a * (1.0 - 0.92 * neutral_amount))
    draw_polygon(
      rect,
      road,
      gradient=Gradient(
        start=(0.0, 1.0),
        end=(0.0, 0.0),
        colors=[
          rl.Color(palette.road_surface.r, palette.road_surface.g, palette.road_surface.b, far_alpha),
          palette.road_surface,
        ],
        stops=[0.0, 1.0],
      ),
    )

    half = len(road) // 2
    if half >= 2:
      left = road[:half]
      right = road[half:][::-1]
      for edge in (left, right):
        for index, (start, end) in enumerate(zip(edge[:-1], edge[1:], strict=True)):
          depth = index / max(1, len(edge) - 2)
          alpha_scale = 1.0 - neutral_amount * 0.90 * depth
          shoulder = rl.Color(
            palette.road_shoulder.r,
            palette.road_shoulder.g,
            palette.road_shoulder.b,
            round(palette.road_shoulder.a * alpha_scale),
          )
          rl.draw_line_ex(rl.Vector2(float(start[0]), float(start[1])),
                          rl.Vector2(float(end[0]), float(end[1])), 3.0, shoulder)

  @staticmethod
  def _scaled_points(cx: float, base_y: float, width: float, height: float,
                     points: tuple[tuple[float, float], ...]) -> list[rl.Vector2]:
    return [rl.Vector2(cx + x * width, base_y + y * height) for x, y in points]

  @staticmethod
  def _draw_poly(points: list[rl.Vector2], color: rl.Color) -> None:
    if len(points) >= 3:
      # Raylib's default 2D batch culls the screen-clockwise winding produced
      # by the normalized top-to-bottom actor geometry.
      points = points[::-1]
      rl.draw_triangle_fan(points, len(points), color)

  def _draw_lead_vehicle(self, cx: float, base_y: float, width: float,
                         opacity: float = 1.0) -> None:
    """Draw a low Tesla-like sedan from a slightly elevated rear view."""
    height = width * LEAD_HEIGHT_TO_WIDTH
    dark = self._theme_variant == "dark"

    def fade(color: rl.Color) -> rl.Color:
      return color_with_opacity(color, opacity)

    body = fade(rl.Color(150, 158, 163, 255) if dark else rl.Color(162, 170, 175, 255))
    highlight = fade(rl.Color(190, 197, 201, 255) if dark else rl.Color(200, 205, 208, 255))
    shade = fade(rl.Color(75, 85, 92, 255) if dark else rl.Color(100, 110, 116, 255))
    glass_color = rl.Color(28, 39, 47, 245) if dark else rl.Color(45, 56, 63, 240)
    glass = fade(glass_color)
    outline = fade(rl.Color(205, 212, 216, 245) if dark else rl.Color(82, 92, 98, 245))

    rl.draw_ellipse(int(cx), int(base_y + height * 0.02),
                    max(1, int(width * 0.44)), max(1, int(height * 0.07)),
                    fade(rl.Color(0, 0, 0, 72 if dark else 54)))

    body_shape = self._scaled_points(cx, base_y, width, height, SEDAN_BODY_POINTS)
    self._draw_poly(body_shape, body)

    # Short, separate facets and body-colored roof prevent the long stacked
    # glass slabs that made the previous actor read like a van.
    for left_points in (SEDAN_LEFT_FRONT_FACET, SEDAN_LEFT_REAR_FACET):
      self._draw_poly(self._scaled_points(cx, base_y, width, height, left_points), shade)
      right_points = tuple((-x, y) for x, y in left_points[::-1])
      self._draw_poly(self._scaled_points(cx, base_y, width, height, right_points), shade)

    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_HOOD_POINTS), highlight)
    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_WINDSHIELD_POINTS), glass)
    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_ROOF_POINTS), highlight)
    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_REAR_GLASS_POINTS),
                    fade(rl.Color(glass_color.r, glass_color.g, glass_color.b, 220)))
    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_TRUNK_POINTS), highlight)
    self._draw_poly(self._scaled_points(cx, base_y, width, height, SEDAN_REAR_FASCIA_POINTS), shade)

    # Draw the sidewalls after the body so the wheels remain legible even when
    # the actor is distant. Their centers sit just outside the sedan silhouette.
    wheel_color = fade(rl.Color(22, 25, 28, 245))
    wheel_hub = fade(rl.Color(98, 106, 111, 230))
    for side in (-1.0, 1.0):
      wheel_x = cx + side * width * SEDAN_WHEEL_CENTER_X
      wheel_y = base_y + height * SEDAN_WHEEL_CENTER_Y
      rl.draw_ellipse(
        int(wheel_x), int(wheel_y),
        max(2, int(width * SEDAN_WHEEL_RADIUS_X)),
        max(2, int(height * SEDAN_WHEEL_RADIUS_Y)),
        wheel_color,
      )
      if width >= 55.0:
        rl.draw_ellipse(
          int(wheel_x), int(wheel_y),
          max(1, int(width * SEDAN_WHEEL_RADIUS_X * 0.34)),
          max(1, int(height * SEDAN_WHEEL_RADIUS_Y * 0.44)),
          wheel_hub,
        )

    if width >= 40.0:
      lamp = fade(rl.Color(192, 55, 54, 225))
      for side in (-1.0, 1.0):
        lamp_rect = rl.Rectangle(
          cx + side * width * 0.33 - width * 0.0575,
          base_y - height * 0.115,
          width * 0.115,
          max(2.0, height * 0.04),
        )
        rl.draw_rectangle_rounded(lamp_rect, 0.4, 4, lamp)

    line_width = max(1.0, width * 0.025)
    for start, end in zip(body_shape, [*body_shape[1:], body_shape[0]], strict=True):
      rl.draw_line_ex(start, end, line_width, outline)

  def _projected_lane_width(self, rect: rl.Rectangle, model_renderer,
                            d_rel: float, y_rel: float) -> float | None:
    half_lane = NOMINAL_LANE_WIDTH_M / 2.0
    left = self._project(model_renderer, rect, d_rel, y_rel - half_lane)
    right = self._project(model_renderer, rect, d_rel, y_rel + half_lane)
    if left is None or right is None:
      return None
    width = abs(float(right[0]) - float(left[0]))
    return width if np.isfinite(width) and width > 0.0 else None

  def render_traffic(self, rect: rl.Rectangle, model_renderer) -> None:
    # The comma 4 keeps BluePilot's compact stock lead/radar presentation.
    if not self.show_lead_vehicle:
      return

    sm = ui_state.sm
    candidate = None
    generation = None
    if sm.alive.get("radarState", False) and sm.valid.get("radarState", False):
      radar_state = sm["radarState"]
      raw_values = valid_primary_lead_values(radar_state.leadOne)
      if raw_values is not None:
        candidate = model_renderer.smoothed_primary_lead() or raw_values
        generation = model_renderer.primary_lead_generation()

    fade_step = 1.0 / max(1.0, float(gui_app.target_fps) * LEAD_FADE_SECONDS)
    display_values, opacity = self._lead_fade.update(candidate, generation, fade_step)
    if display_values is None or opacity <= 0.0:
      return

    d_rel, y_rel, _ = display_values
    if not all(np.isfinite(value) for value in (d_rel, y_rel)):
      return

    point = self._project(model_renderer, rect, d_rel, y_rel)
    if point is None:
      return
    cx, base_y = point
    lane_width = self._projected_lane_width(rect, model_renderer, d_rel, y_rel)
    if lane_width is None:
      return
    width = lead_actor_width(lane_width, rect.height, d_rel)
    height = width * LEAD_HEIGHT_TO_WIDTH
    if not (rect.x - width <= cx <= rect.x + rect.width + width and
            rect.y - height <= base_y):
      return
    base_y = lead_actor_base_y(base_y, width, rect)
    self._draw_lead_vehicle(cx, base_y, width, opacity)
