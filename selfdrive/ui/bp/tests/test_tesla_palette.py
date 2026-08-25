from openpilot.selfdrive.ui.bp.lib.tesla_palette import (
  DARK_PALETTE,
  LIGHT_PALETTE,
  TESLA_PATH_BLUE_DEEP,
  TESLA_PATH_BLUE_LIGHT,
  palette_for_variant,
  tesla_blue_cycle_color,
  tesla_path_gradient_colors,
)
from openpilot.selfdrive.ui.bp.onroad.tesla_style_renderer_bp import TeslaStyleRendererBP


def _brightness(color) -> int:
  return color.r + color.g + color.b


def test_dark_environment_is_materially_darker_than_light():
  assert _brightness(DARK_PALETTE.sky_top) < _brightness(LIGHT_PALETTE.sky_top) / 2
  assert _brightness(DARK_PALETTE.road_surface) < _brightness(LIGHT_PALETTE.road_surface) / 2


def test_dark_lane_colors_are_neutral_not_yellow():
  for color in (DARK_PALETTE.lane_inner, DARK_PALETTE.lane_outer):
    assert max(color.r, color.g, color.b) - min(color.r, color.g, color.b) < 16


def test_palette_variant_selection():
  assert palette_for_variant("light") is LIGHT_PALETTE
  assert palette_for_variant("dark") is DARK_PALETTE
  assert palette_for_variant(None) is LIGHT_PALETTE


def test_only_max_label_uses_longitudinal_state_color():
  for palette in (LIGHT_PALETTE, DARK_PALETTE):
    assert palette.max_active.b > palette.max_active.r
    assert abs(palette.max_inactive.r - palette.max_inactive.b) < 16


def test_centered_ego_actor_is_removed():
  assert not hasattr(TeslaStyleRendererBP, "_draw_ego_vehicle")


def test_tesla_blue_cycle_uses_sampled_reference_endpoints():
  deep = tesla_blue_cycle_color(0.0, 200)
  light = tesla_blue_cycle_color(0.5, 120)

  assert (deep.r, deep.g, deep.b, deep.a) == (*TESLA_PATH_BLUE_DEEP, 200)
  assert (light.r, light.g, light.b, light.a) == (*TESLA_PATH_BLUE_LIGHT, 120)


def test_static_and_cycling_tesla_paths_remain_blue_only():
  static = tesla_path_gradient_colors(LIGHT_PALETTE)
  cycling = tesla_path_gradient_colors(DARK_PALETTE, 0.25)

  assert len(static) == 3
  assert len(cycling) == 4
  for color in (*static, *cycling):
    assert color.b >= color.g >= color.r


def test_tesla_path_opacity_scales_without_changing_hue():
  full = tesla_path_gradient_colors(LIGHT_PALETTE)
  faded = tesla_path_gradient_colors(LIGHT_PALETTE, opacity=0.25)

  assert [(c.r, c.g, c.b) for c in faded] == [(c.r, c.g, c.b) for c in full]
  assert faded[0].a == round(full[0].a * 0.25)
