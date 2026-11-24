#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train.py — 地下街指標牌辨識 YOLO 訓練腳本
============================================================
功能：
  * 使用 ultralytics（YOLOv8）訓練方向指標牌偵測模型
  * 支援 command-line 參數：model size(n/s/m)、epochs、batch、imgsz 等
  * 訓練過程記錄 mAP@0.5、mAP@0.5:0.95、precision、recall
  * 訓練結束後自動輸出 confusion matrix 與 PR curve（ultralytics 內建）

使用範例：
  python train.py --model n --epochs 100 --batch 16 --imgsz 960
  python train.py --model s --epochs 200 --device 0

備註：
  本專案目標為部署於眼鏡端邊緣裝置，故預設使用最輕量的 yolov8n。
  指標牌在畫面中常偏小，imgsz 預設拉高到 960 以輔助小物件偵測。
"""

import argparse
import shutil
from pathlib import Path

# ultralytics 為必要套件，若未安裝直接給出清楚提示
try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit(
        "[錯誤] 找不到 ultralytics 套件，請先執行：pip install -r requirements.txt"
    )

# 本檔案所在目錄，用來組出相對於專案的預設路徑
BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    """解析 command-line 參數"""
    parser = argparse.ArgumentParser(
        description="訓練地下街指標牌 YOLO 偵測模型",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- 模型與資料 ---
    parser.add_argument(
        "--model", type=str, default="n", choices=["n", "s", "m"],
        help="模型大小：n(nano,最輕量,適合邊緣裝置) / s(small) / m(medium)",
    )
    parser.add_argument(
        "--data", type=str, default=str(BASE_DIR / "data.yaml"),
        help="資料集設定檔 data.yaml 路徑",
    )
    parser.add_argument(
        "--weights", type=str, default=None,
        help="預訓練權重路徑；未指定時使用官方 COCO 預訓練 yolov8<model>.pt",
    )

    # --- 訓練超參數 ---
    parser.add_argument("--epochs", type=int, default=100, help="訓練回合數")
    parser.add_argument("--batch", type=int, default=16, help="batch size（-1 為自動偵測）")
    parser.add_argument(
        "--imgsz", type=int, default=960,
        help="輸入影像尺寸；指標牌偏小，拉高解析度有助小物件偵測",
    )
    parser.add_argument(
        "--device", type=str, default=None,
        help="運算裝置：'0' 用第一張 GPU、'0,1' 多卡、'cpu' 用 CPU；None 為自動",
    )
    parser.add_argument("--workers", type=int, default=8, help="資料載入的 worker 數")
    parser.add_argument(
        "--patience", type=int, default=50,
        help="early stopping 容忍回合數；連續 N 回合無進步即提前停止",
    )

    # --- 輸出設定 ---
    parser.add_argument(
        "--project", type=str, default=str(BASE_DIR / "runs" / "train"),
        help="訓練結果輸出的根目錄",
    )
    parser.add_argument("--name", type=str, default="sign_detector", help="本次訓練的子資料夾名稱")
    parser.add_argument(
        "--resume", action="store_true",
        help="從上次中斷的 checkpoint 續訓（需搭配 --weights 指向 last.pt）",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 決定起始權重：未指定則用官方預訓練權重（會自動下載）
    # 例如 model=n → yolov8n.pt。以預訓練權重做 transfer learning，
    # 對小資料集收斂更快、效果更好。
    weights = args.weights or f"yolov8{args.model}.pt"
    print(f"[資訊] 使用起始權重：{weights}")
    print(f"[資訊] 資料設定檔　：{args.data}")

    # 載入模型
    model = YOLO(weights)

    # ------------------------------------------------------------
    # 開始訓練
    # ultralytics 訓練過程會自動於 console 與 results.csv 記錄
    #   metrics/mAP50、metrics/mAP50-95、metrics/precision、metrics/recall
    # 並在訓練/驗證結束後自動輸出：
    #   confusion_matrix.png、PR_curve.png、P_curve.png、R_curve.png、
    #   F1_curve.png、results.png 等圖檔至輸出資料夾。
    # ------------------------------------------------------------
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        project=args.project,
        name=args.name,
        resume=args.resume,
        plots=True,          # 產生 PR curve、confusion matrix 等圖檔
        val=True,            # 每回合在驗證集評估，記錄 mAP/precision/recall
        exist_ok=True,       # 允許覆寫同名輸出資料夾
        fliplr=0.0,          # 關閉水平翻轉：方向箭頭翻轉後語意相反，會害模型左右不分
    )

    # 輸出資料夾（ultralytics 會回傳 save_dir）
    save_dir = Path(results.save_dir)
    print("\n" + "=" * 60)
    print(f"[完成] 訓練結果輸出至：{save_dir}")

    # ------------------------------------------------------------
    # 在驗證集上做最終評估並印出關鍵指標
    # ------------------------------------------------------------
    metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device)
    box = metrics.box  # 偵測任務的框指標
    print("\n[最終驗證指標]")
    print(f"  mAP@0.5      : {box.map50:.4f}")
    print(f"  mAP@0.5:0.95 : {box.map:.4f}")
    print(f"  precision(平均): {box.mp:.4f}")
    print(f"  recall(平均)   : {box.mr:.4f}")

    # 把最佳權重複製一份到專案根，方便 export.py 直接取用
    best_pt = save_dir / "weights" / "best.pt"
    if best_pt.exists():
        dst = BASE_DIR / "best.pt"
        shutil.copy(best_pt, dst)
        print(f"\n[資訊] 已複製最佳權重至：{dst}")

    print("\n[提示] 產出的圖檔（confusion matrix / PR curve 等）位於上述輸出資料夾。")
    print("=" * 60)


if __name__ == "__main__":
    main()
