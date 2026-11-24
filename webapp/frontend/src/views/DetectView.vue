<script setup>
/*
 * DetectView — 辨識測試頁
 * 輸入：上傳照片 / 攝影機。攝影機用「看得見的取景框」(手機才開得起來)，
 * 支援前後鏡頭翻轉；可即時偵測箭頭(YOLO)或拍一張讀文字(OCR)。
 */
import { ref, reactive, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api.js'

const cfg = reactive({ conf: 0.25, iou: 0.45, model_path: '' })
const models = ref([])

// YOLO 結果
const resultImg = ref('')
const detections = ref([])
const latency = ref(null)
const realtimeOk = ref(true)
const busy = ref(false)
const errorMsg = ref('')

// 上傳/單張照片的互動式結果：原圖 + 疊框，可點類別只看該箭頭
const photoImg = ref('')          // 原始照片(data URL)
const photoImgEl = ref(null)      // <img> 參照
const photoOverlay = ref(null)    // 疊框 canvas 參照
const selectedClass = ref(null)   // null=全部；否則只顯示該 class_index

// 依類別統計偵測數量（給篩選按鈕用，多到少排序）
const classCounts = computed(() => {
  const m = new Map()
  detections.value.forEach((d) => {
    if (!m.has(d.class_index))
      m.set(d.class_index, { idx: d.class_index, name: d.class_name, count: 0 })
    m.get(d.class_index).count++
  })
  return [...m.values()].sort((a, b) => b.count - a.count)
})
// 表格用：套用目前類別篩選後的偵測清單
const filteredDetections = computed(() =>
  selectedClass.value === null
    ? detections.value
    : detections.value.filter((d) => d.class_index === selectedClass.value)
)

// OCR 結果
const ocrTexts = ref([])
const ocrJoined = ref('')
const ocrImg = ref('')
const ocrLatency = ref(null)
const ocrBusy = ref(false)

// 目標導向導航
const navTarget = ref('')          // 使用者要前往的路線/目標
const navResult = ref(null)        // { found, sentence, direction, matched_text, annotated_base64, message, texts_read, yolo_error }
const guideBusy = ref(false)       // 導航辨識中
const classList = ref([])          // 全部類別（用來自動帶出圖標快捷詞）
// 快捷目標：你新增的圖標名稱 + 幾個常見文字目標（去重）
const NAV_PRESETS = computed(() => {
  const icons = classList.value.filter((c) => c.kind === 'icon')
    .map((c) => c.display_name || c.name)
  return [...new Set([...icons, '出口', '台鐵', '高鐵'])]
})

// 攝影機狀態
const video = ref(null)
const overlay = ref(null)           // 疊在畫面上畫框的 canvas
const camOn = ref(false)
const facing = ref('environment')   // 'environment'=後鏡頭, 'user'=前鏡頭
const liveDetect = ref(false)       // 是否正在即時偵測箭頭
const liveOverlay = ref(false)      // 目前結果是否用「疊在畫面上」呈現
let stream = null
let camTimer = null

// 記住最後一張靜態照片，滑桿改變門檻時可用同一張重跑
const lastImage = ref('')
const lastMode = ref('')   // 'infer' | 'guide'（ocr 不受 conf 影響）
let reRunTimer = null

onMounted(async () => {
  try { models.value = await api.listModels() } catch (_) {}
  try { classList.value = await api.listClasses() } catch (_) {}
})

// conf / iou 改變 → 用同一張照片自動重新辨識（即時偵測中則交給下一影格）
watch([() => cfg.conf, () => cfg.iou], () => {
  if (liveDetect.value || !lastImage.value) return
  clearTimeout(reRunTimer)
  reRunTimer = setTimeout(() => {
    if (lastMode.value === 'infer') runInfer(lastImage.value)
    else if (lastMode.value === 'nav') runNavigate(lastImage.value)
  }, 300)   // 稍微延遲，避免拖曳過程狂送請求
})

// 清掉上一張的導航結果（切換照片時不要留著舊結果）
function clearGuide() {
  navResult.value = null
}

// ---------- 共用：辨識呼叫 ----------
// useOverlay=true 代表這是「即時攝影機」的影格 → 把框畫在畫面上、不顯示標註圖；
// false 代表上傳/單張 → 顯示後端回傳的標註圖（框畫在那張圖上）。
async function runInfer(base64, useOverlay = false) {
  busy.value = true
  errorMsg.value = ''
  clearGuide()
  try {
    const r = await api.infer(base64, cfg.conf, cfg.iou, cfg.model_path || null)
    resultImg.value = r.annotated_base64
    detections.value = r.detections
    latency.value = r.latency_ms
    realtimeOk.value = r.realtime_ok
    if (useOverlay && camOn.value) {
      liveOverlay.value = true    // 疊在攝影機畫面上
      drawDetections()
    } else {
      liveOverlay.value = false   // 上傳/單張：用原圖 + 前端疊框（可篩選類別）
      clearOverlay()
      selectedClass.value = null  // 新結果預設顯示全部
      photoImg.value = base64
      await nextTick()
      drawPhotoBoxes()
    }
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    busy.value = false
  }
}

// 在疊層 canvas 上畫出偵測框（座標即為送出影格的解析度，直接畫即對齊）
const DET_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#ef4444', '#a855f7',
  '#ec4899', '#14b8a6', '#eab308']
