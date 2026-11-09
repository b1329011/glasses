"""
相機即時偵測的參數（集中在這裡，依實際鏡頭與配戴方式調整）

座標約定：
- 相機座標：x 向右、y 向下、z 沿光軸向前（公尺）
- 世界座標：forward 水平向前、lateral 向右、height 離地高度
- 相機離地 CAMERA_HEIGHT_M，光軸向下傾 CAMERA_PITCH_DEG
"""

from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "models"
DEPTH_MODEL_PATH = MODEL_DIR / "depth_anything_v2_small.onnx"
YOLO_MODEL_PATH = MODEL_DIR / "yolov8n.pt"
# YOLO-World 自訂詞彙模型（python -m camera.build_world_model 產生）；存在時優先使用，可辨識手扶梯、樓梯、柱子等
WORLD_MODEL_PATH = MODEL_DIR / "yolo_world_obstacle.pt"
USE_WORLD_MODEL = True        # False：改用 YOLOv8n（每幀約快 15%，但認不出手扶梯、柱子、三角錐等）

# ---------- 相機幾何（前端可即時調整） ----------
CAMERA_HEIGHT_M = 1.5        # 鏡頭離地高度：眼鏡約 1.5~1.6 m；手持手機約 1.2 m
CAMERA_PITCH_DEG = 15.0      # 鏡頭往下傾的角度
CAMERA_HFOV_DEG = 70.0       # 水平視角：一般筆電/USB 鏡頭約 60~78°

# ---------- 模型 ----------
# Depth Anything 輸入短邊（像素，14 的倍數），長邊依影像比例自動決定，直式畫面不會被壓扁
DEPTH_SHORT_SIDE = {"standard": 252, "high": 364}   # 標準約 0.25 秒；高精度約 0.6 秒（CPU）
YOLO_IMGSZ = 416
YOLO_CONF = 0.35
ANALYSIS_GRID = (120, 160)      # 幾何分析時把深度圖縮到這個解析度（高, 寬）
MAX_RANGE_M = 10.0              # 超過此距離視為「很遠／看不到」

# ---------- 單眼深度 → 公尺：用畫面下方的地面自我校正 ----------
CALIB_ROWS = (0.80, 0.97)       # 取畫面高度 80%~97% 的列當作「應該是地面」的區域
CALIB_COLS = (0.30, 0.70)       # 取中間 40% 欄
CALIB_MAX_REL_ERROR = 0.25      # 擬合誤差超過 25% 視為校正失敗（下方可能不是地面）
CALIB_SMOOTHING = 0.3           # 校正係數的指數平滑（越小越穩定）

CALIB_EXCLUDE_OBJECTS = True    # 校正時排除 YOLO 偵測到的物體（例如畫面下方的腳、箱子）
CALIB_ITERATIONS = 3            # 去除離群點後重複擬合的次數

# ---------- 已知距離校正與時間平滑 ----------
DISTANCE_CALIB_RANGE = (0.5, 2.0)  # 單次校正允許的比例範圍，避免點錯造成離譜結果
DISTANCE_SMOOTHING = 0.5           # 距離指數平滑：新值權重（越小越穩、反應越慢）
DISTANCE_JUMP_RESET_M = 1.0        # 距離一次跳超過此值，視為換了目標，不平滑
TRACK_IOU = 0.3                    # 前後幀同一物體的框重疊門檻

# ---------- 行進通道與障礙物 ----------
CORRIDOR_HALF_WIDTH_M = 0.45    # 行進通道半寬（肩寬一半 + 餘裕）
HANGING_ROI_HALF_ANGLE_DEG = 30.0
HANGING_MIN_HEIGHT_M = 1.0      # 懸空障礙物高度帶：胸部
HANGING_MAX_HEIGHT_M = 2.0      #                  ~ 頭頂上方
FRONT_MIN_HEIGHT_M = 0.25       # 前方障礙物高度帶：避開地面雜訊
FRONT_MAX_HEIGHT_M = 1.0        # （更高的交給懸空障礙物）
NEAREST_PERCENTILE = 5          # 取最近距離時用低百分位數，抗深度雜訊
MIN_OBSTACLE_PIXELS = 12        # ROI 內至少幾個點才算有障礙物

