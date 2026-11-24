// api.js — 封裝後端 REST API 呼叫
// 開發時 Vite 會把 /api 代理到 FastAPI，故一律用相對路徑。

const BASE = '/api'

async function req(path, options = {}) {
  const res = await fetch(BASE + path, options)
  if (!res.ok) {
    let msg = res.statusText
    try {
      const data = await res.json()
      msg = data.detail || msg
    } catch (_) {}
    throw new Error(msg)
  }
  return res.status === 204 ? null : res.json()
}

// --- Agent C 語音導航（合併後端的 /api/agent-c/*）---
const NAV_BASE = '/api/agent-c'

async function navReq(path, body) {
  const res = await fetch(NAV_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  if (!res.ok) throw new Error(`Agent C ${res.status}`)
  return res.json()
}

export const navApi = {
  command: (text, lat, lng) => navReq('/command', { text, lat, lng }),
  segmentComplete: (lat, lng) => navReq('/segment_complete', { lat, lng }),
  position: (lat, lng) => navReq('/position', { lat, lng }),
  signFrame: (image_base64, lat, lng) => navReq('/sign_frame', { image_base64, lat, lng }),
  reset: () => navReq('/reset'),
}

export const api = {
  // --- 推論（箭頭偵測，快）---
  infer: (image_base64, conf, iou, model_path, imgsz) =>
    req('/infer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, conf, iou, model_path, imgsz }),
    }),

  // --- 目標導向導航：先找 target 文字，再找旁邊箭頭報方向 ---
  // opts 可帶 { no_full_ocr, imgsz }（連續模式求快用）
  navigate: (image_base64, target, conf, iou, model_path, opts = {}) =>
    req('/navigate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, target, conf, iou, model_path, ...opts }),
    }),
}
