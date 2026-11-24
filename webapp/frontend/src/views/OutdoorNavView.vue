<script setup>
/*
 * OutdoorNavView — Agent C 語音導航（原 agent_c_navigation/static_index.html 移植）
 *   語音指令（按住說話）→ Agent C 後端 /api/command
 *   GPS 追蹤 + 地圖（Leaflet），走到路段終點自動推進 → /api/segment_complete
 *   走向上車處時回報 GPS → /api/position（進入台北車站範圍自動切換指標導引）
 *   台北車站指標導引：開後鏡頭，每秒送影格 → /api/sign_frame（Agent C 轉送 sign_detector）
 *   台北車站室內定位：「我在哪裡」→ 開鏡頭讀招牌 → /api/locate_frame，認出來播一次就關鏡頭
 *   找人幫忙：大字求助卡，給路人／站務人員看
 * Agent C 的 API 在合併後端的 /api/agent-c/*。
 */
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { navApi } from '../api.js'

const ARRIVAL_THRESHOLD_M = 20      // 距離路段終點小於這個公尺數，視為走到了
const AUTO_ADVANCE_STAGES = ['navigating_walk', 'navigating_transit_before', 'navigating_transit_after']
const GUIDE_INTERVAL_MS = 1000       // 指標導引每秒送一張影格（OCR 較慢，太快只會排隊）

const mapEl = ref(null)
const logEl = ref(null)
const guideVideo = ref(null)
const guideOverlay = ref(null)

const gpsOk = ref(null)              // null=定位中 / true / false
const gpsMsg = ref('定位中...')
const speechOk = ref(false)
const speechMsg = ref('語音辨識檢查中...')
const listening = ref(false)
const stage = ref('idle')
const bubbles = ref([])
const typed = ref('')
const guideActive = ref(false)    // 鏡頭區塊是否顯示（指標導引或室內定位任一在用）
const guideInfo = ref('')
const helpCard = ref('')          // 找人幫忙的大字卡內容
let guideTarget = null            // 指標導引目前要找的指標（null = 沒在導引）
// 羅盤：使用者面向（0=北，順時針）。地下鋼筋會干擾磁力計，所以只送「新鮮且精度可接受」的值
let compass = { heading: null, at: 0 }
let locating = false              // 室內定位掃描中
const confirmReset = ref(false)

let lat = null, lng = null
let map = null, userMarker = null, destMarker = null, routeLine = null
let mapFitted = false, lastHeading = null
let walkSteps = [], stepIndex = 0
let announcedGpsOk = false, announcedGpsFail = false
let geoWatchId = null, recognition = null, listenTimeout = null
let lastPositionReportAt = 0
let guideStream = null, guideRunning = false, lastGuideError = ''
let frameW = 0, frameH = 0

