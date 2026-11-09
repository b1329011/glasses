// 手機版避障 PWA：鏡頭影格 → 電腦後端（WebSocket）→ 橘色物體框 + 底部警示 + 逼逼聲／語音／震動

const $ = (id) => document.getElementById(id);
const video = $('video');
const canvas = $('overlay');
const grab = document.createElement('canvas');

const ORANGE = '#ff8c1a';
const RED = '#e5484d';
const LEVEL_ZH = { critical: '停下', warning: '注意', notice: '提醒', none: '安全' };
const APP_VERSION = '2026.11.09-3';  // 改程式時更新，手機畫面上看得到
const SEND_LONG_SIDE = 640;   // 送到後端的影格長邊（像素）
// 同時在路上的影格數：經過網路通道時延遲約 0.5 秒，送 2 張讓「傳輸」和「運算」重疊，幀率約加倍
const MAX_INFLIGHT = 2;

// ---------- 設定（存在手機上） ----------

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 無痕模式 */ } },
};

// 存取權杖：第一次從 QR code 連結的 ?k= 取得，之後記在手機上
(function readToken() {
  const url = new URL(location.href);
  const k = url.searchParams.get('k');
  if (k) {
    store.set('oa-token', k);
    url.searchParams.delete('k');
    history.replaceState(null, '', url.pathname + url.search);
  }
})();

const prefs = { speech: true, speakAhead: false, vibrate: true, toneFallback: true, gyro: true, hq: false, ...store.get('oa-m-prefs', {}) };
const geometry = store.get('oa-m-geometry', { height_m: 1.3, pitch_deg: 15, hfov_deg: null });
let hfovManual = geometry.hfov_deg != null;

const state = {
  ws: null, running: false, stream: null, lastResult: null, frames: [],
  reconnectTimer: null, wakeLock: null, inflight: 0,
};

// ---------- 狀態顯示 ----------

function setStatus(text, err = false) {
  $('status').textContent = text;
  $('status').classList.toggle('err', err);
}

function renderAlert(ev, hazards) {
  const lv = ev?.priority || 'none';
  $('alert').className = `alert ${lv}`;
  $('alert-level').textContent = LEVEL_ZH[lv];
  $('alert-msg').textContent = ev?.action_required?.audio_message || '前方安全';
  const subs = [];
  if (ev?.data?.label && ev.priority === 'critical') subs.push(ev.data.label);
  if (ev?.data?.distance != null) subs.push(`距離 ${ev.data.distance.toFixed(1)} 公尺`);
  if (ev?.data?.others?.length) subs.push(`另有 ${ev.data.others.length} 項`);
  $('alert-sub').textContent = subs.join('・');
}

// ---------- 畫面疊圖（video 用 object-fit: cover，要換算裁切） ----------

function coverMap() {
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  const vw = video.videoWidth || W;
  const vh = video.videoHeight || H;
  const s = Math.max(W / vw, H / vh);
  return { W, H, s, dx: (W - vw * s) / 2, dy: (H - vh * s) / 2, vw, vh };
}

