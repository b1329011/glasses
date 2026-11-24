#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inference.py — 指標牌辨識推論腳本（ONNX / TensorRT）
============================================================
功能：
  * 讀取匯出的 ONNX 或 TensorRT engine 模型
  * 輸入單張圖片或攝影機串流，輸出偵測結果（bbox、類別、信心值）
  * 信心值門檻（conf）與 NMS IoU 門檻可透過 config/參數調整
  * 印出單張推論延遲（ms），評估是否符合即時性（目標 <200ms）

使用範例：
  # 單張圖片
  python inference.py --model best.onnx --source test.jpg --conf 0.4

  # 攝影機串流（預設鏡頭 0）
  python inference.py --model best.onnx --source 0 --show

  # 使用 TensorRT engine
  python inference.py --model best.engine --source test.jpg

備註：
  * 本腳本透過 ultralytics 的 YOLO 介面載入 .onnx / .engine，
    可自動處理前後處理與 NMS，跨格式一致。
  * 若載入 .engine 但環境無 TensorRT，會提示改用 .onnx。
"""

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit(
        "[錯誤] 找不到 ultralytics，請先執行：pip install -r requirements.txt"
    )

BASE_DIR = Path(__file__).resolve().parent

# 類別 id → 中文顯示名稱（對應 data.yaml）
CLASS_NAMES_ZH = {
    0: "往左",
    1: "往右",
    2: "往前",
    3: "捷運路線",
    4: "高鐵",
}


@dataclass
class InferConfig:
    """推論參數設定（可調整）"""
    conf: float = 0.4      # 信心值門檻：低於此值的偵測會被濾除
    iou: float = 0.45      # NMS 的 IoU 門檻：越低去除越多重疊框
    imgsz: int = 960       # 推論輸入尺寸（建議與訓練一致）
    max_det: int = 300     # 單張最多輸出的偵測框數
    device: str | None = None  # '0' 用 GPU、'cpu' 用 CPU、None 自動


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="指標牌辨識推論（ONNX / TensorRT）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model", type=str, default=str(BASE_DIR / "best.onnx"),
        help="模型路徑：.onnx 或 .engine（TensorRT）",
    )
    parser.add_argument(
        "--source", type=str, default="0",
        help="輸入來源：圖片路徑，或攝影機編號（如 0）",
    )
    parser.add_argument("--conf", type=float, default=0.4, help="信心值門檻")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS 的 IoU 門檻")
    parser.add_argument("--imgsz", type=int, default=960, help="推論輸入尺寸")
    parser.add_argument("--device", type=str, default=None, help="運算裝置：'0'/'cpu'/None(自動)")
    parser.add_argument("--show", action="store_true", help="即時顯示畫面（攝影機串流建議開啟）")
    parser.add_argument(
        "--save", type=str, default=None,
        help="（可選）將標註結果影像存到指定路徑（單張模式）",
    )
    return parser.parse_args()


def load_model(model_path: str) -> YOLO:
    """載入模型；.engine 在無 TensorRT 環境會失敗並提示替代方案"""
    path = Path(model_path)
    if not path.exists():
        raise SystemExit(f"[錯誤] 找不到模型檔：{path}（請先執行 export.py）")
    try:
        return YOLO(str(path))
    except Exception as e:  # noqa: BLE001
        if path.suffix == ".engine":
            raise SystemExit(
                f"[錯誤] 載入 TensorRT engine 失敗：{e}\n"
                "        → 此環境可能未安裝 TensorRT，請改用 ONNX 模型：\n"
                "          python inference.py --model best.onnx --source ..."
            )
        raise SystemExit(f"[錯誤] 載入模型失敗：{e}")


def print_detections(result) -> None:
    """印出單張的偵測結果（類別、信心值、bbox）"""
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        print("  （無偵測結果）")
        return
    for b in boxes:
        cls_id = int(b.cls.item())
        conf = float(b.conf.item())
        x1, y1, x2, y2 = (round(v, 1) for v in b.xyxy[0].tolist())
        name_zh = CLASS_NAMES_ZH.get(cls_id, str(cls_id))
        print(f"  類別={name_zh}({cls_id})  信心={conf:.2f}  bbox=({x1},{y1},{x2},{y2})")


def run_image(model: YOLO, cfg: InferConfig, source: str, save: str | None) -> None:
    """單張圖片推論"""
    print(f"[資訊] 單張推論：{source}")
    t0 = time.perf_counter()
    results = model.predict(
        source=source, conf=cfg.conf, iou=cfg.iou, imgsz=cfg.imgsz,
        max_det=cfg.max_det, device=cfg.device, verbose=False,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    result = results[0]

    print("[偵測結果]")
    print_detections(result)
    print(f"[效能] 單張推論延遲：{latency_ms:.1f} ms "
          f"{'（符合 <200ms 即時性）' if latency_ms < 200 else '（超過 200ms，建議優化）'}")

    if save:
        annotated = result.plot()  # 畫上 bbox 的影像
        cv2.imwrite(save, annotated)
        print(f"[資訊] 已存標註結果：{save}")


def run_stream(model: YOLO, cfg: InferConfig, cam_index: int, show: bool) -> None:
    """攝影機串流推論"""
    print(f"[資訊] 開啟攝影機串流（index={cam_index}），按 q 結束")
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        raise SystemExit(f"[錯誤] 無法開啟攝影機 index={cam_index}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[警告] 讀取影格失敗，結束串流。")
                break

            t0 = time.perf_counter()
            results = model.predict(
                source=frame, conf=cfg.conf, iou=cfg.iou, imgsz=cfg.imgsz,
                max_det=cfg.max_det, device=cfg.device, verbose=False,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            result = results[0]

            # 在畫面左上角疊上延遲/FPS 資訊
            annotated = result.plot()
            fps = 1000 / latency_ms if latency_ms > 0 else 0
            cv2.putText(
                annotated, f"{latency_ms:.0f} ms  {fps:.1f} FPS",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2,
            )

            if show:
                cv2.imshow("sign detector", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                # 無顯示時，於 console 輸出簡要資訊
                print(f"延遲={latency_ms:.0f}ms  偵測框數={len(result.boxes)}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main() -> None:
    args = parse_args()
    cfg = InferConfig(
        conf=args.conf, iou=args.iou, imgsz=args.imgsz, device=args.device
    )

    model = load_model(args.model)
    print(f"[資訊] 已載入模型：{args.model}")
    print(f"[設定] conf={cfg.conf}  iou(NMS)={cfg.iou}  imgsz={cfg.imgsz}")

    # 判斷來源是攝影機（純數字）還是圖片路徑
    if args.source.isdigit():
        run_stream(model, cfg, int(args.source), args.show)
    else:
        run_image(model, cfg, args.source, args.save)


if __name__ == "__main__":
    main()
