# 障礙物避障模組：深度相機 + ToF 融合偵測

視障輔助智慧眼鏡 Navigation Agent 的子模組，偵測兩種碰撞或墜落風險：

| 風險 | 感測來源 | 偵測器 |
|---|---|---|
| 懸空障礙物（招牌、樹枝） | 眼鏡深度相機（ROI 內最近距離，公尺） | `detectors/hanging_obstacle_detector.py` |
| 地面落差（階梯、月台邊緣） | 手杖 ToF / 超音波（公分） | `detectors/ground_drop_detector.py` |

本模組的判斷都是距離門檻與幾何規則，不需要自己訓練模型。模組分成兩部分：

1. **模擬驗證**（`main.py`）：還沒有實體裝置時，用模擬數據驗證判斷邏輯，前端是純靜態網頁。
2. **鏡頭即時偵測**（`server.py` + `camera/`）：開啟鏡頭即時偵測前方障礙物、懸空障礙物、斷崖與樓梯，需要啟動 Python 後端。詳見最後一節。

## 資料流

```
感測器                     偵測器                          融合                 輸出
─────────────────────     ───────────────────────────     ──────────────     ─────────────────────
SimulatedSensorReader ─┐  hanging_obstacle_detector       fusion.fuse()      Navigation Agent 事件
  （現在：模擬資料）     ├─▶  detect(distance) ─────────┐                      priority / vibration /
SensorReader           │                                ├─▶ 取較高危險等級 ─▶ audio_message
  （之後：真實硬體）  ───┘  GroundDropDetector            │
                            update(reading)  ───────────┘
```

1. **感測器**：`reader.read()` 回傳 `SensorFrame(timestamp, depth_min_distance, tof_distance)`
2. **偵測器**
   - 懸空障礙物：距離 < 1.5 m 判定 critical，≤ 3.0 m 判定 warning，其餘為 none
   - 地面落差：用滑動視窗中位數當地面基準；落差 ≥ 30 cm 判定 warning，≥ 50 cm 判定 critical；**需連續 3 筆**才觸發，用來過濾手杖擺動造成的誤報
3. **融合**：取兩者中較高的等級。critical 對應強震動，warning 對應輕震動；none 不發警報，只記為 `status_log`
4. **輸出**：產生 JSON 事件，包含 `agent / timestamp / event_type / priority / data / action_required`

## 擴充點：`sensors/real_sensor_reader.py`

這個檔案定義真實硬體需要實作的介面：

```python
class SensorReader:
    def get_depth_roi_distance(self) -> float: ...  # 深度相機 ROI 最近距離（m）
    def get_tof_distance(self) -> float: ...        # 手杖 ToF 距離（cm）
```

模擬讀取器 `SimulatedSensorReader` 繼承同一個介面。`main.py` 的 `run_pipeline()` 只呼叫 `reader.read()`，串接硬體時只要實作這兩個方法，再把
`SimulatedSensorReader(data)` 換成 `SensorReader()`，處理流程不需要改動。`python main.py --real` 可以檢查真實讀取器是否已經實作。

## 專案結構

```
obstacle-avoidance/
├── sensors/
│   ├── simulate_data.py        四種模擬情境 + SimulatedSensorReader
│   └── real_sensor_reader.py   真實硬體介面（擴充點）
├── detectors/
│   ├── hanging_obstacle_detector.py
│   └── ground_drop_detector.py
├── fusion.py                   融合 + 事件格式
├── visualize.py                matplotlib 圖表 + JSON 結果 + 前端 data.js
├── main.py                     執行四種情境並印出驗證結果
├── frontend/                   視覺化網頁（index.html / app.js / style.css / data.js）
├── output/                     產出的 PNG 與 JSON
└── requirements.txt
```

## 使用方式

```bash
pip install -r requirements.txt
python main.py
```

`main.py` 會依序執行「正常走路 → 懸空障礙物 → 地面落差 → 混合情境」，每個情境會輸出：

- 終端機：critical / warning 觸發次數與時間點，以及逐項驗證結果（通過或失敗）
- `output/<情境>_scenario.png`：上圖為深度距離，下圖為 ToF 距離，共用時間軸，並標出觸發點
- `output/<情境>_result.json`：給前端讀取的結果
- `frontend/data.js`：把所有情境打包成一個檔案，讓網頁直接開啟也讀得到資料

所有情境都通過驗證時，結束碼為 0，任何一項失敗則為 1。

### 前端

直接用瀏覽器開啟 `frontend/index.html`，或在專案根目錄執行：

```bash
python -m http.server 8020
```

然後打開 <http://localhost:8020/frontend/>（不用 8000，避免和 sign_detector 後端衝突）。若 `server.py` 已在執行，也可以直接開 <http://localhost:8010/index.html>。網頁包含：

