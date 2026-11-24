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

export const api = {
  // --- 類別 ---
  listClasses: () => req('/classes'),
  addClass: (name, display_name, kind = 'arrow') =>
    req('/classes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, display_name, kind }),
    }),

  // --- 影像 ---
  listImages: () => req('/images'),
  uploadImage: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return req('/images', { method: 'POST', body: fd })
  },
  imageUrl: (id) => `${BASE}/images/${id}/file`,
  deleteImage: (id) => req(`/images/${id}`, { method: 'DELETE' }),

  // --- 標註 ---
  getAnnotations: (id) => req(`/images/${id}/annotations`),
  saveAnnotations: (id, boxes) =>
    req(`/images/${id}/annotations`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ boxes }),
    }),

  // --- 訓練 ---
  startTrain: (params) =>
    req('/train', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
  trainStatus: () => req('/train/status'),
  listModels: () => req('/models'),

  // --- 推論 ---
  infer: (image_base64, conf, iou, model_path, imgsz) =>
    req('/infer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, conf, iou, model_path, imgsz }),
    }),

  // --- 文字辨識 OCR ---
  ocr: (image_base64, min_conf) =>
    req('/ocr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, min_conf }),
    }),

  // --- 導航提示（箭頭方向 + 文字）；ocr_crop=true 走快速裁切 OCR ---
  guide: (image_base64, conf, iou, model_path, ocr_crop = false) =>
    req('/guide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64, conf, iou, model_path, ocr_crop }),
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
