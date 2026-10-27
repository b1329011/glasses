<script setup>
/*
 * AnnotateView — 標註頁
 * 功能：上傳訓練照片、在照片上框出箭頭位置、指定方向類別（可新增）、儲存
 * 座標處理：畫布上以「原圖像素座標」儲存框，存檔時轉成 YOLO 正規化 (cx,cy,w,h)
 */
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { api } from '../api.js'

// --- 狀態 ---
const images = ref([])            // 影像列表
const classes = ref([])           // 類別列表
const currentImage = ref(null)    // 目前選取的影像 {id,filename,width,height}
const boxes = ref([])             // 目前影像的框：{x,y,w,h,class_index}（原圖像素）
const currentClass = ref(0)       // 新框要標的類別
const status = ref('')
const naturalSize = reactive({ w: 0, h: 0 })
const displaySize = reactive({ w: 0, h: 0 })

// 新照片 / 未標註 篩選與統計
const onlyNew = ref(false)
const onlyUnlabeled = ref(false)
const newCount = computed(() => images.value.filter((i) => i.is_new).length)
const unlabeledCount = computed(() => images.value.filter((i) => !i.num_boxes).length)
const filteredImages = computed(() =>
  images.value.filter((i) =>
    (!onlyNew.value || i.is_new) && (!onlyUnlabeled.value || !i.num_boxes)
  )
)

// 新增類別表單（方向箭頭）
const newClassName = ref('')
const newClassDisplay = ref('')
// 新增圖標表單
const newIconName = ref('')
const newIconDisplay = ref('')
// 新增文字區塊表單
const newTextName = ref('')
const newTextDisplay = ref('')

// 依種類分組（下拉用 optgroup）
const arrowClasses = computed(() => classes.value.filter((c) => (c.kind || 'arrow') === 'arrow'))
const iconClasses = computed(() => classes.value.filter((c) => c.kind === 'icon'))
const textClasses = computed(() => classes.value.filter((c) => c.kind === 'text'))

// 畫布 / 圖片參照
const imgEl = ref(null)
const overlay = ref(null)
const wrap = ref(null)

// 拉框暫存
const drawing = reactive({ active: false, sx: 0, sy: 0, ex: 0, ey: 0 })

const scale = computed(() =>
  naturalSize.w ? displaySize.w / naturalSize.w : 1
)

const classMap = computed(() => {
  const m = {}
  classes.value.forEach((c) => (m[c.class_index] = c.display_name || c.name))
  return m
})
// class_index → kind（arrow/icon/text），用來決定框的顏色
const kindOf = computed(() => {
  const m = {}
  classes.value.forEach((c) => (m[c.class_index] = c.kind || 'arrow'))
  return m
})
// 框顏色：圖標=白、文字區塊=黑、箭頭=依類別配色
function boxColor(idx) {
  const k = kindOf.value[idx]
  if (k === 'icon') return '#ffffff'
  if (k === 'text') return '#000000'
  return palette[idx % palette.length]
}

// --- 載入 ---
async function loadClasses() {
  classes.value = await api.listClasses()
  if (classes.value.length && !classMap.value[currentClass.value]) {
    currentClass.value = classes.value[0].class_index
  }
}
async function loadImages() {
  images.value = await api.listImages()
}

onMounted(async () => {
  await Promise.all([loadClasses(), loadImages()])
})

// --- 上傳 ---
async function onUpload(e) {
  const files = Array.from(e.target.files || [])
  for (const f of files) {
    try {
      await api.uploadImage(f)
    } catch (err) {
      status.value = `上傳失敗：${err.message}`
    }
  }
  e.target.value = ''
  await loadImages()
  status.value = `已上傳 ${files.length} 張`
}

