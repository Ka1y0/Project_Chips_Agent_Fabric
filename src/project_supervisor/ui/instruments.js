import { $, list, node, svg, time } from './dom.js';

export function duration(seconds) {
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(part => String(part).padStart(2, '0')).join(':');
}

export function drawTrace(ticks) {
  const chart = $('heartbeat-trace');
  const series = $('heartbeat-marks');
  const visibleTicks = list(ticks).slice(0, 48).reverse();
  series.replaceChildren();
  const label = tick => Number.isSafeInteger(tick?.sequence) ? '#' + tick.sequence : '—';
  $('tick-start').textContent = label(visibleTicks[0]);
  $('tick-end').textContent = label(visibleTicks.at(-1));
  if (!visibleTicks.length) {
    chart.setAttribute('aria-label', ticks ? '暂无心跳记录' : '心跳投影不可用');
    return;
  }
  series.append(svg('path', { d: 'M0 46H600', class: 'trace-base' }));
  for (const [index, tick] of visibleTicks.entries()) {
    const x = (index + 0.5) * 600 / visibleTicks.length;
    const group = svg('g');
    if (tick.result === 'WOKE') {
      const path = `M${x - 6} 46l2 -4 1.5 7 2.5 -42 2 44 2 -8 2 3`;
      group.append(svg('path', { d: path, class: 'afterglow' }), svg('path', { d: path, class: 'woke-mark' }));
    } else if (tick.result === 'NOOP') {
      group.append(svg('rect', { x: x - 1.3, y: 44.7, width: 2.6, height: 2.6, class: 'noop-mark' }));
    } else continue;
    const title = svg('title');
    title.textContent = `${label(tick)} / ${tick.result} / ${time(tick.evaluated_at)} / ${tick.reason}`;
    group.append(title);
    series.append(group);
  }
  chart.setAttribute('aria-label', `最近 ${visibleTicks.length} 次巡检，${visibleTicks.filter(t => t.result === 'WOKE').length} 次唤醒 (UPLOAD)；横轴按账本序号排列`);
}

const states = { available: '可用', warning: '预警', critical: '临界', exhausted: '耗尽', unknown: '未知' };

function renderQuota(snapshots) {
  // Count the newest sample per pool within the supplied projection window.
  const pools = new Map();
  for (const snapshot of list(snapshots)) {
    const previous = pools.get(snapshot.quotaPoolID);
    if (!previous || Date.parse(snapshot.observedAt) > Date.parse(previous.observedAt)) {
      pools.set(snapshot.quotaPoolID, snapshot);
    }
  }
  const counts = { available: 0, warning: 0, critical: 0, exhausted: 0, unknown: 0 };
  const rows = [];
  const now = Date.now();
  let nextExpiry = Infinity;
  for (const item of pools.values()) {
    const expiry = Date.parse(item.freshUntil);
    const fresh = item.freshness === 'fresh' && Date.parse(item.observedAt) <= now && now <= expiry;
    if (fresh) nextExpiry = Math.min(nextExpiry, expiry + 1);
    const state = fresh && Object.hasOwn(counts, item.quotaState) ? item.quotaState : 'unknown';
    counts[state]++;
    const used = item.used?.state === 'known' ? `${item.used.value} ${item.used.unit}` : '用量未报告';
    const stale = item.freshness === 'stale' || expiry < now;
    const freshness = fresh ? '' : stale ? '已过期 · ' : '时效未知 · ';
    const row = node('div', '', 'quota-row');
    row.append(node('span', item.provider), node('span', `${freshness}${states[state]} · ${used}`));
    row.title = `${item.quotaPoolID} / ${time(item.observedAt)} / ${item.quotaState}`;
    rows.push(row);
  }
  $('quota-matrix').replaceChildren(...Object.entries(states).map(([state, name]) => {
    const cell = node('div');
    if (counts[state] && ['critical', 'exhausted'].includes(state)) cell.className = 'alert';
    cell.append(node('dt', name), node('dd', pools.size ? String(counts[state]) : '—'));
    return cell;
  }));
  if (!rows.length) rows.push(node('p', Array.isArray(snapshots) ? '内存配额未上报 · 保守锁定' : '配额投影不可用'));
  $('quota-summary').replaceChildren(...rows);
  return nextExpiry;
}

function enabledPolicies(budget) {
  return list(budget?.policies).map(host => host.policy).filter(policy => policy?.enabled === true);
}

export function renderBudget(budget, snapshots) {
  const policies = enabledPolicies(budget);
  const caps = new Set(policies.map(p => p.exploration_budget_cap));
  const cap = caps.size === 1 ? [...caps][0] : null;
  const used = budget?.usedToday;
  const knownCap = Number.isSafeInteger(cap) && cap >= 0;
  const knownUsed = Number.isSafeInteger(used) && used >= 0;
  $('exploration-used').textContent = knownUsed ? String(used) : '—';
  $('exploration-cap').textContent = knownCap ? String(cap) : '—';
  const measured = knownUsed && knownCap && cap > 0;
  const meter = $('exploration-meter');
  meter.hidden = !measured;
  meter.parentElement.classList.toggle('unmeasured', !measured);
  if (measured) {
    meter.max = cap;
    meter.value = used;
    meter.setAttribute('aria-valuetext', `已预留 ${used} 次，每日上限 ${cap} 次`);
  }
  $('budget-fill').setAttribute('width', measured ? 300 * Math.min(1, used / cap) : 0);
  const brakes = list(budget?.humanBrakes);
  let status = '探索预算不可用';
  if (budget?.globalControl?.state === 'paused') status = '全局探索已暂停';
  else if (brakes.length) status = `人类制动生效 · ${brakes.length} 个 Goal`;
  else if (Array.isArray(budget?.policies) && !policies.length) status = '自发探索未启用';
  else if (caps.size > 1) status = '宿主限额不同 · 由各宿主裁决';
  else if (knownCap && knownUsed) status = used >= cap ? '每日硬性上限已达 · 探索抑制' : `UTC 账期 · ${budget.utcDay}`;
  $('exploration-status').textContent = status;
  return renderQuota(snapshots);
}

export function updateCooldown(budget, now) {
  const intervals = new Set(enabledPolicies(budget).map(policy => policy.cooldown_seconds));
  const limit = intervals.size === 1 ? [...intervals][0] : null;
  const hasScale = Number.isFinite(limit) && limit > 0;
  const last = budget?.proposals?.[0];
  const start = Date.parse(last?.created_at);
  const needle = $('cooldown-needle');
  let text = '未报告';
  needle.setAttribute('visibility', 'hidden');
  if (intervals.size > 1) text = '宿主策略不同';
  else if (hasScale && Array.isArray(budget?.proposals) && !last) text = '尚无预留';
  else if (hasScale && Number.isFinite(start) && start <= now) {
    const seconds = Math.max(0, (start + limit * 1000 - now) / 1000);
    text = seconds ? duration(Math.ceil(seconds)) : '已到期';
    needle.setAttribute('d', `M${1 + 298 * Math.min(1, seconds / limit)} 0v10`);
    needle.setAttribute('visibility', 'visible');
  }
  $('cooldown-reading').textContent = text;
  $('cooldown-range').textContent = hasScale ? `${limit} s` : '—';
}
