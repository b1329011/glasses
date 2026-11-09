// 即時鏡頭偵測：擷取影格 → WebSocket 送後端 → 繪製疊圖、雷達、深度圖、地面剖面，並語音播報

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const C = {
  s1: css('--s1'), s3: css('--s3'), warning: css('--warning'), critical: css('--critical'), good: css('--good'),
  text: css('--text'), text2: css('--text-2'), muted: css('--muted'), grid: css('--grid'), border: css('--border'),
  hanging: '#9b7bea',
};
const LEVEL_COLOR = { critical: C.critical, warning: C.warning, notice: C.s1, none: C.good };
const LEVEL_ZH = { critical: '停下', warning: '注意', notice: '提醒', none: '安全' };
const VIB_ZH = { strong: '急促震動', medium: '每秒震動', light: '輕震一下' };
const ZONE_ZH = { stop: '太近（< 1.2 m）', near: '接近（1.2~2.5 m）', ahead: '提早告知（2.5~5.5 m）' };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));

const state = {
  ws: null, running: false, source: 'webcam', stream: null, fileUrl: null,
  geometry: { height_m: 1.5, pitch_deg: 15, hfov_deg: 70 },
  frames: [], lastResult: null, lastKey: null, lastSpoken: 0, log: [],
};
const video = $('video');
const still = $('still');
const grab = document.createElement('canvas');

// ---------- 設定與狀態 ----------

function setStatus(parts) {
  $('status').innerHTML = parts.map((p) => `<span class="${p.err ? 'err' : ''}">${esc(p.text ?? p)}</span>`).join('');
}

function mediaSize() {
  if (state.source === 'file' && !still.hidden) return [still.naturalWidth, still.naturalHeight];
  if (state.source === 'depthcam') return [still.naturalWidth || 640, still.naturalHeight || 480];
  return [video.videoWidth || 640, video.videoHeight || 480];
}

function nearestVisibleGround() {
  const [w, h] = mediaSize();
  const g = state.geometry;
  const fx = (w / 2) / Math.tan((g.hfov_deg * Math.PI) / 360);
  const angle = (g.pitch_deg * Math.PI) / 180 + Math.atan((h - 1 - (h - 1) / 2) / fx);
  return angle > 0 ? g.height_m / Math.tan(angle) : Infinity;
}

function renderGeometry() {
  const g = state.geometry;
  $('height-val').textContent = `${g.height_m.toFixed(2)} m`;
  $('pitch-val').textContent = `${g.pitch_deg}°`;
  $('hfov-val').textContent = `${g.hfov_deg}°`;
  const near = nearestVisibleGround();
  $('geo-note').textContent = `畫面最下緣看到的地面約在 ${Number.isFinite(near) ? near.toFixed(2) : '∞'} m 外，比這更近的落差拍不到；`
    + '要偵測近處的斷崖／樓梯，請把鏡頭往下傾。單眼模式靠「畫面下方是地面」來換算距離，請盡量填對高度與傾角。';
}

function sendGeometry(extra = {}) {
  if (state.ws?.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: 'config', ...state.geometry, depth_quality: $('hq').checked ? 'high' : 'standard', ...extra }));
  }
  saveGeometry();
}

// 相機設定（含距離校正後的高度）記在這台瀏覽器，下次開啟沿用
function saveGeometry() {
  try { localStorage.setItem('oa-geometry', JSON.stringify(state.geometry)); } catch { /* 無痕模式等 */ }
}
function loadGeometry() {
  try { return JSON.parse(localStorage.getItem('oa-geometry')); } catch { return null; }
}

function syncSliders() {
  $('height').value = state.geometry.height_m;
  $('pitch').value = state.geometry.pitch_deg;
  $('hfov').value = state.geometry.hfov_deg;
  renderGeometry();
}

for (const [id, key] of [['height', 'height_m'], ['pitch', 'pitch_deg'], ['hfov', 'hfov_deg']]) {
  $(id).addEventListener('input', () => {
    state.geometry[key] = +$(id).value;
    renderGeometry();
  });
  $(id).addEventListener('change', sendGeometry);
}

$('source').addEventListener('change', () => {
  state.source = $('source').value;
  $('device-field').hidden = state.source !== 'webcam';
  $('file-field').hidden = state.source !== 'file';
  if (state.running) stop();
});

