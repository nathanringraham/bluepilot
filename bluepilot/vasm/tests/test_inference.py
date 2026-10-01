import numpy as np

from openpilot.bluepilot.vasm.inference import VASMInference, detection_confidence


def test_detection_confidence_filters_non_vehicle_class():
  output = np.array([[[0, 0, 0, 0, 0.4, 0], [0, 0, 0, 0, 0.9, 2]]], dtype=np.float32)
  assert detection_confidence(output) == np.float32(0.4)


def test_load_config_accepts_one_or_both_sides(tmp_path):
  inference = VASMInference(tmp_path / "missing.onnx")
  assert inference.load_config({"width": 100, "height": 50, "poly_left": [[0, 0], [20, 0], [0, 20]], "poly_right": []})
  assert inference.bboxes["left_raw"] is not None
  assert inference.bboxes["right_raw"] is None


def test_load_config_rejects_missing_geometry(tmp_path):
  inference = VASMInference(tmp_path / "missing.onnx")
  assert not inference.load_config({"width": 0, "height": 0, "poly_left": [], "poly_right": []})


def test_update_preprocesses_nv12_and_activates_detection(tmp_path):
  class FakeSession:
    def __init__(self):
      self.input_shape = None

    def run(self, output_names, inputs):
      self.input_shape = inputs["images"].shape
      return [np.array([[[0, 0, 0, 0, 0.9, 0]]], dtype=np.float32)]

  inference = VASMInference(tmp_path / "missing.onnx")
  inference.session = FakeSession()
  inference.input_name = "images"
  inference.output_name = "output0"
  inference.valid = True
  assert inference.load_config({
    "width": 64,
    "height": 48,
    "poly_left": [[0, 0], [62, 0], [62, 46], [0, 46]],
    "poly_right": [],
  })

  nv12 = np.zeros((48 * 3 // 2, 64), dtype=np.uint8)
  left_active, right_active = inference.update(nv12, 64, 48, 0.5, 0.85, 0.2, "left")
  assert inference.session.input_shape == (1, 3, 256, 352)
  assert left_active
  assert not right_active