// ===== 工具 =====
function haversine(la1, ln1, la2, ln2) {
  const R = 6371000, r = (d) => d * Math.PI / 180
  const a = Math.sin(r(la2 - la1) / 2) ** 2 + Math.cos(r(la1)) * Math.cos(r(la2)) * Math.sin(r(ln2 - ln1) / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function speak(text) {
  if (!text || !('speechSynthesis' in window)) return
  // 新內容一律插隊（barge-in），不排在舊語音後面
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-TW'
  window.speechSynthesis.speak(u)
}

async function addBubble(text, who) {
  if (!text) return
  bubbles.value.push({ text, who, id: Date.now() + Math.random() })
  await nextTick()
  if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
}

// ===== 地圖 =====
const destIcon = L.divIcon({ className: '', html: '<div class="dest-dot"></div>', iconSize: [16, 16] })

function userIcon(heading) {
  const has = heading !== null && heading !== undefined && !isNaN(heading)
  const cone = has ? '<div class="user-cone"></div>' : ''
  const rot = has ? `transform:rotate(${heading}deg);transform-origin:12px 12px;` : ''
  return L.divIcon({
    className: '',
    html: `<div style="width:24px;height:24px;position:relative;${rot}">${cone}<div class="user-dot"></div></div>`,
    iconSize: [24, 24], iconAnchor: [12, 12],
  })
}

function updateUserMarker(heading) {
  if (heading !== null && heading !== undefined && !isNaN(heading)) lastHeading = heading
  const icon = userIcon(lastHeading)
  if (!userMarker) userMarker = L.marker([lat, lng], { icon }).addTo(map)
  else { userMarker.setLatLng([lat, lng]); userMarker.setIcon(icon) }
}

function updateDestMarker(dLat, dLng) {
  if (dLat == null || dLng == null) return
  if (!destMarker) destMarker = L.marker([dLat, dLng], { icon: destIcon }).addTo(map)
  else destMarker.setLatLng([dLat, dLng])
  fitMap()
}

function drawRoute(steps) {
  if (routeLine) { map.removeLayer(routeLine); routeLine = null }
  if (!steps || !steps.length) return
  const pts = lat != null ? [[lat, lng]] : []
  steps.forEach((s) => { if (s.end_lat != null && s.end_lng != null) pts.push([s.end_lat, s.end_lng]) })
  if (pts.length < 2) return
  routeLine = L.polyline(pts, { color: '#4ade80', weight: 4, opacity: 0.8 }).addTo(map)
  fitMap()
}

function clearRoute() {
  walkSteps = []; stepIndex = 0
  if (routeLine) { map.removeLayer(routeLine); routeLine = null }
}

function fitMap() {
  const pts = []
  if (userMarker) pts.push(userMarker.getLatLng())
  if (destMarker) pts.push(destMarker.getLatLng())
  if (routeLine) routeLine.getLatLngs().forEach((p) => pts.push(p))
  if (pts.length >= 2) map.fitBounds(L.latLngBounds(pts), { padding: [30, 30] })
  else if (pts.length === 1) map.setView(pts[0], 16)
}

// ===== 後端互動 =====
function handleResponse(data) {
  stage.value = data.stage
  addBubble(data.reply, 'system')
  speak(data.reply)
  updateDestMarker(data.dest_lat, data.dest_lng)

  // 進入 GPS 自動推進的路段：後端附上完整步驟（含座標）
  if (data.walk_steps && data.walk_steps.length) {
    walkSteps = data.walk_steps; stepIndex = 0
    drawRoute(walkSteps)
  }
  // 台北車站指標導引：有 guide_target 就開鏡頭，離開這個階段就停
  guideTarget = (data.stage === 'station_guidance' && data.guide_target) ? data.guide_target : null
  // 台北車站室內定位：後端說 locate 就開始掃招牌
  if (data.locate) locating = true
  if (data.help_card) helpCard.value = data.help_card
  syncCamera()

  if (data.stage === 'idle') clearRoute()
}

async function sendCommand(text) {
  try {
    handleResponse(await navApi.command(text, lat, lng))
  } catch (e) {
    addBubble('（連線失敗，請確認後端 uvicorn main:app 有在跑）', 'system')
    speak('連線失敗，請確認導航後端有在執行')
  }
}

function sendTyped() {
  const t = typed.value.trim()
  if (!t) return
  typed.value = ''
  addBubble(t, 'user')
  sendCommand(t)
}

async function notifySegmentComplete() {
  try {
    handleResponse(await navApi.segmentComplete(lat, lng))
  } catch (e) {
    addBubble('（連線失敗，無法通知路段完成）', 'system')
    speak('連線失敗，暫時無法更新導航進度，建議停下腳步確認網路連線')
  }
}

function checkAutoAdvance() {
  if (!AUTO_ADVANCE_STAGES.includes(stage.value)) return
  if (!walkSteps.length || stepIndex >= walkSteps.length || lat == null) return
  const t = walkSteps[stepIndex]
  if (t.end_lat == null || t.end_lng == null) return
  if (haversine(lat, lng, t.end_lat, t.end_lng) > ARRIVAL_THRESHOLD_M) return
  stepIndex++
  if (stepIndex < walkSteps.length) {
    const next = walkSteps[stepIndex].voice_text
    addBubble(next, 'system'); speak(next)
  } else {
    notifySegmentComplete()
  }
}

async function reportPositionForStation() {
  if (stage.value !== 'navigating_transit_before') return
  const now = Date.now()
  if (now - lastPositionReportAt < 5000) return
  lastPositionReportAt = now
  try {
    const data = await navApi.position(lat, lng)
    if (data.changed) handleResponse(data)
  } catch (_) {}
}

async function doReset() {
  confirmReset.value = false
  try {
    const data = await navApi.reset()
    stage.value = data.stage
  } catch (_) {}
  guideTarget = null; locating = false; helpCard.value = ''
  syncCamera(); clearRoute()
  bubbles.value = []
  addBubble('已重新開始對話', 'system'); speak('已重新開始對話')
}

// ===== GPS =====
function startGps() {
  if (!('geolocation' in navigator)) {
    gpsOk.value = false; gpsMsg.value = '此瀏覽器不支援定位'
    speak('這個瀏覽器不支援定位功能，無法使用導航')
    return
  }
  geoWatchId = navigator.geolocation.watchPosition(
    (pos) => {
      lat = pos.coords.latitude; lng = pos.coords.longitude
      gpsOk.value = true; gpsMsg.value = '定位成功'
      if (!announcedGpsOk) { announcedGpsOk = true; speak('定位成功，可以開始使用') }
      updateUserMarker(pos.coords.heading)
      if (!mapFitted && !destMarker) { map.setView([lat, lng], 16); mapFitted = true }
      checkAutoAdvance()
      reportPositionForStation()
    },
    (err) => {
      gpsOk.value = false; gpsMsg.value = `定位失敗：${err.message}`
      if (!announcedGpsFail) { announcedGpsFail = true; speak('定位失敗，請確認已允許定位權限，否則無法開始導航') }
    },
    { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
  )
}

// ===== 語音辨識（按住說話）=====
function setupSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SR) {
    speechMsg.value = '此瀏覽器不支援語音辨識（iOS 皆不支援），可用下方文字輸入'
    return
  }
  recognition = new SR()
  recognition.lang = 'zh-TW'
  recognition.continuous = false
  recognition.interimResults = false
  recognition.onresult = (e) => {
    const text = e.results[0][0].transcript
    addBubble(text, 'user'); sendCommand(text)
  }
  recognition.onerror = (e) => {
    clearTimeout(listenTimeout); listening.value = false
    addBubble(`（語音辨識錯誤：${e.error}）`, 'system')
    if (e.error === 'not-allowed' || e.error === 'permission-denied') speak('麥克風權限被拒絕，請到瀏覽器設定允許麥克風權限')
    else if (e.error === 'no-speech') speak('沒有聽到聲音，請再說一次')
    else speak('語音辨識發生錯誤，請再試一次')
  }
  recognition.onend = () => { clearTimeout(listenTimeout); listening.value = false }
  speechOk.value = true
  speechMsg.value = '語音辨識就緒'
}

function startListening() {
  if (!recognition) { speak('此瀏覽器不支援語音辨識，建議改用 Android 的 Chrome'); return }
  window.speechSynthesis.cancel()   // 一按就打斷系統正在講的話
  listening.value = true
  try { recognition.start() } catch (_) {}
  clearTimeout(listenTimeout)
  listenTimeout = setTimeout(() => { listening.value = false }, 10000)
}
function stopListening() {
  if (recognition) { try { recognition.stop() } catch (_) {} }
}

// ===== 鏡頭（指標導引、室內定位共用一個）=====
function cameraLabel() {
  if (locating) return '正在看附近的店家招牌…'
  if (guideTarget) return `正在找「${guideTarget}」的指標`
  return ''
}

// 有任何一種模式在用就開鏡頭，都沒有就關
function syncCamera() {
  if (locating || guideTarget) { guideInfo.value = cameraLabel(); startCamera() }
  else stopCamera()
}

async function startCamera() {
  guideActive.value = true
  if (guideRunning) return
  guideRunning = true
  try {
    guideStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
    await nextTick()
    guideVideo.value.srcObject = guideStream
    await guideVideo.value.play()
    cameraLoop()
  } catch (e) {
    guideRunning = false
    const msg = e.name === 'NotAllowedError' ? '請允許相機權限' : (location.protocol !== 'https:' ? '需用 https 開啟' : e.message)
    addBubble(`（無法開啟相機：${msg}）`, 'system')
    speak('無法開啟相機，請允許相機權限，或說「找人幫忙」')
  }
}

function stopCamera() {
  guideRunning = false
  guideActive.value = false
  if (guideStream) guideStream.getTracks().forEach((t) => t.stop())
  guideStream = null
  drawGuide(null)
}

// 停止室內定位（按鈕「不用找了」）
async function stopLocate() {
  locating = false
  syncCamera()
  try { await navApi.command('不用找了', lat, lng) } catch (_) {}
}

// ===== 羅盤 =====
function onOrientation(e) {
  if (typeof e.webkitCompassHeading === 'number') {          // iOS
    if (e.webkitCompassAccuracy == null || (e.webkitCompassAccuracy >= 0 && e.webkitCompassAccuracy <= 30)) {
      compass = { heading: e.webkitCompassHeading, at: Date.now() }
    }
  } else if (e.absolute && typeof e.alpha === 'number') {    // Android（deviceorientationabsolute）
    compass = { heading: (360 - e.alpha) % 360, at: Date.now() }
  }
}
function startCompass() {
  window.addEventListener('deviceorientationabsolute', onOrientation)
  window.addEventListener('deviceorientation', onOrientation)
}
async function askCompassPermission() {
  // iOS 13+ 要在使用者點擊時請求權限
  try {
    if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
      await DeviceOrientationEvent.requestPermission()
    }
  } catch (_) {}
}
function currentHeading() {
  return compass.heading != null && Date.now() - compass.at < 2000 ? Math.round(compass.heading) : null
}

