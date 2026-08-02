"""BluePilot cropped driver-camera view for lane changes and blindspots."""

import pyray as rl

from msgq.visionipc import VisionStreamType
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.cameraview import (
  CameraView as MiciCameraView,
  FRAME_FRAGMENT_SHADER as MICI_FRAME_FRAGMENT_SHADER,
  VERTEX_SHADER as MICI_VERTEX_SHADER,
)
from openpilot.selfdrive.ui.onroad.cameraview import (
  CameraView as TiciCameraView,
  FRAME_FRAGMENT_SHADER as TICI_FRAME_FRAGMENT_SHADER,
  VERTEX_SHADER as TICI_VERTEX_SHADER,
)
from openpilot.selfdrive.ui.bp.onroad.cropped_dcam_geometry import (
  DEFAULT_WINDOW_CENTER_Y,
  DcamTrigger,
  Region,
  Side,
  ease_visibility_alpha,
  low_light_enhancement,
  panel_region,
  source_crop,
  trigger_badge_region,
)
from openpilot.system.hardware import TICI
from openpilot.system.ui.lib.application import gui_app


FADE_IN_RC = 0.12
FADE_OUT_RC = 0.22
LOW_LIGHT_RC = 0.80
VISIBLE_ALPHA_THRESHOLD = 0.01
TRIGGER_TEXTURE_SIZE = 64


def _with_crop_effects(fragment_shader: str) -> str:
  """Add BP-only opacity and adaptive shadow lift to a native camera shader."""
  output_declaration = "out vec4 fragColor;"
  assert output_declaration in fragment_shader
  shader = fragment_shader.replace(
    output_declaration,
    f"{output_declaration}\nuniform float cropAlpha;\nuniform float cropLowLight;",
    1,
  )
  body, trailing = shader.rsplit("}", 1)
  effects = """
      float cropLuma = dot(fragColor.rgb, vec3(0.299, 0.587, 0.114));
      float cropShadow = cropLowLight * (1.0 - smoothstep(0.08, 0.55, cropLuma));
      vec3 cropLifted = pow(max(fragColor.rgb, vec3(0.0)), vec3(0.72)) + vec3(0.018);
      vec3 cropColor = mix(fragColor.rgb, cropLifted, 0.72 * cropShadow);
      float cropEnhancedLuma = dot(cropColor, vec3(0.299, 0.587, 0.114));
      cropColor = mix(vec3(cropEnhancedLuma), cropColor, 1.0 + 0.06 * cropLowLight);
      fragColor = vec4(clamp(cropColor, 0.0, 1.0), fragColor.a * cropAlpha);
  """
  return f"{body}{effects}}}{trailing}"


def panel_rect(content_rect: rl.Rectangle, side: Side) -> rl.Rectangle:
  """Fill the applicable upper quadrant with a borderless integrated crop."""
  region = panel_region(
    Region(content_rect.x, content_rect.y, content_rect.width, content_rect.height),
    side,
  )
  return rl.Rectangle(region.x, region.y, region.width, region.height)


