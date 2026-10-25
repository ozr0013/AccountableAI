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
const plabel = d => ({ send_reminder: 'Reminder', wait: 'Wait', escalate: 'Escalate',
  handoff_to_anomaly: 'Hand-off', record_verdict: 'Verdict' }[d] || d || '—');
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
window.pReset = () => step('Resetting the books and running day one…', async () => {
  P.shown.clear(); P.batons = new Set(['agreement']); await papi('/pipeline/demo/reset', 'POST');
});
window.pNext = () => step('Agents working…', () => papi('/pipeline/demo/next-day', 'POST'));
window.pPlay = async () => {
  if (P.playing) { P.playing = false; render(); return; }
  P.playing = true;
  while (P.playing && P.demo && !P.demo.done) {
    await window.pNext();
    await pwait(2200);
  }
  P.playing = false; render();
};
window.pDecide = (id, approve) => step(approve ? 'Approving…' : 'Rejecting…', () =>
  papi(`/pipeline/demo/actions/${encodeURIComponent(id)}/decide?approve=${approve}&note=${approve ? 'approved in demo' : 'rejected in demo'}`, 'POST'));
window.pOpen = (kind, id) => { P.drawer = { kind, id }; render(); };
window.pClose = () => { P.drawer = null; render(); };

// -- page -------------------------------------------------------------------------------------------
function timeline() {
  const d = P.demo; if (!d) return '';
  const start = new Date(d.start + 'T12:00:00'), end = new Date(d.end + 'T12:00:00');
  const days = []; for (let t = new Date(start); t <= end; t.setDate(t.getDate() + 1)) days.push(t.toISOString().slice(0, 10));
  const marks = {};
  for (const t of P.flow?.tasks || []) {
    const o = t.outcome || {};
    const tone = t.agent === 'anomaly' ? 'warn' : o.decision === 'handoff_to_anomaly' ? 'warn'
      : o.decided_by === 'schedule' ? 'quiet' : 'claude';
    if (!marks[t.as_of] || tone !== 'quiet') marks[t.as_of] = tone;
  }
  for (const s of d.storyboard) marks[s.on] = marks[s.on] || 'evidence';
  return `<div class="pl-time">${days.map(x => `<span class="pl-tick ${x === d.today ? 'now' : x < d.today ? 'past' : ''} ${marks[x] || ''}" title="${pday(x)}"></span>`).join('')}</div>
    <div class="pl-time-legend"><span><i class="claude"></i>Claude decided</span><span><i class="quiet"></i>schedule waited, no model call</span><span><i class="warn"></i>hand-off / review</span><span><i class="evidence"></i>new evidence arrives</span></div>`;
}

function chips(steps) {
  return `<div class="pl-chips">${(steps || []).map(s => `<span class="pl-chip ${s.refused ? 'refused' : s.terminal ? 'decision' : ''}">${s.refused ? '⛔ ' : s.terminal ? '✓ ' : '🔧 '}${esc(s.tool)}</span>`).join('<span class="pl-sep">→</span>')}</div>`;
}

function agreementCol() {
  const vendor = P.flow?.vendor || '';
  const a = P.agreements.find(x => (x.vendor_name || '').toLowerCase().startsWith(vendor.toLowerCase().split(' ')[0])) || null;
  if (!a) return '<p class="muted">No saved agreement yet.</p>';
  const fields = a.fields.map(f => `<div class="pl-field ${f.status}"><b>${f.status === 'found' ? '✓' : '✗'} ${esc(f.label)}</b>${f.value ? ` <span>${esc(f.value)}</span>` : ''}${f.location ? ` <em>${esc(f.location)}</em>` : ''}</div>`).join('');
  const rules = (P.flow?.rules || []).map(r => `<li><b>${esc(r.rule_type)}</b> ${esc(r.check)}</li>`).join('');
  return `<button class="pl-card" onclick="pOpen('agreement','${esc(a.doc_id)}')">
      <div class="pl-card-head"><span class="pl-badge ${a.status === 'active' || a.status === 'confirmed' ? 'good' : 'warn'}">${esc(a.status)}</span><span class="muted">${esc(a.filename)} · ${a.mode === 'model' ? 'Claude' : 'rules'} · ${a.steps.length} steps</span></div>
      ${fields}</button>
    <div class="pl-rules"><div class="muted">Rules saved for the chaser</div><ul>${rules}</ul></div>`;
}