function locateBtn() { askCompassPermission(); addBubble('我在哪裡', 'user'); sendCommand('我在哪裡') }
function helpBtn() { addBubble('找人幫忙', 'user'); sendCommand('找人幫忙') }

function grabFrame() {
  const v = guideVideo.value
  if (!v || !v.videoWidth) return null
  const scale = Math.min(1, 720 / Math.max(v.videoWidth, v.videoHeight))
  frameW = Math.round(v.videoWidth * scale); frameH = Math.round(v.videoHeight * scale)
  const c = document.createElement('canvas')
  c.width = frameW; c.height = frameH
  c.getContext('2d').drawImage(v, 0, 0, frameW, frameH)
  return c.toDataURL('image/jpeg', 0.7)
}

// 疊框：目標（黃）、箭頭（青）、連線（綠），座標系 = 送出影格的尺寸
function drawGuide(r) {
  const cv = guideOverlay.value
  if (!cv) return
  if (!r || !frameW) { cv.getContext('2d').clearRect(0, 0, cv.width, cv.height); return }
  cv.width = frameW; cv.height = frameH
  const ctx = cv.getContext('2d'), lw = Math.max(3, Math.round(frameW / 220))
  ctx.clearRect(0, 0, cv.width, cv.height)
  const box = (b, col) => { ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.strokeRect(b[0], b[1], b[2] - b[0], b[3] - b[1]) }
  if (r.matched_box) box(r.matched_box, '#ffeb3b')
  if (r.arrow_box) box(r.arrow_box, '#38bdf8')
  if (r.matched_box && r.arrow_box) {
    const m = r.matched_box, a = r.arrow_box
    ctx.strokeStyle = '#00e676'; ctx.beginPath()
    ctx.moveTo((m[0] + m[2]) / 2, (m[1] + m[3]) / 2); ctx.lineTo((a[0] + a[2]) / 2, (a[1] + a[3]) / 2); ctx.stroke()
  }
}