# ---------- 地面：斷崖 / 樓梯 / 落差 ----------
GROUND_MIN_FORWARD_M = 0.6      # 地面剖面分析範圍
GROUND_MAX_FORWARD_M = 5.0
GROUND_BIN_M = 0.1              # 剖面每格 10 cm
GROUND_FLAT_TOL_M = 0.08        # 高度誤差 8 cm 內視為同一平台
MIN_PLATEAU_M = 0.04            # 平台前後至少 4 cm（由上往下看，樓梯踏面常只露出一小段）
STEP_MIN_M = 0.08               # 單一階梯高度範圍
STEP_MAX_M = 0.25
CLIFF_DROP_M = 0.5              # 地面比預期低 50 cm 以上 → 斷崖 / 大落差
FORWARD_GAP_M = 0.8             # 相鄰兩列地面距離跳超過此值 → 地面中斷（邊緣後方看不到）
STAIRS_MIN_STEPS = 2            # 至少 2 個同方向階差才判定樓梯
GROUND_ALERT_RANGE_M = 4.0      # 超過此距離的地面危險只顯示，不發警報
GROUND_CRITICAL_RANGE_M = 2.0   # 斷崖 / 向下樓梯在此距離內 → critical

# ---------- 時間平滑 ----------
HAZARD_CONFIRM_FRAMES = 2       # 最近 HAZARD_WINDOW_FRAMES 幀中至少幾幀偵測到才觸發
HAZARD_WINDOW_FRAMES = 3

# ---------- YOLO 類別中文名稱（COCO） ----------
LABEL_ZH = {
    "person": "行人", "bicycle": "腳踏車", "car": "汽車", "motorcycle": "機車", "bus": "公車",
    "truck": "卡車", "traffic light": "紅綠燈", "fire hydrant": "消防栓", "stop sign": "停車標誌",
    "parking meter": "停車計費器", "bench": "長椅", "dog": "狗", "cat": "貓", "chair": "椅子",
    "couch": "沙發", "potted plant": "盆栽", "bed": "床", "dining table": "桌子", "suitcase": "行李箱",
    "backpack": "背包", "umbrella": "雨傘", "handbag": "手提包", "toilet": "馬桶", "tv": "電視",
    "laptop": "筆電", "refrigerator": "冰箱", "bottle": "瓶子", "cup": "杯子", "book": "書",
    "clock": "時鐘", "vase": "花瓶", "skateboard": "滑板", "sports ball": "球", "bird": "鳥",
    "horse": "馬", "sheep": "羊", "cow": "牛", "keyboard": "鍵盤", "mouse": "滑鼠", "cell phone": "手機",
    "microwave": "微波爐", "oven": "烤箱", "sink": "水槽", "train": "火車", "boat": "船",
}

# ================= 警示規劃（camera/alert_planner.py） =================
#
# 物體分類：(類別, 中文名稱)。類別決定優先順序；不在表內的物體不會發出警示。
# 英文名稱同時是 YOLO-World 的文字提示（WORLD_CLASSES），COCO 名稱也列在這裡以支援 YOLOv8n。
OBJECT_TABLE = {
    # 車輛：會移動、撞到最嚴重
    "car": ("vehicle", "汽車"), "bus": ("vehicle", "公車"), "truck": ("vehicle", "卡車"),
    "motorcycle": ("vehicle", "機車"), "scooter": ("vehicle", "機車"), "bicycle": ("vehicle", "腳踏車"),
    "train": ("vehicle", "列車"),
    # 行人與動物
    "person": ("person", "行人"), "dog": ("person", "狗"), "stroller": ("person", "嬰兒車"),
    "wheelchair": ("person", "輪椅"),
    # 樓梯與手扶梯（語意偵測；方向與距離由深度決定）
    "escalator": ("stairs", "手扶梯"), "stairs": ("stairs", "樓梯"),
    # 固定障礙物：柱子、路障等
    "pole": ("fixed", "柱子"), "pillar": ("fixed", "柱子"), "traffic cone": ("fixed", "三角錐"),
    "barrier": ("fixed", "路障"), "bollard": ("fixed", "路樁"), "trash can": ("fixed", "垃圾桶"),
    "fire hydrant": ("fixed", "消防栓"), "tree": ("fixed", "樹"), "door": ("fixed", "門"),
    "parking meter": ("fixed", "停車計費器"), "stop sign": ("fixed", "標誌桿"),
    # 一般物品
    "bench": ("object", "長椅"), "chair": ("object", "椅子"), "table": ("object", "桌子"),
    "dining table": ("object", "桌子"), "potted plant": ("object", "盆栽"), "suitcase": ("object", "行李箱"),
    "box": ("object", "箱子"), "shopping cart": ("object", "推車"), "couch": ("object", "沙發"),
    # 懸空物（招牌）
    "signboard": ("overhead", "招牌"),
}
WORLD_CLASSES = [
    "person", "car", "bus", "truck", "motorcycle", "scooter", "bicycle", "dog", "stroller", "wheelchair",
    "escalator", "stairs", "pole", "pillar", "traffic cone", "barrier", "bollard", "trash can", "fire hydrant",
    "tree", "door", "bench", "chair", "table", "potted plant", "suitcase", "box", "shopping cart", "signboard",
]

