"""
產生避障用的 YOLO-World 自訂詞彙模型（一次性）：

    python -m camera.build_world_model

YOLO-World 是開放詞彙偵測模型，可以用文字指定要找的東西。這裡把類別寫死後存檔，
執行時就不需要文字編碼器（CLIP），速度和一般 YOLO 一樣用法。
需要下載 yolov8s-worldv2.pt（約 25 MB）與 CLIP 文字編碼器（約 340 MB，只在這一步用到）。
"""

import sys

from camera import config as cfg

BASE_MODEL = "yolov8s-worldv2.pt"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from ultralytics import YOLO

    classes = list(cfg.WORLD_CLASSES)
    print(f"載入 {BASE_MODEL}，設定 {len(classes)} 個類別：{', '.join(classes)}")
    model = YOLO(BASE_MODEL)
    model.set_classes(classes)
    cfg.WORLD_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(cfg.WORLD_MODEL_PATH))
    print(f"完成：{cfg.WORLD_MODEL_PATH}")


if __name__ == "__main__":
    main()