async function listDevices() {
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    const cur = $('device').value;
    $('device').innerHTML = '<option value="">預設鏡頭（手機為後鏡頭）</option>'
      + devices.map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || `鏡頭 ${i + 1}`)}</option>`).join('');
    $('device').value = cur;
  } catch { /* 忽略 */ }
}

// ---------- 開始／停止 ----------

function wsUrl(path) {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`;
}

async function start() {
  if (location.protocol === 'file:') {
    setStatus([{ text: '請先執行 python server.py，再開 http://localhost:8010/live.html（不能用檔案直接開啟）', err: true }]);
    return;
  }
  try {
    if (state.source === 'webcam') await startWebcam();
    else if (state.source === 'file') await startFile();
  } catch (e) {
    setStatus([{ text: cameraErrorHint(e), err: true }]);
    return;
  }
  $('media-empty').hidden = true;
  state.running = true;
  state.frames = [];
  $('start').textContent = '■ 停止';
  $('start').classList.add('on');
  connect();
}

function stop() {
  state.running = false;
  state.ws?.close();
  state.ws = null;
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  video.pause();
  $('start').textContent = '▶ 開始偵測';
  $('start').classList.remove('on');
  speechSynthesis?.cancel();
  setStatus(['已停止']);
}

// 把瀏覽器的鏡頭錯誤翻成看得懂的提示
function cameraErrorHint(e) {
  const hints = {
    NotReadableError: '鏡頭正被其他程式占用（例如指標辨識頁面、視訊軟體）。請先在那邊關閉攝影機，或改用「影片／圖片檔」模式',
    NotAllowedError: '鏡頭權限被拒：請在網址列左側允許使用攝影機後重試',
    NotFoundError: '找不到鏡頭裝置',
    OverconstrainedError: '選的鏡頭不支援目前設定，請改選「預設鏡頭」',
  };
  return hints[e?.name] || `無法開啟影像來源：${e?.message || e}`;
}

async function startWebcam() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('瀏覽器不支援鏡頭，或不是 https / localhost 連線');
  const id = $('device').value;
  state.stream = await navigator.mediaDevices.getUserMedia({
    video: id ? { deviceId: { exact: id }, width: { ideal: 640 }, height: { ideal: 480 } }
      : { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 } },
    audio: false,
  });
  still.hidden = true;
  video.hidden = false;
  video.srcObject = state.stream;
  await video.play();
  listDevices();
}

async function startFile() {
  const f = $('file').files[0];
  if (!f) throw new Error('請先選擇影片或圖片檔');
  if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
  state.fileUrl = URL.createObjectURL(f);
  video.srcObject = null;
  if (f.type.startsWith('image/')) {
    video.hidden = true;
    still.hidden = false;
    // 用 onload 而非 decode()：decode() 在切換來源時偶爾會誤報「無法解碼」
    await new Promise((resolve, reject) => {
      still.onload = resolve;
      still.onerror = () => reject(new Error('圖片無法讀取，請換一張'));
      still.src = state.fileUrl;
    });
    still.onload = still.onerror = null;
  } else {
    still.hidden = true;
    video.hidden = false;
    video.src = state.fileUrl;
    video.loop = true;
    await video.play();
  }
}

function connect() {
  const path = state.source === 'depthcam' ? '/ws/depthcam' : '/ws/camera';
  const ws = new WebSocket(wsUrl(path));
  ws.binaryType = 'arraybuffer';
  state.ws = ws;
  state.lastError = null;
  setStatus(['連線後端中…']);
  ws.onopen = () => {
    sendGeometry();
    if (state.source !== 'depthcam') sendFrame();
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'error') {
      state.lastError = msg.message;
      setStatus([{ text: msg.message, err: true }]);
      return;
    }
    if (msg.type === 'calibrated') {
      onCalibrated(msg);
      return;
    }
    if (msg.type !== 'result') return;
    try {
      onResult(msg);
    } catch (err) {
      console.error('繪製結果失敗', err);  // 畫面出錯也不能讓偵測停下來
    } finally {
      // 用 setTimeout 而非 requestAnimationFrame：分頁在背景時 rAF 會暫停
      if (state.running && state.source !== 'depthcam') setTimeout(sendFrame, 0);
    }
  };
  ws.onclose = () => {
    if (state.running && state.ws === ws) {
      stop();
      setStatus([{ text: state.lastError || '與後端的連線中斷：請確認 python server.py 仍在執行', err: true }]);
    }
  };
}

