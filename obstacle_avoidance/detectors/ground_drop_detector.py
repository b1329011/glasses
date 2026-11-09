"""
地面落差偵測（來源：手杖 ToF / 超音波）

手杖斜向前方地面時，ToF 讀數大致穩定；遇到階梯、月台邊緣等落差，
光束打到更低的地面（或打不到回波），讀數會突然變大。

判斷邏輯：
1. 用滑動視窗（最近 WINDOW_SIZE 筆正常讀數）的中位數當作地面基準（baseline）
2. 落差 = 最新讀數 - baseline
3. 落差 ≥ DROP_WARNING_CM 且「連續 CONFIRM_COUNT 筆」才觸發，
   避免手杖左右擺動時短暫掃到路緣造成誤報
4. 依落差大小分 warning / critical
5. 異常讀數不放進視窗，基準維持在原本的地面高度，警報才會持續到使用者離開落差處；
   若異常連續超過 REBASELINE_COUNT 筆，視為地形真的改變，以新讀數重建基準
"""

import math
import statistics
from collections import deque

DROP_WARNING_CM = 30      # 落差 ≥ 30 cm → warning
DROP_CRITICAL_CM = 50     # 落差 ≥ 50 cm → critical
CONFIRM_COUNT = 3         # 連續幾筆超過門檻才確認
WINDOW_SIZE = 5           # 基準滑動視窗長度
MIN_BASELINE_SAMPLES = 3  # 視窗至少幾筆才開始判斷
REBASELINE_COUNT = 20     # 異常連續幾筆後重建基準（5 Hz 下約 4 秒）
TOF_MAX_RANGE_CM = 400.0  # 量不到回波時視為此距離


class GroundDropDetector:
    """有狀態的地面落差偵測器，逐筆呼叫 update()。"""

    def __init__(self, warning_cm=DROP_WARNING_CM, critical_cm=DROP_CRITICAL_CM,
                 confirm_count=CONFIRM_COUNT, window_size=WINDOW_SIZE):
        self.warning_cm = warning_cm
        self.critical_cm = critical_cm
        self.confirm_count = confirm_count
        self.window = deque(maxlen=window_size)
        self.consecutive = 0
        self._held = deque(maxlen=window_size)  # 最近的異常讀數，重建基準時使用

    def reset(self):
        self.window.clear()
        self._held.clear()
        self.consecutive = 0

    @staticmethod
    def _sanitize(reading):
        if reading is None or math.isnan(reading) or math.isinf(reading) or reading >= TOF_MAX_RANGE_CM:
            return TOF_MAX_RANGE_CM
        return float(reading)

    def _result(self, level, reading, baseline, drop, status):
        return {
            "level": level,
            "reading": round(reading, 1),
            "baseline": None if baseline is None else round(baseline, 1),
            "drop_cm": None if drop is None else round(drop, 1),
            "consecutive": self.consecutive,
            # warmup: 基準建立中 / normal: 正常 / pending: 超過門檻但尚未確認 /
            # confirmed: 已確認落差 / rebaseline: 地形改變，重建基準
            "status": status,
        }

    def update(self, new_reading: float) -> dict:
        """逐筆餵入新讀數（公分），回傳當前判斷結果。"""
        reading = self._sanitize(new_reading)

        if len(self.window) < MIN_BASELINE_SAMPLES:
            self.window.append(reading)
            return self._result("none", reading, None, None, "warmup")

        baseline = statistics.median(self.window)
        drop = reading - baseline

        if drop < self.warning_cm:
            self.consecutive = 0
            self._held.clear()
            self.window.append(reading)
            return self._result("none", reading, baseline, drop, "normal")

        self.consecutive += 1
        self._held.append(reading)

        if self.consecutive >= REBASELINE_COUNT:
            self.window.clear()
            self.window.extend(self._held)
            self._held.clear()
            self.consecutive = 0
            return self._result("none", reading, baseline, drop, "rebaseline")

        if self.consecutive < self.confirm_count:
            return self._result("none", reading, baseline, drop, "pending")

        level = "critical" if drop >= self.critical_cm else "warning"
        return self._result(level, reading, baseline, drop, "confirmed")
