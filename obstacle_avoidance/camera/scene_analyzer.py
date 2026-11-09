"""
鏡頭即時分析：一張影像 →（YOLO 物體 + 深度）→ 三種危險 → 融合事件

    影像 ─┬─ YOLO ───────────────┐
          └─ 深度（單眼 AI／深度相機）→ 公尺深度 → 世界座標
                                    ├─ 前方障礙物（通道內 0.25~1.0 m 高）＋ YOLO 類別
                                    ├─ 懸空障礙物（±30°、1.0~2.0 m 高）→ hanging_obstacle_detector
                                    └─ 地面剖面 → 斷崖／樓梯／階差（ground_analyzer）
                                                       ↓ 時間平滑（N 幀確認）
                                                  fusion.fuse_hazards → Navigation Agent 事件
"""

import base64
import math
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from camera import config as cfg
from camera.depth_source import GroundCalibrator, MonocularDepthEstimator
from camera.geometry import CameraGeometry
from camera.ground_analyzer import HAZARD_ZH, analyze_ground
from camera.object_detector import ObjectDetector
from detectors import hanging_obstacle_detector
from fusion import LEVEL_RANK, fuse_hazards


class Models:
    """模型只載入一次，所有連線共用。"""

    def __init__(self, load_depth=True):
        self.detector = ObjectDetector()
        self.depth = MonocularDepthEstimator() if load_depth else None
        self.yolo_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2)

    def detect_objects(self, bgr):
        with self.yolo_lock:
            return self.detector.detect(bgr)


class HazardDebouncer:
    """最近 HAZARD_WINDOW_FRAMES 幀中至少 HAZARD_CONFIRM_FRAMES 幀達到某等級，才輸出該等級。"""

    def __init__(self):
        self.history = defaultdict(lambda: deque(maxlen=cfg.HAZARD_WINDOW_FRAMES))

    def reset(self):
        self.history.clear()

    def update(self, key, result):
        hist = self.history[key]
        hist.append(result)
        for level in ("critical", "warning"):
            hits = [r for r in hist if LEVEL_RANK[r["level"]] >= LEVEL_RANK[level]]
            if len(hits) >= cfg.HAZARD_CONFIRM_FRAMES:
                out = dict(hits[-1])
                out["level"] = level
                out["raw_level"] = result["level"]
                return out
        out = dict(result)
        out["raw_level"] = result["level"]
        out["level"] = "none"
        return out


def _direction(lateral, forward):
    ang = math.degrees(math.atan2(lateral, max(forward, 0.1)))
    return "左前方" if ang < -12 else "右前方" if ang > 12 else "正前方"


def _nearest(values):
    return float(np.percentile(values, cfg.NEAREST_PERCENTILE))


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _object_mask(objects, gh, gw):
    """YOLO 框在分析網格上的遮罩（校正地面時排除）。"""
    mask = np.zeros((gh, gw), bool)
    for o in objects:
        x1, y1, x2, y2 = o["box"]
        mask[int(y1 * gh):int(np.ceil(y2 * gh)), int(x1 * gw):int(np.ceil(x2 * gw))] = True
    return mask