function sendFrame() {
  if (!state.running || state.ws?.readyState !== WebSocket.OPEN) return;
  const src = !still.hidden ? still : video;
  const [w, h] = mediaSize();
  if (!w || !h) { setTimeout(sendFrame, 100); return; }
  const scale = Math.min(1, 640 / w);
  grab.width = Math.round(w * scale);
  grab.height = Math.round(h * scale);
  grab.getContext('2d').drawImage(src, 0, 0, grab.width, grab.height);
  // 用同步的 toDataURL：部分瀏覽器的 toBlob 會被排到低優先序，單張要等上一秒
  const b64 = grab.toDataURL('image/jpeg', 0.8).split(',', 2)[1];
  const bytes = Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));
  state.ws.send(bytes);
}

$('start').addEventListener('click', () => (state.running ? stop() : start()));

// ---------- 收到結果 ----------

function onResult(r) {
  state.lastResult = r;
  const now = performance.now();
  state.frames.push(now);
  state.frames = state.frames.filter((t) => now - t < 3000);
  const fps = state.frames.length > 1 ? (state.frames.length - 1) / ((now - state.frames[0]) / 1000) : 0;

  if (r.frame) {  // 深度相機模式：畫面由後端提供
    still.hidden = false;
    video.hidden = true;
    still.src = r.frame;
    $('media-empty').hidden = true;
  }
  setStatus([
    `${fps.toFixed(1)} fps`,
    `處理 ${r.timing_ms.total} ms（模型 ${r.timing_ms.models} ms）`,
    { text: r.calibration.status, err: !r.calibration.ok },
    `${r.objects.length} 個物體`,
  ]);
  $('depth-mode').textContent = r.depth_mode === 'depth_camera' ? '（深度相機實測）'
    : `（單眼 AI 估計，經地面校正${r.depth_quality === 'high' ? '・高精度' : ''}）`;
  if (r.depth_image) $('depth-img').src = r.depth_image;

  renderBanner(r.event);
  renderHazards(r.hazards);
  drawOverlay(r);
  drawRadar(r);
  renderProfile(r);
  handleAlert(r.event);
  $('event-json').textContent = JSON.stringify(r.event, null, 2);
}

function hazardText(h) {
  if (!h || !h.type || h.distance == null) return '';
  return `${h.type_zh}${h.label && h.type === 'front_obstacle' ? `（${h.label}）` : ''} ${fmt(h.distance)} m`;
}

function renderBanner(ev) {
  const lv = ev.priority;
  $('banner').className = `banner ${lv}`;
  $('banner-level').textContent = LEVEL_ZH[lv];
  $('banner-msg').textContent = ev.action_required.audio_message || '前方安全';
  const d = ev.data;
  const parts = [];
  if (d.zone) parts.push(ZONE_ZH[d.zone]);
  if (ev.action_required.vibration) parts.push(VIB_ZH[ev.action_required.vibration]);
  if (d.others?.length) parts.push(`另有 ${d.others.length} 項：${d.others.map((o) => o.label).join('、')}`);
  $('banner-sub').textContent = parts.join('・');
}

function renderHazards(hz) {
  const cards = [
    ['front', '前方障礙物', (h) => (h.distance != null ? `${h.direction || ''} ${h.label || ''}` : '通道內無障礙物')],
    ['hanging', '懸空障礙物（胸部～頭頂）', (h) => (h.distance != null ? '±30°、1~2 m 高' : 'ROI 內無障礙物')],
    ['ground', '地面（斷崖／樓梯／階差）', (h) => (h.type ? `${h.height_change_m != null ? `高度變化 ${fmt(h.height_change_m * 100, 0)} cm` : ''}${h.steps ? `・${h.steps} 階` : ''}` : '地面平坦')],
  ];
  $('hazard-cards').innerHTML = cards.map(([k, name, sub]) => {
    const h = hz[k];
    const value = k === 'ground' ? (h.type ? `${h.type_zh} ${fmt(h.distance)} m` : '—') : (h.distance != null ? `${fmt(h.distance)} m` : '—');
    return `<div class="hazard ${h.level}">
      <div class="name">${name}<span class="badge ${h.level}">${h.level}</span></div>
      <div class="value">${esc(value)}</div>
      <div class="sub">${esc(sub(h))}${h.raw_level && h.raw_level !== h.level ? `・本幀 ${h.raw_level}（等待連續確認）` : ''}</div>
    </div>`;
  }).join('');
}

