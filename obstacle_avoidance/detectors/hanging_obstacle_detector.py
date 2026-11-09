"""
懸空障礙物偵測（來源：眼鏡深度相機）

ROI 定義（實際部署時）：
- 水平：相機光軸左右 ±30°，涵蓋使用者行進方向的身體寬度與少量餘裕。
- 垂直：對應「胸部到頭頂上方約 10 cm」的高度帶。這段高度是白手杖掃不到、
  但頭部與上半身會撞到的區域（招牌、樹枝、半開的鐵捲門）。
- 換算方式：依相機安裝高度 h_cam、俯角 θ 與相機垂直 FOV，
  算出距離 d 處高度 [h_chest, h_head_top] 對應的像素列範圍：
      row(y) = cy + fy * tan(θ - atan((y - h_cam) / d))
  因為不同距離對應的像素列不同，實作時可對每個像素反投影成 3D 點，
  只保留高度落在 [h_chest, h_head_top] 且水平角在 ±30° 內的點。
- 地面與腳邊障礙物交給手杖處理，所以 ROI 刻意排除胸部以下。

本模組假設輸入已經是 ROI 內處理過的最近距離值（公尺），只做門檻判斷。
"""

import math

CRITICAL_THRESHOLD_M = 1.5   # 小於此距離 → critical（約 1.5 秒內會撞到）
WARNING_THRESHOLD_M = 3.0    # 介於兩門檻之間 → warning


def detect(distance: float) -> dict:
    """依 ROI 內最近距離判斷懸空障礙物危險等級。"""
    if distance is None or (isinstance(distance, float) and math.isnan(distance)) or distance <= 0:
        # 無有效深度（例如 ROI 全是無效像素），不發出警報
        return {"level": "none", "distance": None}
    if distance < CRITICAL_THRESHOLD_M:
        level = "critical"
    elif distance <= WARNING_THRESHOLD_M:
        level = "warning"
    else:
        level = "none"
    return {"level": level, "distance": distance}