function drawDetections() {
  const v = video.value, cv = overlay.value
  if (!v || !cv || !v.videoWidth) return
  cv.width = v.videoWidth
  cv.height = v.videoHeight
  const ctx = cv.getContext('2d')
  ctx.clearRect(0, 0, cv.width, cv.height)
  const fs = Math.max(16, Math.round(cv.width / 36))
  ctx.lineWidth = Math.max(2, Math.round(cv.width / 320))
  ctx.font = `600 ${fs}px "Noto Sans TC", sans-serif`
  ctx.textBaseline = 'top'
  detections.value.forEach((d) => {
    const [x1, y1, x2, y2] = d.box
    const color = DET_COLORS[d.class_index % DET_COLORS.length]
    ctx.strokeStyle = color
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    const label = `${d.class_name} ${(d.conf * 100).toFixed(0)}%`
    const tw = ctx.measureText(label).width + 10
    const ty = Math.max(0, y1 - fs - 6)
    ctx.fillStyle = color
    ctx.fillRect(x1, ty, tw, fs + 6)
    ctx.fillStyle = '#04283a'
    ctx.fillText(label, x1 + 5, ty + 3)
  })
}
// 在「上傳照片結果」的疊層 canvas 上畫框，套用類別篩選
function drawPhotoBoxes() {
  const img = photoImgEl.value, cv = photoOverlay.value
  if (!img || !cv || !img.naturalWidth) return
  cv.width = img.naturalWidth
  cv.height = img.naturalHeight
  const ctx = cv.getContext('2d')
  ctx.clearRect(0, 0, cv.width, cv.height)
  const fs = Math.max(16, Math.round(cv.width / 36))
  ctx.lineWidth = Math.max(2, Math.round(cv.width / 320))
  ctx.font = `600 ${fs}px "Noto Sans TC", sans-serif`
  ctx.textBaseline = 'top'
  detections.value.forEach((d) => {
    // 有選類別時，只畫該類別的框
    if (selectedClass.value !== null && d.class_index !== selectedClass.value) return
    const [x1, y1, x2, y2] = d.box
    const color = DET_COLORS[d.class_index % DET_COLORS.length]
    ctx.strokeStyle = color
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    const label = `${d.class_name} ${(d.conf * 100).toFixed(0)}%`
    const tw = ctx.measureText(label).width + 10
    const ty = Math.max(0, y1 - fs - 6)
    ctx.fillStyle = color
    ctx.fillRect(x1, ty, tw, fs + 6)
    ctx.fillStyle = '#04283a'
    ctx.fillText(label, x1 + 5, ty + 3)
  })
}
function onPhotoLoad() { drawPhotoBoxes() }
// 點類別 → 只顯示該箭頭；再點同一個 → 取消（顯示全部）
function setClass(idx) {
  selectedClass.value = selectedClass.value === idx ? null : idx
  drawPhotoBoxes()
}

function clearOverlay() {
  const cv = overlay.value
  if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height)
}
async function runOcr(base64) {
  ocrBusy.value = true
  errorMsg.value = ''
  clearGuide()
  try {
    const r = await api.ocr(base64, 0.3)
    ocrImg.value = r.annotated_base64
    ocrTexts.value = r.texts
    ocrJoined.value = r.joined
    ocrLatency.value = r.latency_ms
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    ocrBusy.value = false
  }
}

// ---------- 目標導向導航（先找目標文字 → 找旁邊箭頭 → 報方向）----------
async function runNavigate(base64) {
  if (!navTarget.value.trim()) {
    errorMsg.value = '請先輸入要前往的路線或目標（如：淡水信義線、出口、台鐵）'
    return
  }
  guideBusy.value = true
  errorMsg.value = ''
  navResult.value = null   // 先清掉舊的
  try {
    const r = await api.navigate(base64, navTarget.value.trim(),
                                 cfg.conf, cfg.iou, cfg.model_path || null)
    navResult.value = r
    if (voiceOn.value && r.found && r.sentence) speakText(r.sentence)
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    guideBusy.value = false
  }
}

// ---------- 上傳照片 ----------
// mode 記住這張圖用哪種辨識，之後改門檻可用同一張重跑（ocr 傳 null 不記）
function readAndRun(file, fn, mode) {
  const reader = new FileReader()
  reader.onload = () => {
    if (mode) { lastImage.value = reader.result; lastMode.value = mode }
    fn(reader.result)
  }
  reader.readAsDataURL(file)
}
function onFile(e) {
  const f = e.target.files[0]; if (f) readAndRun(f, runInfer, 'infer'); e.target.value = ''
}
function onOcrFile(e) {
  const f = e.target.files[0]; if (f) readAndRun(f, runOcr, null); e.target.value = ''
}