- **情境切換**：KPI、驗證清單，以及深度 / ToF 時間序列圖（警報點以顏色區分 critical 與 warning）
- **警報紀錄**：列出時間、類型、等級、震動與語音提示，可以切換只看新觸發或看逐筆紀錄
- **即時模擬**：拖動距離滑桿觀察判斷結果。ToF 部分可以自動以 5 Hz 送出讀數，也能模擬擺動突波，展示「連續 3 筆才確認」的濾除效果。融合後的 JSON 會即時更新

前端使用的門檻與語音文字都由 `main.py` 從 Python 常數匯出，和後端一致。

## 調整門檻

所有門檻都集中寫在各檔案頂部的常數：

| 檔案 | 常數 |
|---|---|
| `detectors/hanging_obstacle_detector.py` | `CRITICAL_THRESHOLD_M`、`WARNING_THRESHOLD_M` |
| `detectors/ground_drop_detector.py` | `DROP_WARNING_CM`、`DROP_CRITICAL_CM`、`CONFIRM_COUNT`、`WINDOW_SIZE`、`REBASELINE_COUNT` |
| `sensors/simulate_data.py` | 取樣間隔、步行速度、雜訊幅度、ToF 基準等模擬參數 |
| `fusion.py` | 震動強度與語音提示文字 |

修改後重新執行 `python main.py`，圖表、JSON 和前端資料會一起更新。

## 鏡頭即時偵測

做法類似 Tesla 的純視覺：一般鏡頭的畫面先經 AI 估計每個像素的距離，再用幾何規則判斷危險。用 RealSense 深度相機時，則直接使用實測的公尺深度。

```
影像 ─┬─ YOLOv8n ─────────── 物體類別（行人、機車、椅子…）
      └─ 深度 ─┬─ 單眼：Depth Anything V2（相對深度）→ 用「畫面下方是地面」校正成公尺
               └─ 深度相機：RealSense 實測公尺深度
                       ↓  依相機高度、俯角換算成世界座標（前方距離、左右、離地高度）
      ┌────────────────┼───────────────────────┐
  前方障礙物         懸空障礙物               地面剖面
  通道 ±0.45 m       ±30°、離地 1~2 m          沿通道由近到遠的地面高度
  離地 0.25~1 m      → hanging_obstacle_detector  → 斷崖／向下樓梯／向上樓梯／階差
      └────────────────┼───────────────────────┘
                 連續幀確認（3 幀中 2 幀）→ fusion.fuse_hazards → 震動 + 語音
```

### 啟動

```bash
pip install -r requirements.txt
python setup_assets.py   # 第一次：下載深度模型（約 99 MB）與 cloudflared（約 55 MB），不在 git 裡
python server.py
```

然後開啟 <http://localhost:8010/live.html>，選擇影像來源後按「開始偵測」。

> **與指標辨識（sign_detector）同時使用**：sign_detector 後端固定使用 8000 埠，本模組預設改用 **8010** 埠，兩者可以同時啟動。
> 但同一顆鏡頭在部分 Windows 驅動下只能給一個程式用；若出現「鏡頭正被其他程式占用」，請先關掉另一邊的攝影機，或用「影片／圖片檔」模式測試。
> 兩個後端都在 CPU 上跑 YOLO，同時開會互相搶運算，各自變慢。

- **瀏覽器鏡頭**：筆電或 USB 鏡頭，手機則預設使用後鏡頭。
- **影片／圖片檔**：沒有鏡頭時，可以上傳拍好的樓梯或走廊影片做 demo。
- **深度相機 RealSense**：先執行 `pip install pyrealsense2` 並接上相機，由後端直接讀取。

畫面包含即時影像疊圖（行進通道、物體框與距離、地面邊緣）、鳥瞰雷達、深度圖、地面側視剖面、警報紀錄，並用瀏覽器語音以中文播報。

> **手機連線**：手機瀏覽器只有在 https 或 localhost 下才能開鏡頭。要在同網段用手機測試，需要準備自簽憑證，再執行
> `python server.py --host 0.0.0.0 --ssl-certfile cert.pem --ssl-keyfile key.pem`。

### 鏡頭參數要填對

單眼模式用「畫面下方是平地」來換算公尺距離，所以網頁上的**鏡頭離地高度**與**往下傾角**要接近實際值（預設：眼鏡 1.5 m、往下 15°）。

網頁會顯示「最近可見地面距離」，比這個距離更近的落差在畫面外，偵測不到。要提早看到斷崖或樓梯，請把鏡頭再往下傾。

### 讓距離更準

| 方法 | 怎麼用 | 改善什麼 |
|---|---|---|
| **已知距離校正**（最有效） | 按「📏 距離校正」，點畫面上一個量過距離的位置（地上的膠帶、站著的人、箱子），輸入實際公尺數 | 修正鏡頭高度、視角填不準造成的**整體比例誤差**。結果會換算成等效鏡頭高度，並記在這台瀏覽器 |
| 陀螺儀自動傾角 | 手機上勾選「陀螺儀自動傾角」（需 https） | 走路時鏡頭角度一直變，用陀螺儀即時量測，比固定設定準 |
| 高精度深度 | 勾選「高精度深度」 | 深度圖細節較多；但每幀約慢 2 倍（CPU 約 1.2 fps）。切換後請重新做距離校正 |
| 深度相機 | 改用 RealSense | 實測深度，誤差約 1~2%，最準 |