function draw(r) {
  const dpr = window.devicePixelRatio || 1;
  const m = coverMap();
  if (canvas.width !== Math.round(m.W * dpr) || canvas.height !== Math.round(m.H * dpr)) {
    canvas.width = Math.round(m.W * dpr);
    canvas.height = Math.round(m.H * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, m.W, m.H);
  if (!r) return;
  const X = (x) => m.dx + x * m.vw * m.s;
  const Y = (y) => m.dy + y * m.vh * m.s;

  // 地面邊緣（斷崖／樓梯）
  const g = r.hazards?.ground;
  if (r.ground?.edge && g && g.level !== 'none') {
    ctx.strokeStyle = g.level === 'critical' ? RED : ORANGE;
    ctx.lineWidth = 6;
    ctx.setLineDash([16, 10]);
    ctx.beginPath();
    ctx.moveTo(X(r.ground.edge[0][0]), Y(r.ground.edge[0][1]));
    ctx.lineTo(X(r.ground.edge[1][0]), Y(r.ground.edge[1][1]));
    ctx.stroke();
    ctx.setLineDash([]);
    tag(ctx, `${g.type_zh} ${g.distance?.toFixed(1)} m`, X(r.ground.edge[0][0]) + 8, Y(r.ground.edge[0][1]) - 8,
      g.level === 'critical' ? RED : ORANGE);
  }

  // 物體：橘色框 + 名稱與距離（近的畫在最上層）
  placed.length = 0;
  const topKey = r.candidates?.[0]?.key;
  const objs = (r.objects || []).filter((o) => o.category)  // 杯子、筆電等不在分類表的不畫
    .sort((a, b) => (b.distance ?? 99) - (a.distance ?? 99));
  for (const o of objs) {
    const [x1, y1, x2, y2] = o.box;
    const left = X(x1);
    const top = Y(y1);
    ctx.strokeStyle = ORANGE;
    ctx.lineWidth = `obj${o.track_id}` === topKey ? 7 : o.in_path ? 4 : 2.5;
    ctx.strokeRect(left, top, X(x2) - left, Y(y2) - top);
    const text = o.distance != null ? `${o.label_zh} ${o.distance.toFixed(1)} m` : o.label_zh;
    tag(ctx, text, Math.max(4, left), Math.max(30, top), ORANGE);
  }
}

// 已畫的標籤位置；重疊時往下移，避免互相遮住
const placed = [];
function tag(ctx, text, x, y, color) {
  ctx.font = '700 18px system-ui, "PingFang TC", "Noto Sans TC", sans-serif';
  const w = ctx.measureText(text).width + 14;
  const hit = (yy) => placed.some((p) => x < p.x + p.w && x + w > p.x && yy - 26 < p.y && yy > p.y - 26);
  for (let i = 0; i < 6 && hit(y); i++) y += 28;
  placed.push({ x, y, w });
  ctx.fillStyle = color;
  ctx.fillRect(x, y - 26, w, 26);
  ctx.fillStyle = '#000';
  ctx.fillText(text, x + 7, y - 7);
}

window.addEventListener('resize', () => draw(state.lastResult));

// ---------- 偵測清單（Minecraft 字幕風格） ----------
//
// 每個東西一行：方向箭頭在左（左前方）或右（右前方），正前方用 ▲、頭部高度用 ⤒。
// 顏色：紅＝請停下、黃＝接近（2.5 m 內）、藍框＝提早告知（2.5~5.5 m）、灰＝看到但不在路上。
// 看不到後保留 2.5 秒並慢慢淡出，不會一閃一閃。
const SUB_KEEP_MS = 2500;
const SUB_MAX = 6;
const ZONE_ORDER = { stop: 3, near: 2, ahead: 1, none: 0 };
const subs = new Map();

function updateSubs(listing) {
  const now = performance.now();
  for (const it of listing || []) subs.set(it.key, { ...it, seen: now });
  renderSubs();
}

function renderSubs() {
  const now = performance.now();
  for (const [k, it] of subs) if (now - it.seen > SUB_KEEP_MS) subs.delete(k);
  const items = [...subs.values()]
    .sort((a, b) => (ZONE_ORDER[b.zone] - ZONE_ORDER[a.zone]) || (a.distance - b.distance))
    .slice(0, SUB_MAX);
  $('subs').innerHTML = items.map((it) => {
    const age = now - it.seen;
    const opacity = age < 800 ? 1 : Math.max(0, 1 - (age - 800) / (SUB_KEEP_MS - 800));
    const left = it.direction === 'left' ? '◀' : it.direction === 'up' ? '⤒' : it.direction === 'front' ? '▲' : '';
    const right = it.direction === 'right' ? '▶' : '';
    return `<li class="${it.zone}" style="opacity:${opacity.toFixed(2)}">
      <span class="arrow">${left}</span><span class="label">${it.label}</span>
      <span class="dist">${it.distance.toFixed(1)} m</span><span class="arrow">${right}</span></li>`;
  }).join('');
}
setInterval(renderSubs, 200);  // 淡出動畫

// ---------- 震動（越緊急越快）；手機不支援震動時改用提示音 ----------
//
// urgency 由後端警示規劃決定：3 = 請停下、2 = 快接近（near）、1 = 提早告知（ahead）、0 = 安全
//   3：每 0.35 秒短震一次（連續、急促）
//   2：每 1 秒震一次
//   1：只在播報時輕震一下
const VIBRATE_RHYTHM = { 3: { every: 350, pulse: 180 }, 2: { every: 1000, pulse: 150 } };
const canVibrate = typeof navigator.vibrate === 'function';

let audioCtx = null;

function unlockAudio() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

// 提示音（iPhone 等不支援震動的手機用）
function tone(ms = 120, freq = 1000) {
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = 'square';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(0.3, t0 + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + ms / 1000);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start(t0);
  osc.stop(t0 + ms / 1000 + 0.01);
}

function pulse(ms) {
  if (!prefs.vibrate) return;
  if (canVibrate) navigator.vibrate(ms);
  else if (prefs.toneFallback) tone(Math.min(ms, 150), ms >= 180 ? 1200 : 900);
}

let rhythmTimer = null;
let rhythmUrgency = 0;
function updateVibration(urgency) {
  if (urgency === rhythmUrgency) return;
  rhythmUrgency = urgency;
  clearInterval(rhythmTimer);
  rhythmTimer = null;
  if (canVibrate) navigator.vibrate(0);
  const r = VIBRATE_RHYTHM[urgency];
  if (r) {
    pulse(r.pulse);
    rhythmTimer = setInterval(() => pulse(r.pulse), r.every);
  }
}

// ---------- 語音：後端已節流，只在 speak = true 時開口 ----------

function announce(ev) {
  const act = ev?.action_required;
  if (!act?.speak || !act.audio_message) return;
  if (prefs.speech && window.speechSynthesis) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(act.audio_message);
    u.lang = 'zh-TW';
    u.rate = 1.15;
    speechSynthesis.speak(u);
  }
  if ((act.urgency ?? 0) <= 1) pulse(80);  // 提早告知：播報時輕震一下
}