// ---------- 攝影機 ----------
async function openStream() {
  errorMsg.value = ''
  // 放寬限制：facingMode 用 ideal，找不到指定鏡頭也不會直接失敗
  const constraints = { video: { facingMode: { ideal: facing.value } }, audio: false }
  try {
    if (stream) stream.getTracks().forEach((t) => t.stop())
    stream = await navigator.mediaDevices.getUserMedia(constraints)
    video.value.srcObject = stream
    await video.value.play()
    camOn.value = true
    return true
  } catch (err) {
    camOn.value = false
    errorMsg.value = cameraErrorHint(err)
    return false
  }
}
// 把瀏覽器的錯誤翻成看得懂的提示
function cameraErrorHint(err) {
  const n = err && err.name
  if (n === 'NotAllowedError') return '攝影機權限被拒：請在瀏覽器允許使用相機後重試。'
  if (n === 'NotFoundError') return '找不到攝影機裝置。'
  if (n === 'NotReadableError') return '攝影機被其他程式佔用，請關掉其他用到相機的 App。'
  if (location.protocol !== 'https:' && location.hostname !== 'localhost')
    return '手機需以 https 開啟才能用相機（目前不是 https）。'
  return '無法開啟攝影機：' + (err && err.message || err)
}

async function startCamera() {
  if (!camOn.value) await openStream()
}
function closeCamera() {
  stopLive()
  stopLiveGuide()
  if (stream) stream.getTracks().forEach((t) => t.stop())
  stream = null
  camOn.value = false
}
// 前後鏡頭翻轉
async function flipCamera() {
  facing.value = facing.value === 'environment' ? 'user' : 'environment'
  if (camOn.value) await openStream()   // 用新鏡頭重開串流
}

// 擷取目前取景框成一張 jpeg
function grabFrame() {
  const v = video.value
  if (!v || !v.videoWidth) return null
  const canvas = document.createElement('canvas')
  canvas.width = v.videoWidth
  canvas.height = v.videoHeight
  canvas.getContext('2d').drawImage(v, 0, 0)
  return canvas.toDataURL('image/jpeg', 0.85)
}

// 即時偵測箭頭（每 700ms 一張）
function toggleLive() {
  if (liveDetect.value) { stopLive(); return }
  stopLiveGuide()              // 與連續導航互斥
  liveDetect.value = true
  camTimer = setInterval(() => {
    if (busy.value) return
    const data = grabFrame(); if (data) runInfer(data, true)  // 即時：疊框
  }, 700)
}
function stopLive() {
  liveDetect.value = false
  liveOverlay.value = false
  if (camTimer) { clearInterval(camTimer); camTimer = null }
  clearOverlay()
}

// 拍一張讀文字
function captureOcr() {
  const data = grabFrame()
  if (data) runOcr(data)
  else errorMsg.value = '畫面尚未就緒，請稍候再拍。'
}
// 拍一張做目標導向導航
function captureGuide() {
  const data = grabFrame()
  if (data) { lastImage.value = data; lastMode.value = 'nav'; runNavigate(data) }
  else errorMsg.value = '畫面尚未就緒，請稍候再拍。'
}
function onGuideFile(e) {
  const f = e.target.files[0]; if (f) readAndRun(f, runNavigate, 'nav'); e.target.value = ''
}
// 語音唸出一段文字（視障使用情境；用瀏覽器內建語音合成）
function speakText(text) {
  if (!('speechSynthesis' in window) || !text) return
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-TW'
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
}
function speakNav() {
  if (navResult.value && navResult.value.sentence) speakText(navResult.value.sentence)
}

// ---------- 連續導航模式 ----------
// 策略：箭頭「每 ~1.2 秒」更新(快、即時感)；文字/導航句「每 ~5 秒」整張 OCR
// 更新一次(慢但可靠)，中間沿用上次的句子。兩者獨立、互不阻塞。
const liveGuide = ref(false)
const voiceOn = ref(false)     // 自動語音
let guideTimer = null
let guideTick = 0
let lastSpoken = ''

