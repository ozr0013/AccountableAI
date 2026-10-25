import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('./brief-stream.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { streamBrief } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const originalFetch = globalThis.fetch;
function response(chunks) {
  return new Response(new ReadableStream({ start(controller) {
    for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
}

test('answer updates and final briefing survive split SSE frames', async () => {
  const updates = [];
  const briefing = { answer: 'Hello', claims: [], prior_rulings: [], meta: { provider: 'openai' } };
  globalThis.fetch = async () => response(['data: {"event":"ans', 'wer","text":"Hel"}\n', '\ndata: {"event":"answer","text":"Hello"}\n\n', `data: ${JSON.stringify({ event: 'done', briefing })}\n\n`]);
  try {
    assert.deepEqual(await streamBrief('question', text => updates.push(text)), briefing);
    assert.deepEqual(updates, ['Hel', 'Hello']);
  } finally { globalThis.fetch = originalFetch; }
});

test('provider error rejects instead of showing a fabricated answer', async () => {
  globalThis.fetch = async () => response(['data: {"event":"error","message":"Credit exhausted"}\n\n']);
  try { await assert.rejects(streamBrief('question', () => {}), /Credit exhausted/); }
  finally { globalThis.fetch = originalFetch; }
});

test('interrupted stream rejects incomplete answer', async () => {
  globalThis.fetch = async () => response(['data: {"event":"answer","text":"Partial"}\n\n']);
  try { await assert.rejects(streamBrief('question', () => {}), /before a complete answer/); }
  finally { globalThis.fetch = originalFetch; }
});