// --- 選圖 ---
async function selectImage(im) {
  currentImage.value = im
  boxes.value = []
  const anns = await api.getAnnotations(im.id)
  await nextTick()
  // 等圖片載入拿到原圖尺寸後再換算
  const setup = () => {
    naturalSize.w = imgEl.value.naturalWidth
    naturalSize.h = imgEl.value.naturalHeight
    displaySize.w = imgEl.value.clientWidth
    displaySize.h = imgEl.value.clientHeight
    // 疊框 canvas 解析度必須跟顯示尺寸一致，否則框會被拉伸、位置對不上
    overlay.value.width = displaySize.w
    overlay.value.height = displaySize.h
    // 正規化 → 原圖像素
    boxes.value = anns.map((a) => ({
      class_index: a.class_index,
      x: (a.cx - a.w / 2) * naturalSize.w,
      y: (a.cy - a.h / 2) * naturalSize.h,
      w: a.w * naturalSize.w,
      h: a.h * naturalSize.h,
    }))
    redraw()
  }
  if (imgEl.value && imgEl.value.complete) setup()
  else if (imgEl.value) imgEl.value.onload = setup
}

function onImgLoad() {
  if (!imgEl.value) return
  naturalSize.w = imgEl.value.naturalWidth
  naturalSize.h = imgEl.value.naturalHeight
  displaySize.w = imgEl.value.clientWidth
  displaySize.h = imgEl.value.clientHeight
  overlay.value.width = displaySize.w
  overlay.value.height = displaySize.h
  redraw()
}

// --- 畫框互動（座標以顯示像素計，存進 boxes 時轉回原圖像素）---
function evtPos(e) {
  const rect = overlay.value.getBoundingClientRect()
  return { x: e.clientX - rect.left, y: e.clientY - rect.top }
}
function onDown(e) {
  if (!currentImage.value) return
  const p = evtPos(e)
  drawing.active = true
  drawing.sx = p.x; drawing.sy = p.y; drawing.ex = p.x; drawing.ey = p.y
}
function onMove(e) {
  if (!drawing.active) return
  const p = evtPos(e)
  drawing.ex = p.x; drawing.ey = p.y
  redraw()
}
function onUp() {
  if (!drawing.active) return
  drawing.active = false
  const x1 = Math.min(drawing.sx, drawing.ex)
  const y1 = Math.min(drawing.sy, drawing.ey)
  const w = Math.abs(drawing.ex - drawing.sx)
  const h = Math.abs(drawing.ey - drawing.sy)
  if (w < 6 || h < 6) { redraw(); return } // 太小視為誤觸
  const s = scale.value
  boxes.value.push({
    class_index: currentClass.value,
    x: x1 / s, y: y1 / s, w: w / s, h: h / s,
  })
  redraw()
}

function removeBox(i) {
  boxes.value.splice(i, 1)
  redraw()
}

const palette = ['#38bdf8', '#22c55e', '#f59e0b', '#ef4444', '#a855f7',
  '#ec4899', '#14b8a6', '#eab308']

function redraw() {
  const cv = overlay.value
  if (!cv) return
  const ctx = cv.getContext('2d')
  ctx.clearRect(0, 0, cv.width, cv.height)
  const s = scale.value
  boxes.value.forEach((b) => {
    const color = boxColor(b.class_index)
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.strokeRect(b.x * s, b.y * s, b.w * s, b.h * s)
    ctx.fillStyle = color
    const label = classMap.value[b.class_index] || b.class_index
    ctx.font = '13px sans-serif'
    const tw = ctx.measureText(label).width + 8
    ctx.fillRect(b.x * s, b.y * s - 18, tw, 18)
    // 標籤文字：白底用黑字、黑底用白字，其餘用深色
    ctx.fillStyle = color === '#ffffff' ? '#000000' : color === '#000000' ? '#ffffff' : '#04283a'
    ctx.fillText(label, b.x * s + 4, b.y * s - 5)
  })
  // 正在拉的框
  if (drawing.active) {
    ctx.strokeStyle = '#ffffff'
    ctx.setLineDash([5, 4])
    ctx.strokeRect(
      Math.min(drawing.sx, drawing.ex),
      Math.min(drawing.sy, drawing.ey),
      Math.abs(drawing.ex - drawing.sx),
      Math.abs(drawing.ey - drawing.sy)
    )
    ctx.setLineDash([])
  }
}