function toggleLiveGuide() {
  if (liveGuide.value) { stopLiveGuide(); return }
  stopLive()                   // 與純箭頭即時互斥
  liveGuide.value = true
  guideTick = 0
  liveLoop()                   // 自走式迴圈：一張做完立刻做下一張（不鎖固定間隔）
}
function stopLiveGuide() {
  liveGuide.value = false      // 迴圈下一輪會自行結束
  liveOverlay.value = false
  clearOverlay()
}
// 箭頭以最高速率連續更新；每約 15 張、有目標時順便跑一次完整導航(OCR，不阻塞迴圈)
async function liveLoop() {
  while (liveGuide.value) {
    const data = grabFrame()
    if (!data) { await new Promise((r) => setTimeout(r, 100)); continue }
    await updateArrowsLive(data)                 // 等這張做完 → 立刻換下一張
    guideTick++
    if (navTarget.value.trim() && guideTick % 15 === 0 && !guideBusy.value) {
      const nd = grabFrame()
      if (nd) refreshNav(nd)                     // 不 await：OCR 在背景跑，不卡箭頭
    }
  }
}
// 只更新箭頭框(用 /api/infer，快)
async function updateArrowsLive(base64) {
  busy.value = true
  try {
    const r = await api.infer(base64, cfg.conf, cfg.iou, cfg.model_path || null, 640)  // 即時求快
    detections.value = r.detections
    latency.value = r.latency_ms
    if (camOn.value) { liveOverlay.value = true; redrawLive() }
  } catch (_) { /* 靜默，下一輪再試 */ } finally { busy.value = false }
}
// 更新完整導航結果(整張跑 navigate，較慢但可靠)
async function refreshNav(base64) {
  guideBusy.value = true
  try {
    // 連續模式：跳過整張 OCR 保底、降解析度 → 每輪都快
    const r = await api.navigate(base64, navTarget.value.trim(),
                                 cfg.conf, cfg.iou, cfg.model_path || null,
                                 { no_full_ocr: true, imgsz: 640 })
    navResult.value = r
    if (camOn.value) { liveOverlay.value = true; redrawLive() }  // 立刻把目標/箭頭畫上去
    if (voiceOn.value && r.found && r.sentence && r.sentence !== lastSpoken) {
      lastSpoken = r.sentence; speakText(r.sentence)
    }
  } catch (_) { /* 靜默 */ } finally { guideBusy.value = false }
}
// 即時畫面：先畫所有箭頭，再把導航結果(目標綠框 + 選中箭頭 + 連線)疊上去
function redrawLive() {
  drawDetections()
  drawNavOverlay()
}
function drawNavOverlay() {
  const cv = overlay.value
  const nr = navResult.value
  if (!cv || !liveGuide.value || !nr || !nr.found) return
  const ctx = cv.getContext('2d')
  const fs = Math.max(16, Math.round(cv.width / 32))
  // 目標(文字/圖標) → 綠框
  if (nr.matched_box) {
    const [x1, y1, x2, y2] = nr.matched_box
    ctx.strokeStyle = '#00e676'; ctx.lineWidth = Math.max(3, Math.round(cv.width / 260))
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    // 在目標框上方標出方向句
    if (nr.sentence) {
      ctx.font = `700 ${fs}px "Noto Sans TC", sans-serif`; ctx.textBaseline = 'top'
      const tw = ctx.measureText(nr.sentence).width + 12
      const ty = Math.max(0, y1 - fs - 8)
      ctx.fillStyle = 'rgba(0,80,40,.85)'; ctx.fillRect(x1, ty, tw, fs + 8)
      ctx.fillStyle = '#fff'; ctx.fillText(nr.sentence, x1 + 6, ty + 3)
    }
  }
  // 選中的箭頭 → 青框 + 連線
  if (nr.arrow_box) {
    const [ax1, ay1, ax2, ay2] = nr.arrow_box
    ctx.strokeStyle = '#00b0ff'; ctx.lineWidth = Math.max(3, Math.round(cv.width / 260))
    ctx.strokeRect(ax1, ay1, ax2 - ax1, ay2 - ay1)
    if (nr.matched_box) {
      const [x1, y1, x2, y2] = nr.matched_box
      ctx.strokeStyle = 'rgba(0,200,255,.7)'; ctx.setLineDash([8, 6])
      ctx.beginPath()
      ctx.moveTo((x1 + x2) / 2, (y1 + y2) / 2)
      ctx.lineTo((ax1 + ax2) / 2, (ay1 + ay2) / 2)
      ctx.stroke(); ctx.setLineDash([])
    }
  }
}

// ---------- 批次辨識（一次多張，輸出各箭頭統計）----------
const batchResults = ref([])                     // [{name, detections, error?}]
const batchBusy = ref(false)
const batchProgress = reactive({ done: 0, total: 0 })

function fileToDataURL(file) {
  return new Promise((res) => {
    const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file)
  })
}
async function onBatchFiles(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  if (!files.length) return
  batchBusy.value = true
  batchResults.value = []
  batchProgress.total = files.length
  batchProgress.done = 0
  errorMsg.value = ''
  for (const f of files) {
    try {
      const dataURL = await fileToDataURL(f)
      const r = await api.infer(dataURL, cfg.conf, cfg.iou, cfg.model_path || null)
      // 每個框加一個 mark 欄位（null 未判定 / 1 對 / 0 錯）
      const dets = r.detections.map((d) => ({ ...d, mark: null }))
      const annotated = await renderAnnotated(dataURL, dets)  // 畫上編號框的圖
      batchResults.value.push({ name: f.name, annotated, detections: dets, latency: r.latency_ms })
    } catch (err) {
      batchResults.value.push({ name: f.name, annotated: '', detections: [], latency: null, error: err.message })
    }
    batchProgress.done++
  }
  batchBusy.value = false
}

// 把偵測框（含編號）畫到照片上，回傳 data URL 供顯示
function renderAnnotated(dataURL, detections) {
  return new Promise((resolve) => {
    const im = new Image()
    im.onload = () => {
      const cv = document.createElement('canvas')
      cv.width = im.naturalWidth; cv.height = im.naturalHeight
      const ctx = cv.getContext('2d')
      ctx.drawImage(im, 0, 0)
      const fs = Math.max(16, Math.round(cv.width / 34))
      ctx.lineWidth = Math.max(2, Math.round(cv.width / 300))
      ctx.font = `700 ${fs}px "Noto Sans TC", sans-serif`
      ctx.textBaseline = 'top'
      detections.forEach((d, i) => {
        const [x1, y1, x2, y2] = d.box
        const color = DET_COLORS[d.class_index % DET_COLORS.length]
        ctx.strokeStyle = color
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
        const label = `${i + 1}. ${d.class_name}`
        const tw = ctx.measureText(label).width + 10
        const ty = Math.max(0, y1 - fs - 6)
        ctx.fillStyle = color
        ctx.fillRect(x1, ty, tw, fs + 6)
        ctx.fillStyle = '#04283a'
        ctx.fillText(label, x1 + 5, ty + 3)
      })
      resolve(cv.toDataURL('image/jpeg', 0.85))
    }
    im.onerror = () => resolve(dataURL)
    im.src = dataURL
  })
}
// 標記某張圖第 idx 個框的對錯（再點同一個可取消）
function setMark(img, idx, val) {
  img.detections[idx].mark = img.detections[idx].mark === val ? null : val
}