function reportError(err) {
  if (err && err !== lastGuideError) {
    lastGuideError = err
    addBubble(`（辨識：${err}）`, 'system')
  }
}

async function locateStep(frame) {
  const d = await navApi.locateFrame(frame, lat, lng, currentHeading())
  reportError(d.error)
  if (d.speak && d.text) { addBubble(d.text, 'system'); speak(d.text) }
  if (!d.active) { locating = false; syncCamera() }
}

async function guideStep(frame) {
  const d = await navApi.signFrame(frame, lat, lng)
  if (!d.active) { guideTarget = null; syncCamera(); return }
  guideInfo.value = d.found && d.direction ? `「${d.guide_target}」：${d.direction}` : `正在找「${d.guide_target}」的指標`
  drawGuide(d.found ? d : null)
  if (d.error && d.error !== lastGuideError) {
    reportError(d.error)
    speak('指標辨識服務沒有回應，走到指標旁時可以直接說「到了」')
  }
  if (d.speak && d.text) { addBubble(d.text, 'system'); speak(d.text) }
}

// 室內定位優先（使用者主動問的），定位完再回到指標導引
async function cameraLoop() {
  while (guideRunning) {
    const started = Date.now()
    const frame = grabFrame()
    if (frame) {
      try {
        if (locating) { drawGuide(null); guideInfo.value = cameraLabel(); await locateStep(frame) }
        else if (guideTarget) await guideStep(frame)
      } catch (_) {}
    }
    const wait = GUIDE_INTERVAL_MS - (Date.now() - started)
    if (wait > 0) await new Promise((r) => setTimeout(r, wait))
  }
}

