"""Shared Light/Dark palettes for BluePilot's Tesla-inspired code themes."""

from __future__ import annotations

from dataclasses import dataclass

import pyray as rl


# Sampled from two Tesla driving-visualization references. The newer filled
# path centers near #0071FE; the classic thin path centers near #4495EC.
TESLA_PATH_BLUE_DEEP = (0, 113, 254)
TESLA_PATH_BLUE_MID = (42, 121, 248)
TESLA_PATH_BLUE_LIGHT = (68, 149, 236)
TESLA_PATH_BLUE_CYCLE = (
  TESLA_PATH_BLUE_DEEP,
  TESLA_PATH_BLUE_MID,
  TESLA_PATH_BLUE_LIGHT,
  TESLA_PATH_BLUE_MID,
)


@dataclass(frozen=True)
class TeslaPalette:
  sky_top: rl.Color
  sky_horizon: rl.Color
  ground_horizon: rl.Color
  ground_near: rl.Color
  road_surface: rl.Color
  road_shoulder: rl.Color
  lane_inner: rl.Color
  lane_outer: rl.Color
  road_edge: rl.Color
  path_blue: rl.Color
  path_cyan: rl.Color
  path_disengaged: rl.Color
  blindspot: rl.Color
  set_speed: rl.Color
  max_active: rl.Color
  max_inactive: rl.Color


LIGHT_PALETTE = TeslaPalette(
  sky_top=rl.Color(213, 218, 221, 255),
  sky_horizon=rl.Color(235, 237, 238, 255),
  ground_horizon=rl.Color(211, 215, 217, 255),
  ground_near=rl.Color(190, 196, 199, 255),
  road_surface=rl.Color(169, 175, 179, 255),
  road_shoulder=rl.Color(126, 134, 139, 190),
  lane_inner=rl.Color(245, 247, 248, 210),
  lane_outer=rl.Color(222, 226, 228, 125),
  road_edge=rl.Color(103, 112, 118, 175),
  path_blue=rl.Color(*TESLA_PATH_BLUE_MID, 185),
  path_cyan=rl.Color(*TESLA_PATH_BLUE_LIGHT, 115),
  path_disengaged=rl.Color(92, 108, 119, 80),
  blindspot=rl.Color(224, 76, 66, 255),
  set_speed=rl.Color(174, 180, 184, 255),
  max_active=rl.Color(40, 145, 238, 255),
  max_inactive=rl.Color(135, 142, 147, 255),
)


DARK_PALETTE = TeslaPalette(
  sky_top=rl.Color(20, 25, 34, 255),
  sky_horizon=rl.Color(35, 43, 56, 255),
  ground_horizon=rl.Color(26, 31, 39, 255),
  ground_near=rl.Color(15, 18, 24, 255),
  road_surface=rl.Color(24, 28, 34, 255),
  road_shoulder=rl.Color(82, 91, 98, 175),
  lane_inner=rl.Color(235, 238, 239, 225),
  lane_outer=rl.Color(177, 185, 190, 140),
  road_edge=rl.Color(78, 89, 96, 170),
  path_blue=rl.Color(*TESLA_PATH_BLUE_MID, 200),
  path_cyan=rl.Color(*TESLA_PATH_BLUE_LIGHT, 125),
  path_disengaged=rl.Color(44, 62, 74, 105),
  blindspot=rl.Color(224, 76, 66, 255),
  set_speed=rl.Color(196, 201, 204, 255),
  max_active=rl.Color(72, 166, 238, 255),
  max_inactive=rl.Color(132, 140, 145, 255),
)


def palette_for_variant(variant: str | None) -> TeslaPalette:
  return DARK_PALETTE if variant == "dark" else LIGHT_PALETTE


def tesla_blue_cycle_color(phase: float, alpha: int) -> rl.Color:
  """Interpolate smoothly through Tesla's deep, mid, and light path blues."""
  wrapped = phase % 1.0
  scaled = wrapped * len(TESLA_PATH_BLUE_CYCLE)
  index = int(scaled) % len(TESLA_PATH_BLUE_CYCLE)
  fraction = scaled - int(scaled)
  start = TESLA_PATH_BLUE_CYCLE[index]
  end = TESLA_PATH_BLUE_CYCLE[(index + 1) % len(TESLA_PATH_BLUE_CYCLE)]
  channels = [round(a + (b - a) * fraction) for a, b in zip(start, end, strict=True)]
  return rl.Color(*channels, alpha)


def tesla_path_gradient_colors(palette: TeslaPalette, phase: float | None = None) -> list[rl.Color]:
  """Return static Tesla blue or an animated all-blue shade sequence."""
  if phase is None:
    far = palette.path_cyan
    return [
      palette.path_blue,
      far,
      rl.Color(far.r, far.g, far.b, 0),
    ]

  near = tesla_blue_cycle_color(phase, palette.path_blue.a)
  middle = tesla_blue_cycle_color(phase + 1.0 / 3.0, max(palette.path_cyan.a, 145))
  far = tesla_blue_cycle_color(phase + 2.0 / 3.0, palette.path_cyan.a)
  return [near, middle, far, rl.Color(far.r, far.g, far.b, 0)]
