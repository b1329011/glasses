// 障礙物避障模組：模擬結果視覺化 + 即時判斷展示（純 JS + Chart.js）

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const C = {
  depth: css('--s1'), tof: css('--s3'), warning: css('--warning'), critical: css('--critical'),
  good: css('--good'), text2: css('--text-2'), muted: css('--muted'), grid: css('--grid'), surface2: css('--surface-2'),
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d));

const SCENARIO_ORDER = ['normal_walking', 'hanging_obstacle', 'ground_drop', 'mixed'];
const LEVEL_ZH = { critical: '危險', warning: '警告', none: '安全' };
const TYPE_ZH = { hanging_obstacle: '懸空障礙物', ground_drop: '地面落差', multiple: '懸空 + 落差' };
const VIB_ZH = { strong: '強震動', light: '輕震動' };

// 沒有 data.js 時的預設值（與 Python 常數相同）
const DEFAULTS = {
  thresholds: {
    hanging: { critical_m: 1.5, warning_m: 3.0 },
    ground_drop: {
      warning_cm: 30, critical_cm: 50, confirm_count: 3, window_size: 5,
      min_baseline_samples: 3, rebaseline_count: 20, tof_max_range_cm: 400,
    },
  },
  vibration: { critical: 'strong', warning: 'light', none: null },
  audio: {
    'hanging_obstacle|critical': '前方有懸空障礙物，請注意',
    'hanging_obstacle|warning': '前方約{distance:.1f}公尺有懸空障礙物',
    'ground_drop|critical': '前方地面有大落差，請立即停下',
    'ground_drop|warning': '前方有階梯或落差，請放慢腳步',
    'multiple|critical': '前方有懸空障礙物，且地面有落差，請立即停下',
    'multiple|warning': '前方有懸空障礙物與地面落差，請放慢腳步',
  },
};

// ---------- 讀取資料 ----------

async function loadData() {
  if (window.SIM_DATA) return window.SIM_DATA;
  // 沒有 data.js 時（例如 http server 開啟），改讀 output/ 下的 JSON
  const scenarios = {};
  await Promise.all(SCENARIO_ORDER.map(async (key) => {
    try {
      const r = await fetch(`../output/${key}_result.json`);
      if (r.ok) scenarios[key] = await r.json();
    } catch { /* 忽略 */ }
  }));
  const any = Object.values(scenarios)[0];
  return { ...DEFAULTS, thresholds: any?.thresholds || DEFAULTS.thresholds, scenarios };
}

// ---------- Chart.js ----------

if (window.Chart) {
  Chart.defaults.color = C.text2;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.borderColor = C.grid;
}

const charts = {};
function makeChart(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart($(id), config);
}

function chartOptions(yTitle, xMax, yMin, yMax) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', axis: 'x', intersect: false },
    plugins: {
      legend: { position: 'top', align: 'start', labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8, padding: 14 } },
      tooltip: {
        backgroundColor: '#000', borderColor: '#444', borderWidth: 1, padding: 10,
        filter: (item) => !item.dataset.isThreshold,
        callbacks: { title: (items) => `t = ${fmt(items[0].parsed.x, 1)} 秒` },
      },
    },
    scales: {
      x: { type: 'linear', min: 0, max: xMax, title: { display: true, text: '時間（秒）', color: C.muted }, ticks: { color: C.muted } },
      y: { min: yMin, max: yMax, title: { display: true, text: yTitle, color: C.muted }, ticks: { color: C.muted } },
    },
  };
}

const lineDs = (label, data, color, extra = {}) => ({
  label, data, borderColor: color, backgroundColor: color, borderWidth: 2,
  pointRadius: 0, pointHoverRadius: 4, tension: 0.15, ...extra,
});
const thresholdDs = (label, data, color) => lineDs(label, data, color, {
  borderWidth: 1, borderDash: [6, 4], pointHoverRadius: 0, isThreshold: true, tension: 0,
});
const markerDs = (label, data, color, style = 'circle') => ({
  label, data, type: 'scatter', showLine: false, pointStyle: style, pointRadius: style === 'circle' ? 5 : 6,
  pointHoverRadius: 7, backgroundColor: color, borderColor: style === 'circle' ? C.surface2 : color, borderWidth: style === 'circle' ? 1.5 : 2,
});

function pick(t, y, levels, wanted) {
  return t.map((x, i) => (levels[i] === wanted ? { x, y: y[i] } : null)).filter(Boolean);
}

