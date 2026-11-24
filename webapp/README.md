# 指標牌辨識操作介面（webapp）

Vue 3 前端 + FastAPI 後端的操作介面，讓你不寫程式就能完成整套流程：

1. **標註** — 上傳訓練照片、在照片上框出箭頭、指定方向類別（可新增）
2. **訓練** — 設定參數、一鍵訓練、即時看 mAP / precision / recall 與 log
3. **辨識測試** — 上傳照片或開啟攝影機即時辨識，顯示框、類別、信心值與延遲

影像檔與 YOLO 標註 `.txt` 沿用 `../sign-detection/dataset`；類別、影像、標註、
訓練紀錄等 metadata 存 **MySQL**（連不上時自動退回 SQLite，開箱即用）。

## 目錄結構

```
webapp/
├── backend/            # FastAPI 後端
│   ├── main.py         # API 路由（類別/影像/標註/訓練/推論）
│   ├── db.py           # SQLAlchemy 模型（MySQL / SQLite）
│   ├── config.py       # 路徑與 DB 連線設定
│   ├── schema.sql      # MySQL 建庫參考
│   └── requirements.txt
└── frontend/           # Vue 3 + Vite
    ├── src/views/      # AnnotateView / TrainView / DetectView
    └── ...
```

## 啟動步驟

### 1. 後端

```bash
cd webapp/backend
pip install -r requirements.txt

# （可選）連 MySQL：先建庫，再設定連線字串
#   mysql -u root -p < schema.sql
#   Windows PowerShell： $env:DATABASE_URL="mysql+pymysql://root:密碼@localhost:3306/sign_detector?charset=utf8mb4"
#   未設定或連不上時，會自動用 SQLite（backend/app.db），無須額外安裝。

uvicorn main:app --reload --port 8000
```

### 2. 前端

```bash
cd webapp/frontend
npm install
npm run dev
# 開啟 http://localhost:5173
```

Vite 已設定把 `/api` 代理到後端 `http://localhost:8000`，前後端分開跑即可。

## 使用流程

1. 進 **① 標註**：左側「＋上傳」放入照片 → 選圖 → 上方選類別 → 在圖上拖曳框出箭頭 →
   「💾 儲存標註」。需要新方向就用左側「＋新增類別」（例：`arrow_up` / 往上）。
2. 進 **② 訓練**：設定 epochs/batch/imgsz（預設 yolov8n、imgsz 960）→「🚀 開始訓練」，
   下方即時看進度與指標。完成後 `best.pt` 會存到 `sign-detection/` 與 `sign-detection/models/`。
3. 進 **③ 辨識測試**：上傳照片或「📷 開啟攝影機」，即時顯示偵測結果與延遲（目標 <200ms）。

## 注意事項

- 訓練與推論需要 `ultralytics`；未安裝時 API 會回傳明確錯誤訊息。
- 攝影機需在 `localhost` 或 HTTPS 下才可用（瀏覽器安全限制）。
- 小資料集時，後端會讓驗證集自動指回訓練集（`data.yaml` 的 `val`），方便快速試跑。
- 攝影機模式每 700ms 送一張影格辨識，避免壓垮後端；可依機器效能調整
  `frontend/src/views/DetectView.vue` 的間隔。
