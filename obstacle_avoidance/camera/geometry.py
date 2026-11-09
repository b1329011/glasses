"""
相機幾何：像素 + 深度 ↔ 世界座標（離地高度、前方距離、左右偏移）

相機離地 h、往下傾 θ。對影像第 v 列、第 u 欄，深度 z（沿光軸，公尺）：
    X = (u - cx) / fx * z           （右）
    Y = (v - cy) / fy * z           （下）
    forward = z·cosθ - Y·sinθ
    down    = z·sinθ + Y·cosθ
    height  = h - down               （離地高度；平地 = 0）

平坦地面上 height = 0，可反推每一列「如果是地面，深度應該是多少」：
    z_ground(v) = h / (sinθ + t·cosθ),  t = (v - cy) / fy
這個預期值用來把單眼相對深度校正成公尺，也用來判斷地面是否比預期低（斷崖）或高（台階）。
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class CameraGeometry:
    height_m: float
    pitch_deg: float
    hfov_deg: float

    def intrinsics(self, width, height):
        fx = (width / 2) / math.tan(math.radians(self.hfov_deg) / 2)
        return fx, fx, (width - 1) / 2, (height - 1) / 2

    def expected_ground_depth(self, height, width):
        """每一列的地面預期深度 z（公尺）；地平線以上為 nan。回傳 shape (height,)。"""
        _, fy, _, cy = self.intrinsics(width, height)
        th = math.radians(self.pitch_deg)
        t = (np.arange(height) - cy) / fy
        denom = math.sin(th) + t * math.cos(th)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.where(denom > 1e-3, self.height_m / denom, np.nan)
        return z

    def nearest_visible_ground(self, width, height):
        """畫面最下緣看到的地面距離（公尺）；比這更近的地面在畫面外，偵測不到。"""
        _, fy, _, cy = self.intrinsics(width, height)
        angle = math.radians(self.pitch_deg) + math.atan((height - 1 - cy) / fy)
        return self.height_m / math.tan(angle) if angle > 0 else float("inf")

    def to_world(self, z):
        """深度圖 z (H, W) → forward, lateral, height 三張圖（公尺）。"""
        h_img, w_img = z.shape
        fx, fy, cx, cy = self.intrinsics(w_img, h_img)
        th = math.radians(self.pitch_deg)
        u = (np.arange(w_img) - cx) / fx
        v = (np.arange(h_img) - cy) / fy
        with np.errstate(invalid="ignore"):  # 無限遠（inf）的像素會產生 nan，呼叫端會濾掉
            lateral = z * u[None, :]
            y = z * v[:, None]
            forward = z * math.cos(th) - y * math.sin(th)
            down = z * math.sin(th) + y * math.cos(th)
        return forward, lateral, self.height_m - down

    def project_ground(self, forward, lateral, width, height):
        """地面上一點（離地 0）投影到影像，回傳正規化座標 (x, y)，看不到時回傳 None。"""
        fx, fy, cx, cy = self.intrinsics(width, height)
        th = math.radians(self.pitch_deg)
        down = self.height_m
        z = forward * math.cos(th) + down * math.sin(th)
        if z <= 0.05:
            return None
        y = -forward * math.sin(th) + down * math.cos(th)
        return (cx + fx * lateral / z) / width, (cy + fy * y / z) / height