function renderCharts(s) {
  const t = s.timestamps;
  const th = s.thresholds;
  const xMax = Math.ceil(t[t.length - 1]);
  const xy = (arr) => t.map((x, i) => ({ x, y: arr[i] }));

  makeChart('chart-depth', {
    type: 'line',
    data: {
      datasets: [
        lineDs('深度相機 ROI 最近距離', xy(s.depth_distance), C.depth),
        thresholdDs(`warning 門檻 ${th.hanging.warning_m} m`, [{ x: 0, y: th.hanging.warning_m }, { x: xMax, y: th.hanging.warning_m }], C.warning),
        thresholdDs(`critical 門檻 ${th.hanging.critical_m} m`, [{ x: 0, y: th.hanging.critical_m }, { x: xMax, y: th.hanging.critical_m }], C.critical),
        markerDs('觸發 warning', pick(t, s.depth_distance, s.hanging_levels, 'warning'), C.warning),
        markerDs('觸發 critical', pick(t, s.depth_distance, s.hanging_levels, 'critical'), C.critical),
      ],
    },
    options: chartOptions('距離（m）', xMax, 0, Math.max(8.5, Math.ceil(Math.max(...s.depth_distance)))),
  });

  const base = s.drop_baseline.map((b, i) => ({ x: t[i], y: b }));
  const offset = (d) => s.drop_baseline.map((b, i) => ({ x: t[i], y: b == null ? null : b + d }));
  const filteredLevels = s.drop_status.map((st, i) => (st === 'pending' && s.drop_levels[i] === 'none' ? 'filtered' : ''));
  makeChart('chart-tof', {
    type: 'line',
    data: {
      datasets: [
        lineDs('手杖 ToF 距離', xy(s.tof_distance), C.tof),
        thresholdDs('地面基準（視窗中位數）', base, C.muted),
        thresholdDs(`基準 +${th.ground_drop.warning_cm} cm`, offset(th.ground_drop.warning_cm), C.warning),
        thresholdDs(`基準 +${th.ground_drop.critical_cm} cm`, offset(th.ground_drop.critical_cm), C.critical),
        markerDs(`超過門檻、連續未滿 ${th.ground_drop.confirm_count} 筆`, pick(t, s.tof_distance, filteredLevels, 'filtered'), C.muted, 'crossRot'),
        markerDs('觸發 warning', pick(t, s.tof_distance, s.drop_levels, 'warning'), C.warning),
        markerDs('觸發 critical', pick(t, s.tof_distance, s.drop_levels, 'critical'), C.critical),
      ],
    },
    options: chartOptions('距離（cm）', xMax),
  });
}

// ---------- 情境區 ----------

let DATA = null;
let current = null;

function triggers(alerts) {
  return alerts.filter((a) => a.is_new);
}

function renderTabs() {
  const keys = SCENARIO_ORDER.filter((k) => DATA.scenarios[k]);
  $('tabs').innerHTML = keys.map((k) => {
    const s = DATA.scenarios[k];
    const ok = (s.validation || []).every((v) => v.passed);
    return `<button class="tab" role="tab" data-key="${k}" aria-selected="${k === current}">
      <span class="dot ${ok ? '' : 'fail'}" title="${ok ? '驗證通過' : '驗證失敗'}"></span>${esc(s.title)}</button>`;
  }).join('');
}

function renderScenario() {
  const s = DATA.scenarios[current];
  const trig = triggers(s.alerts);
  const count = (lv) => trig.filter((a) => a.level === lv).length;
  const steps = (lv) => s.alerts.filter((a) => a.level === lv).length;
  const filtered = s.drop_status.filter((st, i) => st === 'pending' && s.drop_status[i + 1] !== 'pending' && s.drop_status[i + 1] !== 'confirmed').length;
  const ok = s.validation.every((v) => v.passed);
  const first = trig[0];

  $('sim-body').innerHTML = `
    <div class="kpis">
      <div class="kpi critical"><div class="label">critical 觸發</div><div class="value">${count('critical')}</div><div class="sub">共 ${steps('critical')} 筆警報</div></div>
      <div class="kpi warning"><div class="label">warning 觸發</div><div class="value">${count('warning')}</div><div class="sub">共 ${steps('warning')} 筆警報</div></div>
      <div class="kpi"><div class="label">首次警報</div><div class="value">${first ? `${fmt(first.timestamp, 1)}<small class="muted"> 秒</small>` : '—'}</div><div class="sub">${first ? `${esc(TYPE_ZH[first.type])}・${LEVEL_ZH[first.level]}` : '全程無警報'}</div></div>
      <div class="kpi"><div class="label">擺動突波濾除</div><div class="value">${filtered}</div><div class="sub">連續未滿 ${s.thresholds.ground_drop.confirm_count} 筆不觸發</div></div>
      <div class="kpi"><div class="label">驗證結果</div><div class="value">${ok ? '<span style="color:#5fd35f">通過</span>' : '<span style="color:#ff8a8a">失敗</span>'}</div><div class="sub">${s.validation.filter((v) => v.passed).length} / ${s.validation.length} 項</div></div>
    </div>
    <ul class="checks">
      ${s.validation.map((v) => `<li><span class="badge ${v.passed ? 'pass' : 'fail'}">${v.passed ? '通過' : '失敗'}</span>${esc(v.desc)}</li>`).join('')}
    </ul>
    <div class="charts">
      <div class="chart-box"><h3>懸空障礙物（眼鏡深度相機）</h3><div class="canvas-wrap"><canvas id="chart-depth" aria-label="深度相機距離時間序列"></canvas></div></div>
      <div class="chart-box"><h3>地面落差（手杖 ToF）</h3><div class="canvas-wrap"><canvas id="chart-tof" aria-label="ToF 距離時間序列"></canvas></div>
        <div class="chart-note">灰色虛線為偵測器的地面基準；異常讀數不會進入視窗，所以落差期間基準維持在原本地面高度。</div></div>
    </div>`;
  renderCharts(s);
  renderAlerts();
}

