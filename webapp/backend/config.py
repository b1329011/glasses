# -*- coding: utf-8 -*-
"""
config.py — 後端設定（路徑、資料庫連線）
============================================================
* 影像檔與 YOLO 標註 .txt 沿用 sign-detection/dataset（ultralytics 需要實體檔）
* 類別、影像、標註、訓練紀錄等 metadata 存 MySQL
* DB 連線可用環境變數 DATABASE_URL 覆寫；若未設定或連不上 MySQL，
  會自動退回 SQLite（webapp/backend/app.db），確保開箱即可執行。
"""

import os
from pathlib import Path

# --- 路徑 ---
BACKEND_DIR = Path(__file__).resolve().parent          # webapp/backend
PROJECT_ROOT = BACKEND_DIR.parents[1]                   # sign_detector 專案根
SIGN_DIR = PROJECT_ROOT / "sign-detection"              # 既有 pipeline
DATASET_DIR = SIGN_DIR / "dataset"
IMAGES_DIR = DATASET_DIR / "images" / "train"           # 標註影像預設放 train
LABELS_DIR = DATASET_DIR / "labels" / "train"
VALID_IMAGES_DIR = DATASET_DIR / "images" / "valid"
VALID_LABELS_DIR = DATASET_DIR / "labels" / "valid"
DATA_YAML = SIGN_DIR / "data.yaml"                       # 動態重生

# 訓練輸出（runs/）刻意放到「非 OneDrive 同步」的本機路徑。
# 原因：專案位於 OneDrive 底下時，訓練過程頻繁重寫 best.pt/checkpoint，
#       OneDrive 會即時鎖檔同步，導致 ultralytics 覆寫時 PermissionDenied。
# 預設用 %LOCALAPPDATA%（絕不會被 OneDrive 同步）；可用環境變數 SIGN_DETECTOR_HOME 覆寫。
LOCAL_HOME = Path(
    os.environ.get("SIGN_DETECTOR_HOME")
    or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sign_detector")
)
RUNS_DIR = LOCAL_HOME / "runs" / "train"                 # 訓練輸出（本機、非同步）
MODELS_DIR = SIGN_DIR / "models"                         # 最終 best.pt 複製回專案

# 只保留最近 N 次訓練的模型檔（runN_best.pt），更舊的自動刪除。
# 可用環境變數 KEEP_MODELS 覆寫；設為 0 表示全部保留、不清理。
KEEP_MODELS = int(os.environ.get("KEEP_MODELS", "3"))

for d in (IMAGES_DIR, LABELS_DIR, VALID_IMAGES_DIR, VALID_LABELS_DIR,
          RUNS_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- 資料庫 ---
# MySQL 範例（請自行改帳密/主機/資料庫名）：
#   mysql+pymysql://user:password@localhost:3306/sign_detector
# 未設定 DATABASE_URL 時，優先嘗試下列預設 MySQL，失敗則退回 SQLite。
DEFAULT_MYSQL_URL = os.environ.get(
    "DATABASE_URL",
    "mysql+pymysql://root:root@localhost:3306/sign_detector?charset=utf8mb4",
)
SQLITE_FALLBACK_URL = f"sqlite:///{(BACKEND_DIR / 'app.db').as_posix()}"

# 前端開發伺服器來源（CORS 白名單）
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
