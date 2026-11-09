"""
物體偵測：優先使用 YOLO-World 自訂詞彙模型（可辨識手扶梯、樓梯、柱子、三角錐…），
沒有時退回 YOLOv8n（COCO 80 類）。距離由深度圖決定。
"""

from ultralytics import YOLO

from camera import config as cfg


class ObjectDetector:
    def __init__(self):
        if cfg.USE_WORLD_MODEL and cfg.WORLD_MODEL_PATH.exists():
            self.model = YOLO(str(cfg.WORLD_MODEL_PATH))
            self.name = "YOLO-World（自訂詞彙）"
        else:
            self.model = YOLO(str(cfg.YOLO_MODEL_PATH))
            self.name = "YOLOv8n（COCO）"

    def detect(self, bgr):
        """回傳 [{label, label_zh, category, conf, box: [x1, y1, x2, y2]（0~1 正規化）}]
        category 為 None 表示不在警示分類表內（只畫出來、不警示）。"""
        h, w = bgr.shape[:2]
        res = self.model(bgr, imgsz=cfg.YOLO_IMGSZ, conf=cfg.YOLO_CONF, verbose=False)[0]
        out = []
        for box, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
            label = res.names[int(cls)]
            category, label_zh = cfg.OBJECT_TABLE.get(label, (None, cfg.LABEL_ZH.get(label, label)))
            x1, y1, x2, y2 = box
            out.append({
                "label": label,
                "label_zh": label_zh,
                "category": category,
                "conf": round(conf, 2),
                "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
            })
        return out
