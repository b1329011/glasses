<script setup>
/*
 * NavView — 手機全螢幕導航畫面
 * 版面：上(輸入目的) / 中(整片鏡頭 + 疊框) / 下(輸出方向句)
 * 重用後端 /api/infer(箭頭，快) + /api/navigate(目標+方向，較慢)。
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api.js'

const video = ref(null)
const overlay = ref(null)
const target = ref('')
const sentence = ref('')
const found = ref(false)
const camOn = ref(false)
const voiceOn = ref(true)
const errMsg = ref('')

let stream = null
let running = false
let tick = 0
let busy = false
let navBusy = false
let navResult = null
let detections = []
let lastSpoken = ''

const DET = ['#38bdf8', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#ec4899', '#14b8a6', '#eab308']

async function start() {
  errMsg.value = ''
  if (camOn.value) return
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
    video.value.srcObject = stream
    await video.value.play()
    camOn.value = true
    running = true
    loop()
  } catch (e) {
    errMsg.value = '無法開啟相機：' + (e.name === 'NotAllowedError' ? '請允許相機權限' :
      (location.protocol !== 'https:' ? '需用 https 開啟' : e.message))
  }
}
function stop() {
  running = false
  camOn.value = false
  if (stream) stream.getTracks().forEach((t) => t.stop())
  stream = null
  clearCanvas()
}
let frameW = 0, frameH = 0
function grab() {
  const v = video.value
  if (!v || !v.videoWidth) return null
  // 先縮到最長邊 720 再傳，減少上傳量(tunnel 特別有感)
  const scale = Math.min(1, 720 / Math.max(v.videoWidth, v.videoHeight))
  frameW = Math.round(v.videoWidth * scale)
  frameH = Math.round(v.videoHeight * scale)
  const c = document.createElement('canvas')
  c.width = frameW; c.height = frameH
  c.getContext('2d').drawImage(v, 0, 0, frameW, frameH)
  return c.toDataURL('image/jpeg', 0.7)
}
// 自走式：箭頭每輪更新(快)，方向句每約 12 輪更新(慢)
async function loop() {
  while (running) {
    const data = grab()
    if (!data) { await new Promise((r) => setTimeout(r, 120)); continue }
    await updateArrows(data)
    tick++
    if (target.value.trim() && tick % 12 === 0 && !navBusy) refreshNav(grab())
  }
}
async function updateArrows(data) {
  busy = true
  try {
    const r = await api.infer(data, 0.25, 0.45, null, 640)
    detections = r.detections || []
    draw()
  } catch (_) {} finally { busy = false }
}
async function refreshNav(data) {
  if (!data) return
  navBusy = true
  try {
    const r = await api.navigate(data, target.value.trim(), 0.25, 0.45, null, { no_full_ocr: true, imgsz: 640 })
    navResult = r
    found.value = !!r.found
    sentence.value = r.found ? r.sentence : (r.message || '')
    draw()
    if (voiceOn.value && r.found && r.sentence && r.sentence !== lastSpoken) {
      lastSpoken = r.sentence; speak(r.sentence)
    }
  } catch (_) {} finally { navBusy = false }
}
function speak(t) {
  if (!('speechSynthesis' in window) || !t) return
  const u = new SpeechSynthesisUtterance(t); u.lang = 'zh-TW'
  window.speechSynthesis.cancel(); window.speechSynthesis.speak(u)
}
// 手動搜尋：立刻跑一次導航
function go() {
  if (!camOn.value) { start(); return }
  if (target.value.trim() && !navBusy) refreshNav(grab())
}

function clearCanvas() {
  const cv = overlay.value
  if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height)
}
function draw() {
  const cv = overlay.value
  if (!cv || !frameW) return
  // canvas 解析度 = 送出影格的尺寸，座標才對齊；CSS object-fit:cover 跟 video 一致裁切
  cv.width = frameW; cv.height = frameH
  const ctx = cv.getContext('2d')
  ctx.clearRect(0, 0, cv.width, cv.height)
  const fs = Math.max(18, Math.round(cv.width / 30))
  const lw = Math.max(3, Math.round(cv.width / 260))
  ctx.font = `700 ${fs}px "Noto Sans TC", sans-serif`
  ctx.textBaseline = 'top'
  // 箭頭
  detections.forEach((d) => {
    const [x1, y1, x2, y2] = d.box
    const col = DET[d.class_index % DET.length]
    ctx.strokeStyle = col; ctx.lineWidth = lw
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    const label = `${d.class_name} ${(d.conf * 100).toFixed(0)}%`
    const tw = ctx.measureText(label).width + 10
    ctx.fillStyle = col; ctx.fillRect(x1, Math.max(0, y1 - fs - 6), tw, fs + 6)
    ctx.fillStyle = '#04283a'; ctx.fillText(label, x1 + 5, Math.max(0, y1 - fs - 3))
  })
  // 導航結果：目標黃框 + 綠連線 + 箭頭框
  const nr = navResult
  if (nr && nr.found) {
    if (nr.matched_box) {
      const [x1, y1, x2, y2] = nr.matched_box
      ctx.strokeStyle = '#ffeb3b'; ctx.lineWidth = lw + 1
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    }
    if (nr.arrow_box && nr.matched_box) {
      const [ax1, ay1, ax2, ay2] = nr.arrow_box
      const [mx1, my1, mx2, my2] = nr.matched_box
      ctx.strokeStyle = '#00e676'; ctx.lineWidth = lw
      ctx.beginPath()
      ctx.moveTo((mx1 + mx2) / 2, (my1 + my2) / 2)
      ctx.lineTo((ax1 + ax2) / 2, (ay1 + ay2) / 2)
      ctx.stroke()
    }
  }
}

onMounted(() => {})
onBeforeUnmount(stop)
</script>

<template>
  <div class="nav-screen">
    <!-- 上：輸入目的 -->
    <div class="top-bar">
      <input v-model="target" class="target-input" placeholder="輸入目的，如：廁所、出口、淡水信義線"
             @keyup.enter="go" />
      <button class="go-btn" @click="go">🔍</button>
    </div>

    <!-- 中：整片鏡頭 -->
    <div class="cam-area">
      <video ref="video" playsinline muted autoplay></video>
      <canvas ref="overlay" class="overlay"></canvas>
      <div v-if="!camOn" class="cam-off">
        <button class="start-btn" @click="start">📷 開啟相機</button>
        <p v-if="errMsg" class="err">{{ errMsg }}</p>
      </div>
      <!-- 右上小工具 -->
      <div v-if="camOn" class="tools">
        <button @click="voiceOn = !voiceOn">{{ voiceOn ? '🔊' : '🔈' }}</button>
        <button @click="stop">✕</button>
      </div>
    </div>

    <!-- 下：輸出方向句 -->
    <div class="result-bar" :class="{ found }">
      <span v-if="sentence">{{ sentence }}</span>
      <span v-else class="hint">對準指標牌，上方輸入目的 → 這裡會顯示方向</span>
    </div>
  </div>
</template>

<style scoped>
.nav-screen {
  position: fixed; inset: 0; z-index: 50;
  display: flex; flex-direction: column;
  background: #000; color: #fff;
}
.top-bar {
  display: flex; gap: 8px; padding: 10px 12px;
  background: #0f172a; align-items: center; flex: none;
}
.target-input {
  flex: 1; font-size: 18px; padding: 12px 14px; border-radius: 10px;
  background: #1e293b; color: #fff; border: 1px solid #334155;
}
.go-btn {
  flex: none; width: 50px; height: 48px; font-size: 20px; border-radius: 10px;
  background: #0e7c86; color: #fff; border: none;
}
.cam-area { position: relative; flex: 1; min-height: 0; background: #000; overflow: hidden; }
.cam-area video { width: 100%; height: 100%; object-fit: cover; display: block; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; pointer-events: none; }
.cam-off { position: absolute; inset: 0; display: grid; place-items: center; gap: 12px; }
.start-btn { font-size: 18px; padding: 14px 22px; border-radius: 12px; background: #0e7c86; color: #fff; border: none; }
.err { color: #fca5a5; font-size: 14px; max-width: 80%; text-align: center; }
.tools { position: absolute; top: 10px; right: 10px; display: flex; gap: 8px; }
.tools button { width: 44px; height: 44px; font-size: 18px; border-radius: 50%; background: rgba(0,0,0,.55); color: #fff; border: none; }
.result-bar {
  flex: none; min-height: 84px; display: flex; align-items: center; justify-content: center;
  padding: 16px 18px; text-align: center; background: #0f172a;
  font-size: 24px; font-weight: 700; line-height: 1.4;
}
.result-bar.found { background: #04321a; color: #fff; }
.result-bar .hint { font-size: 15px; font-weight: 400; color: #94a3b8; }
</style>
