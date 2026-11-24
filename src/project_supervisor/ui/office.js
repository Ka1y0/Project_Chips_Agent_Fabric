import { BREATH_MS, mountPortrait } from './art.js';
import { $, list, node, svg, time } from './dom.js';
import { drawTrace, duration, renderBudget, updateCooldown } from './instruments.js';

const terminal = new Set(['terminated', 'stopped']);
const empty = (target, message) => $(target).replaceChildren(node('p', message, 'empty'));
const routes = {
  capabilities: '/v1/capabilities', goals: '/v1/goals', workers: '/v1/workers',
  hosts: '/v1/autonomy/hosts', leases: '/v1/autonomy/leases',
  heartbeat: '/v1/autonomy/heartbeat', memory: '/v1/memory',
  exploration: '/v1/exploration', bridge: '/v1/bridge',
  resources: '/v1/resources/snapshots?limit=100', events: '/v1/events?limit=100',
};
// All state below is a disposable projection. No local/session storage, cookies or client queue.
let bearer = '';
let revision = 0;
let data = {};
let healthy = false;
let refreshing = false;
let refreshController = null;
let socket = null;
let nextStreamAt = 0;
let streamTimer = null;
let pending = false;
let proposed = null;
let cursor = 0;
let quotaExpiresAt = Infinity;

