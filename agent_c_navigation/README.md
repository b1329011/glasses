# Agent C - 室外空間導航 Demo

## 這個Demo驗證什麼

不是驗證「語音辨識準不準」或「地圖API回傳對不對」，這些之後接真實API就會有答案。
這個demo驗證的是**系統邏輯設計合不合理**：

- 喚醒詞 → 語意解析 → 地點消歧（多筆候選時怎麼問使用者）
- 路線候選池 → 語音摘要（系統直接推薦最佳方案）
- 「換一個」、「我想改搭車」、「取消」這幾種指令的分流邏輯
- 即時運輸資訊如何覆蓋進語音摘要
- 逐步導航的語音提示格式（地標+方向，而非公尺數字）

用文字模擬語音輸入輸出，是因為**先驗證邏輯，比先處理真實語音辨識更快、更便宜**，
邏輯確定沒問題後，之後只要把 `voice_io.py` 換成真實的 STT/TTS 呼叫，
整個對話流程架構完全不用重寫。

---

## 怎麼執行

### 規則版（不用金鑰，馬上能跑）
```bash
cd agent_c_outdoor
pip install -r requirements.txt
python3 main.py
```

### 真實驗證版（接Gemini API，這是給教授看「AI可行性驗證」用的）
```bash
cd agent_c_outdoor
pip install -r requirements.txt
cp .env.example .env
# 打開 .env，把從 https://aistudio.google.com 申請到的金鑰貼進 GEMINI_API_KEY=
python3 modules/nlu.py     # 先單獨測nlu模組，看真實LLM解析口語指令的效果
python3 main.py            # 再跑整合流程，此時main.py會自動偵測到金鑰改用真實LLM
```

`nlu.py`裡設計成「有偵測到`GEMINI_API_KEY`就自動切換成真實API模式，沒有就退回規則版」，
不用手動改程式碼切換，方便你在「快速測邏輯」跟「驗證AI效果」兩種情境間切換。

執行後照著提示輸入文字（模擬你對眼鏡說話）：

```
小北                    ← 模擬喚醒詞
導航去台北車站            ← 觸發導航
換一個                   ← 切換到候選池的下一個方案
我想改搭車               ← 觸發改變交通方式重新規劃
開始                     ← 進入逐步導航模擬
exit                     ← 結束程式
```

也可以直接測試地點消歧（一次輸入多筆候選的地名）：
```
導航去診所
```
會觸發「找到2個天成診所，你是要去哪一個」的語音確認流程。

---

## 檔案結構

```
agent_c_outdoor/
├── main.py              # 主流程，整合所有模組
├── modules/
│   ├── nlu.py            # 語意解析：意圖判斷 + 目的地擷取
│   ├── places.py         # 地點消歧（模擬 Google Places API）
│   ├── routing.py        # 路線候選池 + 換一個機制（模擬 Google Routes API）
│   ├── transit.py        # 即時運輸資訊覆蓋（模擬 TDX API）
│   └── voice_io.py       # 語音輸入輸出（文字模擬，之後換真實STT/TTS）
└── README.md
```

---

## 這樣算是「AI可行性驗證」了嗎？

`nlu.py`現在算是了——它會真的呼叫Gemini API，拿真實LLM對口語指令的解析結果，
這才是教授要的「驗證AI技術本身表現」，而不是驗證系統邏輯。

跑 `python3 modules/nlu.py` 時，建議記錄下來當作驗證證據：
- 哪些口語化句子LLM解析對了、哪些錯了（例如「欸我突然想搭車耶」規則版完全抓不到意圖，
  這正好可以拿來對比展示「為什麼要用LLM而不是關鍵字規則」）
- 抓一個小樣本（例如20句不同講法），計算LLM意圖判斷的正確率，這個數字可以直接寫進論文/報告

`places.py`、`routing.py`、`transit.py`目前還是假資料，要做完整的AI可行性驗證，
這三個也要比照`nlu.py`的做法（偵測有沒有金鑰、自動切換真實API/假資料），
之後申請好Google Cloud跟TDX金鑰後，我可以比照這次的方式幫你改。

## 之後怎麼換成真實API（每個檔案都有標記）

每個模組檔案最上面的docstring都寫了「真實API串接位置」，附上範例程式碼，
包含：

| 模組 | 真實系統要接的API | 需要申請的東西 |
|---|---|---|
| `nlu.py` | Claude/GPT API 做意圖解析 | Anthropic/OpenAI API金鑰 |
| `places.py` | Google Places API | Google Cloud API金鑰 |
| `routing.py` | Google Routes API | Google Cloud API金鑰 |
| `transit.py` | TDX 運輸資料流通服務 | TDX會員帳號 + client_id/secret |
| `voice_io.py` | Whisper（STT）+ TTS引擎 | OpenAI API金鑰（或其他STT/TTS服務） |

換的時候只要把對應函式內容換掉（例如 `search_place()` 改成真的呼叫 `requests.get()`），
外部呼叫這些函式的地方（main.py）完全不用動，這是刻意設計成這樣方便替換的。

---

## 目前這個demo還沒做、之後要補的部分

1. **真實語音**：目前是文字模擬，之後要接真實的喚醒詞引擎（如Porcupine）+ Whisper + TTS
2. **barge-in（語音打斷）**：目前是回合制對話（一問一答），真實系統要做到「TTS播放語音時麥克風仍在監聽」，這在CLI demo無法模擬，需要真實硬體/音訊pipeline測試
3. **GPS即時定位**：目前是寫死的模擬座標，之後要接手機真實GPS
4. **偏離路線偵測**：目前逐步導航是固定唸3句話，真實系統要持續比對GPS位置與規劃路徑，判斷是否偏離

---

## 室內模組接續方式

室外模組跑到「使用者走到室內入口（如捷運站門口）」時，會觸發交接邏輯：

```
偵測到GPS訊號減弱/消失
    ↓
呼叫室內導航模組（之後開發），傳入：
  - 使用者最終目的地（如：「2號月台」）
  - 最後已知的GPS位置（作為室內模組的起點參考）
    ↓
室內模組接手，改用「標誌辨識 + VLM」的方式導航
```

這代表室外模組跟室內模組是**兩個獨立的agent**，中間用一個簡單的「交接事件」串接，
室外模組不需要知道室內怎麼運作，只需要在GPS訊號消失時觸發交接即可。
之後開發室內模組時，可以完全獨立開發、獨立測試，最後再把交接的介面接上。