// ===== 生命週期 =====
onMounted(() => {
  map = L.map(mapEl.value).setView([25.0375, 121.5637], 15)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap', maxZoom: 19 }).addTo(map)
  startGps()
  setupSpeech()
  startCompass()
})

onBeforeUnmount(() => {
  stopCamera()
  if (geoWatchId != null) navigator.geolocation.clearWatch(geoWatchId)
  if (recognition) { try { recognition.abort() } catch (_) {} }
  window.speechSynthesis && window.speechSynthesis.cancel()
  if (map) map.remove()
  window.removeEventListener('deviceorientationabsolute', onOrientation)
  window.removeEventListener('deviceorientation', onOrientation)
})
</script>

<template>
  <div class="onav">
    <div class="status-bar">
      <span><span class="dot" :class="gpsOk ? 'ok' : 'bad'"></span>{{ gpsMsg }}</span>
      <span>／</span>
      <span><span class="dot" :class="speechOk ? 'ok' : 'bad'"></span>{{ speechMsg }}</span>
    </div>

    <div ref="mapEl" class="map"></div>
    <div class="legend">
      <span><i style="background:#4f6bff"></i>目前位置</span>
      <span><i style="background:#f87171"></i>目的地</span>
      <span><i style="background:#4ade80"></i>路線</span>
    </div>

    <div class="hint">
      <b>常用語音指令：</b><br>
      <span class="chip">導航去[地點]</span>開始導航
      <span class="chip">換一個</span>換路線方案
      <span class="chip">好 / 開始</span>確認出發
      <span class="chip">取消</span>隨時中斷導航
      <span class="chip">上車了</span><span class="chip">下車了</span>搭乘大眾運輸時確認
      <span class="chip">到了</span>台北車站站內：走到目前指標後說
      <span class="chip">我在哪裡</span>讀附近招牌，告訴你在哪、靠近哪個出口
      <span class="chip">找人幫忙</span>顯示大字求助卡
    </div>

    <div v-if="AUTO_ADVANCE_STAGES.includes(stage)" class="nav-status">
      🚶 導航進行中，系統會依你走到的位置自動報路，只有想取消時才需要說話
    </div>

    <div v-show="guideActive" class="guide">
      <div class="guide-cam">
        <video ref="guideVideo" playsinline muted autoplay></video>
        <canvas ref="guideOverlay"></canvas>
      </div>
      <div class="guide-info">
        📷 {{ guideInfo }}
        <button v-if="guideInfo.startsWith('正在看附近')" class="secondary small" @click="stopLocate">不用找了</button>
      </div>
    </div>

    <div class="quick">
      <button class="quick-btn locate" @click="locateBtn">📍 我在哪裡</button>
      <button class="quick-btn help" @click="helpBtn">🙋 找人幫忙</button>
    </div>

    <button class="talk" :class="{ listening }"
            @mousedown="startListening" @mouseup="stopListening"
            @touchstart.prevent="startListening" @touchend.prevent="stopListening" @touchcancel.prevent="stopListening">
      按住<br>說話
    </button>

    <form class="typed" @submit.prevent="sendTyped">
      <input v-model="typed" placeholder="或直接打字，例如：導航去台北車站" />
      <button type="submit">送出</button>
    </form>

    <div ref="logEl" class="log" aria-live="polite">
      <div v-for="b in bubbles" :key="b.id" class="bubble" :class="b.who">{{ b.text }}</div>
    </div>

    <div v-if="helpCard" class="help-card" role="dialog" aria-label="求助訊息" @click="helpCard = ''">
      <p>{{ helpCard }}</p>
      <span class="help-close">點一下關閉</span>
    </div>

    <div class="reset">
      <template v-if="!confirmReset">
        <button class="secondary" @click="confirmReset = true">重新開始對話</button>
      </template>
      <template v-else>
        <span class="muted">目前的導航會被中斷，確定？</span>
        <button class="danger" @click="doReset">確定重來</button>
        <button class="secondary" @click="confirmReset = false">取消</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.onav { max-width: 520px; margin: 0 auto; display: flex; flex-direction: column; align-items: stretch; gap: 10px; }