// ---------- 影像疊圖 ----------

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const { clientWidth: w, clientHeight: h } = canvas;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return [ctx, w, h];
}

function label(ctx, text, x, y, color) {
  ctx.font = '600 13px system-ui, "Microsoft JhengHei", sans-serif';
  const w = ctx.measureText(text).width + 10;
  ctx.fillStyle = 'rgba(0,0,0,0.7)';
  ctx.fillRect(x, y - 18, w, 18);
  ctx.fillStyle = color;
  ctx.fillText(text, x + 5, y - 5);
}

function drawOverlay(r) {
  const [ctx, w, h] = fitCanvas($('overlay'));
  const P = ([x, y]) => [x * w, y * h];

  // 行進通道：兩條地面線之間填色（類似 Tesla 的路徑顯示）
  const [left, right] = r.corridor;
  if (left.length > 1 && right.length > 1) {
    const pathLevel = ['front', 'ground'].map((k) => r.hazards[k].level).includes('critical') ? 'critical'
      : ['front', 'ground'].map((k) => r.hazards[k].level).includes('warning') ? 'warning' : 'none';
    const color = pathLevel === 'none' ? C.s1 : LEVEL_COLOR[pathLevel];
    const grad = ctx.createLinearGradient(0, h, 0, Math.min(...left.map((p) => p[1])) * h);
    grad.addColorStop(0, `${color}66`);
    grad.addColorStop(1, `${color}08`);
    ctx.beginPath();
    left.forEach((p, i) => (i ? ctx.lineTo(...P(p)) : ctx.moveTo(...P(p))));
    [...right].reverse().forEach((p) => ctx.lineTo(...P(p)));
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    for (const side of [left, right]) {
      ctx.beginPath();
      side.forEach((p, i) => (i ? ctx.lineTo(...P(p)) : ctx.moveTo(...P(p))));
      ctx.stroke();
    }
    // 距離刻度
    ctx.fillStyle = C.text2;
    ctx.font = '11px system-ui, sans-serif';
    for (const p of right) if ([1, 2, 3, 4, 5].includes(p[2])) ctx.fillText(`${p[2]} m`, p[0] * w + 6, p[1] * h + 4);
  }

  // 地面邊緣（斷崖／樓梯起點）
  const edge = r.ground.edge;
  const g = r.hazards.ground;
  if (edge && r.ground.raw_type) {
    const color = g.level !== 'none' ? LEVEL_COLOR[g.level] : C.muted;
    ctx.lineWidth = 5;
    ctx.strokeStyle = color;
    ctx.setLineDash([12, 6]);
    ctx.beginPath();
    ctx.moveTo(...P(edge[0]));
    ctx.lineTo(...P(edge[1]));
    ctx.stroke();
    ctx.setLineDash([]);
    const [x, y] = P(edge[0]);
    label(ctx, `${r.ground.raw_type_zh} ${fmt(g.distance ?? null)} m`, Math.max(4, x), Math.max(20, y - 4), color);
  }

  // 距離校正的點選位置
  if (calib.active && calib.point) {
    const [px, py] = P([calib.point.x, calib.point.y]);
    ctx.strokeStyle = C.s1;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(px, py, 10, 0, 2 * Math.PI);
    ctx.moveTo(px - 16, py); ctx.lineTo(px + 16, py);
    ctx.moveTo(px, py - 16); ctx.lineTo(px, py + 16);
    ctx.stroke();
  }

  // YOLO 物體
  for (const o of r.objects) {
    const [x1, y1, x2, y2] = o.box;
    const color = o.in_path ? (r.hazards.front.level === 'critical' && o.label_zh === r.hazards.front.label ? C.critical : C.warning) : C.muted;
    ctx.lineWidth = o.in_path ? 3 : 1.5;
    ctx.strokeStyle = color;
    ctx.strokeRect(x1 * w, y1 * h, (x2 - x1) * w, (y2 - y1) * h);
    label(ctx, `${o.label_zh}${o.distance != null ? ` ${fmt(o.distance)} m` : ''}`, x1 * w, Math.max(18, y1 * h), color);
  }
}

