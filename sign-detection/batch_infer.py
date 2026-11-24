#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_infer.py — 批次辨識，輸出各箭頭統計方便手動紀錄
============================================================
一次跑一整個資料夾的照片，對每張做箭頭偵測，並輸出：
  1. 每張照片的偵測結果（類別 + 信心值）
  2. 各類別統計：偵測數量、平均/最低/最高信心值
  3. 兩個 CSV：逐一偵測明細 + 各類別統計
  4. （可選）把畫好框的圖存到輸出資料夾，方便你逐張核對「對/錯」

使用範例：
  # 對某資料夾所有照片批次辨識，存標註圖 + CSV
  python batch_infer.py --source my_test_photos --save

  # 指定模型、信心門檻、輸入尺寸
  python batch_infer.py --source dataset/images/test --model best.pt --conf 0.25 --imgsz 960 --save

備註：
  * 「平均信心值」與「偵測數量」是自動算的。
  * 嚴格的 precision/recall（需要標準答案）請改用 evaluate（model.val）；
    這支腳本是給你「跑一批照片、看結果、手動記對錯」用的。
"""

import argparse
import csv
import statistics
from pathlib import Path

try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit("[錯誤] 找不到 ultralytics，請先安裝：pip install ultralytics")

BASE_DIR = Path(__file__).resolve().parent
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# 類別 id → 中文名（對應 data.yaml）
NAMES = {
    0: "往左", 1: "往右", 2: "往前", 3: "迴轉", 4: "左前",
    5: "右前", 6: "往下", 7: "左下", 8: "右下",
}


def parse_args():
    p = argparse.ArgumentParser(
        description="批次辨識並輸出各箭頭統計",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source", type=str, required=True,
                   help="要辨識的照片資料夾")
    p.add_argument("--model", type=str, default=str(BASE_DIR / "best.pt"),
                   help="模型權重(.pt)")
    p.add_argument("--conf", type=float, default=0.25, help="信心值門檻")
    p.add_argument("--iou", type=float, default=0.45, help="NMS IoU 門檻")
    p.add_argument("--imgsz", type=int, default=960, help="推論影像尺寸")
    p.add_argument("--save", action="store_true",
                   help="另存畫好框的圖到輸出資料夾（方便逐張核對）")
    p.add_argument("--out", type=str, default=str(BASE_DIR / "batch_results"),
                   help="輸出資料夾（CSV 與標註圖）")
    return p.parse_args()


def main():
    args = parse_args()

    src = Path(args.source)
    if not src.exists():
        raise SystemExit(f"[錯誤] 找不到資料夾：{src}")
    images = [p for p in sorted(src.rglob("*")) if p.suffix.lower() in IMG_EXTS]
    if not images:
        raise SystemExit(f"[錯誤] {src} 內沒有影像")

    weights = Path(args.model)
    if not weights.exists():
        raise SystemExit(f"[錯誤] 找不到模型：{weights}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[資訊] 模型：{weights.name}　照片數：{len(images)}　conf={args.conf} imgsz={args.imgsz}")
    model = YOLO(str(weights))

    # 收集每類的信心值，用來算平均
    per_class_confs = {c: [] for c in NAMES}
    detail_rows = []   # 逐一偵測明細
    total_dets = 0

    for img_path in images:
        r = model.predict(source=str(img_path), conf=args.conf, iou=args.iou,
                          imgsz=args.imgsz, verbose=False)[0]
        line_items = []
        for b in r.boxes:
            cls_id = int(b.cls.item())
            conf = float(b.conf.item())
            x1, y1, x2, y2 = [round(float(v), 1) for v in b.xyxy[0].tolist()]
            name = NAMES.get(cls_id, str(cls_id))
            per_class_confs.setdefault(cls_id, []).append(conf)
            detail_rows.append([img_path.name, cls_id, name, round(conf, 3),
                                x1, y1, x2, y2])
            line_items.append(f"{name}={conf:.2f}")
            total_dets += 1

        print(f"  {img_path.name:<40s} {'、'.join(line_items) if line_items else '（無偵測）'}")

        # 存標註圖供人工核對
        if args.save:
            annotated = r.plot()
            import cv2
            cv2.imwrite(str(out_dir / f"det_{img_path.name}"), annotated)

    # ---- 各類別統計 ----
    print("\n" + "=" * 58)
    print(f"{'類別':<8}{'偵測數':>8}{'平均信心':>12}{'最低':>10}{'最高':>10}")
    print("-" * 58)
    summary_rows = []
    for cls_id in sorted(NAMES):
        confs = per_class_confs.get(cls_id, [])
        name = NAMES[cls_id]
        if confs:
            avg = statistics.mean(confs)
            print(f"{name:<8}{len(confs):>8}{avg:>12.3f}{min(confs):>10.3f}{max(confs):>10.3f}")
            summary_rows.append([cls_id, name, len(confs), round(avg, 3),
                                 round(min(confs), 3), round(max(confs), 3)])
        else:
            print(f"{name:<8}{0:>8}{'-':>12}{'-':>10}{'-':>10}")
            summary_rows.append([cls_id, name, 0, "", "", ""])
    print("-" * 58)
    print(f"{'總計':<8}{total_dets:>8}")
    print("=" * 58)

    # ---- 匯出 CSV ----
    detail_csv = out_dir / "detections.csv"
    with open(detail_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["image", "class_id", "class_name", "conf", "x1", "y1", "x2", "y2"])
        w.writerows(detail_rows)

    summary_csv = out_dir / "summary_by_class.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["class_id", "class_name", "num_detections",
                    "avg_conf", "min_conf", "max_conf"])
        w.writerows(summary_rows)

    print(f"\n[完成] 明細 CSV：{detail_csv}")
    print(f"[完成] 各類別統計 CSV：{summary_csv}")
    if args.save:
        print(f"[完成] 標註圖：{out_dir}（檔名 det_*，可逐張核對對/錯）")
    print("\n提示：CSV 用 Excel 開(已含 UTF-8 BOM，中文不亂碼)，方便你手動紀錄。")


if __name__ == "__main__":
    main()