class SceneAnalyzer:
    """每條連線一個（保存校正係數與時間平滑狀態），模型共用。"""

    def __init__(self, models: Models):
        self.models = models
        self.geometry = CameraGeometry(cfg.CAMERA_HEIGHT_M, cfg.CAMERA_PITCH_DEG, cfg.CAMERA_HFOV_DEG)
        self.calibrator = GroundCalibrator()
        self.debouncer = HazardDebouncer()
        self.depth_quality = "standard"
        self._smoothed = {}       # 各危險的平滑後距離
        self._prev_objects = []   # 上一幀物體（追蹤用）
        self._last = None         # 上一幀的世界座標（已知距離校正用）

    def set_geometry(self, height_m=None, pitch_deg=None, hfov_deg=None, reset=True):
        """reset=False：連續微調（例如手機陀螺儀即時更新傾角），保留校正與平滑狀態。"""
        if height_m is not None:
            self.geometry.height_m = float(height_m)
        if pitch_deg is not None:
            self.geometry.pitch_deg = float(pitch_deg)
        if hfov_deg is not None:
            self.geometry.hfov_deg = float(hfov_deg)
        if reset:
            self.calibrator.reset()
            self.debouncer.reset()
            self._smoothed.clear()
            self._prev_objects = []

    def set_depth_quality(self, quality):
        if quality in cfg.DEPTH_SHORT_SIDE:
            self.depth_quality = quality
            self.calibrator.reset()

    def geometry_dict(self):
        g = self.geometry
        return {"height_m": round(g.height_m, 3), "pitch_deg": round(g.pitch_deg, 1), "hfov_deg": g.hfov_deg}

    # ---------- 已知距離校正 ----------

    def calibrate_distance(self, x, y, true_m):
        """
        使用者點選畫面上 (x, y)（0~1 正規化）並輸入實際距離 true_m，修正整體距離比例。
        單眼模式的距離與「鏡頭高度」成正比，所以等同把鏡頭高度乘上 k = 實際 / 量測。
        點在 YOLO 物體框內時用該物體的距離，否則用該點附近的地面／物體距離。
        """
        if self._last is None:
            return {"ok": False, "message": "還沒有分析結果，請先開始偵測"}
        if self._last["mode"] != "monocular":
            return {"ok": False, "message": "深度相機已是實測距離，不需要校正"}
        boxes = [o for o in self._last["objects"] if o.get("distance") is not None
                 and o["box"][0] <= x <= o["box"][2] and o["box"][1] <= y <= o["box"][3]]
        if boxes:
            obj = min(boxes, key=lambda o: (o["box"][2] - o["box"][0]) * (o["box"][3] - o["box"][1]))
            measured, target = obj["distance"], obj["label_zh"]
        else:
            fwd = self._last["forward"]
            gh, gw = fwd.shape
            r, c = int(min(max(y, 0), 0.999) * gh), int(min(max(x, 0), 0.999) * gw)
            win = fwd[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
            win = win[np.isfinite(win)]
            if win.size < 3:
                return {"ok": False, "message": "這個位置沒有有效深度（太遠或無效），請點地面或物體"}
            measured, target = float(np.median(win)), "該點"
        if measured <= 0.1:
            return {"ok": False, "message": "量測距離無效"}
        lo, hi = cfg.DISTANCE_CALIB_RANGE
        k_raw = true_m / measured
        k = min(max(k_raw, lo), hi)
        self.geometry.height_m *= k
        self.calibrator.rescale(k)
        self._smoothed.clear()
        self._prev_objects = []
        msg = f"{target}原本量到 {measured:.2f} m，實際 {true_m:.2f} m，距離整體 ×{k:.2f}"
        if k != k_raw:
            msg += f"（比例 {k_raw:.2f} 超出合理範圍，已限制在 {lo}~{hi}，請確認點選位置）"
        return {"ok": True, "message": msg, "k": round(k, 3), "measured": round(measured, 2),
                "geometry": self.geometry_dict()}

    # ---------- 時間平滑 ----------

    def _smooth(self, key, value):
        """距離指數平滑；換目標（跳很大）或消失時重新開始。"""
        prev = self._smoothed.get(key)
        if value is None:
            self._smoothed.pop(key, None)
            return None
        if prev is not None and abs(value - prev) < cfg.DISTANCE_JUMP_RESET_M:
            value = cfg.DISTANCE_SMOOTHING * value + (1 - cfg.DISTANCE_SMOOTHING) * prev
        self._smoothed[key] = value
        return value

    def _track_objects(self, objects):
        """與上一幀同類別、框重疊的物體視為同一個，距離做平滑。"""
        a = cfg.DISTANCE_SMOOTHING
        for o in objects:
            if o.get("distance") is None:
                continue
            cands = [p for p in self._prev_objects if p["label"] == o["label"] and p.get("distance") is not None]
            best = max(cands, key=lambda p: _iou(p["box"], o["box"]), default=None)
            if best is not None and _iou(best["box"], o["box"]) >= cfg.TRACK_IOU \
                    and abs(best["distance"] - o["distance"]) < cfg.DISTANCE_JUMP_RESET_M:
                o["distance"] = round(a * o["distance"] + (1 - a) * best["distance"], 2)
                o["lateral"] = round(a * o["lateral"] + (1 - a) * best["lateral"], 2)
                o["direction"] = _direction(o["lateral"], o["distance"])
        self._prev_objects = objects

    # ---------- 主流程 ----------

    def process(self, bgr, metric_depth=None):
        """bgr：彩色影像；metric_depth：深度相機的公尺深度（None 表示用單眼 AI 估計）。"""
        t0 = time.perf_counter()
        h_img, w_img = bgr.shape[:2]
        gh = cfg.ANALYSIS_GRID[0]
        gw = int(round(gh * w_img / h_img))

        obj_future = self.models.pool.submit(self.models.detect_objects, bgr)
        if metric_depth is None:
            disp = self.models.depth.predict(bgr, cfg.DEPTH_SHORT_SIDE[self.depth_quality])
            t_depth = time.perf_counter()
            disp_g = cv2.resize(disp, (gw, gh), interpolation=cv2.INTER_AREA)
            expected_z = self.geometry.expected_ground_depth(gh, gw)
            exclude = _object_mask(obj_future.result(), gh, gw) if cfg.CALIB_EXCLUDE_OBJECTS else None
            calibrated = self.calibrator.calibrate(disp_g, expected_z, exclude)
            z = self.calibrator.to_metric(disp_g) if calibrated else None
            depth_mode, calib_status = "monocular", self.calibrator.status
        else:
            t_depth = time.perf_counter()
            z = cv2.resize(metric_depth, (gw, gh), interpolation=cv2.INTER_NEAREST)
            z = np.where(np.isfinite(z) & (z > 0.1) & (z < cfg.MAX_RANGE_M), z, np.inf).astype(np.float32)
            expected_z = self.geometry.expected_ground_depth(gh, gw)
            depth_mode, calib_status = "depth_camera", "深度相機（實測公尺深度）"
        objects = obj_future.result()
        t_models = time.perf_counter()

        result = {
            "image_size": [w_img, h_img],
            "geometry": self.geometry_dict(),
            "depth_mode": depth_mode,
            "depth_quality": self.depth_quality if depth_mode == "monocular" else None,
            "calibration": {"ok": z is not None, "status": calib_status, "rel_error": self.calibrator.rel_error},
            "objects": objects,
        }
        if z is None:
            result.update(self._empty_hazards(w_img, h_img))
            result["timing_ms"] = self._timing(t0, t_depth, t_models)
            return result

        self._analyze(z, expected_z, objects, result, (w_img, h_img))
        self._last["mode"] = depth_mode
        result["depth_image"] = self._depth_image(z)
        result["timing_ms"] = self._timing(t0, t_depth, t_models)
        return result

    # ---------- 幾何分析 ----------

    def _analyze(self, z, expected_z, objects, result, img_size):
        gh, gw = z.shape
        geo = self.geometry
        forward, lateral, height = geo.to_world(z)
        valid = np.isfinite(z) & (z < cfg.MAX_RANGE_M) & np.isfinite(forward) & np.isfinite(height)
        forward = np.where(valid, forward, np.nan)
        lateral = np.where(valid, lateral, np.nan)
        height = np.where(valid, height, np.nan)

        with np.errstate(invalid="ignore"):
            # 懸空障礙物：水平 ±30°、胸部到頭頂
            angle = np.degrees(np.arctan2(lateral, forward))
            hang_mask = valid & (forward > 0.2) & (np.abs(angle) <= cfg.HANGING_ROI_HALF_ANGLE_DEG) & \
                (height >= cfg.HANGING_MIN_HEIGHT_M) & (height <= cfg.HANGING_MAX_HEIGHT_M)
            # 前方障礙物：行進通道內、高於地面雜訊
            front_mask = valid & (forward > 0.2) & (np.abs(lateral) <= cfg.CORRIDOR_HALF_WIDTH_M) & \
                (height >= cfg.FRONT_MIN_HEIGHT_M) & (height < cfg.HANGING_MIN_HEIGHT_M)

        hang_dist = _nearest(forward[hang_mask]) if hang_mask.sum() >= cfg.MIN_OBSTACLE_PIXELS else None
        hang_dist = self._smooth("hanging", hang_dist)
        hanging = hanging_obstacle_detector.detect(hang_dist)
        hanging = {"type": "hanging_obstacle", "level": hanging["level"],
                   "distance": None if hang_dist is None else round(hang_dist, 2)}

        # YOLO 物體距離：框內離地 > 15 cm 的點取近端
        for obj in objects:
            x1, y1, x2, y2 = obj["box"]
            sl = (slice(int(y1 * gh), max(int(y2 * gh), int(y1 * gh) + 1)),
                  slice(int(x1 * gw), max(int(x2 * gw), int(x1 * gw) + 1)))
            with np.errstate(invalid="ignore"):
                m = valid[sl] & (height[sl] > 0.15)
            if m.sum() >= 4:
                f = forward[sl][m]
                lat = lateral[sl][m]
                obj["distance"] = round(float(np.percentile(f, 20)), 2)
                obj["lateral"] = round(float(np.median(lat)), 2)
                lo, hi = np.percentile(lat, [10, 90])
                obj["in_path"] = bool(lo <= cfg.CORRIDOR_HALF_WIDTH_M and hi >= -cfg.CORRIDOR_HALF_WIDTH_M)
                obj["direction"] = _direction(obj["lateral"], obj["distance"])
            else:
                obj.update({"distance": None, "lateral": None, "in_path": False, "direction": None})
        self._track_objects(objects)

        front_dist, front_lat, label = None, None, None
        if front_mask.sum() >= cfg.MIN_OBSTACLE_PIXELS:
            front_dist = _nearest(forward[front_mask])
            near = front_mask & (forward <= front_dist + 0.3)
            front_lat = float(np.nanmedian(lateral[near]))
            front_dist = self._smooth("front_depth", front_dist)
        else:
            self._smooth("front_depth", None)
        in_path = [o for o in objects if o.get("in_path") and o.get("distance") is not None]
        if in_path:
            nearest_obj = min(in_path, key=lambda o: o["distance"])
            if front_dist is None or nearest_obj["distance"] <= front_dist + 0.6:
                label = nearest_obj["label_zh"]
                if front_dist is None or nearest_obj["distance"] < front_dist:
                    front_dist, front_lat = nearest_obj["distance"], nearest_obj["lateral"]
        front = hanging_obstacle_detector.detect(front_dist)  # 共用同一組距離門檻
        front = {"type": "front_obstacle", "level": front["level"],
                 "distance": None if front_dist is None else round(front_dist, 2),
                 "label": label or "障礙物",
                 "direction": None if front_dist is None else _direction(front_lat or 0.0, front_dist)}

        # 地面：斷崖／樓梯／階差
        _, fy, _, cy = geo.intrinsics(gw, gh)
        fx, _, cx, _ = geo.intrinsics(gw, gh)
        th = math.radians(geo.pitch_deg)
        t = (np.arange(gh) - cy) / fy
        expected_forward = expected_z * (math.cos(th) - t * math.sin(th))
        u_norm = (np.arange(gw) - cx) / fx
        ground_raw = analyze_ground(np.where(valid, forward, np.nan), height, expected_z, expected_forward, u_norm)
        ground = {k: ground_raw[k] for k in ("type", "level", "distance", "height_change_m", "steps")}
        if ground["distance"] is not None:
            ground["distance"] = round(self._smooth(f"ground:{ground['type']}", ground["distance"]), 2)

        # 向上樓梯的踢面也會被當成前方障礙物；沒有 YOLO 類別且位在樓梯起點之後的，視為樓梯本身
        if ground["type"] in ("stairs_up", "step_up") and label is None and front_dist is not None \
                and front_dist >= ground["distance"] - 0.3:
            front["level"] = "none"

        self._last = {"forward": forward, "objects": objects}

        raw = {"front": front, "hanging": hanging, "ground": ground}
        confirmed = {k: self.debouncer.update(k, v) for k, v in raw.items()}
        event = fuse_hazards(list(confirmed.values()))

        result.update({
            "hazards": {k: self._public_hazard(v) for k, v in confirmed.items()},
            "event": event,
            "ground": {
                "profile": ground_raw["profile"],
                "plateaus": ground_raw["plateaus"],
                "edge": self._edge_line(ground_raw, *img_size),
                "raw_type": ground_raw["type"],
                "raw_type_zh": HAZARD_ZH.get(ground_raw["type"]),
            },
            "corridor": self._corridor(*img_size),
            "radar": self._radar_points(forward, lateral, height, valid),
        })

    @staticmethod
    def _public_hazard(h):
        h = dict(h)
        h["type_zh"] = {"front_obstacle": "前方障礙物", "hanging_obstacle": "懸空障礙物", **HAZARD_ZH}.get(h["type"])
        return h

    def _empty_hazards(self, w, h):
        none = {"level": "none", "raw_level": "none", "distance": None}
        return {
            "hazards": {"front": {**none, "type": "front_obstacle", "type_zh": "前方障礙物"},
                        "hanging": {**none, "type": "hanging_obstacle", "type_zh": "懸空障礙物"},
                        "ground": {**none, "type": None, "type_zh": None}},
            "event": fuse_hazards([]),
            "ground": {"profile": [], "plateaus": [], "edge": None, "raw_type": None, "raw_type_zh": None},
            "corridor": self._corridor(w, h),
            "radar": [],
        }

    def _corridor(self, w, h):
        """行進通道在影像上的左右兩條線（正規化座標），讓前端畫出類似 Tesla 的路徑。"""
        sides = []
        for lat in (-cfg.CORRIDOR_HALF_WIDTH_M, cfg.CORRIDOR_HALF_WIDTH_M):
            pts = []
            for f in np.arange(cfg.GROUND_MIN_FORWARD_M, cfg.GROUND_MAX_FORWARD_M + 0.01, 0.25):
                p = self.geometry.project_ground(float(f), lat, w, h)
                if p and -0.5 <= p[0] <= 1.5 and 0 <= p[1] <= 1.2:
                    pts.append([round(p[0], 4), round(p[1], 4), round(float(f), 2)])
            sides.append(pts)
        return sides

    def _edge_line(self, ground, w, h):
        if ground["type"] is None or ground["distance"] is None:
            return None
        pts = [self.geometry.project_ground(ground["distance"], lat, w, h)
               for lat in (-cfg.CORRIDOR_HALF_WIDTH_M * 1.6, cfg.CORRIDOR_HALF_WIDTH_M * 1.6)]
        if not all(pts):
            return None
        return [[round(p[0], 4), round(p[1], 4)] for p in pts]

    @staticmethod
    def _radar_points(forward, lateral, height, valid, max_points=700):
        """鳥瞰雷達用的障礙物點：[左右, 前方, 高度]。"""
        with np.errstate(invalid="ignore"):
            m = valid & (height >= cfg.FRONT_MIN_HEIGHT_M) & (height <= cfg.HANGING_MAX_HEIGHT_M) & \
                (forward > 0) & (forward < 8) & (np.abs(lateral) < 4)
        idx = np.flatnonzero(m)
        if idx.size > max_points:
            idx = idx[np.linspace(0, idx.size - 1, max_points).astype(int)]
        f, lat, hh = forward.ravel()[idx], lateral.ravel()[idx], height.ravel()[idx]
        return [[round(float(a), 2), round(float(b), 2), round(float(c), 2)] for a, b, c in zip(lat, f, hh)]

    @staticmethod
    def _depth_image(z):
        """深度圖上色（近 = 亮），JPEG base64。"""
        zz = np.where(np.isfinite(z), z, cfg.MAX_RANGE_M)
        norm = np.clip((np.log(cfg.MAX_RANGE_M) - np.log(np.clip(zz, 0.3, cfg.MAX_RANGE_M))) /
                       (np.log(cfg.MAX_RANGE_M) - np.log(0.3)), 0, 1)
        img = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        img = cv2.resize(img, (img.shape[1] * 2, img.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else None

    @staticmethod
    def _timing(t0, t_depth, t_models):
        now = time.perf_counter()
        return {"depth": round((t_depth - t0) * 1000), "models": round((t_models - t0) * 1000),
                "total": round((now - t0) * 1000)}
