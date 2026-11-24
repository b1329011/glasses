# 地下街指標牌辨識 — YOLO 訓練 Pipeline

視障輔助智慧眼鏡系統 Navigation Agent 子模組。使用 YOLOv8 即時辨識地下街方向指標牌，
最終部署於眼鏡端邊緣運算裝置，故模型需輕量化並支援低延遲推論。

## 專案結構

```
sign-detection/
├── dataset/
│   ├── images/{train,valid,test}   # 放入影像（.jpg/.png…）
│   └── labels/{train,valid,test}   # 放入 YOLO 格式標註（.txt）
├── data.yaml         # 資料集與類別設定
├── train.py          # 訓練
├── augment.py        # 離線資料增強（albumentations）
├── export.py         # 匯出 ONNX / TensorRT(INT8)
├── inference.py      # 推論（單張圖片 / 攝影機串流）
├── requirements.txt
└── README.md
```

## 類別定義

| id | 名稱 | 說明 |
|----|------|------|
| 0 | sign_direction_left | 方向：往左 |
| 1 | sign_direction_right | 方向：往右 |
| 2 | sign_direction_forward | 方向：往前 |
| 3 | sign_train_line | 捷運路線指標 |
| 4 | sign_hsr | 高鐵指標 |

## 快速開始

```bash
# 1. 安裝相依套件
pip install -r requirements.txt

# 2.（可選）資料增強：對 train 每張圖各產生 3 張增強樣本
python augment.py --split train --n-aug 3

# 3. 訓練（預設 yolov8n、imgsz=960）
python train.py --model n --epochs 100 --batch 16 --imgsz 960

# 4. 匯出部署模型（ONNX；如有 NVIDIA 環境可加 --format all --int8）
python export.py --weights best.pt --format onnx --imgsz 960

# 5. 推論
python inference.py --model best.onnx --source test.jpg --conf 0.4   # 單張
python inference.py --model best.onnx --source 0 --show              # 攝影機
```

## 標註格式

每張影像對應一個同名 `.txt`，每行一個框（YOLO 正規化座標，0~1）：

```
<class_id> <cx> <cy> <w> <h>
```

## 環境備註

- **TensorRT / INT8 量化**需 NVIDIA GPU + CUDA + TensorRT。若環境不支援，
  `export.py` 會自動捕捉例外並提示改用 **ONNX Runtime** 推論，不影響其餘流程。
- 無 GPU 時，`requirements.txt` 的 `onnxruntime-gpu` 請改裝 `onnxruntime`（CPU 版）。
- 即時性目標：單張推論 **< 200 ms**；`export.py` / `inference.py` 皆會印出延遲供評估。