function taskCard(t) {
  const o = t.outcome || {}, quiet = o.decided_by === 'schedule';
  const text = o.summary || o.rationale || o.error || t.reason;
  const badge = o.verdict ? `<span class="pl-badge ${o.verdict === 'clean' ? 'good' : 'warn'}">${esc(o.verdict)}</span>`
    : `<span class="pl-badge ${o.decision === 'wait' ? 'quiet' : o.decision === 'handoff_to_anomaly' ? 'warn' : 'claude'}">${plabel(o.decision)}</span>`;
  return `<button class="pl-card ${quiet ? 'quiet' : ''} ${t.status}" onclick="pOpen('task','${esc(t.task_id)}')">
    <div class="pl-card-head">${badge}<span class="muted">${pday(t.as_of)} · ${quiet ? 'schedule' : 'Claude'}${t.created_by === 'chaser_agent' ? ' · handed off by chaser' : ''}</span></div>
    ${quiet ? '' : chips(t.trace?.steps)}
    <p class="pl-text">${esc((text || '').slice(0, quiet ? 90 : 260))}${(text || '').length > (quiet ? 90 : 260) ? '…' : ''}</p>
    ${(o.finding_codes || []).map(c => `<span class="pl-code">${esc(c)}</span>`).join('')}</button>`;
}

function column(n, key, name, when, body, active) {
  return `<div class="flow-col"><section class="card flow-card pl-col ${active ? 'running' : body ? 'done' : 'waiting'}" id="pl-${key}">
    <header><span class="flow-n">${n}</span><div><h2>${name}</h2><p class="muted">${when}</p></div></header>
    <div class="pl-stack">${body || '<p class="muted pl-idle">Waiting</p>'}</div></section>
    ${key !== 'anomaly' ? `<span class="flow-arrow ${body ? 'on' : ''}">→</span>${P.batons.has(key) ? `<span class="pl-baton" onanimationend="P.batons.delete('${key}')">${esc(P.demo?.focus || '')}</span>` : ''}` : ''}</div>`;
}

function pendingApproval() {
  return (P.flow?.chaser_actions || []).find(a => ['needs_approval', 'needs_controller'].includes(a.status));
}

function pipelinePage() {
  const d = P.demo, busy = !!P.busy;
  const cost = (P.flow?.tasks || []).reduce((s, t) => {
    const u = t.trace?.usage || {}; return s + (u.input_tokens || 0) * 4e-6 + (u.cache_read_input_tokens || 0) * 4e-7 + (u.output_tokens || 0) * 2e-5;
  }, 0);
  const calls = (P.flow?.tasks || []).filter(t => t.trace?.model && !/schedule/.test(t.trace.model)).length;
  const controls = d ? `<span class="pill">Claude · ${calls} calls · $${cost.toFixed(2)}</span>
      <span class="pl-date">${pday(d.today)}</span>
      <button onclick="pReset()" ${busy || P.playing ? 'disabled' : ''}>↺ Reset</button>
      <button onclick="pNext()" ${busy || P.playing || d.done ? 'disabled' : ''}>Next day</button>
      <button class="primary" onclick="pPlay()" ${busy && !P.playing ? 'disabled' : ''}>${P.playing ? '❚❚ Pause' : '▶ Play'}</button>`
    : `<button class="primary" onclick="pReset()" ${busy ? 'disabled' : ''}>Start the demo</button>`;
  const tasks = P.flow?.tasks || [];
  const chaser = tasks.filter(t => t.agent === 'chaser').map(taskCard).reverse().join('');
  const anomaly = tasks.filter(t => t.agent === 'anomaly').map(taskCard).reverse().join('');
  const pend = pendingApproval();
  return `<div class="pagehead"><div><div class="crumb">Judgment &nbsp;›</div><h1>Agent Workflow</h1></div><div class="pl-controls">${controls}</div></div>`
    + `<p class="muted flow-intro">Owner funds the middleman, the middleman pays the vendor. Three Claude agents watch every step, wake up on their own each day, and hand work to each other. ${d ? `Following <b>${esc(d.focus)}</b>${P.flow ? ` · ${esc(P.flow.vendor)}` : ''}.` : ''}</p>
    ${P.error ? `<section class="card flow-error">${esc(P.error)}</section>` : ''}
    ${P.busy ? `<div class="pl-busy"><span class="flow-caret"></span> ${esc(P.busy)}</div>` : ''}
    ${!d && P.tried ? `<section class="card"><p>Start the demo: the books are copied, the calendar starts on Apr 22 and the agents run day one. Needs <code>python -m chaser_agent.api</code> on port 8773.</p></section>` : ''}
    ${timeline()}
    ${pend ? `<section class="card pl-inbox"><b>Waiting for you:</b> the chaser drafted a <b>${esc(pend.level || pend.action)}</b> for ${esc(pend.invoice_id)}. <button onclick="pDecide('${esc(pend.action_id)}',true)" ${busy ? 'disabled' : ''}>Approve</button> <button onclick="pDecide('${esc(pend.action_id)}',false)" ${busy ? 'disabled' : ''}>Reject</button></section>` : ''}
    <div class="flow">
      ${column('1', 'agreement', 'Agreement agent', 'Reads the contract, quotes every term, saves the rules', d ? agreementCol() : '', false)}
      ${column('2', 'chaser', 'Chaser agent', 'Every day: is it due, was it paid, what next?', chaser, P.busy && !anomaly)}
      ${column('3', 'anomaly', 'Anomaly agent', 'Money left: right amount, payee, account, time?', anomaly, false)}
    </div>
    <section class="card pl-ticker"><div class="pl-ticker-head"><b>Activity</b><span class="muted">everything the agents and people did · newest first · no clicks needed</span></div>
      ${(P.activity || []).slice(0, 14).map(i => `<div class="pl-tick-line ${i.who}"><span>${pday(i.on)}</span><b>${esc(i.who)}</b><span>${esc(i.invoice_id)}</span><span class="pl-what">${esc(i.decision ? plabel(i.decision) + (i.verdict ? ' · ' + i.verdict : '') + (i.by === 'schedule' ? ' (schedule)' : ' (Claude)') : i.status)}</span><span class="muted">${esc((i.text || '').slice(0, 120))}</span></div>`).join('') || '<p class="muted">Nothing yet.</p>'}
    </section>
    ${drawer()}`;
}

