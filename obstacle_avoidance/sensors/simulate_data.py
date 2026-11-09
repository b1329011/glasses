"""
模擬感測數據

每個情境函式回傳：
    {"timestamp": [...], "depth_min_distance": [...], "tof_distance": [...]}

    timestamp           秒（情境開始後經過的時間，取樣間隔 SAMPLE_INTERVAL_S）
    depth_min_distance  眼鏡深度相機 ROI 內最近距離（公尺）
    tof_distance        手杖 ToF 量到的距離（公分）

雜訊幅度可用 depth_noise_m / tof_noise_cm 調整；seed 固定時結果可重現。
"""

import numpy as np

from sensors.real_sensor_reader import SensorFrame, SensorReader

# ---------- 模擬參數（依需要調整） ----------
SAMPLE_INTERVAL_S = 0.2          # 取樣間隔 0.2 秒（5 Hz）
WALK_SPEED_M_PER_STEP = 0.2      # 步行速度約 1 m/s → 每筆前進 0.2 m
OPEN_SPACE_DEPTH_M = 7.5         # 前方空曠時 ROI 內最近距離（公尺）
DEPTH_MAX_RANGE_M = 10.0         # 深度相機量程上限
OBSTACLE_TRIGGER_DEPTH_M = 1.4   # 懸空障礙物在 obstacle_at_step 時的距離（略低於偵測門檻 1.5 m）
OBSTACLE_CLOSEST_M = 1.0         # 使用者停下時與障礙物的最近距離
TOF_BASELINE_CM = 100.0          # 手杖斜向地面時 ToF 正常讀數
TOF_TERRAIN_RIPPLE_CM = 2.0      # 地面起伏造成的緩慢變化幅度
TOF_MAX_RANGE_CM = 400.0         # ToF 量程上限
DEFAULT_DEPTH_NOISE_M = 0.05     # 深度相機雜訊標準差（公尺）
DEFAULT_TOF_NOISE_CM = 1.5       # ToF 雜訊標準差（公分）
DEFAULT_SEED = 42

# 手杖左右擺動時偶爾會掃到路緣/坑洞外，讀數瞬間跳高 1~2 筆（應被 CONFIRM_COUNT 濾除）
SWING_SPIKE_CM = (35.0, 70.0)
SWING_SPIKE_MAX_LEN = 2


def _rng(seed):
    return np.random.default_rng(seed)


def _timestamps(steps):
    return np.arange(steps) * SAMPLE_INTERVAL_S


def _base_depth(steps, rng, noise):
    return OPEN_SPACE_DEPTH_M + rng.normal(0, noise, steps)


def _base_tof(steps, rng, noise):
    t = np.arange(steps)
    ripple = TOF_TERRAIN_RIPPLE_CM * np.sin(2 * np.pi * t / 23.0)
    return TOF_BASELINE_CM + ripple + rng.normal(0, noise, steps)


def _add_swing_spikes(tof, rng, count, lo, hi):
    """在 [lo, hi) 範圍內隨機加入 count 個手杖擺動造成的短暫跳增。"""
    if count <= 0 or hi - lo < 4:
        return []
    spike_starts = []
    candidates = list(range(lo, hi - SWING_SPIKE_MAX_LEN))
    rng.shuffle(candidates)
    for s in candidates:
        if len(spike_starts) >= count:
            break
        # 突波之間至少間隔 6 筆，避免被當成連續異常
        if any(abs(s - p) < 6 for p in spike_starts):
            continue
        spike_starts.append(s)
    for s in spike_starts:
        length = int(rng.integers(1, SWING_SPIKE_MAX_LEN + 1))
        tof[s:s + length] += rng.uniform(*SWING_SPIKE_CM)
    return sorted(spike_starts)


def _hanging_profile(steps, obstacle_at_step, dodge_at_step=None):
    """懸空障礙物距離曲線：走近 → 降到門檻以下 → 放慢停下（或在 dodge_at_step 側身閃避）。"""
    dist = np.full(steps, np.inf)
    for i in range(steps):
        if dodge_at_step is not None and i >= dodge_at_step:
            break  # 已閃避，障礙物離開 ROI
        if i <= obstacle_at_step:
            dist[i] = OBSTACLE_TRIGGER_DEPTH_M + (obstacle_at_step - i) * WALK_SPEED_M_PER_STEP
        else:
            # 收到警示後放慢腳步，最後停在 OBSTACLE_CLOSEST_M
            dist[i] = max(OBSTACLE_CLOSEST_M, OBSTACLE_TRIGGER_DEPTH_M - (i - obstacle_at_step) * 0.08)
    return dist


