# glasses

視障輔助智慧眼鏡：室外導航（Agent C）＋ 地下街指標牌辨識（sign_detector）。

```
glasses/
├── agent_c_navigation/   # 室外導航：語音指令、路線規劃、逐步導航（Flask，port 8080）
├── sign-detection/       # 指標牌 YOLO 訓練 / 匯出 / 推論 pipeline（best.pt）
└── webapp/               # 指標牌標註/訓練/辨識介面 + 導航 API（FastAPI，port 8000）
```

## 兩者怎麼串起來：台北車站指標導引

導航偵測到使用者「在台北車站」時，依路線決定要找哪些指標，把鏡頭影格送給
sign_detector 的 `/api/navigate` 辨識方向，再用語音播報（例如「淡水信義線在往左的方向」）。

**什麼時候算在台北車站**：路線上的站名是台北車站，或 GPS 進入台北車站周圍 350 公尺內，
而且這時要進出的是捷運、台鐵或高鐵。在台北車站站牌搭公車不會觸發。

| 情境 | 依序要找的指標 |
|---|---|
| 在台北車站搭淡水信義線往淡水 | `淡水信義線` → `往淡水` |
| 板南線到台北車站，站內轉淡水信義線往象山 | `淡水信義線` → `往象山` |
| 轉機場捷運 | `機場捷運` → `往機場` |
| 轉台鐵／高鐵 | `台鐵` → `往樹林`／`高鐵` → `往左營` |
| 在台北車站下車要出站（或出站轉公車） | `出口` |

- 區間車與英文終點站會先換成指標牌上寫的方向，例如 `北投`→`往淡水`、`Dingpu`→`往頂埔`、`亞東醫院`→`往頂埔`
- 連續 2 張影格方向一致才播報，同一句 8 秒內不重複；沒看到目標時，會提示使用者轉頭找
- 使用者走到目前的指標後說「到了」就換下一個指標；最後一個走完，會接著進入「確認上車」或「走向目的地」

程式位置：
- `agent_c_navigation/modules/station_guide.py`：判斷是否在台北車站、規劃要找的指標、穩定播報
- `agent_c_navigation/modules/sign_client.py`：呼叫指標辨識（合併後端直接呼叫函式；單獨啟動時走 HTTP）
- `agent_c_navigation/modules/routing.py`：`transit_legs` 支援多段搭乘（轉車）
- `agent_c_navigation/nav_api.py`：導航 API（`station_guidance` 階段、`/sign_frame`、`/position`），掛在合併後端的 `/api/agent-c`

## 執行

前端和後端都已經合成一個：

- **後端**：`webapp/backend`（FastAPI，port 8000）＝ 指標辨識 API ＋ Agent C 語音導航 API（`/api/agent-c/*`，程式在 `agent_c_navigation/nav_api.py`）。
  台北車站指標導引要辨識時，直接在同一個程式裡呼叫指標辨識，不經過 HTTP。
- **前端**：`webapp/frontend`（Vue）

| 頁面 | 功能 |
|---|---|
| 🗣️ 語音導航（首頁） | 語音指令、GPS 地圖、逐步導航、台北車站指標導引 |
| 🧭 指標導航 | 手動輸入目標，對準指標牌報方向 |

標註、訓練、辨識測試頁已從前端移除（後端的訓練 API 還在；要再訓練可用 `sign-detection/train.py`）。

```bash
# 終端機 1：後端
cd webapp/backend
pip install -r requirements.txt
uvicorn main:app --port 8000

# 終端機 2：前端
cd webapp/frontend
npm install
npm run dev        # 手機開 https://電腦IP:5173
```

Agent C 的金鑰（GEMINI_API_KEY、GOOGLE_MAPS_API_KEY、TDX_*）一樣放在 `agent_c_navigation/.env`，
從 `webapp/backend` 啟動也會讀到。

只想測導航、電腦沒裝 YOLO 時，可以單獨啟動 Agent C（舊版單頁介面，https://電腦IP:8080）：

```bash
cd agent_c_navigation
pip install -r requirements.txt
SIGN_DETECTOR_MOCK=1 python3 app.py     # Windows PowerShell：$env:SIGN_DETECTOR_MOCK="1"; python app.py
```

文字模擬版與轉乘示範：

```bash
cd agent_c_navigation
python3 main.py
python3 modules/transit_navigation.py transfer
```

`agent_c_navigation/.env` 可設定：

```
SIGN_DETECTOR_URL=http://127.0.0.1:8000   # sign_detector 位址（預設）
SIGN_DETECTOR_MOCK=1                       # 沒有模型時用模擬辨識結果展示流程
```

沒有 Google Maps 金鑰時，假資料路線池裡有一條轉乘示範路線：說「導航去台北車站」，
再說兩次「換一個」就能選到它。