// --- 儲存標註（轉 YOLO 正規化）---
async function saveAnnotations() {
  if (!currentImage.value) return
  const yolo = boxes.value.map((b) => ({
    class_index: b.class_index,
    cx: (b.x + b.w / 2) / naturalSize.w,
    cy: (b.y + b.h / 2) / naturalSize.h,
    w: b.w / naturalSize.w,
    h: b.h / naturalSize.h,
  }))
  try {
    await api.saveAnnotations(currentImage.value.id, yolo)
    status.value = `已儲存 ${yolo.length} 個標註框`
    await loadImages()
  } catch (err) {
    status.value = `儲存失敗：${err.message}`
  }
}

// --- 刪圖 ---
async function deleteImage(im) {
  if (!confirm(`確定刪除 ${im.filename}？`)) return
  await api.deleteImage(im.id)
  if (currentImage.value && currentImage.value.id === im.id) {
    currentImage.value = null
    boxes.value = []
  }
  await loadImages()
}

// --- 新增類別 ---
async function addClass() {
  if (!newClassName.value.trim()) return
  try {
    await api.addClass(newClassName.value.trim(), newClassDisplay.value.trim(), 'arrow')
    newClassName.value = ''
    newClassDisplay.value = ''
    await loadClasses()
    status.value = '已新增方向類別'
  } catch (err) {
    status.value = `新增失敗：${err.message}`
  }
}
async function addIcon() {
  if (!newIconName.value.trim()) return
  try {
    await api.addClass(newIconName.value.trim(), newIconDisplay.value.trim(), 'icon')
    newIconName.value = ''
    newIconDisplay.value = ''
    await loadClasses()
    status.value = '已新增圖標類別'
  } catch (err) {
    status.value = `新增失敗：${err.message}`
  }
}
async function addText() {
  if (!newTextName.value.trim()) return
  try {
    await api.addClass(newTextName.value.trim(), newTextDisplay.value.trim(), 'text')
    newTextName.value = ''
    newTextDisplay.value = ''
    await loadClasses()
    status.value = '已新增文字區塊類別'
  } catch (err) {
    status.value = `新增失敗：${err.message}`
  }
}
</script>

