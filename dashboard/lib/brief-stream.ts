import type { Briefing } from './types';

/** Read complete SSE records across arbitrary network chunk boundaries. */
export async function streamBrief(question: string, onAnswer: (text: string) => void): Promise<Briefing> {
  const response = await fetch('/api/brief/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  if (!response.ok || !response.body) throw new Error(`AI request failed (${response.status}).`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.event === 'answer') onAnswer(event.text);
        if (event.event === 'error') throw new Error(event.message);
        if (event.event === 'done') return event.briefing as Briefing;
      }
      if (done) throw new Error('AI stream ended before a complete answer arrived.');
    }
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}