// ---------- 鳥瞰雷達 ----------

function drawRadar(r) {
  const [ctx, w, h] = fitCanvas($('radar'));
  if (w < 60 || h < 60) return;  // 版面太窄或尚未排版
  const RANGE = 6;
  const s = (h - 24) / RANGE;
  const ox = w / 2;
  const oy = h - 14;
  const X = (lat) => ox + lat * s;
  const Y = (f) => oy - f * s;

  // 距離圈與門檻
  ctx.lineWidth = 1;
  for (let d = 1; d <= RANGE; d++) {
    ctx.strokeStyle = C.grid;
    ctx.beginPath();
    ctx.arc(ox, oy, d * s, Math.PI, 2 * Math.PI);
    ctx.stroke();
    ctx.fillStyle = C.muted;
    ctx.font = '10px system-ui, sans-serif';
    ctx.fillText(`${d}m`, ox + 3, Y(d) - 2);
  }
  for (const [d, color] of [[1.5, C.critical], [3.0, C.warning]]) {
    ctx.strokeStyle = `${color}99`;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.arc(ox, oy, d * s, Math.PI, 2 * Math.PI);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // 視角範圍
  const half = (r.geometry.hfov_deg / 2) * (Math.PI / 180);
  ctx.strokeStyle = C.border;
  ctx.beginPath();
  ctx.moveTo(ox, oy);
  ctx.lineTo(ox - Math.sin(half) * RANGE * s, oy - Math.cos(half) * RANGE * s);
  ctx.moveTo(ox, oy);
  ctx.lineTo(ox + Math.sin(half) * RANGE * s, oy - Math.cos(half) * RANGE * s);
  ctx.stroke();
  // 行進通道
  ctx.fillStyle = `${C.s1}22`;
  ctx.fillRect(X(-0.45), Y(RANGE), 0.9 * s, RANGE * s);

  // 障礙物點
  for (const [lat, f, hh] of r.radar) {
    ctx.fillStyle = hh >= 1.0 ? C.hanging : C.s3;
    ctx.fillRect(X(lat) - 1.5, Y(f) - 1.5, 3, 3);
  }
  // 地面危險
  const g = r.hazards.ground;
  if (r.ground.raw_type && g.distance != null) {
    ctx.strokeStyle = g.level !== 'none' ? LEVEL_COLOR[g.level] : C.muted;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(X(-0.8), Y(g.distance));
    ctx.lineTo(X(0.8), Y(g.distance));
    ctx.stroke();
    label(ctx, r.ground.raw_type_zh, X(0.85), Y(g.distance) + 6, ctx.strokeStyle);
  }
  // YOLO 物體
  for (const o of r.objects) {
    if (o.distance == null || o.distance > RANGE) continue;
    const color = o.in_path ? C.warning : C.text2;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(X(o.lateral) - 7, Y(o.distance) - 7, 14, 14);
    ctx.fillStyle = color;
    ctx.font = '11px system-ui, "Microsoft JhengHei", sans-serif';
    ctx.fillText(o.label_zh, X(o.lateral) + 10, Y(o.distance) + 4);
  }
  // 使用者
  ctx.fillStyle = C.text;
  ctx.beginPath();
  ctx.moveTo(ox, oy - 10);
  ctx.lineTo(ox - 7, oy + 6);
  ctx.lineTo(ox + 7, oy + 6);
  ctx.closePath();
  ctx.fill();
}

// ---------- 地面剖面 ----------

let profileChart = null;
function renderProfile(r) {
  if (!window.Chart) return;
  const pts = r.ground.profile.map(([x, y]) => ({ x, y }));
  const plateaus = r.ground.plateaus.flatMap((p) => [{ x: p.f0, y: p.h }, { x: p.f1, y: p.h }, { x: null, y: null }]);
  const g = r.hazards.ground;
  const edge = r.ground.raw_type && g.distance != null ? [{ x: g.distance, y: -1.2 }, { x: g.distance, y: 0.8 }] : [];
  const datasets = [
    { label: '實測地面高度', data: pts, type: 'scatter', pointRadius: 2.5, backgroundColor: C.s3, borderColor: C.s3 },
    { label: '平台', data: plateaus, type: 'line', borderColor: C.text2, borderWidth: 3, pointRadius: 0, spanGaps: false },
    { label: '地面邊緣', data: edge, type: 'line', borderColor: g.level !== 'none' ? LEVEL_COLOR[g.level] : C.muted, borderDash: [6, 4], borderWidth: 2, pointRadius: 0 },
  ];
  if (profileChart) {
    profileChart.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; ds.borderColor = datasets[i].borderColor; });
    profileChart.update('none');
    return;
  }
  Chart.defaults.color = C.text2;
  profileChart = new Chart($('profile-chart'), {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { position: 'top', align: 'start', labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8 } } },
      scales: {
        x: { min: 0, max: 6, title: { display: true, text: '前方距離（m）', color: C.muted }, grid: { color: C.grid }, ticks: { color: C.muted } },
        y: { min: -1.2, max: 0.8, title: { display: true, text: '離地高度（m）', color: C.muted }, grid: { color: C.grid }, ticks: { color: C.muted } },
      },
    },
  });
}