<template>
  <div class="grid">
    <!-- 左欄2：類別 -->
    <aside class="col class-col">
      <div class="panel col">
        <strong>類別（方向 / 圖標）</strong>
        <div class="group-label">🡒 方向箭頭</div>
        <div class="chips">
          <span v-for="c in arrowClasses" :key="c.class_index" class="tag"
                :style="{ borderColor: palette[c.class_index % palette.length] }">
            {{ c.class_index }}. {{ c.display_name || c.name }}
          </span>
          <span v-if="!arrowClasses.length" class="muted">尚無</span>
        </div>
        <div class="group-label">🚻 圖標</div>
        <div class="chips">
          <span v-for="c in iconClasses" :key="c.class_index" class="tag"
                :style="{ borderColor: palette[c.class_index % palette.length] }">
            {{ c.class_index }}. {{ c.display_name || c.name }}
          </span>
          <span v-if="!iconClasses.length" class="muted">尚無</span>
        </div>
        <div class="group-label">🔤 文字區塊</div>
        <div class="chips">
          <span v-for="c in textClasses" :key="c.class_index" class="tag"
                :style="{ borderColor: palette[c.class_index % palette.length] }">
            {{ c.class_index }}. {{ c.display_name || c.name }}
          </span>
          <span v-if="!textClasses.length" class="muted">尚無</span>
        </div>

        <!-- 新增方向類別 -->
        <div class="col add-form">
          <div class="group-label">＋ 新增方向類別</div>
          <input v-model="newClassName" placeholder="英文 id，如 arrow_up" />
          <input v-model="newClassDisplay" placeholder="中文顯示，如 往上" />
          <button class="success" @click="addClass">＋ 新增方向</button>
        </div>
        <!-- 新增圖標類別 -->
        <div class="col add-form">
          <div class="group-label">＋ 新增圖標類別</div>
          <input v-model="newIconName" placeholder="英文 id，如 toilet" />
          <input v-model="newIconDisplay" placeholder="中文顯示，如 廁所" />
          <button class="icon-btn" @click="addIcon">＋ 新增圖標</button>
        </div>
        <!-- 新增文字區塊類別 -->
        <div class="col add-form">
          <div class="group-label">＋ 新增文字區塊類別（路線名招牌）</div>
          <input v-model="newTextName" placeholder="英文 id，如 dest_text" />
          <input v-model="newTextDisplay" placeholder="中文顯示，如 目的地文字" />
          <button class="text-btn" @click="addText">＋ 新增文字區塊</button>
        </div>
      </div>
    </aside>

    <!-- 左欄1：訓練照片（移到類別左邊，用 order 排到最左）-->
    <aside class="col photos-col">
      <div class="panel col">
        <div class="row" style="justify-content:space-between">
          <strong>訓練照片</strong>
          <label class="upload-btn">
            ＋上傳
            <input type="file" accept="image/*" multiple hidden @change="onUpload" />
          </label>
        </div>
        <!-- 摘要：新照片 / 未標註 數量 -->
        <div class="summary">
          共 {{ images.length }} 張
          <span v-if="newCount" class="badge new">🆕 {{ newCount }} 新</span>
          <span v-if="unlabeledCount" class="badge todo">⚠️ {{ unlabeledCount }} 未標註</span>
        </div>
        <div class="img-filter">
          <label><input type="checkbox" v-model="onlyNew" /> 只看新增</label>
          <label><input type="checkbox" v-model="onlyUnlabeled" /> 只看未標註</label>
        </div>
        <div class="img-list">
          <div v-for="im in filteredImages" :key="im.id"
               class="img-item" :class="{ active: currentImage && currentImage.id === im.id }"
               @click="selectImage(im)">
            <img :src="api.imageUrl(im.id)" />
            <div class="meta">
              <div class="fn">
                #{{ im.id }}
                <span v-if="im.is_new" class="badge new mini">新</span>
              </div>
              <div class="muted">
                <span v-if="im.num_boxes">{{ im.num_boxes }} 框</span>
                <span v-else class="badge todo mini">未標註</span>
              </div>
            </div>
            <button class="danger tiny" @click.stop="deleteImage(im)">✕</button>
          </div>
          <p v-if="!filteredImages.length" class="muted">沒有符合的照片。</p>
        </div>
      </div>
    </aside>

    <!-- 中間：畫布 -->
    <section class="col main">
      <div class="panel col">
        <div class="row" style="justify-content:space-between; align-items:flex-end">
          <div class="class-selects">
            <label class="field">
              🡒 方向箭頭
              <select :value="arrowClasses.some(c => c.class_index === currentClass) ? currentClass : ''"
                      @change="currentClass = Number($event.target.value)">
                <option value="" disabled>選擇…</option>
                <option v-for="c in arrowClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </select>
            </label>
            <label class="field">
              🚻 圖標
              <select :value="iconClasses.some(c => c.class_index === currentClass) ? currentClass : ''"
                      @change="currentClass = Number($event.target.value)">
                <option value="" disabled>{{ iconClasses.length ? '選擇…' : '尚無' }}</option>
                <option v-for="c in iconClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </select>
            </label>
            <label class="field">
              🔤 文字區塊
              <select :value="textClasses.some(c => c.class_index === currentClass) ? currentClass : ''"
                      @change="currentClass = Number($event.target.value)">
                <option value="" disabled>{{ textClasses.length ? '選擇…' : '尚無' }}</option>
                <option v-for="c in textClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </select>
            </label>
          </div>
          <button class="success" :disabled="!currentImage" @click="saveAnnotations">
            💾 儲存標註
          </button>
        </div>
        <p class="muted">在照片上按住滑鼠拖曳即可框出箭頭；框會套用上方選定的類別。</p>

        <div ref="wrap" class="canvas-wrap">
          <template v-if="currentImage">
            <img ref="imgEl" :src="api.imageUrl(currentImage.id)"
                 class="base-img" @load="onImgLoad" />
            <canvas ref="overlay" class="overlay"
                    @mousedown="onDown" @mousemove="onMove"
                    @mouseup="onUp" @mouseleave="onUp"></canvas>
          </template>
          <p v-else class="muted placeholder">← 從左側選一張照片開始標註</p>
        </div>
      </div>
    </section>

    <!-- 右欄：框列表 -->
    <aside class="col side">
      <div class="panel col">
        <strong>本圖標註框（{{ boxes.length }}）</strong>
        <div class="box-list">
          <div v-for="(b, i) in boxes" :key="i" class="box-row">
            <span class="dot" :style="{ background: palette[b.class_index % palette.length] }"></span>
            <select v-model.number="b.class_index" @change="redraw">
              <optgroup label="方向箭頭">
                <option v-for="c in arrowClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </optgroup>
              <optgroup label="圖標" v-if="iconClasses.length">
                <option v-for="c in iconClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </optgroup>
              <optgroup label="文字區塊" v-if="textClasses.length">
                <option v-for="c in textClasses" :key="c.class_index" :value="c.class_index">
                  {{ c.display_name || c.name }}
                </option>
              </optgroup>
            </select>
            <button class="danger tiny" @click="removeBox(i)">刪</button>
          </div>
          <p v-if="!boxes.length" class="muted">尚未框選。</p>
        </div>
      </div>
      <p v-if="status" class="panel muted">{{ status }}</p>
    </aside>
  </div>
