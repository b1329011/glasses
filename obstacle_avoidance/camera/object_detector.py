"""YOLOv8 物體偵測（COCO 80 類），提供障礙物的類別名稱。距離由深度圖決定。"""

from ultralytics import YOLO

from camera import config as cfg


class ObjectDetector:
    def __init__(self, model_path=cfg.YOLO_MODEL_PATH):
        self.model = YOLO(str(model_path))

    def detect(self, bgr):
        """回傳 [{label, label_zh, conf, box: [x1, y1, x2, y2]（0~1 正規化）}]"""
        h, w = bgr.shape[:2]
        res = self.model(bgr, imgsz=cfg.YOLO_IMGSZ, conf=cfg.YOLO_CONF, verbose=False)[0]
        out = []
        for box, cls, conf in zip(res.boxes.xyxy.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
            label = res.names[int(cls)]
            x1, y1, x2, y2 = box
            out.append({
                "label": label,
                "label_zh": cfg.LABEL_ZH.get(label, label),
                "conf": round(conf, 2),
                "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
            })
        return out