// 各類別統計：偵測數、平均/最低/最高信心值
const batchSummary = computed(() => {
  const m = new Map()
  batchResults.value.forEach((img) => {
    img.detections.forEach((d) => {
      if (!m.has(d.class_index))
        m.set(d.class_index, { idx: d.class_index, name: d.class_name, confs: [], correct: 0, wrong: 0 })
      const e = m.get(d.class_index)
      e.confs.push(d.conf)
      if (d.mark === 1) e.correct++
      else if (d.mark === 0) e.wrong++
    })
  })
  return [...m.values()].map((c) => {
    const n = c.confs.length
    const marked = c.correct + c.wrong
    return {
      idx: c.idx, name: c.name, count: n,
      avg: n ? c.confs.reduce((a, b) => a + b, 0) / n : 0,
      min: n ? Math.min(...c.confs) : 0,
      max: n ? Math.max(...c.confs) : 0,
      correct: c.correct, marked, acc: marked ? c.correct / marked : null,
    }
  }).sort((a, b) => b.count - a.count)
})
const batchTotal = computed(() =>
  batchResults.value.reduce((s, i) => s + i.detections.length, 0)
)
// 平均每張推論時間
const avgLatency = computed(() => {
  const arr = batchResults.value.filter((i) => i.latency != null).map((i) => i.latency)
  return arr.length ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length) : null
})