class _CroppedDcamMixin:
  """Shared renderer layered onto the native TICI/MICI camera implementations."""

  CROP_VERTEX_SHADER: str
  CROP_FRAGMENT_SHADER: str

  def __init__(self):
    super().__init__("camerad", VisionStreamType.VISION_STREAM_DRIVER)
    self._window_center_y = DEFAULT_WINDOW_CENTER_Y
    self._side_alpha = {
      "left": FirstOrderFilter(0.0, FADE_IN_RC, 1.0 / gui_app.target_fps),
      "right": FirstOrderFilter(0.0, FADE_IN_RC, 1.0 / gui_app.target_fps),
    }
    self._low_light_filter = FirstOrderFilter(0.0, LOW_LIGHT_RC, 1.0 / gui_app.target_fps)
    self._side_trigger: dict[Side, DcamTrigger | None] = {"left": None, "right": None}
    self._trigger_textures = {
      ("left", "blind_spot"): gui_app.texture(
        "icons_mici/onroad/blind_spot_left.png", TRIGGER_TEXTURE_SIZE, TRIGGER_TEXTURE_SIZE,
      ),
      ("right", "blind_spot"): gui_app.texture(
        "icons_mici/onroad/blind_spot_left.png", TRIGGER_TEXTURE_SIZE, TRIGGER_TEXTURE_SIZE, flip_x=True,
      ),
      ("left", "turn_signal"): gui_app.texture(
        "icons_mici/onroad/turn_signal_left.png", TRIGGER_TEXTURE_SIZE, TRIGGER_TEXTURE_SIZE,
      ),
      ("right", "turn_signal"): gui_app.texture(
        "icons_mici/onroad/turn_signal_left.png", TRIGGER_TEXTURE_SIZE, TRIGGER_TEXTURE_SIZE, flip_x=True,
      ),
    }

    # The native shaders output opaque camera pixels. Replace only this child
    # view's shader with an alpha-aware equivalent so the road view underneath
    # remains visible throughout the transition.
    native_shader = self.shader
    self.shader = rl.load_shader_from_memory(self.CROP_VERTEX_SHADER, self.CROP_FRAGMENT_SHADER)
    rl.unload_shader(native_shader)
    self._texture1_loc = rl.get_shader_location(self.shader, "texture1") if not TICI else -1
    if hasattr(self, "_engaged_loc"):
      self._engaged_loc = rl.get_shader_location(self.shader, "engaged")
      self._enhance_driver_loc = rl.get_shader_location(self.shader, "enhance_driver")
    self._crop_alpha_loc = rl.get_shader_location(self.shader, "cropAlpha")
    self._crop_alpha_value = rl.ffi.new("float[1]", [0.0])
    self._crop_low_light_loc = rl.get_shader_location(self.shader, "cropLowLight")
    self._crop_low_light_value = rl.ffi.new("float[1]", [0.0])

  def is_visible(self) -> bool:
    return any(alpha_filter.x > VISIBLE_ALPHA_THRESHOLD for alpha_filter in self._side_alpha.values())

  def _update_side_alpha(self, side: Side, active: bool) -> float:
    alpha_filter = self._side_alpha[side]
    alpha_filter.update_alpha(FADE_IN_RC if active else FADE_OUT_RC)
    return ease_visibility_alpha(alpha_filter.update(1.0 if active else 0.0))

  def update_frame(self) -> bool:
    """Keep the conflated driver stream warm for immediate signal activation."""
    if self.client is None or not self._ensure_connection():
      return False

    buffer = self.client.recv(timeout_ms=0)
    if buffer:
      self._texture_needs_update = True
      self.frame = buffer
    elif not self.client.is_connected():
      self.frame = None
    return self.frame is not None

  def render_crops(self, content_rect: rl.Rectangle, left_active: bool, right_active: bool,
                   calibration_rpy: tuple[float, float, float], window_center_y: float,
                   focal_length: float, light_sensor: float = -1.0,
                   left_trigger: DcamTrigger | None = None,
                   right_trigger: DcamTrigger | None = None) -> None:
    # Do not advance the transition before the first usable frame; otherwise a
    # newly connected stream could pop in after the fade has already completed.
    if not self.update_frame() or self.frame is None:
      return

    side_alpha = {
      "left": self._update_side_alpha("left", left_active),
      "right": self._update_side_alpha("right", right_active),
    }
    current_triggers = {"left": left_trigger, "right": right_trigger}
    for side in ("left", "right"):
      if current_triggers[side] is not None:
        self._side_trigger[side] = current_triggers[side]
      elif side_alpha[side] <= VISIBLE_ALPHA_THRESHOLD:
        self._side_trigger[side] = None
    if max(side_alpha.values()) <= VISIBLE_ALPHA_THRESHOLD:
      return

    # Smooth driver-model landmark changes so a marginal face detection cannot
    # make the safety view jump between frames.
    self._window_center_y = 0.95 * self._window_center_y + 0.05 * window_center_y
    low_light = self._low_light_filter.update(low_light_enhancement(light_sensor))
    for side in ("left", "right"):
      alpha = side_alpha[side]
      if alpha <= VISIBLE_ALPHA_THRESHOLD:
        continue
      destination = panel_rect(content_rect, side)
      crop = source_crop(self.frame.width, self.frame.height, destination.width, destination.height, side,
                         calibration_rpy, self._window_center_y, focal_length)
      self._draw_crop(content_rect, destination, crop, alpha, low_light, side, self._side_trigger[side])

  def _draw_crop(self, content_rect: rl.Rectangle, destination: rl.Rectangle,
                 crop: Region, alpha: float, low_light: float, side: Side,
                 trigger: DcamTrigger | None) -> None:
    rl.begin_scissor_mode(int(destination.x), int(destination.y),
                          int(destination.width), int(destination.height))
    # A negative source width mirrors only the selected side crop, matching the
    # stock full-screen driver-camera behavior.
    source = rl.Rectangle(crop.x, crop.y, -crop.width, crop.height)
    self._crop_alpha_value[0] = alpha
    self._crop_low_light_value[0] = low_light
    rl.set_shader_value(
      self.shader,
      self._crop_alpha_loc,
      self._crop_alpha_value,
      rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT,
    )
    rl.set_shader_value(
      self.shader,
      self._crop_low_light_loc,
      self._crop_low_light_value,
      rl.ShaderUniformDataType.SHADER_UNIFORM_FLOAT,
    )
    if TICI:
      self._render_egl(source, destination)
    else:
      self._render_textures(source, destination)

    # Restore the parent's scissor rectangle for all subsequent UI renderers.
    rl.begin_scissor_mode(int(content_rect.x), int(content_rect.y),
                          int(content_rect.width), int(content_rect.height))
    if trigger is not None:
      self._draw_trigger_badge(destination, side, trigger, alpha)

  def _draw_trigger_badge(self, destination: rl.Rectangle, side: Side,
                          trigger: DcamTrigger, alpha: float) -> None:
    badge = trigger_badge_region(Region(destination.x, destination.y, destination.width, destination.height))
    rl.draw_circle_v(
      rl.Vector2(badge.x + badge.width / 2, badge.y + badge.height / 2),
      badge.width / 2,
      rl.Color(0, 0, 0, int(165 * alpha)),
    )

    texture = self._trigger_textures[(side, trigger)]
    icon_size = badge.width * 0.70
    scale = icon_size / max(texture.width, texture.height)
    pos = rl.Vector2(
      badge.x + (badge.width - texture.width * scale) / 2,
      badge.y + (badge.height - texture.height * scale) / 2,
    )
    rl.draw_texture_ex(texture, pos, 0.0, scale, rl.Color(255, 255, 255, int(255 * alpha)))


class CroppedDcamViewBP(_CroppedDcamMixin, TiciCameraView):
  CROP_VERTEX_SHADER = TICI_VERTEX_SHADER
  CROP_FRAGMENT_SHADER = _with_crop_effects(TICI_FRAME_FRAGMENT_SHADER)


class MiciCroppedDcamViewBP(_CroppedDcamMixin, MiciCameraView):
  CROP_VERTEX_SHADER = MICI_VERTEX_SHADER
  CROP_FRAGMENT_SHADER = _with_crop_effects(MICI_FRAME_FRAGMENT_SHADER)