async function request(path, { method = 'GET', body, signal } = {}) {
  const response = await fetch(path, {
    method, credentials: 'omit', cache: 'no-store', signal,
    headers: { ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw new Error(response.status === 401 || response.status === 403
    ? `授权未通过 (${response.status})` : `后端请求失败 (${response.status})`);
  const result = await response.json();
  if (result.apiVersion !== 'v1' || !Object.hasOwn(result, 'data')) throw new Error('投影格式无效');
  return result;
}
function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
  $('notice').classList.toggle('error', error);
}
function connection() {
  const live = healthy && socket?.readyState === WebSocket.OPEN;
  $('connection-state').textContent = healthy ? (live ? '神经链路 · 已接驳' : 'REST 轮询 · 已连接') : '未连接 / 投影不可用';
  $('connection-dot').classList.toggle('live', healthy);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  const epoch = revision;
  const controller = new AbortController();
  refreshController = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  $('refresh').disabled = true;
  try {
    const entries = Object.entries(routes);
    const results = await Promise.allSettled(entries.map(([, path]) => request(path, { signal: controller.signal })));
    if (epoch !== revision) return;
    const fresh = {};
    const errors = [];
    results.forEach((result, index) => {
      const key = entries[index][0];
      if (result.status === 'fulfilled') {
        fresh[key] = result.value.data;
        if (key === 'events') cursor = Math.max(cursor, result.value.meta?.highestSequence || 0);
      } else errors.push(`${key}: ${result.reason.message}`);
    });
    data = fresh;
    healthy = Boolean(data.capabilities && data.goals);
    notice(errors.length ? `部分投影不可用；控制按可用授权决定。${errors.join(' · ')}` : '', true);
    $('last-updated').textContent = `读取于 ${time(new Date().toISOString())}`;
    render();
    connectStream();
  } catch {
    if (epoch === revision) { healthy = false; notice('无法读取后端。请检查本地 API 与访问授权。', true); }
  } finally {
    clearTimeout(timeout);
    if (epoch === revision) {
      refreshing = false; $('refresh').disabled = false; connection(); controls();
    }
  }
}
function connectStream() {
  if (!healthy || socket || Date.now() < nextStreamAt) return;
  const url = new URL('/v1/stream', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('afterSequence', String(cursor));
  const stream = new WebSocket(url, 'chips-observe-v1');
  socket = stream;
  stream.onopen = () => {
    if (socket !== stream) { stream.close(); return; }
    stream.send(JSON.stringify({ type: 'authenticate', token: bearer }));
  };
  stream.onmessage = event => {
    if (socket !== stream) return;
    try {
      const frame = JSON.parse(event.data);
      if (frame.type === 'events') {
        for (const item of list(frame.events)) if (Number.isSafeInteger(item.sequence)) cursor = Math.max(cursor, item.sequence);
      }
      if (frame.type === 'events' || frame.type === 'snapshot') {
        clearTimeout(streamTimer);
        streamTimer = setTimeout(refresh, 250);
      }
      connection();
    } catch { stream.close(); }
  };
  stream.onerror = () => stream.close();
  stream.onclose = () => {
    if (socket !== stream) return;
    socket = null; nextStreamAt = Date.now() + 10000; connection();
  };
}
function hostLive(host) {
  const ttl = host.metadata?.leaseTTLSeconds;
  const age = Date.now() - Date.parse(host.heartbeatAt);
  return host.state === 'running' && Number.isFinite(ttl) && ttl > 0 && age >= -1000 && age <= ttl * 1000;
}
function renderOverview() {
  const hosts = list(data.hosts);
  $('host-count').textContent = data.hosts ? String(hosts.filter(hostLive).length) : '—';
  const goals = list(data.goals);
  $('goal-count').textContent = data.goals ? String(goals.filter(g => !terminal.has(g.state)).length) : '—';
  const ticks = list(data.heartbeat?.ticks);
  $('wake-count').textContent = data.heartbeat ? String(ticks.filter(t => t.result === 'WOKE').length) : '—';
  $('hosts').replaceChildren(...hosts.map(host => {
    const row = node('span', '', 'host-row');
    row.append(node('i', '', `dot${hostLive(host) ? ' live' : ''}`), node('span', `${host.hostID} / ${hostLive(host) ? '心跳有效' : host.state === 'running' ? '心跳过期或有效期未报告' : host.state}`));
    row.title = `最后心跳 ${time(host.heartbeatAt)}`;
    return row;
  }));
  if (!hosts.length) $('hosts').append(node('span', 'HOST / 未注册或不可用'));
  const workflow = goals.reduce((counts, goal) => {
    const key = goal.wakeState === 'sleeping' && !terminal.has(goal.state) ? `${goal.state}·sleeping` : goal.state;
    counts.set(key, (counts.get(key) || 0) + 1); return counts;
  }, new Map());
  $('hosts').append(node('span', `WORKFLOW / ${[...workflow].map(([key, count]) => `${key} ${count}`).join(' · ') || (data.goals ? '无 Goal' : '未报告')}`));
  drawTrace(data.heartbeat?.ticks);
  countdown();
}

function countdown() {
  const now = Date.now();
  if (now >= quotaExpiresAt) quotaExpiresAt = renderBudget(data.exploration, data.resources);
  updateCooldown(data.exploration, now);
  const ticks = data.heartbeat?.ticks;
  const tick = ticks?.[0];
  const host = list(data.hosts).find(h => hostLive(h) && (tick ? h.hostID === tick.host_id : h.activeGoalCount > 0));
  if (healthy && Array.isArray(ticks) && host && (!tick || tick.result === 'WOKE')) {
    const start = Date.parse(tick ? tick.evaluated_at : host.startedAt);
    const expiry = Date.parse(host.heartbeatAt) + host.metadata.leaseTTLSeconds * 1000;
    const end = host.activeGoalCount > 0 ? expiry : Math.min(expiry, start + BREATH_MS);
    if (start <= now && now < end) setPortraitMotion(start, end, tick?.result === 'WOKE' && now - start < BREATH_MS ? tick.sequence : null);
    else setPortraitMotion(0, 0);
  } else {
    // In particular, an observed NOOP always stops the grains, even on a busy host.
    setPortraitMotion(0, 0);
  }

  const sleepers = list(data.goals).filter(g => g.wakeState === 'sleeping' && !terminal.has(g.state));
  const due = sleepers.filter(g => !['softPaused', 'hardPaused'].includes(g.state) && Number.isFinite(Date.parse(g.nextWakeAt)))
    .sort((a, b) => Date.parse(a.nextWakeAt) - Date.parse(b.nextWakeAt));
  const goal = due[0];
  const reading = $('next-wake');
  if (!goal) {
    reading.textContent = !data.goals ? '未报告' : sleepers.length ? '等待条件 / 人类控制' : '未安排';
    reading.removeAttribute('title');
    return;
  }
  const seconds = Math.ceil((Date.parse(goal.nextWakeAt) - now) / 1000);
  reading.textContent = seconds > 0 ? duration(seconds) : '已到期 · 等待巡检裁决';
  reading.title = `${goal.id} / ${time(goal.nextWakeAt)}`;
}

const harnesses = new Map([
  ['codex', ['Codex', 'codex']], ['claude', ['Claude', 'claude']], ['claudeCode', ['Claude', 'claude']],
  ['grok', ['Grok', 'grok']], ['grokBuild', ['Grok', 'grok']], ['googleAGY', ['AGY', 'agy']],
  ['agy', ['AGY', 'agy']], ['antigravity', ['AGY', 'agy']],
]);
function workerCard(worker) {
  const [name, glyph] = harnesses.get(worker.harness) || [worker.harness || 'Worker', null];
  const state = worker.state || 'unknown';
  const active = state === 'running' || state === 'busy';
  const quota = worker.quota?.state || 'unknown';
  const card = node('article', '', active ? 'worker-card active' : 'worker-card');
  const head = node('div', '', 'worker-head');
  const sigil = svg('svg', { class: 'worker-sigil', viewBox: '0 0 64 64', 'aria-hidden': 'true' });
  if (glyph) sigil.append(svg('use', { href: `./sigils.svg#${glyph}` }));
  else sigil.append(svg('image', { href: './mark.svg', width: 64, height: 64 }));
  const identity = node('div');
  identity.append(node('h3', name), node('span', worker.id, 'mono'));
  head.append(sigil, identity);

  const badges = node('div', '', 'worker-badges');
  function badge(text, status) {
    const element = node('span', text, 'badge');
    if (['failed', 'exhausted', 'rateLimited', 'critical', 'warning'].includes(status)) element.classList.add('warning');
    if (['offline', 'unknown'].includes(status)) element.classList.add('quiet-state');
    if (['running', 'busy'].includes(status)) element.classList.add('active');
    return element;
  }
  badges.append(badge(state.toUpperCase(), state), badge(`QUOTA · ${quota.toUpperCase()}`, quota));

  const fields = node('dl', '', 'worker-data');
  const lease = list(data.leases).find(l => l.goalID === worker.currentGoalID && l.state === 'owned');
  const leaseText = lease ? `${lease.hostID} · g${lease.generation}${Date.parse(lease.expiresAt) <= Date.now() ? ' · 已过期' : ''}`
    : !data.leases ? '未报告' : worker.currentTaskID && !worker.currentGoalID ? '未报告 Goal 归属' : '无活动租约';
  for (const [label, value] of [
    ['MODEL', worker.model?.displayName || worker.model?.identifier || '未报告'],
    ['NODE', worker.nodeID || '未报告'], ['SEEN', time(worker.heartbeat?.lastSeenAt)],
    ['TASK', [worker.currentTaskID, worker.currentTaskTitle].filter(Boolean).join(' · ') || '无活动任务'],
    ['LEASE', leaseText],
  ]) {
    const row = node('div');
    row.append(node('dt', label), node('dd', value));
    fields.append(row);
  }
  card.append(head, badges, fields);
  return card;
}

function renderWorkers() {
  const filter = $('worker-filter').value;
  const workers = list(data.workers).filter(w => filter === 'all' || (filter === 'offline' ? ['offline','failed','unhealthy'].includes(w.state) : filter === 'running' ? ['running','busy'].includes(w.state) : w.state === filter));
  $('workers').replaceChildren(...workers.map(workerCard));
  if (!workers.length) empty('workers', data.workers ? '当前筛选下没有已注册的 Worker。' : 'Worker 投影不可用。');
}
function entry({ kind, at, content, goal, event, hash }) {
  const card = node('article');
  const heading = node('div', '', 'entry-heading'); heading.append(node('span', kind), node('time', time(at)));
  card.append(heading, node('p', content));
  if (goal || event) card.append(node('code', [goal && `GOAL ${goal}`, event && `EVENT ${event}`].filter(Boolean).join(' · ')));
  if (hash) card.append(node('code', `SHA-256 ${hash}`));
  return card;
}
function renderMemory() {
  const memory = list(data.memory?.entries);
  $('memory-feed').replaceChildren(...memory.map(m => entry({ kind: m.kind, at: m.created_at, content: m.content, goal: m.source_goal_id, event: m.source_event_id, hash: m.content_sha256 })));
  if (!memory.length) empty('memory-feed', data.memory ? '暂无长期记忆。已完结 Goal 的经校验结论会沉淀于此。' : '记忆投影不可用。');
  const reflections = list(data.memory?.reflections);
  $('reflection-feed').replaceChildren(...reflections.map(r => {
    let content = '摘要格式不可读';
    try {
      const summary = JSON.parse(r.summary_json);
      const ids = list(summary.memoryIDs);
      const excerpts = memory.filter(m => ids.includes(m.id)).map(m => m.content);
      content = `关联 ${ids.length} 条已校验记忆 · 仅供参考\n${excerpts.join('\n') || '关联条目不在当前投影窗口内。'}`;
    } catch { /* fail as unavailable */ }
    return entry({ kind: 'REFLECTION', at: r.created_at, content, goal: r.goal_id, event: r.source_event_id, hash: r.content_sha256 });
  }));
  if (!reflections.length) empty('reflection-feed', '暂无反思摘要。');
  const events = list(data.events);
  $('events-feed').replaceChildren(...events.map(e => entry({ kind: e.kind, at: e.createdAt || e.timestamp, content: e.message, goal: e.goalID, event: e.id || e.eventID })));
  if (!events.length) empty('events-feed', data.events ? '暂无事件。' : '事件投影不可用。');
}
function renderBridge() {
  const messages = list(data.bridge?.messages);
  $('bridge-feed').replaceChildren(...messages.map(m => {
    const card = node('article');
    const expired = !m.content?.expires_at || Date.parse(m.content.expires_at) <= Date.now();
    card.append(node('p', `${m.peer_node_id} / ${m.kind} / ${m.direction}${expired ? ' / 历史观测' : ''}`), node('p', time(m.observed_at)), node('code', `SHA-256 ${m.envelope_sha256}`));
    if (m.content?.kind === 'nodeStatus') card.append(node('p', `${m.content.state} · ${m.content.worker_count} Workers · 远程通告`));
    if (m.content?.kind === 'delegationProposal') card.append(node('p', `TASK ${m.content.task_id} · GREEN · 只读委派提案`));
    return card;
  }));
  if (!messages.length) empty('bridge-feed', data.bridge ? '尚无经认证的节点通告。Bridge 仅提供派生观测。' : 'Bridge 投影不可用。');
}
function controls() {
  const goal = list(data.goals).find(g => g.id === $('goal-select').value);
  const enabled = healthy && list(data.capabilities?.mutations).includes('goal.pause');
  $('authority-mode').textContent = enabled ? '接入仓已解锁' : '只读观测';
  $('control-policy').textContent = !enabled ? '后端只读或授权不可用。' : !bearer ? '请输入具备 goal:control 权限的令牌。' : '操作仍须服务端鉴权；所有控制均需确认。';
  $('goal-detail').textContent = goal ? `${goal.state} / ${goal.wakeState} / v${goal.version}\n${goal.id}` : '所有控制以 SQLite 回执为准。';
  const control = data.exploration?.globalControl;
  const known = ['open','paused'].includes(control?.state) && Number.isSafeInteger(control.revision);
  $('exploration-control-state').textContent = known ? `全局探索 · ${control.state === 'paused' ? '已暂停' : '开放'} · v${control.revision}` : '全局探索状态待确认';
  $('exploration-toggle').textContent = control?.state === 'paused' ? '开启黑墙通道' : '黑墙封锁（禁止自主漫游）';
  $('exploration-toggle').classList.toggle('danger-outline', control?.state !== 'paused');
  $('exploration-toggle').disabled = pending || !known || !healthy || !bearer || !list(data.capabilities?.mutations).includes('exploration.control');
  for (const button of document.querySelectorAll('[data-action]')) {
    const name = button.dataset.action;
    button.disabled = pending || !enabled || !bearer || !goal || terminal.has(goal.state)
      || (name === 'resume' && !['softPaused', 'hardPaused'].includes(goal.state));
  }
}
function render() {
  quotaExpiresAt = renderBudget(data.exploration, data.resources);
  renderOverview(); renderWorkers(); renderMemory(); renderBridge();
  const selected = $('goal-select').value;
  $('goal-select').replaceChildren(node('option', '选择 Goal'));
  $('goal-select').firstChild.value = '';
  for (const goal of list(data.goals)) {
    const option = node('option', `${goal.id} · ${goal.state}`); option.value = goal.id; $('goal-select').append(option);
  }
  if (list(data.goals).some(g => g.id === selected)) $('goal-select').value = selected;
  controls();
}
const descriptions = {
  soft: ['挂起队列 · SOFT PAUSE', '允许正在执行的工作完成，停止新的派发。'],
  hard: ['系统重置 · HARD PAUSE', '冻结 Goal，并请求取消可取消的在途任务。'],
  resume: ['重新接驳 · RESUME', '从后端保存的权威状态继续运行。'],
  steer: ['注入魔偶 · STEER', '记录人类指引并重新评估，保留已有有效工作。'],
  stop: ['强制断连 · STOP', '将此 Goal 标记为主动终止。此操作不能通过 RESUME 撤销。'],
};
for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click', () => {
  proposed = { action: button.dataset.action, goalID: $('goal-select').value };
  const [title, description] = descriptions[proposed.action];
  confirmControl(title, description, proposed.goalID, proposed.action === 'steer' ? '人类指引' : '操作原因', 10000);
});
$('exploration-toggle').addEventListener('click', () => {
  const control = data.exploration.globalControl;
  const state = control.state === 'paused' ? 'open' : 'paused';
  proposed = { action: 'exploration', state, expected_revision: control.revision };
  confirmControl($('exploration-toggle').textContent,
    state === 'paused' ? '暂停新提案和探索派发，并请求取消在途探索。' : '恢复后仍须满足活跃 Goal 制动、冷却和预算条件。',
    '当前数据库中的自发探索', '操作原因', 1000);
});
function confirmControl(title, description, target, label, maxLength) {
  const danger = ['hard', 'stop'].includes(proposed.action) || proposed.state === 'paused';
  $('control-dialog').classList.toggle('hazard', danger);
  $('confirm-title').textContent = `确认 ${title}`;
  $('confirm-description').textContent = description;
  $('confirm-goal').textContent = target;
  $('reason-label').textContent = label;
  $('reason').maxLength = maxLength;
  $('reason').value = '';
  $('control-dialog').showModal(); $('reason').focus();
}
$('control-form').addEventListener('submit', async event => {
  event.preventDefault();
  const reason = $('reason').value.trim();
  if (!reason || !proposed || pending) return;
  const { action, goalID, state, expected_revision } = proposed;
  const epoch = revision;
  const path = action === 'soft' || action === 'hard' ? 'pause' : action;
  const globalControl = action === 'exploration';
  const url = globalControl ? '/v1/exploration/control' : `/v1/goals/${encodeURIComponent(goalID)}/${path}`;
  const body = globalControl ? { state, expected_revision, reason } : path === 'pause' ? { mode: action, reason } : action === 'steer' ? { instruction: reason, preserveValidWork: true } : { reason };
  pending = true; controls(); $('control-dialog').close();
  $('receipt').classList.remove('error');
  $('receipt').textContent = '已提交，等待后端事务回执…';
  // No automatic mutation retries: an interrupted receipt can be reconciled with GET/events.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const result = await request(url, { method: 'POST', body, signal: controller.signal });
    if (epoch === revision) $('receipt').textContent = globalControl
      ? `全局探索已确认 / STATE ${result.data.state} · VERSION ${result.data.revision}`
      : `已确认 / ${result.data.id}\nSTATE ${result.data.state} · VERSION ${result.data.version} · CURSOR ${result.data.eventCursor}\n更新于 ${time(result.data.updatedAt)}`;
  } catch (error) {
    if (epoch === revision) {
      $('receipt').classList.add('error');
      $('receipt').textContent = `${error.name === 'AbortError' ? '未取得回执；操作结果未知' : error.message}。请刷新状态与事件确认；不会自动重试。`;
    }
  } finally {
    clearTimeout(timer); pending = false; proposed = null;
    if (epoch === revision) { controls(); await refresh(); }
  }
});
$('control-cancel').addEventListener('click', () => { proposed = null; $('control-dialog').close(); });
$('connect-open').addEventListener('click', () => { $('bearer').value = ''; $('connect-dialog').showModal(); $('bearer').focus(); });
$('connect-cancel').addEventListener('click', () => { $('bearer').value = ''; $('connect-dialog').close(); });
$('connect-dialog').addEventListener('close', () => { $('bearer').value = ''; });
$('connect-form').addEventListener('submit', event => {
  event.preventDefault();
  bearer = $('bearer').value.trim(); $('bearer').value = ''; $('connect-dialog').close();
  revision++; refreshController?.abort(); refreshing = false;
  const previous = socket; socket = null; previous?.close(); nextStreamAt = 0; cursor = 0;
  healthy = false; data = {}; $('receipt').textContent = ''; render(); refresh();
});
$('refresh').addEventListener('click', refresh);
$('worker-filter').addEventListener('change', renderWorkers);
$('goal-select').addEventListener('change', controls);
const tabs = [...document.querySelectorAll('[data-tab]')];
function selectTab(tab) {
  for (const button of tabs) {
    const selected = button === tab; button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1; $(`${button.dataset.tab}-feed`).hidden = !selected;
  }
}
for (const tab of tabs) {
  tab.addEventListener('click', () => selectTab(tab));
  tab.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[index]); tabs[index].focus();
  });
}
selectTab(tabs[0]);
function markSection() {
  const hash = location.hash || '#overview';
  for (const link of document.querySelectorAll('nav a')) {
    const selected = link.hash === hash;
    link.classList.toggle('selected', selected);
    if (selected) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
}
window.addEventListener('hashchange', markSection);
markSection();
window.addEventListener('pagehide', () => { bearer = ''; socket?.close(); refreshController?.abort(); });
window.addEventListener('pageshow', event => { if (event.persisted) { healthy = false; controls(); refresh(); } });
const setPortraitMotion = mountPortrait($('sculpture'));
render(); refresh();
setInterval(countdown, 1000);
setInterval(() => { if (!document.hidden) refresh(); }, 10000);