# 類別優先順序（數字越大越優先）。同一個距離區間內，先比優先順序，再比距離。
CATEGORY_PRIORITY = {
    "drop": 100,      # 斷崖、大落差：會跌落
    "stairs": 90,     # 樓梯、手扶梯
    "vehicle": 80,    # 車輛
    "wall": 70,       # 牆壁、大面積障礙（深度幾何判斷）
    "overhead": 65,   # 頭部高度的懸空物
    "person": 60,     # 行人、動物
    "fixed": 55,      # 柱子、三角錐等固定物
    "object": 50,     # 椅子、箱子等一般物品
    "obstacle": 45,   # 深度偵測到但認不出是什麼的東西
    "step": 40,       # 單一台階、路緣
}

# 距離區間（公尺）：stop＝直接說「請停下」；near＝快接近；ahead＝提早告知方向（2.5~5.5 m）
STOP_DISTANCE_M = 1.2
NEAR_DISTANCE_M = 2.5
AHEAD_DISTANCE_M = 5.5
ZONE_HYSTERESIS_M = 0.3          # 離開某區間要多退這麼多，避免在邊界來回跳
DROP_STOP_DISTANCE_M = 2.0       # 斷崖／大落差在此距離內 → 請停下

# 方向：物體中心偏離正前方的角度
SIDE_ANGLE_DEG = 12              # 超過 ±12° 算「左前方／右前方」
AVOID_HALF_WIDTH_M = 1.0         # 左右 1 m 內的物體才可能擋路（通道兩側再加餘裕），之外不警示
WALL_MIN_WIDTH_M = 1.2           # 同一距離的障礙點橫跨超過 1.2 m、高度超過 0.8 m → 牆壁
WALL_MIN_HEIGHT_M = 0.8

# 語音政策：畫面下方會列出所有看到的東西；語音只講要緊的
SPEAK_AHEAD = False              # 2.5~5.5 m 的「提早告知」是否也唸出來（預設只列在畫面上）
MIN_SPEECH_GAP_S = 4.0           # 兩次播報至少間隔 4 秒（「請停下」與變得更近不受限）
DEDUPE_S = 10.0                  # 10 秒內唸過「同類東西、同方向」就不再唸（避免編號變動造成重複）
LIST_MAX_DISTANCE_M = 8.0        # 畫面下方清單列出的最遠距離
TRACK_MAX_AGE_S = 1.5            # 物體漏偵測 1.5 秒內再出現，仍視為同一個（編號不變）

# 語音節流：同一個危險不重複唸
REPEAT_STOP_S = 3.0              # 「請停下」持續時每 3 秒重複
REPEAT_NEAR_S = 10.0             # near 區間同一目標每 10 秒最多一次
FORGET_AFTER_S = 6.0             # 目標消失超過 6 秒，再出現才重新播報
AHEAD_REANNOUNCE_DROP_M = 1.5    # ahead 區間同一目標距離縮短 1.5 m 以上才再唸一次

