"""
幾何規則驗證：用光線投射（ray casting）產生「已知答案」的合成深度圖，
走深度相機路徑（公尺深度）跑完整分析，檢查斷崖／樓梯／障礙物是否判斷正確。

執行：python -m camera.test_scenes
"""

import math
import sys

import numpy as np

from camera import config as cfg
from camera.geometry import CameraGeometry
from camera.scene_analyzer import Models, SceneAnalyzer

H, W = 240, 320


def render(height_fn, boxes=(), cam_h=cfg.CAMERA_HEIGHT_M, pitch=cfg.CAMERA_PITCH_DEG, hfov=cfg.CAMERA_HFOV_DEG):
    """
    height_fn(forward) → 該處地面高度（公尺）；boxes: [(f0, f1, lat0, lat1, h0, h1)] 立體障礙物。
    回傳每個像素的相機深度 z（公尺），打不到東西為 nan。
    """
    fx = (W / 2) / math.tan(math.radians(hfov) / 2)
    u = (np.arange(W) - (W - 1) / 2) / fx
    v = (np.arange(H) - (H - 1) / 2) / fx
    th = math.radians(pitch)
    U, V = np.meshgrid(u, v)
    d_f = math.cos(th) - V * math.sin(th)      # z = 1 時的 forward
    d_down = math.sin(th) + V * math.cos(th)
    z = np.full((H, W), np.nan)
    hit = np.zeros((H, W), bool)
    for s in np.arange(0.05, cfg.MAX_RANGE_M, 0.01):
        f, lat, hh = s * d_f, s * U, cam_h - s * d_down
        new = ~hit & (hh <= height_fn(f))
        for f0, f1, l0, l1, h0, h1 in boxes:
            new |= ~hit & (f >= f0) & (f <= f1) & (lat >= l0) & (lat <= l1) & (hh >= h0) & (hh <= h1)
        z[new] = s
        hit |= new
    return z.astype(np.float32)


def flat(f):
    return np.zeros_like(f)


def cliff_at(d, drop=1.2):
    return lambda f: np.where(f < d, 0.0, -drop)


def stairs_down_at(d, rise=0.17, tread=0.30, n=6):
    return lambda f: np.where(f < d, 0.0, -rise * np.minimum(np.floor((f - d) / tread) + 1, n))


def stairs_up_at(d, rise=0.17, tread=0.30, n=6):
    return lambda f: np.where(f < d, 0.0, rise * np.minimum(np.floor((f - d) / tread) + 1, n))


def curb_down_at(d, drop=0.15):
    return lambda f: np.where(f < d, 0.0, -drop)


CASES = [
    # 名稱, 地面, 立體障礙物, 預期 (hazard key, type, level)
    ("平地，無障礙", flat, [], [("ground", None, "none"), ("front", "front_obstacle", "none"), ("hanging", "hanging_obstacle", "none")]),
    ("1.9 m 處斷崖（落差 1.2 m）", cliff_at(1.9), [], [("ground", "cliff", "critical")]),
    ("3.0 m 處斷崖", cliff_at(3.0), [], [("ground", "cliff", "warning")]),
    ("1.9 m 處向下樓梯", stairs_down_at(1.9), [], [("ground", "stairs_down", "critical")]),
    ("2.0 m 處向上樓梯", stairs_up_at(2.0), [], [("ground", "stairs_up", "warning")]),
    ("2.0 m 處路緣下降 15 cm", curb_down_at(2.0), [], [("ground", "step_down", "warning")]),
    ("1.2 m 處箱子（高 0.8 m）", flat, [(1.2, 1.6, -0.3, 0.3, 0.0, 0.8)], [("front", "front_obstacle", "critical")]),
    ("2.5 m 處箱子", flat, [(2.5, 2.9, -0.3, 0.3, 0.0, 0.8)], [("front", "front_obstacle", "warning")]),
    ("1.3 m 處懸空招牌（1.4~1.9 m 高）", flat, [(1.3, 1.4, -0.5, 0.5, 1.4, 1.9)], [("hanging", "hanging_obstacle", "critical"), ("front", "front_obstacle", "none")]),
    ("箱子在通道外（右側 1.5 m）", flat, [(1.5, 2.0, 1.2, 1.8, 0.0, 0.8)], [("front", "front_obstacle", "none")]),
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    models = Models(load_depth=False)
    blank = np.full((H, W, 3), 128, np.uint8)
    failed = 0
    near = CameraGeometry(cfg.CAMERA_HEIGHT_M, cfg.CAMERA_PITCH_DEG, cfg.CAMERA_HFOV_DEG).nearest_visible_ground(W, H)
    print(f"相機高度 {cfg.CAMERA_HEIGHT_M} m、俯角 {cfg.CAMERA_PITCH_DEG}°、水平視角 {cfg.CAMERA_HFOV_DEG}°，"
          f"最近可見地面 {near:.2f} m\n")
    for name, ground, boxes, expects in CASES:
        z = render(ground, boxes)
        analyzer = SceneAnalyzer(models)
        for _ in range(cfg.HAZARD_CONFIRM_FRAMES):
            res = analyzer.process(blank, metric_depth=z)
        ok_all = True
        details = []
        for key, typ, level in expects:
            hz = res["hazards"][key]
            ok = hz["level"] == level and (level == "none" and key == "ground" or hz["type"] == typ)
            ok_all &= ok
            dist = f"{hz['distance']} m" if hz.get("distance") is not None else "—"
            details.append(f"{key}: {hz.get('type_zh') or '無'} / {hz['level']} / {dist}"
                           + ("" if ok else f"  ← 預期 {typ} / {level}"))
        # 融合後的最終事件類型也要正確（例如箱子不能被說成斷崖）
        top = next((t for _, t, lv in expects if lv != "none"), None)
        if res["event"]["data"]["obstacle_type"] != top:
            ok_all = False
            details.append(f"融合事件：{res['event']['data']['obstacle_type']}  ← 預期 {top}")
        failed += not ok_all
        print(f"{'[通過]' if ok_all else '[失敗]'} {name}")
        for d in details:
            print(f"        {d}")
        if res["event"]["action_required"]["audio_message"]:
            print(f"        語音：{res['event']['action_required']['audio_message']}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