.status-bar { font-size: 12px; color: var(--muted); text-align: center; display: flex; gap: 6px; justify-content: center; flex-wrap: wrap; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
.dot.ok { background: #4ade80; } .dot.bad { background: #f87171; }
.map { width: 100%; height: 240px; border-radius: 12px; border: 1px solid var(--border); z-index: 0; }
.legend { font-size: 11px; color: var(--muted); display: flex; gap: 14px; }
.legend i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 4px; }
.hint { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; font-size: 12.5px; color: var(--muted); line-height: 1.8; }
.hint b { color: var(--text); }
.chip { display: inline-block; background: var(--panel-2); color: var(--accent); padding: 1px 7px; border-radius: 6px; margin: 1px 2px; font-size: 11.5px; }
.nav-status { background: #16241c; border: 1px solid #234a34; border-radius: 10px; padding: 8px 14px; font-size: 12.5px; color: #8fe0b0; }
.guide { border: 2px solid #facc15; border-radius: 12px; overflow: hidden; background: #000; }
.guide-cam { position: relative; }
.guide-cam video { width: 100%; max-height: 300px; object-fit: cover; display: block; }
.guide-cam canvas { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; pointer-events: none; }
.guide-info { padding: 8px 12px; font-size: 15px; font-weight: 600; color: #facc15; background: #1a1d24; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.small { padding: 6px 10px; font-size: 13px; }
.quick { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.quick-btn { min-height: 64px; font-size: 19px; border-radius: 14px; }
.quick-btn.locate { background: #facc15; color: #1a1400; }
.quick-btn.help { background: #f97316; color: #1f0b00; }
.help-card { position: fixed; inset: 0; z-index: 100; background: #fff; color: #000; display: flex; flex-direction: column;
  justify-content: center; align-items: center; padding: 28px; gap: 24px; cursor: pointer; }
.help-card p { font-size: clamp(30px, 8vw, 46px); font-weight: 800; line-height: 1.45; margin: 0; text-align: left; }
.help-close { font-size: 16px; color: #555; }
.talk { align-self: center; width: 130px; height: 130px; border-radius: 50%; border: none; color: #fff; font-size: 16px; font-weight: 600;
  background: linear-gradient(145deg, #4f6bff, #3a4fd8); box-shadow: 0 8px 24px rgba(79,107,255,.4); margin: 8px 0; transition: transform .15s; user-select: none; -webkit-user-select: none; }
.talk.listening { transform: scale(.95); background: linear-gradient(145deg, #ff5f5f, #d83a3a); animation: pulse 1s infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(248,113,113,.5) } 70% { box-shadow: 0 0 0 20px rgba(248,113,113,0) } 100% { box-shadow: 0 0 0 0 rgba(248,113,113,0) } }
@media (prefers-reduced-motion: reduce) { .talk.listening { animation: none; } }
.typed { display: flex; gap: 8px; }
.typed input { flex: 1; font-size: 16px; padding: 10px 12px; }
.log { display: flex; flex-direction: column; gap: 10px; max-height: 420px; overflow-y: auto; }
.bubble { padding: 12px 16px; border-radius: 14px; font-size: 15px; line-height: 1.5; max-width: 88%; }
.bubble.user { align-self: flex-end; background: var(--panel-2); }
.bubble.system { align-self: flex-start; background: #1c2c4a; color: #d6e2ff; }
.reset { display: flex; gap: 8px; align-items: center; justify-content: center; flex-wrap: wrap; margin-top: 8px; }
:deep(.dest-dot) { width: 16px; height: 16px; background: #f87171; border: 3px solid #fff; border-radius: 50%; box-shadow: 0 0 6px rgba(0,0,0,.5); }
:deep(.user-dot) { position: absolute; top: 4px; left: 4px; width: 16px; height: 16px; background: #4f6bff; border: 3px solid #fff; border-radius: 50%; box-shadow: 0 0 6px rgba(0,0,0,.5); }
:deep(.user-cone) { position: absolute; top: -8px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 7px solid transparent; border-right: 7px solid transparent; border-bottom: 12px solid #4f6bff; opacity: .9; }
</style>
