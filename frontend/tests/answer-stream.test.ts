import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteChatSession, streamAnswer } from '../src/api/client';
import type { AnswerStreamEvent } from '../src/api/types';

function response(text: string, byteSize = 1): Response {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      for (let index = 0; index < bytes.length; index += byteSize) controller.enqueue(bytes.slice(index, index + byteSize));
      controller.close();
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}
afterEach(() => vi.unstubAllGlobals());

describe('answer SSE client', () => {
  it('accepts an empty 204 deletion response without parsing JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(deleteChatSession('session-1')).resolves.toBeUndefined();
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/chat-sessions\/session-1$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
  });
  it('handles split UTF-8 characters, CRLF boundaries and multiple events', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(
      ': heartbeat\r\n\r\nevent: delta\r\ndata: {"text":"中文答案"}\r\n\r\n'
      + 'event: done\r\ndata: {"answer":"中文答案","supporting_evidence":[]}\r\n\r\n',
    )));
    const events: AnswerStreamEvent[] = [];
    await streamAnswer({ query: '问题' }, (event) => events.push(event));
    expect(events.map((event) => event.event)).toEqual(['delta', 'done']);
    expect(events[0].data).toEqual({ text: '中文答案' });
  });
  it('rejects streams without completion', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('event: delta\ndata: {"text":"partial"}\n\n')));
    await expect(streamAnswer({ query: 'q' }, () => {})).rejects.toThrow('回答连接中断');
  });
  it('reports server errors after headers have been sent', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('event: error\ndata: {"message":"找不到对话"}\n\n')));
    await expect(streamAnswer({ query: 'q' }, () => {})).rejects.toThrow('找不到对话');
  });
});
