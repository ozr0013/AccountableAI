// The Agent Workflow tab, connected to the live backend (dashboard/feed/serve.py). Nothing else.
//
// app.js is the designer's dashboard and stays exactly as designed: its pages, data and opening
// screen. This file replaces only the body of her Judgment page ("Agent Workflow"): the three
// agents run one after another (agreement, chaser, anomaly detector) through POST /api/loop/*, and
// each agent's reasoning shows up in its card as it decides, from the /api/stream event stream.
// Reminders and the statement note are emailed by the backend; each email shows in its card.
//
// Served by the backend at its own address, so the API is same-origin. Opened another way
// (python -m http.server), it talks to http://127.0.0.1:8765.

const FEED = window.ACCOUNTABLE_FEED ??
  (location.protocol === 'file:' || location.port === '8000' ? 'http://127.0.0.1:8765' : '');
const LIVE = { on: false, tried: false, agents: 'rules' };
const INVOICE = 'INV-KW-0301';

// is the backend up, and are its agents on Claude or rules?
async function checkBackend() {
  try {
    const r = await fetch(`${FEED}/api/state`, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const m = (await r.json()).meta;
    Object.assign(LIVE, { on: true, tried: true, agents: m.agents });
  } catch (e) {
    Object.assign(LIVE, { on: false, tried: true });
  }
  if (state.page === 'judgment') paint();
}

// -- 2. the agent workflow (the Judgment page) ----------------------------------------------------
const AGENTS = [
  { key: 'agreement', n: '1', name: 'Agreement agent', when: "An agreement arrives in the owner's Gmail",
    listens: d => d.agent === 'agreement_check' },
  { key: 'chaser', n: '2', name: 'Chaser agent', when: 'The due date passes. Was the vendor paid?',
    listens: d => d.agent === 'chaser' || (['trail', 'cause'].includes(d.agent) && d.invoice_id === INVOICE) },
  { key: 'anomaly', n: '3', name: 'Anomaly detector', when: 'The middleman sends a financial statement',
    listens: d => d.agent === 'anomaly' || (d.agent === 'verifier' && d.invoice_id === INVOICE) },
];
const MAIN = new Set(['agreement_check', 'chaser', 'anomaly']);   // supporting checks show their conclusion only
const WHO = { agreement_check: 'Agreement agent', chaser: 'Chaser agent', trail: 'Payment-trail check',
  cause: 'Delay-cause check', anomaly: 'Anomaly detector', verifier: 'Verifier' };
const flow = { status: {}, lines: {}, out: {}, running: false, error: null, active: null, queue: [], asked: new Set() };
function resetFlow() {
  for (const a of AGENTS) { flow.status[a.key] = 'waiting'; flow.lines[a.key] = []; }
  flow.out = {}; flow.error = null; flow.queue = []; flow.asked = new Set();
}
resetFlow();

const wait = ms => new Promise(r => setTimeout(r, ms));
async function call(step) {
  const r = await fetch(`${FEED}/api/loop/${step}`, { method: 'POST', cache: 'no-store' });
  if (!r.ok) throw new Error(`${step}: ${r.status}`);
  return r.json();
}
const usd = n => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const short = s => s ? new Date(s + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '?';

function mailed(emails, kind) {
  const sent = (emails || []).filter(e => e.kind === kind && e.status === 'sent');
  return sent.length ? `Emailed ${esc(sent[0].to)}${sent.length > 1 ? ` (${sent.length} emails)` : ''}.` : '';
}
function result(key) {
  const o = flow.out[key];
  if (!o) return '';
  const box = (tone, html) => `<div class="flow-result ${tone}">${html}</div>`;
  if (key === 'agreement') return o.items.map(i => i.complete && i.stored
    ? box('good', `<b>Complete → stored.</b> ${esc(i.stored.vendor)} · ${esc(i.stored.amount)} · ${esc(i.stored.due)}`)
    : box('warn', `<b>Missing info → owner notified.</b> ${esc((i.owner_notice?.subject || '').replace('Action needed: ', ''))}`)).join('');
  if (key === 'chaser') {
    const ladder = [...o.earlier, ...o.new].map(r => r.level).join(' → ');
    return box('warn', `<b>${esc(o.invoice)} not paid.</b> ${esc(o.vendor)} · ${usd(o.amount)} · due ${short(o.due)}, ${o.days_late} days late, no bank or vendor proof.`)
      + box('warn', `<b>Reminders: ${esc(ladder)}.</b> ${o.escalated ? 'Owner notified; middleman told the owner knows.' : 'Next reminder on schedule.'} ${mailed(o.emails, 'reminder')}`);
  }
  return o.caught
    ? box('warn', `<b>Mismatch caught.</b> Owner approved ${usd(o.approved)}, statement shows ${usd(o.stated)}. Owner alerted.`)
      + (o.middleman ? box('warn', `<b>Note to the middleman.</b> ${mailed(o.emails, 'statement_notice') || 'Not emailed (email not connected).'} ${esc(o.middleman.body)}`) : '')
    : box('good', '<b>Statement matches</b> what the owner approved.');
}
function lineHtml(l) {
  return `<p class="flow-line ${l.kind}">${l.kind === 'question' ? `<b>${esc(l.who)}</b> ${esc(l.text)}` : esc(l.text)}</p>`;
}
function cardHtml(a, i) {
  const st = flow.status[a.key], lines = flow.lines[a.key];
  return `<div class="flow-col"><section class="card flow-card ${st}" id="flow-${a.key}">
    <header><span class="flow-n">${st === 'done' ? '✓' : a.n}</span><div><h2>${a.name}</h2><p class="muted">${a.when}</p></div></header>
    <div class="flow-think" aria-live="polite">${lines.length ? lines.map(lineHtml).join('')
      : `<p class="muted">${st === 'running' ? 'Starting…' : st === 'waiting' ? 'Waiting' : ''}</p>`}${st === 'running' ? '<span class="flow-caret"></span>' : ''}</div>
    ${result(a.key)}</section>${i < AGENTS.length - 1 ? `<span class="flow-arrow ${st === 'done' ? 'on' : ''}">→</span>` : ''}</div>`;
}
function agentsPage() {
  const mode = !LIVE.tried ? 'Connecting…' : !LIVE.on ? 'Backend not connected'
    : LIVE.agents === 'live' ? 'Claude agents' : 'Rules agents · $0';
  const run = `<span class="pill">${mode}</span><button class="primary" onclick="runLoop()" ${flow.running || !LIVE.on ? 'disabled' : ''}>${flow.running ? 'Running…' : flow.out.anomaly ? 'Run again' : 'Run the loop'}</button>`;
  return header('Judgment &nbsp;›', 'Agent Workflow', run)
    + `<p class="muted flow-intro">Owner pays the middleman, the middleman pays the vendor. Three agents check every step.</p>
    ${LIVE.tried && !LIVE.on ? '<section class="card"><p>The agents need the live backend: <code>python dashboard/feed/serve.py</code></p></section>' : ''}
    ${flow.error ? `<section class="card flow-error">${esc(flow.error)}</section>` : ''}
    <div class="flow">${AGENTS.map(cardHtml).join('')}</div>
    <p class="muted">Reminders and the statement note are emailed to the middleman from the owner's Gmail, using approved templates only. Nothing moves money.</p>`;
}
function paint(key) {
  if (state.page !== 'judgment') return;
  if (!key) { $('#main').innerHTML = agentsPage(); return; }
  const a = AGENTS.find(x => x.key === key), el = document.getElementById(`flow-${key}`);
  if (!el) { $('#main').innerHTML = agentsPage(); return; }
  el.parentElement.outerHTML = cardHtml(a, AGENTS.indexOf(a));
  const box = document.querySelector(`#flow-${key} .flow-think`);
  if (box) box.scrollTop = box.scrollHeight;
}

// reasoning as it happens: every message on the stream goes to the agent card that is running
function take(d) {
  const key = flow.active;
  return key && AGENTS.find(a => a.key === key).listens(d) ? key : null;
}
function ask(key, agent, text) {
  const id = `${key}|${agent}|${text}`;
  if (flow.asked.has(id)) return;
  flow.asked.add(id);
  flow.queue.push({ key, line: { who: WHO[agent] || agent, kind: 'question', text } });
}
function listen() {
  if (typeof EventSource === 'undefined') return;
  const es = new EventSource(`${FEED}/api/stream`);
  const on = (type, f) => es.addEventListener(type, e => f(JSON.parse(e.data)));
  on('action', m => { if (m.status === 'started') flow.asked.clear(); });
  on('agent_started', m => { const k = take(m); if (k) ask(k, m.agent, m.question); });
  on('thinking', m => { const k = take(m); if (k) flow.queue.push({ key: k, line: { who: WHO[m.agent] || m.agent, kind: 'thinking', text: m.text } }); });
  on('email', m => {
    const k = flow.active;
    if (k) flow.queue.push({ key: k, line: { who: 'Email', kind: m.status === 'sent' ? 'email' : 'guard',
      text: m.status === 'sent' ? `Emailed ${m.to}: ${m.subject}` : `Email to ${m.to} failed: ${m.error || ''}` } });
  });
  on('decision', d => {
    const k = take(d);
    if (!k) return;
    ask(k, d.agent, d.question);
    const steps = MAIN.has(d.agent) ? d.steps : d.steps.filter(s => s.kind === 'result');
    for (const s of steps) if (s.kind !== 'thinking') flow.queue.push({ key: k, line: { who: WHO[d.agent] || d.agent, kind: s.kind, text: s.text } });
  });
}
// one line at a time so a person can follow it; rules-mode agents decide in milliseconds
setInterval(() => {
  const next = flow.queue.shift();
  if (!next) return;
  flow.lines[next.key].push(next.line);
  // append just this line, so the lines already shown stay put (redrawing the card would replay
  // every line's fade-in and they'd keep vanishing while the agent streams)
  const box = state.page === 'judgment' && document.querySelector(`#flow-${next.key} .flow-think`);
  if (!box) return paint(next.key);
  box.querySelector(':scope > .muted')?.remove();
  const caret = box.querySelector('.flow-caret');
  caret ? caret.insertAdjacentHTML('beforebegin', lineHtml(next.line)) : box.insertAdjacentHTML('beforeend', lineHtml(next.line));
  box.scrollTop = box.scrollHeight;
}, 170);

async function runAgent(key) {
  flow.active = key; flow.status[key] = 'running'; paint(key);
  const res = await call(key);
  await wait(250);                                   // the last stream messages land just after the response
  while (flow.queue.length) await wait(120);
  await wait(300);
  flow.out[key] = res; flow.status[key] = 'done'; paint(key);
}
window.runLoop = async function () {
  flow.running = true; resetFlow(); paint();
  try {
    await call('start');
    await runAgent('agreement'); await wait(600);
    await runAgent('chaser'); await wait(600);
    await runAgent('anomaly');
    flow.active = null;
  } catch (e) {
    flow.error = `${e}. Is the backend running? python dashboard/feed/serve.py`;
  }
  flow.running = false; paint();
};

// her Judgment page ("Agent Workflow") becomes the live three-agent workflow; nothing else changes
judgment = agentsPage;
if (state.page === 'judgment') render();
checkBackend();
listen();