// ---------- 後端連線 ----------

function wsUrl() {
  const token = store.get('oa-token', '');
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/camera${token ? `?token=${encodeURIComponent(token)}` : ''}`;
}

function sendConfig(extra = {}) {
  if (state.ws?.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify({
    type: 'config', ...geometry, hfov_deg: geometry.hfov_deg ?? autoHfov(),
    depth_quality: prefs.hq ? 'high' : 'standard', speak_ahead: !!prefs.speakAhead, ...extra,
  }));
}

function connect() {
  clearTimeout(state.reconnectTimer);
  setStatus('連線電腦中…');
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  let opened = false;
  ws.onopen = () => {
    opened = true;
    state.inflight = 0;
    sendConfig();
    pump();
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'error') {
      setStatus(msg.message, true);
      return;
    }
    if (msg.type === 'calibrated') {
      onCalibrated(msg);
      return;
    }
    if (msg.type !== 'result') return;
    state.inflight = Math.max(0, state.inflight - 1);
    try {
      onResult(msg);
    } catch (err) {
      console.error(err);
    } finally {
      if (state.running) setTimeout(pump, 0);
    }
  };
  ws.onclose = (e) => {
    if (state.ws !== ws || !state.running) return;
    if (e.code === 4401) {
      setStatus('連結已失效：請重新掃描電腦上的 QR code', true);
      return;
    }
    if (!opened) {
      // 連不上時分辨：電腦沒開 phone.py，或是這個網址已失效（免費通道每次啟動網址都不同）
      fetch('/api/status', { cache: 'no-store' })
        .then((r) => setStatus(r.ok ? '連不到辨識服務，重試中…' : '網址已失效：請重新掃描電腦上的 QR code', true))
        .catch(() => setStatus('網址已失效或電腦未開 phone.py：請重新掃描電腦上的 QR code', true));
    } else {
      setStatus('連線中斷，重新連線中…', true);
    }
    state.reconnectTimer = setTimeout(connect, 2000);
  };
}

function pump() {
  while (state.running && state.inflight < MAX_INFLIGHT && sendFrame()) { /* 補滿在路上的影格 */ }
}

function sendFrame() {
  if (!state.running || state.ws?.readyState !== WebSocket.OPEN) return false;
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) { setTimeout(pump, 100); return false; }
  const s = Math.min(1, SEND_LONG_SIDE / Math.max(vw, vh));
  grab.width = Math.round(vw * s);
  grab.height = Math.round(vh * s);
  grab.getContext('2d').drawImage(video, 0, 0, grab.width, grab.height);
  // toDataURL 是同步的；部分瀏覽器的 toBlob 會被延遲到一秒左右
  const b64 = grab.toDataURL('image/jpeg', 0.75).split(',', 2)[1];
  state.ws.send(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
  state.inflight += 1;
  return true;
}

function onResult(r) {
  state.lastResult = r;
  const now = performance.now();
  state.frames = [...state.frames.filter((t) => now - t < 3000), now];
  const fps = state.frames.length > 1 ? (state.frames.length - 1) / ((now - state.frames[0]) / 1000) : 0;
  setStatus(r.calibration.ok ? `${fps.toFixed(1)} fps` : r.calibration.status, !r.calibration.ok);
  draw(r);
  updateSubs(r.listing);
  renderAlert(r.event, r.hazards);
  updateVibration(r.event?.action_required?.urgency ?? 0);
  announce(r.event);
}

// ---------- 開始／停止 ----------

async function start() {
  unlockAudio();  // 必須在使用者點擊時建立，iOS 才允許出聲
  $('start-hint').textContent = '';
  $('start-hint').classList.remove('err');
  if (!window.isSecureContext) {
    showStartError('需要 https 連線才能開鏡頭：請掃描電腦上 phone.py 顯示的 QR code');
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch (e) {
    const hints = {
      NotAllowedError: '鏡頭權限被拒：請到瀏覽器設定允許此網站使用相機',
      NotFoundError: '找不到鏡頭',
      NotReadableError: '鏡頭被其他 App 占用，請先關閉它',
    };
    showStartError(hints[e.name] || `無法開啟鏡頭：${e.message}`);
    return;
  }
  video.srcObject = state.stream;
  // play() 在畫面被遮住時可能一直不回應，最多等 1.5 秒
  await Promise.race([video.play().catch(() => {}), new Promise((r) => setTimeout(r, 1500))]);
  if (prefs.gyro) await enableGyro();
  await keepAwake();
  $('start-screen').hidden = true;
  state.running = true;
  connect();
}

function showStartError(text) {
  $('start-hint').textContent = text;
  $('start-hint').classList.add('err');
}

function stop() {
  state.running = false;
  clearTimeout(state.reconnectTimer);
  state.ws?.close();
  state.ws = null;
  state.stream?.getTracks().forEach((t) => t.stop());
  updateVibration(0);
  speechSynthesis?.cancel();
  state.wakeLock?.release().catch(() => {});
  state.lastResult = null;
  draw(null);
  subs.clear();
  renderSubs();
  renderAlert(null);
  $('start-screen').hidden = false;
  setStatus('已停止');
}

$('start-btn').addEventListener('click', start);
$('stop-btn').addEventListener('click', () => { $('settings').close(); stop(); });

// 螢幕常亮
async function keepAwake() {
  try { state.wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* 不支援或省電模式 */ }
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.running) keepAwake();
});

// ---------- 陀螺儀：即時量測傾角 ----------

const gyro = { pitch: null, lastSent: 0, sent: null, on: false };

function onOrientation(e) {
  if (e.beta == null) return;
  const angle = screen.orientation?.angle ?? window.orientation ?? 0;
  // 直式：手機立起、後鏡頭朝正前方時 beta = 90；往下傾 → beta 變小
  const raw = angle % 180 === 0 ? 90 - e.beta : 90 - Math.abs(e.gamma);
  const p = Math.min(60, Math.max(0, raw));
  gyro.pitch = gyro.pitch == null ? p : 0.7 * gyro.pitch + 0.3 * p;
  const now = Date.now();
  if (now - gyro.lastSent > 500 && (gyro.sent == null || Math.abs(gyro.pitch - gyro.sent) >= 1)) {
    gyro.lastSent = now;
    gyro.sent = gyro.pitch;
    geometry.pitch_deg = Math.round(gyro.pitch);
    renderSettings();
    sendConfig({ live: true });  // live：不重置校正與平滑
  }
}

async function enableGyro() {
  try {
    if (typeof DeviceOrientationEvent?.requestPermission === 'function'
      && (await DeviceOrientationEvent.requestPermission()) !== 'granted') throw new Error('denied');
    if (!gyro.on) window.addEventListener('deviceorientation', onOrientation);
    gyro.on = true;
  } catch {
    prefs.gyro = false;
    savePrefs();
  }
}

function disableGyro() {
  window.removeEventListener('deviceorientation', onOrientation);
  gyro.on = false;
}

// ---------- 設定面板 ----------

function autoHfov() {
  // 手機主鏡頭：直拿時水平視角約 57°，橫拿約 73°（距離校正可以修正誤差）
  return (video.videoHeight > video.videoWidth) ? 57 : 73;
}

function savePrefs() { store.set('oa-m-prefs', prefs); }
function saveGeometry() { store.set('oa-m-geometry', hfovManual ? geometry : { ...geometry, hfov_deg: null }); }

function renderSettings() {
  const hfov = geometry.hfov_deg ?? autoHfov();
  $('height').value = geometry.height_m;
  $('pitch').value = geometry.pitch_deg;
  $('hfov').value = hfov;
  $('height-val').textContent = `${(+geometry.height_m).toFixed(2)} m`;
  $('pitch-val').textContent = `${geometry.pitch_deg}°${gyro.on ? '（陀螺儀）' : ''}`;
  $('hfov-val').textContent = `${hfov}°${hfovManual ? '' : '（自動）'}`;
  $('pitch').disabled = gyro.on;
  for (const k of ['gyro', 'toneFallback', 'speech', 'speakAhead', 'vibrate', 'hq']) $(k).checked = !!prefs[k];
  $('vibrate-note').textContent = canVibrate ? '' : '這支手機不支援震動（例如 iPhone），會改用提示音';
}

$('settings-btn').addEventListener('click', () => { renderSettings(); $('settings').showModal(); });

for (const [id, key] of [['height', 'height_m'], ['pitch', 'pitch_deg'], ['hfov', 'hfov_deg']]) {
  $(id).addEventListener('input', () => {
    geometry[key] = +$(id).value;
    if (id === 'hfov') hfovManual = true;
    renderSettings();
  });
  $(id).addEventListener('change', () => { saveGeometry(); sendConfig(); });
}

for (const k of ['toneFallback', 'speech', 'speakAhead', 'vibrate', 'hq']) {
  $(k).addEventListener('change', () => {
    prefs[k] = $(k).checked;
    savePrefs();
    if (k === 'hq' || k === 'speakAhead') sendConfig();
    if (k === 'vibrate' && !prefs.vibrate) updateVibration(0);
  });
}
$('gyro').addEventListener('change', async () => {
  prefs.gyro = $('gyro').checked;
  savePrefs();
  if (prefs.gyro) await enableGyro(); else disableGyro();
  renderSettings();
});
// 測試：依序示範三種節奏（提醒 → 接近 → 請停下），各 2 秒
$('test-vibrate').addEventListener('click', () => {
  unlockAudio();
  pulse(80);
  setTimeout(() => updateVibration(2), 1000);
  setTimeout(() => updateVibration(3), 3000);
  setTimeout(() => updateVibration(state.lastResult?.event?.action_required?.urgency ?? 0), 5000);
});

// ---------- 距離校正：點畫面上量過距離的位置 ----------

$('calib-btn').addEventListener('click', () => {
  if (!state.running || !state.lastResult) {
    $('calib-msg').textContent = '請先開始偵測，再做距離校正';
    return;
  }
  $('settings').close();
  document.body.classList.add('calibrating');
  $('calib-tip').hidden = false;
});

document.addEventListener('click', (e) => {
  if (!document.body.classList.contains('calibrating') || e.target.closest('button, dialog')) return;
  const m = coverMap();
  const x = (e.clientX - m.dx) / (m.vw * m.s);
  const y = (e.clientY - m.dy) / (m.vh * m.s);
  document.body.classList.remove('calibrating');
  $('calib-tip').hidden = true;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const ans = prompt('這個位置實際距離幾公尺？', '2.0');
  const d = parseFloat(ans);
  if (!(d > 0.3)) return;
  state.ws?.send(JSON.stringify({ type: 'calibrate_distance', x, y, distance_m: d }));
});

function onCalibrated(msg) {
  if (!msg.ok) {
    setStatus(msg.message, true);
    $('calib-msg').textContent = msg.message;
    return;
  }
  geometry.height_m = msg.geometry.height_m;
  saveGeometry();
  $('calib-msg').textContent = `✔ ${msg.message}`;
  setStatus('距離已校正');
}

// ---------- 安裝（PWA） ----------

let installPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  installPrompt = e;
  $('install-btn').hidden = false;
});
$('install-btn').addEventListener('click', async () => {
  installPrompt?.prompt();
  await installPrompt?.userChoice;
  installPrompt = null;
  $('install-btn').hidden = true;
});
const standalone = matchMedia('(display-mode: fullscreen), (display-mode: standalone)').matches || navigator.standalone;
if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone) $('ios-hint').hidden = false;

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' })
    .then((reg) => reg.update())
    .catch((e) => console.warn('Service Worker 註冊失敗', e));
  // 從舊版更新時（原本就有 Service Worker 在管這頁），新版接手後重新整理一次，確保用的是最新的 app.js。
  // 第一次安裝不重新整理；偵測中也不打斷。
  const hadController = !!navigator.serviceWorker.controller;
  let reloaded = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController && !reloaded && !state.running) { reloaded = true; location.reload(); }
  });
}
$('version').textContent = `版本 ${APP_VERSION}${canVibrate ? '' : '・此手機不支援震動，改用提示音'}`;

renderSettings();
renderAlert(null);