</template>

<style scoped>
/* 四欄：訓練照片 | 類別 | 畫布 | 框列表 */
.grid { display: grid; grid-template-columns: 190px 220px 1fr 200px; gap: 16px; align-items: start; }
.photos-col { order: -1; }          /* 訓練照片排到最左 */
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
.side { gap: 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.group-label { font-size: 12px; color: var(--muted); font-weight: 600; margin-top: 4px; }
.class-selects { display: flex; gap: 12px; flex-wrap: wrap; }
.class-selects .field { min-width: 140px; font-size: 13px; }
.class-selects select { min-width: 140px; font-size: 16px; padding: 9px 10px; }
.add-form { gap: 6px; padding-top: 8px; border-top: 1px dashed var(--border); }
.icon-btn { background: var(--warn); color: #3b2600; }
.text-btn { background: #a855f7; color: #fff; }
.upload-btn {
  background: var(--accent); color: #04283a; padding: 6px 10px;
  border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;
}
.summary { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.img-filter { display: flex; gap: 14px; font-size: 12.5px; color: var(--muted); }
.img-filter label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.badge {
  display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11.5px; font-weight: 600;
}
.badge.new { background: color-mix(in srgb, var(--accent-2) 22%, transparent); color: var(--accent-2); }
.badge.todo { background: color-mix(in srgb, var(--warn) 22%, transparent); color: var(--warn); }
.badge.mini { padding: 0 6px; font-size: 10.5px; margin-left: 4px; }
.img-list { display: flex; flex-direction: column; gap: 8px; max-height: 52vh; overflow-y: auto; }
.img-item {
  display: flex; align-items: center; gap: 8px; padding: 6px;
  border: 1px solid var(--border); border-radius: 8px; cursor: pointer;
}
.img-item.active { border-color: var(--accent); background: var(--panel-2); }
.img-item img { width: 48px; height: 48px; object-fit: cover; border-radius: 6px; }
.img-item .meta { flex: 1; }
.tiny { padding: 3px 7px; font-size: 12px; }
.canvas-wrap { position: relative; display: inline-block; max-width: 100%; }
.base-img { max-width: 100%; display: block; border-radius: 8px; user-select: none; }
.overlay { position: absolute; left: 0; top: 0; cursor: crosshair; }
.placeholder { padding: 40px; text-align: center; }
.box-list { display: flex; flex-direction: column; gap: 6px; max-height: 60vh; overflow-y: auto; }
.box-row { display: flex; align-items: center; gap: 6px; }
.box-row select { flex: 1; }
.dot { width: 12px; height: 12px; border-radius: 3px; flex: none; }
</style>
