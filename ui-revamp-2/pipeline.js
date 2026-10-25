// Judgment › Agent Workflow, on the agent pipeline (chaser_agent/pipeline_api.py, port 8773).
//
// Three columns - agreement agent, chaser agent, anomaly agent - fill in as the agents work.
// The time-lapse moves the demo calendar one day at a time; each day the agents wake on their own
// (no clicks), Claude is called only when there is a decision, and every step is clickable to see
// the reasoning summary, each tool call with its input and result, and refusals by the guardrails.
// Loaded after app.js and live.js; it replaces only the Judgment page body.

const AGENTS_API = window.ACCOUNTABLE_AGENTS ?? 'http://127.0.0.1:8773';
const P = { on: false, tried: false, demo: null, flow: null, activity: [], agreements: [],
  playing: false, busy: '', error: '', drawer: null, batons: new Set(), shown: new Set() };

async function papi(path, method = 'GET') {
  const r = await fetch(`${AGENTS_API}${path}`, { method, cache: 'no-store' });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${path}: ${r.status}`);
  return body;
}
const pwait = ms => new Promise(r => setTimeout(r, ms));
const pday = s => s ? new Date(s + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
const plabel = d => ({ send_reminder: 'Reminder', wait: 'Wait', escalate: 'Escalation',
  handoff_to_anomaly: 'Hand-off', record_verdict: 'Verdict' }[d] || d || '—');
const pverdict = v => v ? v.charAt(0).toUpperCase() + v.slice(1).replace('_', ' ') : '';
const pjson = v => esc(JSON.stringify(v, null, 2));

async function refresh() {
  try {
    P.demo = await papi('/pipeline/demo');
    const [flow, activity, agreements] = await Promise.all([
      papi(`/pipeline/demo/flow/${P.demo.focus}`), papi('/pipeline/demo/activity?limit=40'),
      papi('/pipeline/demo/agreements')]);
    const sig = JSON.stringify([P.demo, flow, activity.length, agreements.length]);
    Object.assign(P, { flow, activity, agreements, on: true, tried: true });
    if (sig === P.sig && P.tried) return;     // nothing changed: keep open drawers and scroll as they are
    P.sig = sig;
    for (const t of flow.tasks) if (t.agent === 'anomaly' && !P.shown.has(t.task_id)) {
      P.shown.add(t.task_id); P.batons.add('chaser');
    }
  } catch (e) {
    Object.assign(P, { on: /demo not started/.test(String(e)), tried: true });
    if (!/demo not started/.test(String(e))) P.error = `Agents API not reachable at ${AGENTS_API}: ${e.message}`;
    P.demo = null;
  }
  if (state.page === 'judgment') render();
}

// -- controls -------------------------------------------------------------------------------------
async function step(label, fn) {
  P.busy = label; P.error = ''; render();
  try { await fn(); } catch (e) { P.error = String(e.message || e); P.playing = false; }
  P.busy = ''; await refresh(); render();
}
window.pReset = () => step('Resetting and running the first day', async () => {
  P.shown.clear(); P.batons = new Set(['agreement']); await papi('/pipeline/demo/reset', 'POST');
});
window.pNext = () => step('Agents working', () => papi('/pipeline/demo/next-day', 'POST'));
window.pPlay = async () => {
  if (P.playing) { P.playing = false; render(); return; }
  P.playing = true;
  while (P.playing && P.demo && !P.demo.done) {
    await window.pNext();
    await pwait(2200);
  }
  P.playing = false; render();
};
window.pDecide = (id, approve) => step(approve ? 'Approving' : 'Rejecting', () =>
  papi(`/pipeline/demo/actions/${encodeURIComponent(id)}/decide?approve=${approve}&note=${approve ? 'approved in demo' : 'rejected in demo'}`, 'POST'));
window.pOpen = (kind, id) => { P.drawer = { kind, id }; render(); };
window.pClose = () => { P.drawer = null; render(); };

// -- page ----------------------------------------------------------------------------------------
const PRICE = { input: 4e-6, cached: 4e-7, output: 2e-5 };   // Claude Opus 5.5, USD per token

function usage() {
  const u = { calls: 0, input: 0, cached: 0, output: 0 };
  for (const t of P.flow?.tasks || []) {
    const x = t.trace?.usage || {};
    if (!x.output_tokens) continue;
    u.calls += 1; u.input += x.input_tokens || 0; u.cached += x.cache_read_input_tokens || 0;
    u.output += x.output_tokens || 0;
  }
  u.cost = u.input * PRICE.input + u.cached * PRICE.cached + u.output * PRICE.output;
  return u;
}
const num = n => Number(n || 0).toLocaleString('en-US');

function metrics() {
  const u = usage(), d = P.demo;
  const cell = (k, v) => `<div class="pl-metric"><span>${k}</span><b>${v}</b></div>`;
  return `<div class="pl-metrics">${cell('Day', d ? pday(d.today) : '—')}${cell('Model calls', num(u.calls))}
    ${cell('Input tokens', num(u.input + u.cached))}${cell('Output tokens', num(u.output))}${cell('Cost', '$' + u.cost.toFixed(3))}</div>`;
}

function timeline() {
  const d = P.demo; if (!d) return '';
  const start = new Date(d.start + 'T12:00:00'), end = new Date(d.end + 'T12:00:00');
  const days = []; for (let t = new Date(start); t <= end; t.setDate(t.getDate() + 1)) days.push(t.toISOString().slice(0, 10));
  const marks = {};
  for (const t of P.flow?.tasks || []) {
    const o = t.outcome || {};
    const tone = t.agent === 'anomaly' || o.decision === 'handoff_to_anomaly' ? 'handoff'
      : o.decided_by === 'schedule' ? 'wait' : 'model';
    if (!marks[t.as_of] || tone !== 'wait') marks[t.as_of] = tone;
  }
  for (const s of d.storyboard) marks[s.on] = marks[s.on] || 'evidence';
  return `<div class="pl-timeline"><div class="pl-time">${days.map(x => `<span class="pl-tick ${x <= d.today ? 'past' : ''} ${x === d.today ? 'now' : ''} ${marks[x] || ''}" title="${pday(x)}"></span>`).join('')}</div>
    <div class="pl-time-axis"><span>${pday(d.start)}</span><span class="pl-legend"><i class="model"></i>Model decision<i class="wait"></i>Scheduled wait<i class="handoff"></i>Hand-off<i class="evidence"></i>New evidence</span><span>${pday(d.end)}</span></div></div>`;
}

function toolPath(steps) {
  if (!steps || !steps.length) return '';
  return `<div class="pl-path">${steps.map(s => `<span class="${s.refused ? 'refused' : s.terminal ? 'final' : ''}">${esc(s.tool)}${s.refused ? ' (refused)' : ''}</span>`).join('<em>→</em>')}</div>`;
}

function agreementCol() {
  const vendor = (P.flow?.vendor || '').toLowerCase().split(' ')[0];
  const a = P.agreements.find(x => (x.vendor_name || '').toLowerCase().startsWith(vendor)) || null;
  if (!a) return '';
  const terms = a.fields.map(f => `<div class="pl-term"><i class="pl-sq ${f.status === 'found' ? 'on' : ''}"></i><span>${esc(f.label)}</span><b>${esc(f.value || f.status)}</b></div>`).join('');
  const rules = (P.flow?.rules || []).map(r => `<div class="pl-rule"><span>${esc(r.rule_type)}</span>${esc(r.check)}</div>`).join('');
  return `<button class="pl-item" onclick="pOpen('agreement','${esc(a.doc_id)}')">
      <div class="pl-item-head"><span class="pl-status ${a.status === 'active' || a.status === 'confirmed' ? 'ok' : 'hold'}">${esc(a.status)}</span><span>${esc(a.filename)}</span></div>
      ${terms}</button>
    <div class="pl-label">Rules</div>${rules}`;
}

function taskItem(t) {
  const o = t.outcome || {}, wait = o.decided_by === 'schedule';
  const text = o.summary || o.rationale || o.error || t.reason || '';
  const outcome = o.verdict ? pverdict(o.verdict) : plabel(o.decision);
  return `<button class="pl-item ${wait ? 'quiet' : ''} ${t.status === 'failed' ? 'failed' : ''}" onclick="pOpen('task','${esc(t.task_id)}')">
    <div class="pl-item-head"><span class="pl-date">${pday(t.as_of)}</span><span class="pl-status ${o.verdict === 'needs_review' || o.decision === 'handoff_to_anomaly' ? 'hold' : o.verdict === 'clean' ? 'ok' : ''}">${esc(outcome)}</span><span class="pl-src">${wait ? 'Schedule' : 'Model'}</span></div>
    ${wait ? '' : toolPath(t.trace?.steps)}
    <p class="pl-text ${wait ? 'one' : ''}">${esc(text)}</p>
    ${(o.finding_codes || []).length ? `<div class="pl-codes">${o.finding_codes.map(c => `<span>${esc(c)}</span>`).join('')}</div>` : ''}</button>`;
}

// consecutive days on which the schedule simply waited collapse into one row
function groupWaits(list) {
  const out = []; let run = [];
  const flush = () => {
    if (!run.length) return;
    if (run.length === 1) out.push(taskItem(run[0]));
    else {
      const last = run[0], first = run[run.length - 1];
      out.push(`<button class="pl-item quiet" onclick="pOpen('task','${esc(last.task_id)}')">
        <div class="pl-item-head"><span class="pl-date">${pday(first.as_of)} – ${pday(last.as_of)}</span><span class="pl-status">Wait</span><span class="pl-src">Schedule · ${run.length} days</span></div>
        <p class="pl-text one">${esc(last.outcome?.rationale || '')}</p></button>`);
    }
    run = [];
  };
  for (const t of list) {
    if (t.outcome?.decided_by === 'schedule') run.push(t); else { flush(); out.push(taskItem(t)); }
  }
  flush();
  return out;
}

function column(n, key, name, body, active) {
  const state_ = active ? 'active' : body ? 'done' : 'idle';
  return `<div class="flow-col"><section class="card pl-col ${state_}" id="pl-${key}">
    <header><i class="pl-dot ${state_}"></i><h2>${name}</h2><span class="pl-n">${n} of 3</span></header>
    <div class="pl-stack">${body || '<p class="pl-idle">No activity</p>'}</div></section>
    ${key !== 'anomaly' ? `<span class="pl-arrow ${body ? 'on' : ''}"></span>${P.batons.has(key) ? `<span class="pl-baton" onanimationend="P.batons.delete('${key}')">${esc(P.demo?.focus || '')}</span>` : ''}` : ''}</div>`;
}

function pendingApproval() {
  return (P.flow?.chaser_actions || []).find(a => ['needs_approval', 'needs_controller'].includes(a.status));
}

function activity() {
  const rows = (P.activity || []).slice(0, 12).map(i => {
    const action = i.decision ? plabel(i.decision) + (i.verdict ? ` · ${pverdict(i.verdict)}` : '') : i.status;
    const src = i.kind === 'task' ? (i.by === 'schedule' ? 'Schedule' : 'Model') : i.who === 'controller' ? 'Controller' : 'System';
    return `<tr><td>${pday(i.on)}</td><td><i class="pl-agent ${esc(i.who)}"></i>${esc(i.who)}</td><td>${esc(i.invoice_id)}</td><td>${esc(action)}</td><td>${src}</td><td class="pl-detail">${esc((i.text || '').slice(0, 140))}</td></tr>`;
  }).join('');
  return `<section class="card pl-activity"><div class="pl-label">Activity</div>
    ${rows ? `<table><thead><tr><th>Date</th><th>Agent</th><th>Invoice</th><th>Action</th><th>Source</th><th>Detail</th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="pl-idle">No activity</p>'}</section>`;
}

function pipelinePage() {
  const d = P.demo, busy = !!P.busy;
  const controls = d ? `<button class="pl-btn" onclick="pReset()" ${busy || P.playing ? 'disabled' : ''}>Reset</button>
      <button class="pl-btn" onclick="pNext()" ${busy || P.playing || d.done ? 'disabled' : ''}>Next day</button>
      <button class="pl-btn primary" onclick="pPlay()" ${busy && !P.playing ? 'disabled' : ''}>${P.playing ? 'Pause' : 'Play'}</button>`
    : `<button class="pl-btn primary" onclick="pReset()" ${busy ? 'disabled' : ''}>Start</button>`;
  const tasks = P.flow?.tasks || [];
  const chaser = groupWaits(tasks.filter(t => t.agent === 'chaser').reverse()).join('');
  const anomaly = tasks.filter(t => t.agent === 'anomaly').map(taskItem).reverse().join('');
  const pend = pendingApproval();
  return `<div class="pl-page"><div class="pagehead"><div><div class="crumb">Judgment</div><h1>Agent Workflow</h1>
      ${d && P.flow ? `<div class="pl-sub">${esc(d.focus)} · ${esc(P.flow.vendor)}</div>` : ''}</div>
      <div class="pl-controls">${P.busy ? `<span class="pl-busy">${esc(P.busy)}</span>` : ''}${controls}</div></div>
    ${P.error ? `<div class="pl-alert">${esc(P.error)}</div>` : ''}
    ${!d && P.tried ? `<section class="card pl-empty">Start runs the first day on a copy of the books. Requires the agent service on port 8773.</section>` : ''}
    ${d ? metrics() + timeline() : ''}
    ${pend ? `<div class="pl-approval"><span class="pl-label">Approval required</span><span>${esc((pend.level || pend.action).replace('_', ' '))} reminder · ${esc(pend.invoice_id)}</span>
      <span class="pl-grow"></span><button class="pl-btn" onclick="pDecide('${esc(pend.action_id)}',false)" ${busy ? 'disabled' : ''}>Reject</button><button class="pl-btn primary" onclick="pDecide('${esc(pend.action_id)}',true)" ${busy ? 'disabled' : ''}>Approve</button></div>` : ''}
    ${d ? `<div class="pl-flow">
      ${column('1', 'agreement', 'Agreement agent', agreementCol(), false)}
      ${column('2', 'chaser', 'Chaser agent', chaser, P.busy && !anomaly)}
      ${column('3', 'anomaly', 'Anomaly agent', anomaly, false)}
    </div>${activity()}` : ''}
    ${drawer()}</div>`;
}

// -- drawer: the full record of one task or one agreement review ----------------------------------
function drawer() {
  if (!P.drawer) return '';
  const meta = rows => `<dl class="pl-meta">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v ?? '—')}</dd>`).join('')}</dl>`;
  let body = '';
  if (P.drawer.kind === 'task') {
    const t = (P.flow?.tasks || []).find(x => x.task_id === P.drawer.id);
    if (!t) return '';
    const tr = t.trace || {}, o = t.outcome || {}, u = tr.usage || {};
    body = `<div class="crumb">${esc(t.agent)} agent</div><h2>${pday(t.as_of)} · ${esc(o.verdict ? pverdict(o.verdict) : plabel(o.decision))}</h2>
      ${meta([['Task', t.task_id], ['Created by', t.created_by], ['Model', tr.model], ['Status', t.status],
              ['Tokens', u.output_tokens ? `${num((u.input_tokens || 0) + (u.cache_read_input_tokens || 0))} in · ${num(u.output_tokens)} out` : 'none']])}
      <div class="pl-label">Trigger</div><p class="pl-para">${esc(t.reason)}</p>
      ${(tr.thinking || []).length ? `<div class="pl-label">Reasoning summary</div>${tr.thinking.map(x => `<p class="pl-para pl-quote">${esc(x.trim())}</p>`).join('')}` : ''}
      <div class="pl-label">Tool calls</div>${(tr.steps || []).length ? tr.steps.map((s, i) => `<details class="pl-step ${s.refused ? 'refused' : ''}" ${s.refused || s.terminal ? 'open' : ''}>
          <summary><i class="pl-ring ${s.refused ? 'refused' : 'on'}"></i><span class="pl-stepn">${i + 1}</span><b>${esc(s.tool)}</b><span class="pl-steptag">${s.refused ? 'refused' : s.terminal ? 'decision' : 'read'}</span></summary>
          <div class="pl-io"><span>Input</span><pre>${pjson(s.input)}</pre><span>Result</span><pre>${pjson(s.result)}</pre></div></details>`).join('')
        : '<p class="pl-para muted">No model call. The approved schedule had nothing to decide on this day.</p>'}
      <div class="pl-label">Decision</div><p class="pl-para">${esc(o.summary || o.rationale || o.error || '')}</p>
      ${(o.finding_codes || []).length ? `<div class="pl-codes">${o.finding_codes.map(c => `<span>${esc(c)}</span>`).join('')}</div>` : ''}`;
  } else {
    const a = P.agreements.find(x => x.doc_id === P.drawer.id);
    if (!a) return '';
    body = `<div class="crumb">Agreement agent</div><h2>${esc(a.filename)}</h2>
      ${meta([['Vendor', a.vendor_name], ['Status', a.status], ['Mode', a.mode === 'model' ? 'Model' : 'Rules'], ['Confirmed by', a.confirmed_by]])}
      <div class="pl-label">Terms</div>${a.fields.map(f => `<div class="pl-termrow"><div><i class="pl-sq ${f.status === 'found' ? 'on' : ''}"></i><b>${esc(f.label)}</b><span class="muted">${esc(f.location || f.status)}</span></div>${f.quote ? `<blockquote>${esc(f.quote)}</blockquote>` : `<p class="pl-para muted">${esc(f.note)}</p>`}</div>`).join('')}
      <div class="pl-label">Steps</div><ol class="pl-steps">${a.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>`;
  }
  return `<div class="pl-shade" onclick="pClose()"></div><aside class="pl-drawer"><button class="pl-btn pl-x" onclick="pClose()">Close</button>${body}</aside>`;
}

judgment = pipelinePage;
if (state.page === 'judgment') render();
refresh();
setInterval(() => { if (state.page === 'judgment' && !P.busy) refresh(); }, 4000);
