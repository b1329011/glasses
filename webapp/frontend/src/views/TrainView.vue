<script setup>
/*
 * TrainView — 訓練頁
 * 設定訓練參數 → 呼叫後端啟動訓練 → 輪詢進度、指標與 log
 */
import { ref, reactive, onMounted, onBeforeUnmount, computed } from 'vue'
import { api } from '../api.js'

const params = reactive({
  model: 'n',        // n/s/m
  epochs: 50,
  batch: 16,
  imgsz: 960,
  name: 'ui_run',
  finetune: false,   // 從上一個模型繼續訓練（暖啟動）
})

const status = ref(null)   // /train/status 回傳
const models = ref([])
const starting = ref(false)
const errorMsg = ref('')
let timer = null

const running = computed(() => status.value && status.value.running)
const progressPct = computed(() => {
  if (!status.value) return 0
  const { epoch, epochs } = status.value.progress
  return epochs ? Math.round((epoch / epochs) * 100) : 0
})

async function refresh() {
  try {
    status.value = await api.trainStatus()
  } catch (_) {}
}
async function refreshModels() {
  try { models.value = await api.listModels() } catch (_) {}
}

async function startTraining() {
  errorMsg.value = ''
  starting.value = true
  try {
    await api.startTrain({ ...params })
    await refresh()
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    starting.value = false
  }
}

onMounted(() => {
  refresh()
  refreshModels()
  // 每 2 秒輪詢訓練狀態；訓練結束後刷新模型清單
  timer = setInterval(async () => {
    const wasRunning = running.value
    await refresh()
    if (wasRunning && !running.value) refreshModels()
  }, 2000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="col" style="gap:16px">
    <div class="panel col">
      <strong>訓練參數</strong>
      <div class="row">
        <label class="field">
          模型大小
          <select v-model="params.model">
            <option value="n">yolov8n（最輕量，邊緣裝置建議）</option>
            <option value="s">yolov8s</option>
            <option value="m">yolov8m</option>
          </select>
        </label>
        <label class="field">Epochs<input type="number" v-model.number="params.epochs" min="1" /></label>
        <label class="field">Batch<input type="number" v-model.number="params.batch" /></label>
        <label class="field">影像尺寸 imgsz<input type="number" v-model.number="params.imgsz" step="32" /></label>
        <label class="field">名稱<input v-model="params.name" /></label>
      </div>
      <p class="muted">imgsz 建議 960（指標牌偏小，高解析度有助小物件偵測）。</p>
      <label class="checkbox">
        <input type="checkbox" v-model="params.finetune" />
        <span>從上一個模型繼續訓練（fine-tune）</span>
      </label>
      <p class="muted">
        勾選：從目前最新的 best.pt 接著練，<b>保留先前學習</b>、通常用較少 epochs 即可，較快。<br>
        不勾：從零(官方預訓練)開始全新訓練。<br>
        兩者都會用<b>全部</b>已標註照片(舊+新)，這樣模型才不會忘掉舊的。
      </p>
      <div class="row">
        <button class="success" :disabled="running || starting" @click="startTraining">
          {{ running ? '訓練進行中…' : '🚀 開始訓練' }}
        </button>
        <span v-if="errorMsg" class="muted" style="color:var(--danger)">{{ errorMsg }}</span>
      </div>
    </div>

    <!-- 進度與指標 -->
    <div class="panel col" v-if="status">
      <div class="row" style="justify-content:space-between">
        <strong>訓練狀態</strong>
        <span class="tag">{{ running ? '執行中' : (status.error ? '錯誤' : '閒置/完成') }}</span>
      </div>
      <div class="progress"><div class="bar" :style="{ width: progressPct + '%' }"></div></div>
      <div class="muted">
        Epoch {{ status.progress.epoch }} / {{ status.progress.epochs }}（{{ progressPct }}%）
      </div>

      <div class="metrics">
        <div class="metric"><div class="k">mAP@0.5</div><div class="v">{{ status.metrics.map50.toFixed(3) }}</div></div>
        <div class="metric"><div class="k">mAP@0.5:0.95</div><div class="v">{{ status.metrics.map.toFixed(3) }}</div></div>
        <div class="metric"><div class="k">Precision</div><div class="v">{{ status.metrics.precision.toFixed(3) }}</div></div>
        <div class="metric"><div class="k">Recall</div><div class="v">{{ status.metrics.recall.toFixed(3) }}</div></div>
      </div>

      <p v-if="status.error" class="muted" style="color:var(--danger)">錯誤：{{ status.error }}</p>

      <details open>
        <summary class="muted">訓練 Log</summary>
        <pre class="logbox">{{ (status.logs || []).join('\n') }}</pre>
      </details>
    </div>

    <!-- 已訓練模型 -->
    <div class="panel col">
      <strong>已訓練模型</strong>
      <table class="tbl" v-if="models.length">
        <thead><tr><th>檔名</th><th>大小 (MB)</th></tr></thead>
        <tbody>
          <tr v-for="m in models" :key="m.path"><td>{{ m.name }}</td><td>{{ m.size_mb }}</td></tr>
        </tbody>
      </table>
      <p v-else class="muted">尚無已訓練模型。</p>
    </div>
  </div>
</template>

<style scoped>
.checkbox { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
.checkbox input { width: 18px; height: 18px; }
.progress { height: 12px; background: var(--panel-2); border-radius: 999px; overflow: hidden; }
.bar { height: 100%; background: var(--accent-2); transition: width .3s; }
.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
@media (max-width: 640px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
.metric { background: var(--panel-2); border-radius: 8px; padding: 10px; text-align: center; }
.metric .k { font-size: 12px; color: var(--muted); }
.metric .v { font-size: 22px; font-weight: 700; }
.logbox {
  background: #0b1220; border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; max-height: 240px; overflow: auto; font-size: 12px; white-space: pre-wrap;
}
.tbl { width: 100%; border-collapse: collapse; }
.tbl th, .tbl td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); font-size: 14px; }
</style>