另外自動進行的改善：
- 地面校正時排除 YOLO 框到的物體，並反覆剔除離群點。
- 前後幀追蹤同一個物體，距離做平滑，數字不會亂跳。
- 深度模型輸入依畫面比例決定，直式手機畫面不會被壓扁。

建議 demo 前做一次校正：在前方地上 2 m、3 m 處各貼一條膠帶，點 3 m 那條輸入 3.0 校正，再看 2 m 那條是否接近 2.0。

### 驗證

```bash
python -m camera.test_scenes                    # 光線投射合成 10 種已知場景，驗證幾何規則
python -m camera.analyze_file 照片或影片.mp4    # 離線分析，輸出標註圖到 output/camera/
```

`test_scenes` 涵蓋平地、近／遠斷崖、向下／向上樓梯、路緣、近／遠箱子、懸空招牌、通道外物體，目前 10/10 通過。

### 限制

- 單眼 AI 深度是估計值，距離大約有 10~20% 誤差。反光地面、玻璃、純色牆面容易出錯。深度相機最準。
- 斷崖與樓梯靠地面幾何判斷，鏡頭下方必須拍到地面。鏡頭太平（傾角太小）時，近處落差在畫面外。
- CPU 上約 2~3 fps（深度模型約 0.25 秒＋YOLO 約 0.08 秒），有 GPU 或改用更小的輸入尺寸（`camera/config.py` 的 `DEPTH_INPUT_SIZE`）可以更快。

### 檔案

| 檔案 | 說明 |
|---|---|
| `server.py` | FastAPI 後端：網頁、`/ws/camera`（瀏覽器影格）、`/ws/depthcam`（RealSense） |
| `camera/config.py` | 相機幾何、模型、通道寬度、高度帶、樓梯／斷崖門檻等所有參數 |
| `camera/depth_source.py` | Depth Anything ONNX、地面自我校正、RealSense 讀取 |
| `camera/geometry.py` | 像素 + 深度 ↔ 世界座標 |
| `camera/ground_analyzer.py` | 地面剖面 → 斷崖／樓梯／階差 |
| `camera/scene_analyzer.py` | 整合流程、時間平滑、輸出給前端的資料 |
| `camera/models/` | `depth_anything_v2_small.onnx`（Apache-2.0，來自 Hugging Face `onnx-community/depth-anything-v2-small`）、`yolov8n.pt` |
| `frontend/live.html`、`live.js` | 即時鏡頭頁 |

## 手機 App（PWA）

手機版是一個可以「安裝到主畫面」的網頁 App：
- 整個畫面都是鏡頭影像，辨識到的物體會用**橘色框**標出名稱和距離。
- 下方顯示警示。**危險（critical）時會持續發出逼逼聲**，越近嗶得越急；同時會震動，並用語音播報。

模型仍然跑在電腦上，手機透過 Cloudflare 免費 https 通道連線。

### 使用方式

1. 在電腦上雙擊 `start_phone.bat`，或執行 `python phone.py`。
2. 電腦會自動打開一頁 QR code，用手機相機掃描。
3. 手機瀏覽器開啟後按「開始偵測」，允許相機權限。
4. 安裝成 App：Android（Chrome）點「安裝到主畫面」；iPhone（Safari）點「分享 → 加入主畫面」。

右上角 ⚙ 可以調整鏡頭高度、陀螺儀自動傾角、逼逼聲、語音、震動、高精度深度，也能做距離校正。

### 注意

- **電腦要開著 phone.py**，手機也要有網路。關掉視窗就停止服務。
- **免費通道每次啟動的網址都不同**，已安裝的 App 會連到舊網址。每次重新啟動後，請重新掃描 QR code；需要的話，刪掉舊的 App 再重新安裝。
  要固定網址，需要註冊 Cloudflare 並有自己的網域（Cloudflare Named Tunnel），或使用 ngrok 免費的固定網域。
- 連結內含一組隨機存取權杖，只有掃到 QR code 的手機能使用辨識功能；每次啟動都會重新產生。
- 經過網路通道約有 0.5 秒延遲。App 會同時送 2 張影格，讓網路傳輸和運算重疊，實測約 3 fps。

### 檔案

| 檔案 | 說明 |
|---|---|
| `phone.py`、`start_phone.bat` | 一鍵啟動後端 + https 通道 + QR code |
| `tools/cloudflared.exe` | Cloudflare 通道程式（約 55 MB，來自 github.com/cloudflare/cloudflared） |
| `frontend/m/` | 手機 PWA：`index.html`、`app.js`、`app.css`、`manifest.webmanifest`、`sw.js`、`icons/` |
| `frontend/phone.html` | 電腦上顯示 QR code 的頁面 |
