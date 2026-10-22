import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/api/client';
import type { AnswerStreamEvent, GenerationInfo } from '../src/api/types';
import { AiChatPage } from '../src/pages/AiChatPage';

vi.mock('../src/api/client', () => ({
  listChatSessions: vi.fn(), getChatSession: vi.fn(), createChatSession: vi.fn(), streamAnswer: vi.fn(),
}));
const session = { id: 'session-1', title: 'PX4', created_at: '', updated_at: '' };
const generation: GenerationInfo = {
  status: 'generated', model: 'test', reason: null, latency_ms: 1, total_latency_ms: 2,
  evidence_count: 0, context_chars: 0, context_chunk_ids: [], retrieval_query: 'PX4', rewrite_status: 'not_needed',
};
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.listChatSessions).mockResolvedValue([]);
  vi.mocked(api.createChatSession).mockResolvedValue(session);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function send() {
  const button = await screen.findByRole('button', { name: '发送' });
  await waitFor(() => expect(button).toBeEnabled());
  await act(async () => { fireEvent.click(button); });
}

describe('AI chat streaming and sessions', () => {
  it('shows deltas before completion and passes the saved session id', async () => {
    let emit!: (event: AnswerStreamEvent) => void;
    let finish!: () => void;
    vi.mocked(api.streamAnswer).mockImplementation((_body, callback) => {
      emit = callback;
      return new Promise((resolve) => { finish = resolve; });
    });
    render(<AiChatPage initialQuery="PX4" />);
    await send();
    await waitFor(() => expect(api.streamAnswer).toHaveBeenCalledOnce());
    act(() => emit({ event: 'delta', data: { text: '第一段' } }));
    expect(screen.getByText('第一段')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument();
    act(() => emit({ event: 'delta', data: { text: '第二段' } }));
    expect(screen.getByText('第一段第二段')).toBeInTheDocument();
    await act(async () => {
      emit({ event: 'done', data: { query: {} as never, answer: '完整回答', supporting_evidence: [], generation } });
      finish();
    });
    expect(screen.getByText('完整回答')).toBeInTheDocument();
    expect(localStorage.getItem('rag-chat-session')).toBe(session.id);
    expect(vi.mocked(api.streamAnswer).mock.calls[0][0]).toEqual({ query: 'PX4', filters: {}, top_k: 10, session_id: session.id });
  });

  it('replaces failed partial output with explicitly labeled excerpts', async () => {
    vi.mocked(api.streamAnswer).mockImplementation(async (_body, emit) => {
      emit({ event: 'delta', data: { text: '错误的未完成答案' } });
      const fallback = { ...generation, status: 'fallback' as const, reason: 'model_error' };
      emit({ event: 'reset', data: { text: '[1] 原文摘录', generation: fallback } });
      emit({ event: 'done', data: { query: {} as never, answer: '[1] 原文摘录', supporting_evidence: [], generation: fallback } });
    });
    render(<AiChatPage initialQuery="PX4" />);
    await send();
    expect(await screen.findByText('证据摘录 · 未生成模型答案')).toBeInTheDocument();
    expect(screen.queryByText('错误的未完成答案')).not.toBeInTheDocument();
    expect(screen.getByText('[1] 原文摘录')).toBeInTheDocument();
  });

  it('stops the request and marks partial output incomplete', async () => {
    let signal!: AbortSignal;
    vi.mocked(api.streamAnswer).mockImplementation((_body, emit, requestSignal) => {
      signal = requestSignal!;
      emit({ event: 'delta', data: { text: '未完成内容' } });
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')));
      });
    });
    render(<AiChatPage initialQuery="PX4" />);
    await send();
    expect(await screen.findByText('未完成内容')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '停止生成' })); });
    expect(await screen.findByText('已停止 · 内容未完成')).toBeInTheDocument();
    expect(signal.aborted).toBe(true);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('restores saved history and starts a separate conversation', async () => {
    localStorage.setItem('rag-chat-session', session.id);
    vi.mocked(api.listChatSessions).mockResolvedValue([session]);
    vi.mocked(api.getChatSession).mockResolvedValue({ session, turns: [{
      id: 'turn-1', session_id: session.id, query: '先前问题', answer: '先前回答',
      status: 'completed', generation, supporting_evidence: [], created_at: '',
    }] });
    render(<AiChatPage />);
    expect(await screen.findByText('先前回答')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建对话' }));
    expect(screen.queryByText('先前回答')).not.toBeInTheDocument();
    expect(localStorage.getItem('rag-chat-session')).toBeNull();
    expect(screen.getByRole('heading', { name: '从一个问题开始' })).toBeInTheDocument();
  });

  it('aborts generation on page unmount', async () => {
    let signal!: AbortSignal;
    vi.mocked(api.streamAnswer).mockImplementation((_body, _emit, requestSignal) => {
      signal = requestSignal!;
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')));
      });
    });
    const page = render(<AiChatPage initialQuery="PX4" />);
    await send();
    await waitFor(() => expect(api.streamAnswer).toHaveBeenCalledOnce());
    page.unmount();
    expect(signal.aborted).toBe(true);
  });
});