// -- the drawer: full trace for a task, or the agreement review -----------------------------------
function drawer() {
  if (!P.drawer) return '';
  let body = '';
  if (P.drawer.kind === 'task') {
    const t = (P.flow?.tasks || []).find(x => x.task_id === P.drawer.id);
    if (!t) return '';
    const tr = t.trace || {}, o = t.outcome || {};
    body = `<h2>${esc(t.agent)} agent · ${pday(t.as_of)}</h2>
      <p class="muted">${esc(t.task_id)} · created by ${esc(t.created_by)} · ${esc(tr.model || '')} · ${esc(t.status)}</p>
      <h3>Why it ran</h3><p>${esc(t.reason)}</p>
      ${(tr.thinking || []).length ? `<h3>🧠 Reasoning summary</h3>${tr.thinking.map(x => `<p class="flow-line thinking">${esc(x)}</p>`).join('')}` : ''}
      <h3>🔧 Tool calls</h3>${(tr.steps || []).length ? tr.steps.map((s, i) => `<details class="pl-step ${s.refused ? 'refused' : ''}" ${s.refused || s.terminal ? 'open' : ''}>
          <summary>${i + 1}. ${s.refused ? '⛔ refused · ' : s.terminal ? '✓ decision · ' : ''}<b>${esc(s.tool)}</b></summary>
          <div class="muted">input</div><pre>${pjson(s.input)}</pre><div class="muted">result</div><pre>${pjson(s.result)}</pre></details>`).join('')
        : '<p class="muted">No model call: the approved schedule had nothing to decide today.</p>'}
      <h3>✅ Decision</h3><p><b>${plabel(o.decision)}${o.verdict ? ' · ' + esc(o.verdict) : ''}</b></p><p>${esc(o.summary || o.rationale || o.error || '')}</p>
      ${tr.usage && tr.usage.output_tokens ? `<p class="muted">tokens: ${tr.usage.input_tokens} in · ${tr.usage.cache_read_input_tokens || 0} cached · ${tr.usage.output_tokens} out</p>` : ''}`;
  } else {
    const a = P.agreements.find(x => x.doc_id === P.drawer.id);
    if (!a) return '';
    body = `<h2>Agreement agent · ${esc(a.filename)}</h2><p class="muted">${esc(a.vendor_name || '')} · ${esc(a.status)} · ${a.mode === 'model' ? 'Claude' : 'rules'}${a.confirmed_by ? ' · confirmed by ' + esc(a.confirmed_by) : ''}</p>
      <h3>Terms, each with the quote it came from</h3>${a.fields.map(f => `<div class="pl-step ${f.status === 'found' ? '' : 'refused'}"><b>${esc(f.label)}</b> · ${esc(f.status)} ${f.location ? '· ' + esc(f.location) : ''}${f.quote ? `<blockquote>${esc(f.quote)}</blockquote>` : `<p class="muted">${esc(f.note)}</p>`}</div>`).join('')}
      <h3>🔧 What the agent did</h3><ol class="pl-steps">${a.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>`;
  }
  return `<div class="pl-shade" onclick="pClose()"></div><aside class="pl-drawer"><button class="pl-x" onclick="pClose()">✕</button>${body}</aside>`;
}

judgment = pipelinePage;
if (state.page === 'judgment') render();
refresh();
setInterval(() => { if (state.page === 'judgment' && !P.busy) refresh(); }, 4000);
