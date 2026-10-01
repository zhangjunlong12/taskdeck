/* TaskDeck 前端逻辑 */
'use strict';

const state = {
  tasks: [],
  runs: [],
  templates: [],
  selectedId: null,
  selectedRunId: null,
  keyword: '',
  status: '',
  editingId: null,
  currentTpl: null,
  deleteArmed: false,
  lastRunSig: '',
  settings: null,   // 全局设置（含全局钉钉机器人）
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ------------------------------ 工具 ------------------------------ */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function pad(n) { return String(n).padStart(2, '0'); }

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtShort(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDur(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${sec.toFixed(1)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m${pad(Math.round(sec % 60))}s`;
  return `${Math.floor(sec / 3600)}h${pad(Math.floor((sec % 3600) / 60))}m`;
}

const STATUS_CN = {
  success: '成功', failed: '失败', timeout: '超时', killed: '已终止', running: '运行中', '': '未运行',
};
const TRIGGER_CN = { schedule: '定时', manual: '手动', retry: '重试' };

// 客户端来源标识：窗口 ?src=app-window、托盘浏览器 ?src=browser，便于服务端诊断轮询
const CLIENT_SRC = new URLSearchParams(location.search).get('src') || 'direct';

async function api(path, options) {
  const res = await fetch(path, Object.assign({
    headers: { 'Content-Type': 'application/json', 'X-TaskDeck-Client': CLIENT_SRC },
  }, options || {}));
  let data;
  try { data = await res.json(); } catch (e) { data = { ok: false, error: '响应解析失败' }; }
  if (!data.ok && !res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, type) {
  type = type || 'info';
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 260);
  }, type === 'error' ? 4200 : 2400);
}

/* 前端轻量调度描述，与后端 describe_schedule 保持一致 */
function describeSchedule(type, expr) {
  if (type === 'interval') {
    const n = parseInt(expr, 10) || 0;
    if (n < 60) return `每 ${n} 秒`;
    if (n < 3600) return n % 60 === 0 ? `每 ${n / 60} 分钟` : `每 ${n} 秒`;
    if (n < 86400) return n === 3600 ? '每小时' : `每 ${(n / 3600).toFixed(n % 3600 === 0 ? 0 : 1)} 小时`;
    if (n === 86400) return '每天';
    return `每 ${(n / 86400).toFixed(n % 86400 === 0 ? 0 : 1)} 天`;
  }
  if (type === 'once') return `${String(expr).replace('T', ' ')} 执行一次`;
  if (type === 'cron') return cronDesc(expr);
  return expr || '—';
}

const WEEK = { 0: '周日', 1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', '*': '每天' };

function cronDesc(expr) {
  let p = String(expr || '').trim().split(/\s+/);
  if (p.length === 6) p = p.slice(1);
  if (p.length !== 5) return expr;
  let [mi, ho, dom, mon, dow] = p;
  const isNum = (v) => /^\d{1,2}$/.test(v);
  let hm = '';
  if (isNum(mi)) {
    hm = isNum(ho) ? `${pad(+ho)}:${pad(+mi)}` : (ho === '*' ? `每小时第 ${+mi} 分` : '');
  }
  if (dow !== '*') {
    const days = dow.split(',').map((d) => WEEK[d] || d).join('/');
    return hm ? `${days} ${hm}` : `${days}（${expr}）`;
  }
  if (dom !== '*') return hm ? `每月 ${dom} 日 ${hm}` : `每月 ${dom} 日`;
  if (mon !== '*') return hm ? `每年 ${mon} 月 ${hm}` : `每年 ${mon} 月`;
  if (hm) return hm.includes(':') ? `每天 ${hm}` : hm;
  if (/^\*\/\d+$/.test(mi)) return `每 ${mi.slice(2)} 分钟`;
  return expr;
}

/* ------------------------------ 统计 ------------------------------ */
async function loadStats() {
  try {
    const { item } = await api('/api/stats');
    $('#stats').innerHTML = `
      <div class="stat"><div class="stat-v">${item.total}</div><div class="stat-l">总任务</div></div>
      <div class="stat ok"><div class="stat-v">${item.enabled}</div><div class="stat-l">已启用</div></div>
      <div class="stat run"><div class="stat-v">${item.running}</div><div class="stat-l">运行中</div></div>
      <div class="stat"><div class="stat-v">${item.today_runs}</div><div class="stat-l">今日执行</div></div>
      <div class="stat ${item.today_failed ? 'bad' : ''}"><div class="stat-v">${item.today_failed}</div><div class="stat-l">今日失败</div></div>`;
  } catch (e) { /* 忽略 */ }
}

/* ------------------------------ 任务列表 ------------------------------ */
async function loadTasks() {
  const qs = new URLSearchParams({ keyword: state.keyword, status: state.status });
  try {
    const { items } = await api('/api/tasks?' + qs.toString());
    state.tasks = items;
    renderTasks();
  } catch (e) {
    toast('加载任务失败：' + e.message, 'error');
  }
}

function renderTasks() {
  const box = $('#taskList');
  if (!state.tasks.length) {
    box.innerHTML = `<div class="empty-list">${state.keyword || state.status ? '没有匹配的任务' : '还没有任务，点击「新建任务」或「模板库」开始'}</div>`;
    return;
  }
  box.innerHTML = state.tasks.map((t) => {
    const dot = t.running ? 'running' : (t.last_status || '');
    return `
    <div class="task-card ${state.selectedId === t.id ? 'active' : ''} ${t.enabled ? '' : 'disabled'}" data-id="${t.id}">
      <div class="tc-top">
        <span class="tc-dot ${dot}"></span>
        <span class="tc-name" title="${esc(t.name)}">${esc(t.name)}</span>
      </div>
      <div class="tc-cmd" title="${esc(t.command)}">${esc(t.command)}</div>
      <div class="tc-foot">
        <span class="tc-sched" title="${esc(t.schedule_desc)}">${esc(t.schedule_desc)}</span>
        <span class="tc-next">${t.running ? '运行中…' : (t.enabled ? '下次 ' + (t.next_run ? t.next_run.slice(5) : '—') : '已停用')}</span>
        <span class="tc-actions">
          ${t.running
            ? `<button class="mini-btn stop" data-act="stop" data-id="${t.id}">终止</button>`
            : `<button class="mini-btn run" data-act="run" data-id="${t.id}">运行</button>`}
        </span>
      </div>
    </div>`;
  }).join('');
}

async function selectTask(id) {
  state.selectedId = id;
  state.selectedRunId = null;
  state.deleteArmed = false;
  $('#dDelete').textContent = '删除';
  renderTasks();
  renderDetail();
  await loadRuns();
}

/* ------------------------------ 详情 ------------------------------ */
function currentTask() {
  return state.tasks.find((t) => t.id === state.selectedId) || null;
}

function renderDetail() {
  const t = currentTask();
  if (!t) {
    $('#detailEmpty').hidden = false;
    $('#detailBody').hidden = true;
    return;
  }
  $('#detailEmpty').hidden = true;
  $('#detailBody').hidden = false;

  $('#dName').textContent = t.name;
  const st = t.running ? 'running' : (t.last_status || 'idle');
  const badge = $('#dStatus');
  badge.className = 'badge ' + (t.running ? 'running' : (t.last_status || 'idle'));
  badge.textContent = STATUS_CN[st] || st;

  $('#dMeta').innerHTML = `
    <span><span class="k">调度：</span>${esc(t.schedule_desc)}</span>
    <span><span class="k">下次运行：</span>${t.enabled ? (t.next_run || '—') : '已停用'}</span>
    <span><span class="k">上次运行：</span>${fmtTime(t.last_run_at)}</span>
    <span><span class="k">累计：</span>${t.run_count} 次 · 失败 ${t.fail_count} 次</span>
    <span><span class="k">命令：</span><code>${esc(t.command)}</code></span>
    ${t.cwd ? `<span><span class="k">目录：</span><code>${esc(t.cwd)}</code></span>` : ''}
    ${t.tags ? `<span><span class="k">标签：</span>${esc(t.tags)}</span>` : ''}`;

  $('#dEnabled').checked = !!t.enabled;
  $('#dRun').hidden = !!t.running;
  $('#dStop').hidden = !t.running;

  renderInfoTab();
  renderEnvTab();
  renderNotifyTab();
}

function renderInfoTab() {
  const t = currentTask();
  if (!t) return;
  const rows = [
    ['任务 ID', `<code>${esc(t.id)}</code>`],
    ['执行命令', `<code>${esc(t.command)}</code>`],
    ['工作目录', t.cwd ? `<code>${esc(t.cwd)}</code>` : '<span class="muted">项目根目录</span>'],
    ['执行方式', t.use_shell ? '通过 Shell' : '直接执行'],
    ['调度类型', `${{ interval: '固定间隔', cron: 'Cron', once: '单次' }[t.schedule_type] || t.schedule_type}`],
    ['调度表达式', `<code>${esc(t.schedule_expr)}</code>`],
    ['超时', t.timeout ? `${t.timeout} 秒` : '不限制'],
    ['失败重试', t.retries ? `${t.retries} 次，间隔 ${t.retry_delay} 秒` : '不重试'],
    ['状态', t.enabled ? '已启用' : '已停用'],
    ['创建时间', fmtTime(t.created_at)],
    ['更新时间', fmtTime(t.updated_at)],
    ['备注', t.note ? esc(t.note) : '<span class="muted">—</span>'],
  ];
  $('#infoGrid').innerHTML = rows
    .map(([k, v]) => `<div class="info-row"><div class="k">${k}</div><div class="v">${v}</div></div>`)
    .join('');
}

function renderEnvTab() {
  const t = currentTask();
  if (!t) return;
  const envs = Object.entries(t.env || {});
  $('#envView').innerHTML = envs.length
    ? envs.map(([k, v]) => `<div class="env-row"><div class="ek">${esc(k)}</div><div class="ev">${esc(v)}</div></div>`).join('')
    : '<div class="empty-list">未设置环境变量</div>';
}

function renderNotifyTab() {
  const t = currentTask();
  if (!t) return;
  const n = t.notify || {};
  const g = (state.settings && state.settings.dingtalk) || {};
  const globalActive = g.enabled && (g.webhook || '').trim();
  const ownActive = n.type === 'dingtalk' && (n.webhook || '').trim() && n.on !== 'never';
  const source = ownActive ? '任务单独配置' : (globalActive ? '全局机器人（未单独配置时使用）' : '—');
  const on = { never: '不通知', failure: '仅在失败时通知', always: '总是通知' }[n.on || 'never'];
  const enabled = (n.on && n.on !== 'never' && n.webhook);
  $('#notifyView').innerHTML = `
    <div class="info-row"><div class="k">通知方式</div><div class="v">${n.type === 'dingtalk' ? '钉钉机器人' : (globalActive ? '全局钉钉机器人' : '未配置')}</div></div>
    <div class="info-row"><div class="k">通知来源</div><div class="v">${source}</div></div>
    <div class="info-row"><div class="k">触发时机</div><div class="v">${ownActive ? on : (globalActive ? ({ always: '总是通知', failure: '仅在失败时通知' }[g.on] || '总是通知') : '—')}</div></div>
    <div class="info-row"><div class="k">Webhook</div><div class="v"><code>${esc(ownActive ? (n.webhook || '—') : (globalActive ? g.webhook : '—'))}</code></div></div>
    <div class="info-row"><div class="k">加签密钥</div><div class="v"><code>${(ownActive ? n.secret : g.secret) ? '已设置（' + (ownActive ? n.secret : g.secret).slice(0, 6) + '…）' : '—'}</code></div></div>
    <div class="info-row"><div class="k">@ 所有人</div><div class="v">${(ownActive ? n.at_all : g.at_all) ? '是' : '否'}</div></div>
    <div style="margin-top:14px; display:flex; gap:8px;">
      <button class="btn tiny ghost" id="btnNotifyTest2" ${ownActive ? '' : 'disabled'}>发送测试消息</button>
      ${!ownActive && globalActive ? '<span class="hint">右上角「设置」里可测试全局机器人</span>' : ''}
    </div>`;
  const btn = $('#btnNotifyTest2');
  if (btn) btn.onclick = () => testNotify(n.webhook, n.secret);
}

async function testNotify(webhook, secret) {
  if (!webhook) { toast('请先填写 Webhook', 'error'); return; }
  try {
    const r = await api('/api/notify/test', {
      method: 'POST', body: JSON.stringify({ webhook, secret }),
    });
    toast(r.ok ? '测试消息已发送，请查看钉钉群' : '发送失败：' + r.error, r.ok ? 'success' : 'error');
  } catch (e) { toast('发送失败：' + e.message, 'error'); }
}

/* ------------------------------ 运行日志 ------------------------------ */
async function loadRuns() {
  if (!state.selectedId) return;
  try {
    const { items } = await api(`/api/runs?task_id=${state.selectedId}&limit=80`);
    state.runs = items;
    if (!state.selectedRunId && items.length) state.selectedRunId = items[0].id;
    renderRuns();
    await renderOutput();
  } catch (e) { /* 忽略 */ }
}

function renderRuns() {
  const box = $('#runsList');
  if (!state.runs.length) {
    box.innerHTML = '<div class="empty-list">暂无运行记录</div>';
    return;
  }
  box.innerHTML = state.runs.map((r) => `
    <div class="run-item ${state.selectedRunId === r.id ? 'active' : ''}" data-run="${r.id}">
      <div class="ri-top">
        <span class="ri-dot ${r.status}"></span>
        <span class="ri-time">${fmtShort(r.start_at)}</span>
        <span class="ri-tag">${TRIGGER_CN[r.trigger] || r.trigger}${r.attempt > 1 ? ' #' + r.attempt : ''}</span>
        <span style="margin-left:auto">${STATUS_CN[r.status] || r.status}</span>
      </div>
      <div class="ri-sub">耗时 ${fmtDur(r.duration)}${r.exit_code != null ? ' · 退出码 ' + r.exit_code : ''}</div>
    </div>`).join('');
}

async function renderOutput() {
  const run = state.runs.find((r) => r.id === state.selectedRunId);
  const body = $('#outBody');
  if (!run) {
    $('#outTitle').textContent = '输出';
    body.innerHTML = '选择左侧一条运行记录查看输出';
    return;
  }
  $('#outTitle').textContent =
    `${fmtTime(run.start_at)} · ${STATUS_CN[run.status] || run.status} · 耗时 ${fmtDur(run.duration)}`;

  let html;
  if (run.status === 'running') {
    const live = run.live_output || '(等待输出…)';
    html = esc(live).split('\n')
      .map((line) => line.startsWith('[err] ')
        ? `<span class="err">${line}</span>`
        : line).join('\n');
  } else {
    const out = esc(run.output || '');
    const err = esc(run.error || '');
    html = (out ? out : '') +
      (err ? (out ? '\n' : '') + `<span class="err">${err}</span>` : '') +
      (!out && !err ? '(无输出)' : '');
  }

  // 内容没变就不重绘，避免每轮轮询打断用户的文本选中
  if (body.dataset.sig !== html) {
    const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 24;
    body.dataset.sig = html;
    body.innerHTML = html;
    if (atBottom) body.scrollTop = body.scrollHeight;
  }

  // 运行已结束但库里还没落定（end_at 为空），补拉一次拿最终输出
  if (run.status !== 'running' && run.end_at == null) {
    try {
      const { item } = await api('/api/runs/' + run.id);
      const idx = state.runs.findIndex((r) => r.id === run.id);
      if (idx >= 0) { state.runs[idx] = item; renderRuns(); }
    } catch (e) { /* 忽略 */ }
  }
}

/** 触发运行，并立刻选中本次运行记录以实时展示输出。 */
async function triggerRun(id) {
  const res = await api(`/api/tasks/${id}/run`, { method: 'POST' });
  toast('已触发运行', 'success');
  if (state.selectedId !== id) { state.selectedId = id; renderDetail(); }
  if (res.run_id) state.selectedRunId = res.run_id;
  await loadRuns();
  renderRuns();
  await renderOutput();
}

/* ------------------------------ 轮询 ------------------------------ */
async function tick() {
  await loadStats();
  await loadTasks();
  try { state.settings = (await api('/api/settings')).item; } catch (e) { /* 忽略 */ }
  if (state.selectedId) {
    const t = currentTask();
    if (t) renderDetail();
    await loadRuns();
    // 运行中与刚结束都要刷新输出区：结束后需要把实时缓冲换成落库的最终结果
    if (state.selectedRunId) await renderOutput();
  }
}

/* ------------------------------ 全局设置 ------------------------------ */
async function openSettings() {
  try { state.settings = (await api('/api/settings')).item; } catch (e) { /* 忽略 */ }
  const g = (state.settings && state.settings.dingtalk) || {};
  $('#sDingEnabled').checked = !!g.enabled;
  $('#sWebhook').value = g.webhook || '';
  $('#sSecret').value = g.secret || '';
  $('#sOn').value = g.on || 'always';
  $('#sAtAll').checked = !!g.at_all;
  $('#settingsModal').hidden = false;
}

async function saveSettings() {
  const payload = { dingtalk: {
    enabled: $('#sDingEnabled').checked,
    webhook: $('#sWebhook').value.trim(),
    secret: $('#sSecret').value.trim(),
    on: $('#sOn').value,
    at_all: $('#sAtAll').checked,
  } };
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
    toast('设置已保存');
    $('#settingsModal').hidden = true;
    await tick();
  } catch (e) { toast('保存失败：' + e.message, 'error'); }
}

async function testGlobalNotify() {
  const webhook = $('#sWebhook').value.trim();
  if (!webhook) { toast('请先填写 Webhook', 'error'); return; }
  try {
    const r = await api('/api/settings/test', {
      method: 'POST', body: JSON.stringify({ webhook, secret: $('#sSecret').value.trim() }),
    });
    toast(r.ok ? '测试消息已发送，请查看钉钉群' : '发送失败：' + r.error, r.ok ? 'success' : 'error');
  } catch (e) { toast('发送失败：' + e.message, 'error'); }
}

/* ------------------------------ 任务表单 ------------------------------ */
function addEnvRow(key, value) {
  const row = document.createElement('div');
  row.className = 'env-input-row';
  row.innerHTML = `
    <input class="input mono" placeholder="KEY" value="${esc(key || '')}">
    <input class="input mono" placeholder="VALUE" value="${esc(value || '')}">
    <button class="btn tiny ghost" type="button" data-del>×</button>`;
  row.querySelector('[data-del]').onclick = () => row.remove();
  $('#envRows').appendChild(row);
}

function splitInterval(seconds) {
  const s = parseInt(seconds, 10) || 60;
  if (s % 86400 === 0) return [s / 86400, 86400];
  if (s % 3600 === 0) return [s / 3600, 3600];
  if (s % 60 === 0) return [s / 60, 60];
  return [s, 1];
}

function syncScheduleInputs() {
  const type = $('#fSchedType').value;
  $('#intervalWrap').hidden = type !== 'interval';
  $('#cronWrap').hidden = type !== 'cron';
  $('#onceWrap').hidden = type !== 'once';
  $('#cronHelper').hidden = type !== 'cron';
  updateSchedulePreview();
}

function currentScheduleExpr() {
  const type = $('#fSchedType').value;
  if (type === 'interval') {
    const n = Math.max(1, parseInt($('#fInterval').value, 10) || 1);
    return String(n * parseInt($('#fIntervalUnit').value, 10));
  }
  if (type === 'cron') return ($('#fCron').value || '').trim();
  return ($('#fOnce').value || '').trim();   // YYYY-MM-DDTHH:MM
}

function updateSchedulePreview() {
  const expr = currentScheduleExpr();
  const type = $('#fSchedType').value;
  if (!expr) { $('#schedPreview').textContent = '请填写调度表达式'; return; }
  $('#schedPreview').textContent = '执行节奏：' + describeSchedule(type, expr);
}

function fillTaskForm(t) {
  state.editingId = t ? t.id : null;
  $('#taskModalTitle').textContent = t ? '编辑任务' : '新建任务';
  $('#fName').value = t ? t.name : '';
  $('#fCommand').value = t ? t.command : '';
  $('#fCwd').value = t ? (t.cwd || '') : '';
  $('#fShell').value = t ? String(t.use_shell) : '1';
  $('#fTags').value = t ? (t.tags || '') : '';
  $('#fNote').value = t ? (t.note || '') : '';
  $('#fAfterSuccess').value = t ? (t.after_success_cmd || '') : '';

  $('#fSchedType').value = t ? t.schedule_type : 'interval';
  const expr = t ? t.schedule_expr : '3600';
  if (t && t.schedule_type === 'interval') {
    const [v, u] = splitInterval(expr);
    $('#fInterval').value = v;
    $('#fIntervalUnit').value = String(u);
  } else {
    $('#fInterval').value = 60;
    $('#fIntervalUnit').value = '60';
  }
  $('#fCron').value = (t && t.schedule_type === 'cron') ? expr : '0 9 * * *';
  $('#fOnce').value = (t && t.schedule_type === 'once')
    ? String(expr).replace('T', ' ').slice(0, 16).replace(' ', 'T') : '';

  $('#fTimeout').value = t ? t.timeout : 300;
  $('#fRetries').value = t ? t.retries : 0;
  $('#fRetryDelay').value = t ? t.retry_delay : 30;
  $('#fEnabled').checked = t ? !!t.enabled : true;

  $('#envRows').innerHTML = '';
  const envs = (t && t.env) ? t.env : {};
  const keys = Object.keys(envs);
  if (keys.length) keys.forEach((k) => addEnvRow(k, envs[k]));
  else addEnvRow('', '');

  const n = (t && t.notify) ? t.notify : {};
  $('#fNotifyOn').value = n.on || 'never';
  $('#fWebhook').value = n.webhook || '';
  $('#fSecret').value = n.secret || '';
  $('#fAtAll').value = n.at_all ? '1' : '0';

  syncScheduleInputs();
}

function collectTaskForm() {
  const env = {};
  $$('#envRows .env-input-row').forEach((row) => {
    const [k, v] = Array.from(row.querySelectorAll('input')).map((i) => i.value.trim());
    if (k) env[k] = v;
  });
  const notifyOn = $('#fNotifyOn').value;
  // 下拉框选择始终持久化；webhook 可留空（运行时回退全局机器人）。
  // 注意：不能因为 webhook 为空就把 notify 丢成 {}，否则「总是通知/不通知」的
  // 选择保存后会被静默还原（表现为“保存不起”）。
  const notify = (notifyOn === 'never')
    ? { type: 'dingtalk', on: 'never' }
    : { type: 'dingtalk', on: notifyOn, webhook: $('#fWebhook').value.trim(),
        secret: $('#fSecret').value.trim(), at_all: $('#fAtAll').value === '1' };
  return {
    name: $('#fName').value.trim(),
    command: $('#fCommand').value.trim(),
    cwd: $('#fCwd').value.trim(),
    use_shell: parseInt($('#fShell').value, 10),
    tags: $('#fTags').value.trim(),
    note: $('#fNote').value.trim(),
    after_success_cmd: $('#fAfterSuccess').value.trim(),
    schedule_type: $('#fSchedType').value,
    schedule_expr: currentScheduleExpr(),
    timeout: parseInt($('#fTimeout').value, 10) || 0,
    retries: parseInt($('#fRetries').value, 10) || 0,
    retry_delay: parseInt($('#fRetryDelay').value, 10) || 0,
    enabled: $('#fEnabled').checked ? 1 : 0,
    env, notify,
  };
}

async function saveTask() {
  const data = collectTaskForm();
  if (!data.command) { toast('请填写执行命令', 'error'); return; }
  if (!data.name) data.name = data.command.slice(0, 40);
  if (!data.schedule_expr) { toast('请填写调度配置', 'error'); return; }
  try {
    if (state.editingId) {
      await api('/api/tasks/' + state.editingId, { method: 'PUT', body: JSON.stringify(data) });
      toast('任务已更新', 'success');
    } else {
      await api('/api/tasks', { method: 'POST', body: JSON.stringify(data) });
      toast('任务已创建', 'success');
    }
    $('#taskModal').hidden = true;
    await loadTasks();
    await loadStats();
    if (state.selectedId) renderDetail();
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
  }
}

/* ------------------------------ 模板 ------------------------------ */
async function openTemplates() {
  $('#tplModal').hidden = false;
  $('#tplForm').hidden = true;
  $('#tplFoot').hidden = true;
  $('#tplList').hidden = false;
  try {
    const { items } = await api('/api/templates');
    state.templates = items;
    $('#tplList').innerHTML = items.map((t) => `
      <div class="tpl-card" data-tpl="${t.id}">
        <div class="tpl-card-title">${t.icon || '📦'} ${esc(t.name)}</div>
        <div class="tpl-card-desc">${esc(t.desc)}</div>
        <div class="tpl-card-meta">
          <span>默认调度：${esc(describeSchedule(t.schedule_type, t.schedule_expr))}</span>
          <span>需要填写 ${t.field_count} 项配置</span>
        </div>
      </div>`).join('');
  } catch (e) {
    toast('加载模板失败：' + e.message, 'error');
  }
}

async function openTemplateForm(id) {
  try {
    const { item } = await api('/api/templates/' + id);
    state.currentTpl = item;
    $('#tplList').hidden = true;
    $('#tplForm').hidden = false;
    $('#tplFoot').hidden = false;
    $('#tplName').textContent = `${item.icon || '📦'} ${item.name}`;
    $('#tplTaskName').value = item.name;
    $('#tplSchedType').value = item.schedule_type;
    if (item.schedule_type === 'interval') {
      const [v, u] = splitInterval(item.schedule_expr);
      $('#tplInterval').value = v; $('#tplIntervalUnit').value = String(u);
    } else if (item.schedule_type === 'cron') {
      $('#tplCron').value = item.schedule_expr;
    }
    syncTplSchedule();

    const saved = {};
    $('#tplFields').innerHTML = (item.fields || []).map((f) => `
      <div class="tpl-field">
        <label>${esc(f.label)}${f.required ? ' <span class="req">*</span>' : ''}</label>
        <input class="input mono" data-fk="${esc(f.key)}" placeholder="${esc(f.placeholder || '')}" value="${esc(saved[f.key] || '')}">
        ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}
      </div>`).join('');
    updateTplCommand();
  } catch (e) {
    toast('加载模板失败：' + e.message, 'error');
  }
}

function updateTplCommand() {
  const tpl = state.currentTpl;
  if (!tpl) return;
  fetch('/api/system/info').then((r) => r.json()).then(({ item }) => {
    $('#tplCommand').textContent = `"${item.python}" "${item.scripts_dir}\\${tpl.script}"`;
  }).catch(() => {
    $('#tplCommand').textContent = `python scripts/${tpl.script}`;
  });
}

function syncTplSchedule() {
  const type = $('#tplSchedType').value;
  $('#tplIntervalWrap').hidden = type !== 'interval';
  $('#tplCronWrap').hidden = type !== 'cron';
  $('#tplOnceWrap').hidden = type !== 'once';
}

function tplScheduleExpr() {
  const type = $('#tplSchedType').value;
  if (type === 'interval') {
    const n = Math.max(1, parseInt($('#tplInterval').value, 10) || 1);
    return String(n * parseInt($('#tplIntervalUnit').value, 10));
  }
  if (type === 'cron') return ($('#tplCron').value || '').trim();
  return ($('#tplOnce').value || '').trim();
}

async function createFromTemplate() {
  const tpl = state.currentTpl;
  if (!tpl) return;
  const env = {};
  $$('#tplFields input[data-fk]').forEach((i) => {
    const v = i.value.trim();
    if (v) env[i.dataset.fk] = v;
  });
  const payload = {
    name: $('#tplTaskName').value.trim() || tpl.name,
    env,
    schedule_type: $('#tplSchedType').value,
    schedule_expr: tplScheduleExpr(),
    timeout: tpl.timeout || 300,
    retries: 1,
    retry_delay: 60,
  };
  try {
    await api(`/api/templates/${tpl.id}/create`, { method: 'POST', body: JSON.stringify(payload) });
    toast('任务已创建，可在右侧立即运行测试', 'success');
    $('#tplModal').hidden = true;
    await loadTasks();
    await loadStats();
  } catch (e) {
    toast('创建失败：' + (e.message || ''), 'error');
  }
}

/* ------------------------------ 事件绑定 ------------------------------ */
function bindEvents() {
  // 顶栏
  $('#btnNew').onclick = () => { fillTaskForm(null); $('#taskModal').hidden = false; };
  $('#btnSettings').onclick = openSettings;
  $('#btnSettingsSave').onclick = saveSettings;
  $('#btnSettingsTest').onclick = testGlobalNotify;
  $('#btnTemplates').onclick = openTemplates;
  $('#btnRefresh').onclick = async () => { await tick(); toast('已刷新'); };

  // 搜索与筛选
  let searchTimer = null;
  $('#search').oninput = (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.keyword = e.target.value.trim();
      loadTasks();
    }, 250);
  };
  $$('#filters .chip').forEach((chip) => {
    chip.onclick = () => {
      $$('#filters .chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.status = chip.dataset.status;
      loadTasks();
    };
  });

  // 任务列表（事件委托）
  $('#taskList').onclick = async (e) => {
    const btn = e.target.closest('[data-act]');
    if (btn) {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === 'run') {
        try {
          await triggerRun(id);
        } catch (err) { toast('运行失败：' + err.message, 'error'); }
      } else {
        await api(`/api/tasks/${id}/stop`, { method: 'POST' });
        toast('已发送终止信号');
        await tick();
      }
      return;
    }
    const card = e.target.closest('.task-card');
    if (card) selectTask(card.dataset.id);
  };

  // 详情操作
  $('#dEnabled').onchange = async (e) => {
    const t = currentTask();
    if (!t) return;
    await api(`/api/tasks/${t.id}/enable`, {
      method: 'POST', body: JSON.stringify({ enabled: e.target.checked }),
    });
    toast(e.target.checked ? '任务已启用' : '任务已停用', 'success');
    await loadTasks();
    renderDetail();
  };
  $('#dRun').onclick = async () => {
    const t = currentTask();
    if (!t) return;
    try {
      await api(`/api/tasks/${t.id}/run`, { method: 'POST' });
      toast('已触发运行', 'success');
      await tick();
    } catch (e) { toast('运行失败：' + e.message, 'error'); }
  };
  $('#dStop').onclick = async () => {
    const t = currentTask();
    if (!t) return;
    await api(`/api/tasks/${t.id}/stop`, { method: 'POST' });
    toast('已终止任务');
    await tick();
  };
  $('#dEdit').onclick = () => {
    const t = currentTask();
    if (!t) return;
    fillTaskForm(t);
    $('#taskModal').hidden = false;
  };
  $('#dDelete').onclick = async () => {
    const t = currentTask();
    if (!t) return;
    if (!state.deleteArmed) {
      state.deleteArmed = true;
      $('#dDelete').textContent = '再点一次确认删除';
      setTimeout(() => {
        state.deleteArmed = false;
        $('#dDelete').textContent = '删除';
      }, 3500);
      return;
    }
    await api(`/api/tasks/${t.id}`, { method: 'DELETE' });
    state.selectedId = null;
    state.deleteArmed = false;
    $('#dDelete').textContent = '删除';
    toast('任务已删除', 'success');
    await loadTasks();
    await loadStats();
    renderDetail();
  };

  // Tab 切换
  $$('#tabs .tab').forEach((tab) => {
    tab.onclick = () => {
      $$('#tabs .tab').forEach((x) => x.classList.remove('active'));
      $$('.tab-panel').forEach((x) => x.classList.remove('active'));
      tab.classList.add('active');
      $(`.tab-panel[data-panel="${tab.dataset.tab}"]`).classList.add('active');
    };
  });

  // 运行记录
  $('#runsList').onclick = (e) => {
    const item = e.target.closest('[data-run]');
    if (!item) return;
    state.selectedRunId = item.dataset.run;
    renderRuns();
    renderOutput();
  };
  $('#btnCopy').onclick = () => {
    const text = $('#outBody').innerText;
    navigator.clipboard.writeText(text).then(
      () => toast('已复制到剪贴板', 'success'),
      () => toast('复制失败', 'error'));
  };
  $('#btnClearRuns').onclick = async () => {
    const t = currentTask();
    if (!t) return;
    if ($('#btnClearRuns').dataset.armed === '1') {
      await api(`/api/runs?task_id=${t.id}`, { method: 'DELETE' });
      state.selectedRunId = null;
      $('#btnClearRuns').dataset.armed = '0';
      $('#btnClearRuns').textContent = '清空历史';
      toast('历史记录已清空', 'success');
      await loadRuns();
    } else {
      $('#btnClearRuns').dataset.armed = '1';
      $('#btnClearRuns').textContent = '确认清空？';
      setTimeout(() => {
        $('#btnClearRuns').dataset.armed = '0';
        $('#btnClearRuns').textContent = '清空历史';
      }, 3500);
    }
  };

  // 任务弹窗
  $('#btnSaveTask').onclick = saveTask;
  $('#btnAddEnv').onclick = () => addEnvRow('', '');
  $('#fSchedType').onchange = syncScheduleInputs;
  ['#fInterval', '#fIntervalUnit', '#fCron', '#fOnce'].forEach((s) => {
    const el = $(s);
    if (el) el.addEventListener('input', updateSchedulePreview);
  });
  $$('.cron-chip').forEach((chip) => {
    chip.onclick = () => { $('#fCron').value = chip.dataset.cron; updateSchedulePreview(); };
  });
  $('#btnTestNotify').onclick = () => testNotify($('#fWebhook').value.trim(), $('#fSecret').value.trim());

  // 模板弹窗
  $('#tplList').onclick = (e) => {
    const card = e.target.closest('[data-tpl]');
    if (card) openTemplateForm(card.dataset.tpl);
  };
  $('#btnBackTpl').onclick = () => {
    $('#tplForm').hidden = true;
    $('#tplFoot').hidden = true;
    $('#tplList').hidden = false;
  };
  $('#tplSchedType').onchange = syncTplSchedule;
  $('#btnCreateTpl').onclick = createFromTemplate;

  // 弹窗关闭
  $$('[data-close]').forEach((el) => {
    el.onclick = () => {
      const modal = el.closest('.modal');
      if (modal) modal.hidden = true;
    };
  });
  // F5 / Ctrl+R 刷新页面（WebView2 长时间运行偶发渲染僵死时的恢复手段）
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F5' || (e.ctrlKey && (e.key === 'r' || e.key === 'R'))) {
      e.preventDefault();
      location.reload();
    }
  });
  // 暴露给托盘菜单刷新调用
  window.__taskdeck_reload = () => location.reload();
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $$('.modal').forEach((m) => { m.hidden = true; });
  });
}

/* ------------------------------ 启动 ------------------------------ */
async function boot() {
  bindEvents();
  await tick();
  setInterval(tick, 2500);
}

document.addEventListener('DOMContentLoaded', boot);