function renderAlerts() {
  const s = DATA.scenarios[current];
  const onlyNew = $('only-new').checked;
  const rows = onlyNew ? s.alerts.filter((a) => a.is_new) : s.alerts;
  if (!s.alerts.length) {
    $('alerts-body').innerHTML = '<div class="empty">此情境沒有觸發任何警報（符合預期：只記錄 status_log，不震動、不播語音）</div>';
    return;
  }
  const value = (a) => {
    const parts = [];
    if (a.distance != null && a.type !== 'ground_drop') parts.push(`距離 ${fmt(a.distance)} m`);
    if (a.drop_cm != null && a.type !== 'hanging_obstacle') parts.push(`落差 ${fmt(a.drop_cm, 1)} cm`);
    return parts.join('・');
  };
  $('alerts-body').innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th class="num">時間（秒）</th><th>時間戳記</th><th>類型</th><th>等級</th><th>量測值</th><th>震動</th><th>語音提示</th><th>狀態</th></tr></thead>
      <tbody>${rows.map((a) => `<tr class="${a.is_new ? '' : 'cont'}">
        <td class="num">${fmt(a.timestamp, 1)}</td>
        <td class="muted small">${esc(a.time)}</td>
        <td>${esc(TYPE_ZH[a.type] || a.type)}</td>
        <td><span class="badge ${a.level}">${a.level}</span></td>
        <td>${value(a)}</td>
        <td>${esc(VIB_ZH[a.vibration] || '—')}</td>
        <td class="note">${esc(a.audio_message)}</td>
        <td>${a.is_new ? '<span class="badge info">新觸發</span>' : '<span class="muted small">持續</span>'}</td>
      </tr>`).join('')}</tbody>
    </table></div>
    <p class="muted small">${onlyNew ? `顯示 ${rows.length} 次新觸發（共 ${s.alerts.length} 筆警報；取消勾選可看逐筆紀錄）` : `共 ${s.alerts.length} 筆警報`}</p>
    <details><summary>融合輸出 JSON 範例（每種等級／類型各一筆）</summary>
      <pre class="json">${esc(JSON.stringify(s.sample_events, null, 2))}</pre>
    </details>`;
}

function selectScenario(key) {
  current = key;
  document.querySelectorAll('.tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.key === key)));
  renderScenario();
}

// ---------- 即時模擬：與 Python 偵測器相同的邏輯 ----------

function detectHanging(distance, th) {
  if (distance == null || Number.isNaN(distance) || distance <= 0) return { level: 'none', distance: null };
  if (distance < th.critical_m) return { level: 'critical', distance };
  if (distance <= th.warning_m) return { level: 'warning', distance };
  return { level: 'none', distance };
}

const median = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

class GroundDropDetector {
  constructor(cfg) { this.cfg = cfg; this.reset(); }
  reset() { this.window = []; this.held = []; this.consecutive = 0; }
  push(arr, v) { arr.push(v); if (arr.length > this.cfg.window_size) arr.shift(); }
  result(level, reading, baseline, drop, status) {
    return { level, reading, baseline, drop_cm: drop, consecutive: this.consecutive, status };
  }
  update(raw) {
    const c = this.cfg;
    const reading = raw == null || !Number.isFinite(raw) || raw >= c.tof_max_range_cm ? c.tof_max_range_cm : raw;
    if (this.window.length < c.min_baseline_samples) {
      this.push(this.window, reading);
      return this.result('none', reading, null, null, 'warmup');
    }
    const baseline = median(this.window);
    const drop = reading - baseline;
    if (drop < c.warning_cm) {
      this.consecutive = 0;
      this.held = [];
      this.push(this.window, reading);
      return this.result('none', reading, baseline, drop, 'normal');
    }
    this.consecutive += 1;
    this.push(this.held, reading);
    if (this.consecutive >= c.rebaseline_count) {
      this.window = [...this.held];
      this.held = [];
      this.consecutive = 0;
      return this.result('none', reading, baseline, drop, 'rebaseline');
    }
    if (this.consecutive < c.confirm_count) return this.result('none', reading, baseline, drop, 'pending');
    return this.result(drop >= c.critical_cm ? 'critical' : 'warning', reading, baseline, drop, 'confirmed');
  }
}

// 對應 Python 的 str.format（只用到 {distance:.1f} 這種寫法）
const formatMsg = (tpl, vars) => tpl.replace(/\{(\w+)(?::\.(\d)f)?\}/g, (_, k, d) => (d ? Number(vars[k] || 0).toFixed(+d) : vars[k]));

function fuse(h, g) {
  const rank = { none: 0, warning: 1, critical: 2 };
  const priority = rank[h.level] >= rank[g.level] ? h.level : g.level;
  let type = null;
  if (priority !== 'none') type = rank[h.level] === rank[g.level] ? 'multiple' : rank[h.level] > rank[g.level] ? 'hanging_obstacle' : 'ground_drop';
  const data = { obstacle_type: type };
  if (type === 'hanging_obstacle' || type === 'multiple' || priority === 'none') data.distance = h.distance == null ? null : +h.distance.toFixed(2);
  if (type === 'ground_drop' || type === 'multiple' || priority === 'none') data.drop_height_cm = g.drop_cm == null ? null : +g.drop_cm.toFixed(1);
  return {
    agent: 'navigation_agent',
    timestamp: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    event_type: priority === 'none' ? 'status_log' : 'obstacle_alert',
    priority,
    data,
    action_required: {
      vibration: DATA.vibration[priority] ?? null,
      audio_message: priority === 'none' ? null : formatMsg(DATA.audio[`${type}|${priority}`], { distance: h.distance }),
    },
  };
}

function verdictHtml(level, title, detail) {
  const vib = DATA.vibration[level];
  return `<div class="head"><span class="badge ${level}">${level}</span>${esc(title)}</div>
    <div class="action">${esc(detail)}${vib ? `・震動：${VIB_ZH[vib]}` : '・不震動，只記錄'}</div>`;
}

function setupLive() {
  const th = DATA.thresholds;
  const det = new GroundDropDetector(th.ground_drop);
  let lastDrop = { level: 'none', drop_cm: null, baseline: null, consecutive: 0, status: 'warmup' };
  let timer = null;
  let spikeLeft = 0;

  // 深度量尺：依門檻畫出紅／黃／綠區段
  const min = +$('depth-slider').min, max = +$('depth-slider').max;
  const pct = (v) => ((v - min) / (max - min)) * 100;
  const pc = pct(th.hanging.critical_m), pw = pct(th.hanging.warning_m);
  $('depth-scale').style.background = `linear-gradient(90deg, ${C.critical} 0 ${pc}%, ${C.warning} ${pc}% ${pw}%, ${C.good} ${pw}% 100%)`;
  $('depth-scale').innerHTML = [min, th.hanging.critical_m, th.hanging.warning_m, max]
    .map((v) => `<span style="left:${pct(v)}%">${v} m</span>`).join('');

  function renderFused() {
    const h = detectHanging(+$('depth-slider').value, th.hanging);
    $('fused-json').textContent = JSON.stringify(fuse(h, lastDrop), null, 2);
  }

  function renderDepth() {
    const d = +$('depth-slider').value;
    $('depth-value').textContent = `${d.toFixed(1)} m`;
    const h = detectHanging(d, th.hanging);
    const el = $('depth-verdict');
    el.className = `verdict ${h.level}`;
    const desc = h.level === 'critical' ? `距離 < ${th.hanging.critical_m} m`
      : h.level === 'warning' ? `${th.hanging.critical_m} m ≤ 距離 ≤ ${th.hanging.warning_m} m` : `距離 > ${th.hanging.warning_m} m`;
    el.innerHTML = verdictHtml(h.level, `${LEVEL_ZH[h.level]}（${desc}）`,
      h.level === 'none' ? '前方上半身高度無障礙物' : formatMsg(DATA.audio[`hanging_obstacle|${h.level}`], { distance: d }));
    renderFused();
  }

  function renderTof() {
    const g = th.ground_drop;
    $('tof-value').textContent = `${$('tof-slider').value} cm`;
    $('tof-window').innerHTML = det.window.map((v) => `<span class="chip">${v.toFixed(0)}</span>`).join('')
      + det.held.map((v) => `<span class="chip held" title="異常讀數，不放進基準視窗">${v.toFixed(0)}</span>`).join('')
      || '<span class="muted small">尚無讀數，按「自動送出」或「送出一筆」</span>';
    const dots = Array.from({ length: g.confirm_count }, (_, i) => `<i class="${i < Math.min(lastDrop.consecutive, g.confirm_count) ? 'on' : ''}"></i>`).join('');
    $('tof-stats').innerHTML = `
      <div class="stat"><div class="label">地面基準</div><div class="value">${fmt(lastDrop.baseline, 1)} cm</div></div>
      <div class="stat"><div class="label">落差</div><div class="value">${fmt(lastDrop.drop_cm, 1)} cm</div></div>
      <div class="stat"><div class="label">連續異常</div><div class="value"><span class="confirm-dots">${dots}</span> ${lastDrop.consecutive}</div></div>`;
    const statusZh = {
      warmup: `建立基準中（需 ${g.min_baseline_samples} 筆）`, normal: '正常',
      pending: `超過門檻，確認中（${lastDrop.consecutive}/${g.confirm_count}）`, confirmed: '已確認落差',
      rebaseline: '異常持續過久，視為地形改變並重建基準',
    }[lastDrop.status];
    const el = $('tof-verdict');
    el.className = `verdict ${lastDrop.level}`;
    el.innerHTML = verdictHtml(lastDrop.level, `${LEVEL_ZH[lastDrop.level]}・${statusZh}`,
      lastDrop.level === 'none' ? `落差 ≥ ${g.warning_cm} cm 且連續 ${g.confirm_count} 筆才觸發` : DATA.audio[`ground_drop|${lastDrop.level}`]);
    renderFused();
  }

  function feed() {
    let v = +$('tof-slider').value + (Math.random() - 0.5) * 3;  // 加一點感測雜訊
    if (spikeLeft > 0) { v += 55; spikeLeft -= 1; }
    lastDrop = det.update(v);
    renderTof();
  }

  function setAuto(on) {
    clearInterval(timer);
    timer = on ? setInterval(feed, 200) : null;
    $('tof-auto').classList.toggle('on', on);
    $('tof-auto').textContent = on ? '❚❚ 暫停' : '▶ 自動送出（5 Hz）';
  }

  $('depth-slider').addEventListener('input', renderDepth);
  $('tof-slider').addEventListener('input', renderTof);
  $('tof-auto').addEventListener('click', () => setAuto(!timer));
  $('tof-step').addEventListener('click', feed);
  $('tof-spike').addEventListener('click', () => {
    spikeLeft = th.ground_drop.confirm_count - 1;  // 剛好比確認次數少一筆 → 不應觸發
    if (!timer) setAuto(true);
  });
  $('tof-reset').addEventListener('click', () => {
    setAuto(false);
    det.reset();
    spikeLeft = 0;
    $('tof-slider').value = 100;
    lastDrop = { level: 'none', drop_cm: null, baseline: null, consecutive: 0, status: 'warmup' };
    renderTof();
  });

  renderDepth();
  renderTof();
}

// ---------- 啟動 ----------

(async function init() {
  DATA = await loadData();
  DATA.vibration ||= DEFAULTS.vibration;
  DATA.audio ||= DEFAULTS.audio;
  DATA.thresholds ||= DEFAULTS.thresholds;
  if (DATA.generated) $('generated').textContent = `資料產生時間 ${DATA.generated}`;

  const keys = SCENARIO_ORDER.filter((k) => DATA.scenarios?.[k]);
  if (!keys.length) {
    const msg = '<div class="empty">找不到模擬結果，請先在專案根目錄執行 <code>python main.py</code></div>';
    $('sim-body').innerHTML = msg;
    $('alerts-body').innerHTML = msg;
  } else if (!window.Chart) {
    $('sim-body').innerHTML = '<div class="empty">Chart.js 載入失敗（需要網路連線讀取 CDN）</div>';
  } else {
    current = keys.includes('mixed') ? 'mixed' : keys[0];
    renderTabs();
    $('tabs').addEventListener('click', (e) => {
      const btn = e.target.closest('.tab');
      if (btn) selectScenario(btn.dataset.key);
    });
    $('only-new').addEventListener('change', renderAlerts);
    renderScenario();
  }
  setupLive();
})();
