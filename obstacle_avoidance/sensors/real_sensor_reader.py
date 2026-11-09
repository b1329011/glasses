"""
真實感測器讀取介面（擴充點）

目前尚無實體裝置，這個檔案只定義「真實硬體需要提供哪些方法」。
之後串接硬體時，只需要實作 SensorReader 的兩個方法：

    get_depth_roi_distance()  眼鏡深度相機 ROI 內的最近距離（公尺）
    get_tof_distance()        手杖 ToF / 超音波量到的距離（公分）

main.py 的處理流程只依賴 read() 回傳的 SensorFrame，
模擬資料（sensors/simulate_data.py 的 SimulatedSensorReader）也實作同一個介面，
所以從模擬切換到真實硬體時，main.py 的呼叫方式不需要改動：

    reader = SimulatedSensorReader(data)   # 現在：模擬
    reader = SensorReader()                # 之後：真實硬體（實作完成後）
    frame = reader.read()
"""

import time
from dataclasses import dataclass


@dataclass
class SensorFrame:
    """單一時間點的感測讀數。"""

    timestamp: float            # 秒（模擬資料為情境開始後的秒數；真實硬體為 epoch 秒）
    depth_min_distance: float   # 深度相機 ROI 內最近距離（公尺）
    tof_distance: float         # 手杖 ToF 距離（公分）


class SensorReader:
    """真實硬體讀取器。串接硬體時，在子類別或直接在此實作下列兩個方法。"""

    def get_depth_roi_distance(self) -> float:
        """從深度相機讀取ROI內最近距離，實際硬體串接時在此實作

        實作提示：
        1. 取得一張 depth frame（例如 RealSense / OAK-D SDK）
        2. 依 detectors/hanging_obstacle_detector.py 註解的 ROI 定義裁切
        3. 排除無效像素（深度 = 0）後取最小值或低百分位數（抗雜訊），單位換成公尺
        """
        raise NotImplementedError("尚未串接真實硬體，請改用 simulate_data.py")

    def get_tof_distance(self) -> float:
        """從手杖ToF讀取距離，實際硬體串接時在此實作

        實作提示：
        1. 透過 BLE / 序列埠接收手杖端的 ToF 讀數（例如 VL53L1X 回傳 mm）
        2. 換算成公分回傳；量不到回波（超出量程）時回傳 float("inf") 或 None，
           偵測器會視為超出量程的大落差
        """
        raise NotImplementedError("尚未串接真實硬體，請改用 simulate_data.py")

    def read(self) -> SensorFrame:
        """讀取一筆同步的感測資料。main.py 只呼叫這個方法。"""
        return SensorFrame(
            timestamp=time.time(),
            depth_min_distance=self.get_depth_roi_distance(),
            tof_distance=self.get_tof_distance(),
        )
