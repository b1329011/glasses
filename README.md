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
- `agent_c_navigation/modules/station_locator.py`：讀招牌定位、組「附近有什麼、靠近哪個出口」、求助卡
- `agent_c_navigation/modules/sign_client.py`：呼叫指標辨識（合併後端直接呼叫函式；單獨啟動時走 HTTP）
- `agent_c_navigation/modules/routing.py`：`transit_legs` 支援多段搭乘（轉車）
- `agent_c_navigation/nav_api.py`：導航 API（`station_guidance` 階段、`/sign_frame`、`/position`），掛在合併後端的 `/api/agent-c`

## 台北車站室內定位：我在哪裡、附近有什麼

跟教授討論後，室內以台北車站為主，不做全程逐步導引，改成告訴使用者「大概在哪、附近有什麼、靠近哪個出口」，
讓使用者自己判斷怎麼走，或找人幫忙。

- **我在哪裡**（語音說「我在哪裡／附近有什麼／最近的出口」，或按 📍 按鈕）：開鏡頭，OCR 讀店家招牌和指標上的出口編號（Y13、Z4…），
  比對 `agent_c_navigation/data/taipei_main_station.json`，連續 2 張影格指向同一處才播報，播完自動關鏡頭。
  例：「你在站前地下街（Z區），新光三越附近，靠近Z2出口（南陽街…）、Z4出口（館前路…），附近還有國立台灣博物館。」
- **找人幫忙**（說「找人幫忙」或按 🙋 按鈕）：螢幕顯示大字求助卡，內容帶入最後確認的位置和要去的地方（例如正在找的月台），可以拿給路人或站務人員看。
- 任何時候都能用，不會打斷正在進行的導航；原本的「依路線找指標」（淡水信義線 → 往淡水、轉乘、找出口）保留。

### 店家與出口資料

`agent_c_navigation/data/taipei_main_station.json`，主要來自 **OpenStreetMap**（© OpenStreetMap contributors，ODbL 授權，報告/展示需標示出處），
再加上市場處、維基百科等公開資料：

- 66 個出口有座標（Y/Z/K/M/R，加上台北車站東西南北門；只差 Y22）
- 130 個地下店家／地點（餐廳、便利商店、台鐵高鐵進出站口、詢問處…），各自有座標、所屬區域、最近出口
- 台北地下街的服飾／百貨／電子／美食區

有座標所以能說「M6 出口約 20 公尺，在你左前方」：方向用手機羅盤（Android 直接可用，iOS 按 📍 時會詢問權限）。
**地下鋼筋會干擾羅盤**，只有讀數新鮮、精度可接受時才會說左右，否則只報距離。

同名連鎖店（7-Eleven 4 家、國泰世華 ATM 5 家、吉豚屋 2 家…）只看到它時不會亂猜，會請使用者再對準旁邊別的招牌。

更新資料：

```bash
cd agent_c_navigation
python tools/osm_import.py --fetch   # 從 OpenStreetMap 抓最新（需要網路）
python tools/osm_import.py           # 用已存的 data/osm_raw_taipei_main.json 重建
```

實地發現 OSM 沒有的店，可以直接在 JSON 的 `shops` 加（有座標最好，沒有就填 `near_exits`），或到 openstreetmap.org 補上再重新匯入。
存檔後後端會自動重讀。

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