def _pack(depth, tof):
    depth = np.clip(depth, 0.1, DEPTH_MAX_RANGE_M)
    tof = np.clip(tof, 1.0, TOF_MAX_RANGE_CM)
    return {
        "timestamp": [round(float(t), 2) for t in _timestamps(len(depth))],
        "depth_min_distance": [round(float(d), 3) for d in depth],
        "tof_distance": [round(float(d), 1) for d in tof],
    }


# ---------- 情境 ----------

def simulate_normal_walking(steps=50, depth_noise_m=DEFAULT_DEPTH_NOISE_M,
                            tof_noise_cm=DEFAULT_TOF_NOISE_CM, swing_spikes=2, seed=DEFAULT_SEED):
    """正常走路：深度與 ToF 讀數穩定，只有隨機雜訊與偶發的手杖擺動突波。"""
    rng = _rng(seed)
    depth = _base_depth(steps, rng, depth_noise_m)
    tof = _base_tof(steps, rng, tof_noise_cm)
    _add_swing_spikes(tof, rng, swing_spikes, 8, steps)
    return _pack(depth, tof)


def simulate_hanging_obstacle(steps=50, obstacle_at_step=30, depth_noise_m=DEFAULT_DEPTH_NOISE_M,
                              tof_noise_cm=DEFAULT_TOF_NOISE_CM, swing_spikes=1, seed=DEFAULT_SEED):
    """走近懸空招牌：ROI 最近距離逐漸變近，在 obstacle_at_step 附近降到危險門檻以下。"""
    rng = _rng(seed)
    obstacle = _hanging_profile(steps, obstacle_at_step)
    depth = np.minimum(_base_depth(steps, rng, depth_noise_m), obstacle + rng.normal(0, depth_noise_m, steps))
    tof = _base_tof(steps, rng, tof_noise_cm)
    _add_swing_spikes(tof, rng, swing_spikes, 8, steps)
    return _pack(depth, tof)


def simulate_ground_drop(steps=50, drop_at_step=35, drop_cm=60.0, depth_noise_m=DEFAULT_DEPTH_NOISE_M,
                         tof_noise_cm=DEFAULT_TOF_NOISE_CM, swing_spikes=2, seed=DEFAULT_SEED):
    """走向月台邊緣/階梯：ToF 讀數在 drop_at_step 突然跳增 drop_cm 並維持。"""
    rng = _rng(seed)
    depth = _base_depth(steps, rng, depth_noise_m)
    tof = _base_tof(steps, rng, tof_noise_cm)
    _add_swing_spikes(tof, rng, swing_spikes, 8, drop_at_step - 6)
    tof[drop_at_step:] += drop_cm
    return _pack(depth, tof)


def simulate_mixed_scenario(steps=80, obstacle_at_step=28, dodge_at_step=33, drop_at_step=64,
                            drop_cm=40.0, depth_noise_m=DEFAULT_DEPTH_NOISE_M,
                            tof_noise_cm=DEFAULT_TOF_NOISE_CM, swing_spikes=2, seed=DEFAULT_SEED):
    """混合情境：正常走路 → 遇懸空障礙物 → 閃避 → 繼續走 → 遇到階梯（地面落差）。"""
    rng = _rng(seed)
    obstacle = _hanging_profile(steps, obstacle_at_step, dodge_at_step=dodge_at_step)
    depth = np.minimum(_base_depth(steps, rng, depth_noise_m), obstacle + rng.normal(0, depth_noise_m, steps))
    tof = _base_tof(steps, rng, tof_noise_cm)
    _add_swing_spikes(tof, rng, swing_spikes, dodge_at_step + 4, drop_at_step - 6)
    tof[drop_at_step:] += drop_cm
    return _pack(depth, tof)


# ---------- 與真實硬體相同介面的模擬讀取器 ----------

class SimulatedSensorReader(SensorReader):
    """把模擬情境資料逐筆回放，介面與 SensorReader 相同，main.py 可直接替換。"""

    def __init__(self, data):
        self._data = data
        self._i = 0

    def __len__(self):
        return len(self._data["timestamp"])

    def get_depth_roi_distance(self) -> float:
        return self._data["depth_min_distance"][self._i]

    def get_tof_distance(self) -> float:
        return self._data["tof_distance"][self._i]

    def read(self) -> SensorFrame:
        if self._i >= len(self):
            raise StopIteration("模擬資料已全部讀完")
        frame = SensorFrame(
            timestamp=self._data["timestamp"][self._i],
            depth_min_distance=self.get_depth_roi_distance(),
            tof_distance=self.get_tof_distance(),
        )
        self._i += 1
        return frame
