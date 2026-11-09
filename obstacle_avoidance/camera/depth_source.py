"""
深度來源

1. MonocularDepthEstimator：一般鏡頭 + Depth Anything V2（ONNX），輸出相對深度，
   再由 GroundCalibrator 用「畫面下方是地面」的幾何關係換算成公尺（類似 Tesla 純視覺做法）。
2. RealSenseSource：Intel RealSense 深度相機，直接輸出公尺深度，不需要校正。
   需要 `pip install pyrealsense2` 並接上相機；其他深度相機（OAK-D 等）照同樣介面實作 read() 即可。
"""

import cv2
import numpy as np
import onnxruntime as ort

from camera import config as cfg

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class MonocularDepthEstimator:
    """Depth Anything V2 Small：輸入 BGR 影像，輸出相對視差（數值越大越近）。"""

    def __init__(self, model_path=cfg.DEPTH_MODEL_PATH):
        if not model_path.exists():
            raise FileNotFoundError(f"找不到深度模型 {model_path}，請先執行 python setup_assets.py 下載")
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    @staticmethod
    def input_size(img_h, img_w, short_side):
        """依影像比例決定模型輸入大小（14 的倍數），避免直式畫面被壓成橫式而扭曲深度。"""
        scale = short_side / min(img_h, img_w)
        return (max(14, round(img_h * scale / 14) * 14), max(14, round(img_w * scale / 14) * 14))

    def predict(self, bgr, short_side=cfg.DEPTH_SHORT_SIDE["standard"]):
        h, w = self.input_size(*bgr.shape[:2], short_side)
        rgb = cv2.cvtColor(cv2.resize(bgr, (w, h), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
        x = (rgb.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        x = x.transpose(2, 0, 1)[None]
        return self.session.run(None, {self.input_name: x})[0][0]


class GroundCalibrator:
    """
    把相對視差 D 換成公尺深度 z。Depth Anything 的輸出與 1/z 成仿射關係：
        1/z ≈ a·D + c
    取畫面下方（CALIB_ROWS × CALIB_COLS）當作地面，用幾何算出的地面預期深度擬合 a、c。
    擬合失敗（下方不是地面，例如貼近牆壁）時沿用上一次的係數。
    """

    def __init__(self):
        self.coef = None   # (a, c)
        self.status = "尚未校正"
        self.rel_error = None

    def reset(self):
        self.coef = None
        self.status = "尚未校正"

    def calibrate(self, disparity, expected_z, exclude=None):
        """exclude：不該當作地面的像素（例如 YOLO 框到的物體），True 表示排除。"""
        h, w = disparity.shape
        r0, r1 = int(h * cfg.CALIB_ROWS[0]), int(h * cfg.CALIB_ROWS[1])
        c0, c1 = int(w * cfg.CALIB_COLS[0]), int(w * cfg.CALIB_COLS[1])
        d = disparity[r0:r1, c0:c1]
        z = np.broadcast_to(expected_z[r0:r1, None], d.shape)
        ok = np.isfinite(z) & (z > 0) & (z < 8)
        if exclude is not None and (ok & ~exclude[r0:r1, c0:c1]).sum() > 50:
            ok &= ~exclude[r0:r1, c0:c1]
        fit = None
        if ok.sum() > 50:
            dd, inv = d[ok].ravel(), 1.0 / z[ok].ravel()
            A = np.stack([dd, np.ones_like(dd)], axis=1)
            keep = np.ones(len(dd), bool)
            sol = np.linalg.lstsq(A, inv, rcond=None)[0]
            # 反覆剔除離群點（地上的雜物、陰影）後重新擬合
            for _ in range(cfg.CALIB_ITERATIONS - 1):
                res = np.abs(A @ sol - inv)
                new_keep = res <= 2.5 * np.median(res[keep]) + 1e-6
                if new_keep.sum() < 30 or np.array_equal(new_keep, keep):
                    break
                keep = new_keep
                sol = np.linalg.lstsq(A[keep], inv[keep], rcond=None)[0]
            a, c = sol
            A, inv = A[keep], inv[keep]
            pred = A @ sol
            if a > 0 and np.all(pred > 0):
                self.rel_error = float(np.median(np.abs(1.0 / pred - 1.0 / inv) * inv))
                if self.rel_error <= cfg.CALIB_MAX_REL_ERROR:
                    fit = (float(a), float(c))

        if fit is not None:
            if self.coef is None:
                self.coef = fit
            else:
                k = 1 - cfg.CALIB_SMOOTHING
                self.coef = (k * fit[0] + (1 - k) * self.coef[0], k * fit[1] + (1 - k) * self.coef[1])
            self.status = "地面校正正常"
        elif self.coef is not None:
            self.status = "畫面下方不像地面，沿用上一次校正"
        else:
            self.status = "校正失敗：請讓鏡頭下方拍到地面"
        return self.coef is not None

    def rescale(self, k):
        """距離整體乘上 k（已知距離校正用）：1/z = (a·D + c) / k。"""
        if self.coef is not None:
            self.coef = (self.coef[0] / k, self.coef[1] / k)

    def to_metric(self, disparity):
        a, c = self.coef
        inv = a * disparity + c
        with np.errstate(divide="ignore"):
            z = np.where(inv > 1.0 / cfg.MAX_RANGE_M, 1.0 / inv, np.inf)
        return z.astype(np.float32)


class RealSenseSource:
    """Intel RealSense 深度相機（彩色 + 對齊後的公尺深度）。"""

    def __init__(self, width=640, height=480, fps=15):
        try:
            import pyrealsense2 as rs
        except ImportError as e:
            raise RuntimeError("未安裝 pyrealsense2（pip install pyrealsense2）") from e
        self.rs = rs
        self.pipeline = rs.pipeline()
        conf = rs.config()
        conf.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        conf.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        try:
            profile = self.pipeline.start(conf)
        except RuntimeError as e:
            raise RuntimeError(f"找不到 RealSense 深度相機：{e}") from e
        self.depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
        self.align = rs.align(rs.stream.color)
        intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        self.hfov_deg = float(np.degrees(2 * np.arctan(intr.width / (2 * intr.fx))))

    def read(self):
        """回傳 (bgr, depth_m)；深度 0（無效）轉成 nan。"""
        frames = self.align.process(self.pipeline.wait_for_frames())
        color = np.asanyarray(frames.get_color_frame().get_data())
        depth = np.asanyarray(frames.get_depth_frame().get_data()).astype(np.float32) * self.depth_scale
        depth[depth <= 0] = np.nan
        return color, depth

    def close(self):
        self.pipeline.stop()