// 下載 CSV（本機瀏覽器，可正常下載；含 UTF-8 BOM 防中文亂碼）
function downloadBatchCsv() {
  const rows = [['image', 'idx', 'class_id', 'class_name', 'conf', 'x1', 'y1', 'x2', 'y2', 'mark(1對/0錯)', 'latency_ms(每張)']]
  batchResults.value.forEach((img) => {
    const lat = img.latency != null ? img.latency : ''
    if (!img.detections.length) rows.push([img.name, '', '', '（無偵測）', '', '', '', '', '', '', lat])
    img.detections.forEach((d, i) => {
      const mark = d.mark === 1 ? 1 : d.mark === 0 ? 0 : ''
      rows.push([img.name, i + 1, d.class_index, d.class_name, d.conf.toFixed(3), ...d.box, mark, lat])
    })
  })
  const csv = '﻿' + rows.map((r) => r.map((v) => `"${v}"`).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'batch_detections.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

onBeforeUnmount(closeCamera)
</script>

<template>
  <div class="grid">
    <!-- 設定 + 輸入 -->
    <div class="col side">
      <div class="panel col">
        <strong>辨識設定</strong>
        <label class="field">模型
          <select v-model="cfg.model_path">
            <option value="">最新 best.pt（預設）</option>
            <option v-for="m in models" :key="m.path" :value="m.path">{{ m.name }}</option>
          </select>
        </label>
        <label class="field">信心值門檻 conf：{{ cfg.conf }}
          <input type="range" min="0.05" max="0.9" step="0.05" v-model.number="cfg.conf" />
        </label>
        <label class="field">NMS IoU：{{ cfg.iou }}
          <input type="range" min="0.1" max="0.9" step="0.05" v-model.number="cfg.iou" />
        </label>
      </div>

      <div class="panel col">
        <strong>上傳照片</strong>
        <label class="btn-full accent">📁 辨識箭頭
          <input type="file" accept="image/*" hidden @change="onFile" />
        </label>
        <label class="btn-full warn">📝 辨識文字（OCR）
          <input type="file" accept="image/*" hidden @change="onOcrFile" />
        </label>
        <label class="btn-full guide">🧭 產生導航提示
          <input type="file" accept="image/*" hidden @change="onGuideFile" />
        </label>
        <label class="btn-full batch">📂 批次辨識（多張）
          <input type="file" accept="image/*" multiple hidden @change="onBatchFiles" />
        </label>
      </div>
    </div>

    <!-- 主區：導航提示 + 攝影機 + 結果 -->
    <div class="col main">
      <!-- 批次辨識結果（各箭頭統計）-->
      <div class="panel col" v-if="batchBusy || batchResults.length">
        <div class="row" style="justify-content:space-between">
          <strong>📂 批次辨識結果</strong>
          <button v-if="batchResults.length && !batchBusy" class="secondary small"
                  @click="downloadBatchCsv">⬇ 下載 CSV</button>
        </div>
        <p v-if="batchBusy" class="muted">辨識中… {{ batchProgress.done }}/{{ batchProgress.total }}</p>
        <table class="tbl" v-if="batchSummary.length">
          <thead><tr><th>箭頭類別</th><th>偵測數</th><th>平均信心</th><th>最低</th><th>最高</th><th>人工準確率</th></tr></thead>
          <tbody>
            <tr v-for="c in batchSummary" :key="c.idx">
              <td>{{ c.name }}</td>
              <td>{{ c.count }}</td>
              <td>{{ (c.avg * 100).toFixed(0) }}%</td>
              <td>{{ (c.min * 100).toFixed(0) }}%</td>
              <td>{{ (c.max * 100).toFixed(0) }}%</td>
              <td>
                <span v-if="c.marked">{{ c.correct }}/{{ c.marked }}（{{ (c.acc * 100).toFixed(0) }}%）</span>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!batchBusy && !batchSummary.length" class="muted">這批照片沒有任何偵測結果。</p>
        <p v-if="batchResults.length" class="muted">
          共 {{ batchResults.length }} 張、{{ batchTotal }} 個偵測<span v-if="avgLatency != null"> · 平均每張推論 {{ avgLatency }} ms</span>。逐張核對箭頭並按「對(1)/錯(0)」，上表的人工準確率會自動更新。
        </p>

        <details v-if="batchResults.length" open>
          <summary class="muted" style="cursor:pointer">各張明細（核對對錯）</summary>
          <div class="batch-list">
            <div v-for="(img, i) in batchResults" :key="i" class="batch-item">
              <img v-if="img.annotated" :src="img.annotated" class="batch-img" />
              <div class="batch-dets">
                <div class="fn">
                  {{ img.name }}
                  <span v-if="img.latency != null" class="lat">⏱ {{ img.latency }} ms · {{ img.detections.length }} 箭頭</span>
                </div>
                <div v-for="(d, di) in img.detections" :key="di" class="det-row">
                  <span class="num" :style="{ background: DET_COLORS[d.class_index % DET_COLORS.length] }">{{ di + 1 }}</span>
                  <span class="dname">{{ d.class_name }} · {{ (d.conf * 100).toFixed(0) }}%</span>
                  <button class="mk ok" :class="{ on: d.mark === 1 }" @click="setMark(img, di, 1)">對 1</button>
                  <button class="mk ng" :class="{ on: d.mark === 0 }" @click="setMark(img, di, 0)">錯 0</button>
                </div>
                <p v-if="!img.detections.length" class="muted">（無偵測{{ img.error ? '：' + img.error : '' }}）</p>
              </div>
            </div>
          </div>
        </details>
      </div>

      <!-- 目標導向導航（本頁重點）-->
      <div class="panel col guide-panel">
        <div class="row" style="justify-content:space-between">
          <strong>🧭 導航提示</strong>
          <div class="row" style="gap:8px">
            <button v-if="navResult && navResult.sentence" class="secondary small" @click="speakNav">🔊 唸出來</button>
            <span v-if="guideBusy" class="muted">辨識中…</span>
          </div>
        </div>

        <!-- 目標輸入 -->
        <input v-model="navTarget" class="nav-input"
               placeholder="輸入要前往的路線或目標，如：淡水信義線、出口、台鐵"
               @keyup.enter="lastImage && lastMode === 'nav' && runNavigate(lastImage)" />
        <div class="chips">
          <span v-for="p in NAV_PRESETS" :key="p" class="chip" @click="navTarget = p">{{ p }}</span>
        </div>

        <!-- 結果 -->
        <template v-if="navResult">
          <div v-if="navResult.found && navResult.direction" class="guide-line">
            <span class="dir-badge">{{ navResult.direction }}</span>
            <span class="sentence">{{ navResult.sentence }}</span>
          </div>
          <p v-else class="guide-hint">{{ navResult.sentence || navResult.message }}</p>
          <img v-if="navResult.annotated_base64" :src="navResult.annotated_base64" class="result-img" />
          <p v-if="!navResult.found && navResult.texts_read && navResult.texts_read.length" class="muted">
            畫面讀到：{{ navResult.texts_read.join('、') }}
          </p>
          <p v-if="navResult.yolo_error" class="err">箭頭偵測：{{ navResult.yolo_error }}</p>
        </template>
        <p v-else class="guide-hint">
          先輸入目標，再按下方「🧭 拍一張導航」或左側「🧭 產生導航提示」上傳照片，<br>
          這裡會顯示像「<b>淡水信義線在左前的方向</b>」的指引。
        </p>
      </div>

      <div class="panel col">
        <strong>攝影機</strong>
        <div class="viewfinder" :class="{ on: camOn }">
          <video ref="video" playsinline muted autoplay></video>
          <canvas ref="overlay" class="det-overlay"></canvas>
          <button v-if="camOn" class="flip" @click="flipCamera" title="翻轉鏡頭">🔄</button>
          <span v-if="camOn && liveDetect && latency !== null" class="live-badge">
            即時偵測 · {{ latency }} ms · {{ detections.length }} 個
          </span>
          <span v-if="camOn && liveGuide && guideBusy" class="live-badge nav">導航辨識中…</span>
          <!-- 導航結果直接疊在鏡頭畫面上 -->
          <div v-if="camOn && navResult && navResult.sentence" class="nav-banner"
               :class="{ found: navResult.found && navResult.direction }">
            <span v-if="navResult.direction" class="dir-badge">{{ navResult.direction }}</span>
            <span class="nav-text">{{ navResult.sentence }}</span>
          </div>
          <div v-if="!camOn" class="vf-empty">
            <button class="accent" @click="startCamera">📷 開啟攝影機</button>
          </div>
        </div>

        <!-- 相機區直接輸入導航目標 -->
        <div v-if="camOn" class="cam-target">
          <span>🎯 導航目標</span>
          <input v-model="navTarget" placeholder="如：淡水信義線、出口、台鐵" />
          <span v-for="p in NAV_PRESETS" :key="p" class="chip" @click="navTarget = p">{{ p }}</span>
        </div>

        <div v-if="camOn" class="cam-controls">
          <button :class="liveGuide ? 'danger' : 'guide'" @click="toggleLiveGuide">
            {{ liveGuide ? '■ 停止連續導航' : '🧭 連續導航' }}
          </button>
          <button class="secondary" :class="{ von: voiceOn }" @click="voiceOn = !voiceOn">
            {{ voiceOn ? '🔊 語音開' : '🔈 語音關' }}
          </button>
          <button class="guide" :disabled="guideBusy" @click="captureGuide">🧭 拍一張導航</button>
          <button class="warn" :disabled="ocrBusy" @click="captureOcr">📝 拍一張讀文字</button>
          <button :class="liveDetect ? 'danger' : 'accent'" @click="toggleLive">
            {{ liveDetect ? '■ 停止即時偵測' : '🎯 即時偵測箭頭' }}
          </button>
          <button class="secondary" @click="flipCamera">🔄 翻轉鏡頭</button>
          <button class="secondary" @click="closeCamera">關閉</button>
        </div>
        <p v-if="errorMsg" class="err">{{ errorMsg }}</p>
      </div>

      <!-- 箭頭辨識結果 -->
      <div class="panel col" v-if="(photoImg && !liveOverlay) || detections.length">
        <div class="row" style="justify-content:space-between">
          <strong>箭頭辨識（{{ detections.length }}）</strong>
          <span v-if="latency !== null" class="tag"
                :style="{ color: realtimeOk ? 'var(--accent-2)' : 'var(--warn)' }">
            {{ latency }} ms
          </span>
        </div>

        <!-- 即時攝影機時框疊在畫面上；上傳/單張時用原圖 + 可篩選的疊框 -->
        <div v-if="photoImg && !liveOverlay" class="photo-result">
          <img ref="photoImgEl" :src="photoImg" class="result-img" @load="onPhotoLoad" />
          <canvas ref="photoOverlay" class="photo-overlay"></canvas>
        </div>

        <!-- 類別篩選：點一下只看該箭頭，再點取消 -->
        <div v-if="photoImg && !liveOverlay && classCounts.length" class="class-filter">
          <button :class="{ active: selectedClass === null }" @click="setClass(null)">
            全部 {{ detections.length }}
          </button>
          <button v-for="c in classCounts" :key="c.idx"
                  :class="{ active: selectedClass === c.idx }" @click="setClass(c.idx)">
            {{ c.name }} {{ c.count }}
          </button>
        </div>
        <p v-if="selectedClass !== null" class="muted">
          目前只顯示「{{ (classCounts.find(c => c.idx === selectedClass) || {}).name }}」，點「全部」或再點一次可取消。
        </p>

        <table class="tbl" v-if="detections.length">
          <thead><tr><th>類別</th><th>信心</th></tr></thead>
          <tbody>
            <tr v-for="(d, i) in filteredDetections" :key="i">
              <td>{{ d.class_name }}</td><td>{{ (d.conf * 100).toFixed(0) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- OCR 結果 -->
      <div class="panel col" v-if="ocrImg || ocrTexts.length || ocrBusy">
        <div class="row" style="justify-content:space-between">
          <strong>文字辨識（{{ ocrTexts.length }}）</strong>
          <span v-if="ocrLatency !== null" class="tag">{{ ocrLatency }} ms</span>
        </div>
        <p v-if="ocrBusy" class="muted">辨識中…（首次會下載中文模型）</p>
        <img v-if="ocrImg" :src="ocrImg" class="result-img" />
        <div v-if="ocrJoined" class="joined">📄 {{ ocrJoined }}</div>
        <table class="tbl" v-if="ocrTexts.length">
          <thead><tr><th>#</th><th>文字</th><th>信心</th></tr></thead>
          <tbody>
            <tr v-for="t in ocrTexts" :key="t.index">
              <td>{{ t.index }}</td><td>{{ t.text }}</td><td>{{ (t.conf * 100).toFixed(0) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 300px 1fr; gap: 16px; align-items: start; }
/* 手機：改為單欄堆疊，設定移到最上方 */
@media (max-width: 720px) {
  .grid { grid-template-columns: 1fr; }
}

.btn-full {
  display: block; text-align: center; padding: 10px 12px; border-radius: 8px;
  font-weight: 600; cursor: pointer;
}
.btn-full.accent { background: var(--accent); color: #04283a; }
.btn-full.warn { background: var(--warn); color: #3b2600; }
.btn-full.guide { background: var(--accent-2); color: #04321a; }
.btn-full.batch { background: var(--panel-2); color: var(--text); border: 1px solid var(--border); }
button.guide { background: var(--accent-2); color: #04321a; }
button.small { padding: 5px 10px; font-size: 13px; }

/* 批次明細：照片 + 對錯核對 */
.batch-list { display: flex; flex-direction: column; gap: 14px; margin-top: 10px; }
.batch-item {
  display: flex; gap: 12px; padding: 10px; border: 1px solid var(--border);
  border-radius: 10px; background: var(--panel-2); flex-wrap: wrap;
}
.batch-img { width: 260px; max-width: 100%; border-radius: 8px; align-self: flex-start; }
.batch-dets { flex: 1; min-width: 200px; display: flex; flex-direction: column; gap: 6px; }
.batch-dets .fn { font-weight: 600; font-size: 13px; word-break: break-all; }
.batch-dets .lat {
  margin-left: 6px; font-weight: 600; font-size: 11.5px; color: var(--accent);
  background: var(--panel); border: 1px solid var(--border); border-radius: 999px; padding: 1px 8px;
  white-space: nowrap;
}
.det-row { display: flex; align-items: center; gap: 8px; }
.det-row .num {
  width: 20px; height: 20px; border-radius: 5px; color: #04283a; font-weight: 700;
  font-size: 12px; display: grid; place-items: center; flex: none;
}
.det-row .dname { flex: 1; font-size: 13.5px; }
button.mk { padding: 3px 10px; font-size: 12.5px; background: var(--panel); color: var(--muted); border: 1px solid var(--border); }
button.mk.ok.on { background: var(--accent-2); color: #04321a; border-color: var(--accent-2); }
button.mk.ng.on { background: var(--danger); color: #fff; border-color: var(--danger); }
button.small { padding: 5px 10px; font-size: 13px; }

/* 導航提示輸出區 */
.guide-panel { border: 1px solid var(--accent-2); }
.guide-list { display: flex; flex-direction: column; gap: 10px; }
.guide-line {
  display: flex; align-items: center; gap: 12px;
  background: var(--panel-2); border-radius: 10px; padding: 12px 14px;
}
.dir-badge {
  flex: none; background: var(--accent-2); color: #04321a; font-weight: 700;
  padding: 4px 12px; border-radius: 8px; font-size: 15px; white-space: nowrap;
}
.sentence { font-size: 20px; font-weight: 600; color: var(--text); line-height: 1.4; }
.guide-hint { color: var(--muted); font-size: 15px; line-height: 1.7; margin: 4px 0; }
.nav-input { width: 100%; font-size: 15px; }
.guide-panel .chips { display: flex; flex-wrap: wrap; gap: 6px; }
.guide-panel .chip {
  cursor: pointer; padding: 3px 12px; border-radius: 999px; font-size: 13px;
  background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
}
.guide-panel .chip:hover { border-color: var(--accent-2); color: var(--accent-2); }

/* 取景框：video 以原始比例填滿寬度，疊層 canvas 對齊之，框才不會偏移 */
.viewfinder {
  position: relative; width: 100%; background: #000; border-radius: 10px;
  overflow: hidden; min-height: 220px;
}
.viewfinder video { width: 100%; height: auto; display: block; }
.viewfinder:not(.on) video { display: none; }
.det-overlay {
  position: absolute; left: 0; top: 0; width: 100%; height: 100%;
  pointer-events: none;
}
.viewfinder:not(.on) .det-overlay { display: none; }
.live-badge {
  position: absolute; left: 10px; top: 10px; background: rgba(0,0,0,.55);
  color: #fff; font-size: 12.5px; padding: 4px 9px; border-radius: 999px;
}
.live-badge.nav { top: 40px; background: rgba(14,124,134,.8); }
/* 導航結果橫幅：疊在鏡頭底部 */
.nav-banner {
  position: absolute; left: 0; right: 0; bottom: 0;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 12px 14px; background: rgba(0,0,0,.6); color: #fff;
}
.nav-banner.found { background: rgba(4,50,26,.82); }
.nav-banner .nav-text { font-size: 18px; font-weight: 700; }
.nav-banner .dir-badge { flex: none; }
/* 相機區的目標輸入列 */
.cam-target { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cam-target > span:first-child { font-size: 13px; color: var(--muted); white-space: nowrap; }
.cam-target input { flex: 1; min-width: 160px; }
.cam-target .chip {
  cursor: pointer; padding: 2px 10px; border-radius: 999px; font-size: 12.5px;
  background: var(--panel-2); border: 1px solid var(--border); color: var(--text);
}
.cam-target .chip:hover { border-color: var(--accent-2); color: var(--accent-2); }
.vf-empty { position: absolute; inset: 0; display: grid; place-items: center; }
.flip {
  position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,.5);
  color: #fff; border-radius: 50%; width: 42px; height: 42px; font-size: 18px; padding: 0;
}

.cam-controls { display: flex; flex-wrap: wrap; gap: 8px; }
.cam-controls button { flex: 1 1 auto; min-width: 120px; }
button.von { background: var(--accent-2); color: #04321a; border-color: var(--accent-2); }
button.accent { background: var(--accent); color: #04283a; }
button.warn { background: var(--warn); color: #3b2600; }

.result-img { max-width: 100%; border-radius: 8px; display: block; }
/* 上傳照片結果：原圖 + 疊框對齊 */
.photo-result { position: relative; display: inline-block; max-width: 100%; }
.photo-overlay { position: absolute; left: 0; top: 0; width: 100%; height: 100%; pointer-events: none; }
/* 類別篩選按鈕 */
.class-filter { display: flex; flex-wrap: wrap; gap: 6px; }
.class-filter button {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
  padding: 5px 11px; font-size: 13px; font-weight: 600;
}
.class-filter button.active { background: var(--accent); color: #04283a; border-color: var(--accent); }
.joined {
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; font-size: 16px; line-height: 1.6;
}
.err {
  background: color-mix(in srgb, var(--danger) 15%, transparent);
  border: 1px solid var(--danger); color: #fff; border-radius: 8px;
  padding: 8px 12px; font-size: 13.5px; margin: 0;
}
.tbl { width: 100%; border-collapse: collapse; }
.tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); font-size: 14px; }
</style>