// ---------- 語音、震動、紀錄 ----------

// 後端的警示規劃已經做了節流：speak = true 才唸，紀錄也只記有播報的
function handleAlert(ev) {
  const act = ev.action_required;
  if (!act.speak || !act.audio_message) return;
  if ($('speech').checked && window.speechSynthesis) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(act.audio_message);
    u.lang = 'zh-TW';
    u.rate = 1.15;
    speechSynthesis.speak(u);
  }
  if ($('vibrate').checked && navigator.vibrate) {
    navigator.vibrate(act.urgency >= 3 ? [180, 120, 180, 120, 180] : act.urgency === 2 ? [150] : [80]);
  }
  state.log.unshift({ time: new Date().toLocaleTimeString('zh-TW', { hour12: false }), ev });
  state.log = state.log.slice(0, 50);
  renderLog();
}

const CATEGORY_ZH = {
  drop: '落差', stairs: '樓梯／手扶梯', vehicle: '車輛', wall: '牆壁', overhead: '懸空物', person: '行人',
  fixed: '固定物', object: '物品', obstacle: '未知障礙', step: '台階',
};

function renderLog() {
  if (!state.log.length) {
    $('log-body').innerHTML = '<div class="empty">尚無警報</div>';
    return;
  }
  $('log-body').innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>時間</th><th>類別</th><th>目標</th><th>等級</th><th class="num">距離</th><th>震動</th><th>語音提示</th><th>為何播報</th></tr></thead>
    <tbody>${state.log.map(({ time, ev }) => `<tr>
      <td class="muted">${time}</td>
      <td>${esc(CATEGORY_ZH[ev.data.category] || ev.data.category)}</td>
      <td>${esc(ev.data.label)}</td>
      <td><span class="badge ${ev.priority}">${LEVEL_ZH[ev.priority]}</span></td>
      <td class="num">${fmt(ev.data.distance)} m</td>
      <td>${VIB_ZH[ev.action_required.vibration] || '—'}</td>
      <td class="note">${esc(ev.action_required.audio_message)}</td>
      <td class="muted small">${esc(ev.action_required.speak_reason || '')}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

$('clear-log').addEventListener('click', () => { state.log = []; renderLog(); });

// ---------- 已知距離校正 ----------

const calib = { active: false, point: null };

function showCalibMsg(text, err = false) {
  $('calib-msg').hidden = false;
  $('calib-msg').textContent = text;
  $('calib-msg').classList.toggle('err', err);
}

function setCalibrating(on) {
  calib.active = on;
  calib.point = null;
  $('calib-panel').hidden = !on;
  $('calib-hint').hidden = false;
  $('calib-input').hidden = true;
  $('media-wrap').classList.toggle('calibrating', on);
  if (!on && state.lastResult) drawOverlay(state.lastResult);
}

$('calib').addEventListener('click', () => {
  if (!state.running) {
    showCalibMsg('請先按「開始偵測」，再做距離校正', true);
    return;
  }
  if (state.lastResult?.depth_mode === 'depth_camera') {
    showCalibMsg('深度相機已是實測距離，不需要校正', true);
    return;
  }
  setCalibrating(!calib.active);
});
$('calib-cancel').addEventListener('click', () => setCalibrating(false));

$('media-wrap').addEventListener('click', (e) => {
  if (!calib.active) return;
  const media = !still.hidden ? still : video;
  const rect = media.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  calib.point = { x, y };
  $('calib-hint').hidden = true;
  $('calib-input').hidden = false;
  $('calib-dist').focus();
  drawOverlay(state.lastResult);
});

$('calib-panel').addEventListener('submit', (e) => {
  e.preventDefault();
  const d = +$('calib-dist').value;
  if (!calib.point || !(d > 0.3)) return;
  state.ws?.send(JSON.stringify({ type: 'calibrate_distance', ...calib.point, distance_m: d }));
});

function onCalibrated(msg) {
  if (!msg.ok) {
    showCalibMsg(msg.message, true);
    return;
  }
  state.geometry = { ...state.geometry, ...msg.geometry };
  syncSliders();
  saveGeometry();
  setCalibrating(false);
  showCalibMsg(`✔ ${msg.message}。鏡頭高度已換算為等效值 ${msg.geometry.height_m.toFixed(2)} m，並記在這台瀏覽器。`);
}

$('hq').addEventListener('change', () => {
  sendGeometry();
  // 深度模型輸入大小改變後，相對深度的尺度也會變，之前的距離校正不一定適用
  if (state.running) showCalibMsg(`已切換為${$('hq').checked ? '高精度' : '標準'}深度；若之前做過距離校正，建議重新校正一次`);
});

// ---------- 手機陀螺儀：即時量測鏡頭傾角 ----------

const gyro = { pitch: null, lastSent: 0, sentPitch: null, seen: false };

function onOrientation(e) {
  if (e.beta == null) return;
  gyro.seen = true;
  const angle = screen.orientation?.angle ?? window.orientation ?? 0;
  // 直式：手機立起、後鏡頭朝正前方時 beta = 90；往下傾 → beta 變小
  const raw = angle % 180 === 0 ? 90 - e.beta : 90 - Math.abs(e.gamma);
  const p = Math.min(60, Math.max(0, raw));
  gyro.pitch = gyro.pitch == null ? p : 0.7 * gyro.pitch + 0.3 * p;
  const now = Date.now();
  if (now - gyro.lastSent > 500 && (gyro.sentPitch == null || Math.abs(gyro.pitch - gyro.sentPitch) >= 1)) {
    gyro.lastSent = now;
    gyro.sentPitch = gyro.pitch;
    state.geometry.pitch_deg = Math.round(gyro.pitch);
    $('pitch').value = state.geometry.pitch_deg;
    renderGeometry();
    sendGeometry({ live: true });  // live：不重置校正與平滑
  }
}

$('gyro').addEventListener('change', async () => {
  if (!$('gyro').checked) {
    window.removeEventListener('deviceorientation', onOrientation);
    $('pitch').disabled = false;
    return;
  }
  try {
    // iOS 需要使用者點擊後請求權限
    if (typeof DeviceOrientationEvent?.requestPermission === 'function'
      && (await DeviceOrientationEvent.requestPermission()) !== 'granted') throw new Error('denied');
  } catch {
    $('gyro').checked = false;
    showCalibMsg('無法使用陀螺儀：這台裝置不支援，或權限被拒（手機需用 https 連線）', true);
    return;
  }
  gyro.seen = false;
  window.addEventListener('deviceorientation', onOrientation);
  $('pitch').disabled = true;
  setTimeout(() => {
    if (!gyro.seen) {
      window.removeEventListener('deviceorientation', onOrientation);
      $('gyro').checked = false;
      $('pitch').disabled = false;
      showCalibMsg('這台裝置沒有陀螺儀資料（筆電通常沒有），請手動設定傾角', true);
    }
  }, 2000);
});
window.addEventListener('resize', () => state.lastResult && (drawOverlay(state.lastResult), drawRadar(state.lastResult)));

// ---------- 初始化 ----------

(async function init() {
  try {
    const s = await (await fetch('/api/status')).json();
    state.geometry = { ...s.defaults, ...(loadGeometry() || {}) };
    if (!s.realsense_installed) {
      $('source').querySelector('[value=depthcam]').textContent = '深度相機 RealSense（未安裝 pyrealsense2）';
    }
    setStatus(['後端已就緒，按「開始偵測」']);
  } catch {
    setStatus([{ text: '連不到後端：請在專案根目錄執行 python server.py，再開 http://localhost:8010/live.html', err: true }]);
  }
  syncSliders();
})();
