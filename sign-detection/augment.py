#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
augment.py — 地下街場景資料增強腳本
============================================================
針對地下街拍攝指標牌時常見的干擾做離線資料增強，擴充訓練樣本：
  1. 隨機亮度/對比度調整 → 模擬螢光燈反光、逆光、曝光不均
  2. 隨機模糊（動態模糊/高斯模糊）→ 模擬使用者走動造成的晃動
  3. 隨機遮擋（CoarseDropout）→ 模擬路人、柱子部分遮住指標牌

使用 albumentations 實作，並同步轉換 YOLO 格式的 bounding box 標註。

使用範例：
  # 對 train 切分每張圖各產生 3 張增強樣本
  python augment.py --split train --n-aug 3

  # 指定來源與輸出資料夾
  python augment.py --img-dir dataset/images/train \
                    --label-dir dataset/labels/train --n-aug 2

備註：
  * 標註採 YOLO 格式：每行 "class cx cy w h"（皆為 0~1 正規化座標）
  * 增強後影像/標註會以「原檔名_aug{i}」命名寫回同一資料夾（可用 --out-* 改）
"""

import argparse
import random
from pathlib import Path

import cv2

try:
    import albumentations as A
except ImportError:
    raise SystemExit(
        "[錯誤] 找不到 albumentations，請先執行：pip install -r requirements.txt"
    )

BASE_DIR = Path(__file__).resolve().parent
# 可接受的影像副檔名
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def build_transform() -> A.Compose:
    """
    建立增強流程。
    bbox_params 設定為 yolo 格式，讓 albumentations 在幾何變換時
    自動同步調整標註框；min_visibility 過濾遮擋後幾乎消失的框。
    """
    return A.Compose(
        [
            # --- 1. 亮度 / 對比度：模擬反光與逆光 ---
            A.RandomBrightnessContrast(
                brightness_limit=0.3,   # 亮度上下浮動 ±30%
                contrast_limit=0.3,     # 對比上下浮動 ±30%
                p=0.8,
            ),
            # 額外的光影變化：色調/飽和/明度，模擬不同色溫燈源
            A.HueSaturationValue(
                hue_shift_limit=10, sat_shift_limit=20, val_shift_limit=20, p=0.4
            ),

            # --- 2. 模糊：模擬移動造成的動態模糊 ---
            A.OneOf(
                [
                    A.MotionBlur(blur_limit=(3, 9), p=1.0),   # 動態模糊（最貼近走動晃動）
                    A.GaussianBlur(blur_limit=(3, 7), p=1.0),  # 一般失焦模糊
                    A.MedianBlur(blur_limit=5, p=1.0),
                ],
                p=0.5,
            ),

            # --- 3. 遮擋：模擬路人/柱子部分遮住指標牌 ---
            # CoarseDropout 會在影像上挖掉數個矩形區塊（填黑），
            # 迫使模型學習從局部特徵辨識，提升遮擋情境的強健度。
            A.CoarseDropout(
                max_holes=6, max_height=0.15, max_width=0.15,  # 以比例表示區塊大小
                min_holes=1, min_height=0.05, min_width=0.05,
                fill_value=0, p=0.5,
            ),

            # --- 附加：輕微雜訊，模擬低光源感光雜訊 ---
            A.GaussNoise(var_limit=(10.0, 50.0), p=0.2),
        ],
        bbox_params=A.BboxParams(
            format="yolo",              # YOLO 正規化 (cx, cy, w, h)
            label_fields=["class_labels"],
            min_visibility=0.3,         # 遮擋後可見度 <30% 的框會被丟棄
        ),
    )


def read_yolo_labels(label_path: Path):
    """讀取 YOLO 格式標註，回傳 (bboxes, class_labels)"""
    bboxes, class_labels = [], []
    if not label_path.exists():
        return bboxes, class_labels
    for line in label_path.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        cls, cx, cy, w, h = line.split()[:5]
        class_labels.append(int(cls))
        bboxes.append([float(cx), float(cy), float(w), float(h)])
    return bboxes, class_labels


def write_yolo_labels(label_path: Path, bboxes, class_labels) -> None:
    """把增強後的標註寫回 YOLO 格式檔案"""
    lines = [
        f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
        for cls, (cx, cy, w, h) in zip(class_labels, bboxes)
    ]
    label_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="地下街指標牌資料增強（albumentations）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--split", type=str, default="train", choices=["train", "valid", "test"],
        help="要增強的資料切分；未同時指定 --img-dir 時，用此推得預設路徑",
    )
    parser.add_argument("--img-dir", type=str, default=None, help="來源影像資料夾")
    parser.add_argument("--label-dir", type=str, default=None, help="來源標註資料夾")
    parser.add_argument("--out-img-dir", type=str, default=None, help="輸出影像資料夾（預設同來源）")
    parser.add_argument("--out-label-dir", type=str, default=None, help="輸出標註資料夾（預設同來源）")
    parser.add_argument("--n-aug", type=int, default=3, help="每張原圖產生的增強樣本數量")
    parser.add_argument("--seed", type=int, default=42, help="隨機種子，確保可重現")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    # 決定來源/輸出路徑
    img_dir = Path(args.img_dir) if args.img_dir else BASE_DIR / "dataset" / "images" / args.split
    label_dir = Path(args.label_dir) if args.label_dir else BASE_DIR / "dataset" / "labels" / args.split
    out_img_dir = Path(args.out_img_dir) if args.out_img_dir else img_dir
    out_label_dir = Path(args.out_label_dir) if args.out_label_dir else label_dir
    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_label_dir.mkdir(parents=True, exist_ok=True)

    if not img_dir.exists():
        raise SystemExit(f"[錯誤] 找不到影像資料夾：{img_dir}")

    transform = build_transform()

    # 收集所有原始影像（排除先前產生的 _aug 檔，避免重複增強）
    images = [
        p for p in sorted(img_dir.iterdir())
        if p.suffix.lower() in IMG_EXTS and "_aug" not in p.stem
    ]
    if not images:
        print(f"[警告] {img_dir} 內沒有可增強的影像，請先放入資料。")
        return

    print(f"[資訊] 來源影像數：{len(images)}，每張產生 {args.n_aug} 張增強樣本")
    total = 0
    for img_path in images:
        image = cv2.imread(str(img_path))
        if image is None:
            print(f"[警告] 無法讀取影像，略過：{img_path}")
            continue

        label_path = label_dir / f"{img_path.stem}.txt"
        bboxes, class_labels = read_yolo_labels(label_path)

        for i in range(args.n_aug):
            try:
                augmented = transform(
                    image=image, bboxes=bboxes, class_labels=class_labels
                )
            except Exception as e:  # noqa: BLE001 增強偶發失敗時略過該樣本
                print(f"[警告] 增強失敗（{img_path.name} #{i}）：{e}")
                continue

            aug_img = augmented["image"]
            aug_boxes = augmented["bboxes"]
            aug_labels = augmented["class_labels"]

            # 寫出影像與標註
            out_name = f"{img_path.stem}_aug{i}"
            cv2.imwrite(str(out_img_dir / f"{out_name}{img_path.suffix}"), aug_img)
            write_yolo_labels(out_label_dir / f"{out_name}.txt", aug_boxes, aug_labels)
            total += 1

    print(f"[完成] 共產生 {total} 張增強影像 → {out_img_dir}")


if __name__ == "__main__":
    main()
