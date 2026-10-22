import { useEffect, useRef, useState } from 'react';

import { createChatSession, getChatSession, listChatSessions, streamAnswer } from '../api/client';
import type { ChatSession, ChatSessionDetail, EvidenceObject, GenerationInfo } from '../api/types';
import { EvidenceCard } from '../components/EvidenceCard';
import { formatErrorMessage } from '../utils/errors';

const SESSION_KEY = 'rag-chat-session';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  evidence?: EvidenceObject[];
  generation?: GenerationInfo | null;
  state?: 'pending' | 'completed' | 'cancelled' | 'error';
};

function readSessionId(): string {
  try { return localStorage.getItem(SESSION_KEY) || ''; } catch { return ''; }
}

function rememberSession(id: string) {
  try {
    if (id) localStorage.setItem(SESSION_KEY, id);
    else localStorage.removeItem(SESSION_KEY);
  } catch { /* The server still saves history if browser storage is disabled. */ }
}

function historyMessages(detail: ChatSessionDetail): ChatMessage[] {
  return detail.turns.flatMap((turn): ChatMessage[] => [
    { id: `${turn.id}-user`, role: 'user', text: turn.query },
    {
      id: turn.id, role: 'assistant', text: turn.answer,
      evidence: turn.supporting_evidence, generation: turn.generation, state: turn.status,
    },
  ]);
}

function answerLabel(message: ChatMessage): string {
  if (message.state === 'cancelled') return '已停止 · 内容未完成';
  if (message.state === 'error') return '生成失败 · 内容未完成';
  if (message.state === 'pending') return '正在生成';
  if (message.generation?.status === 'fallback') return '证据摘录 · 未生成模型答案';
  if (message.generation?.status === 'no_evidence') return '未找到支持证据';
  return '基于证据的回答';
}

type AiChatPageProps = {
  initialQuery?: string;
  onOpenContent?: (contentItemId: string) => void;
};

export function AiChatPage({ initialQuery = '', onOpenContent }: AiChatPageProps) {
  const [input, setInput] = useState(initialQuery);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [stage, setStage] = useState('正在检索证据');
  const [error, setError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const controller = useRef<AbortController>();
  const busy = useRef(false);
  const version = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    let active = true;
    void (async () => {
      try {
        const items = await listChatSessions(abort.signal);
        if (!active) return;
        setSessions(items);
        const remembered = readSessionId();
        if (remembered && items.some((item) => item.id === remembered)) {
          const detail = await getChatSession(remembered, abort.signal);
          if (!active) return;
          setSessionId(remembered);
          setMessages(historyMessages(detail));
        }
      } catch (caught) {
        if (active) setHistoryError(formatErrorMessage('加载历史对话失败', caught));
      } finally {
        if (active) setRestoring(false);
      }
    })();
    return () => { active = false; version.current++; controller.current?.abort(); };
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView?.({ block: 'nearest' }); }, [messages, stage]);

  function patchMessage(id: string, update: (message: ChatMessage) => ChatMessage) {
    setMessages((current) => current.map((message) => message.id === id ? update(message) : message));
  }

  async function selectSession(id: string) {
    if (busy.current || restoring) return;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const currentVersion = ++version.current;
    setError('');
    if (!id) {
      setSessionId(''); setMessages([]); rememberSession('');
      return;
    }
    setRestoring(true);
    try {
      const detail = await getChatSession(id, abort.signal);
      if (version.current !== currentVersion) return;
      setSessionId(id); rememberSession(id); setMessages(historyMessages(detail));
    } catch (caught) {
      if (version.current === currentVersion) setError(formatErrorMessage('加载对话失败', caught));
    } finally {
      if (version.current === currentVersion) setRestoring(false);
    }
  }

  async function sendQuestion() {
    const trimmed = input.trim();
    if (!trimmed || busy.current || restoring) return;
    busy.current = true;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const currentVersion = ++version.current;
    const id = `live-${++nextId.current}`;
    setInput(''); setError(''); setLoading(true); setStage('正在检索证据');
    setMessages((current) => [...current,
      { id: `${id}-user`, role: 'user', text: trimmed },
      { id, role: 'assistant', text: '', state: 'pending' },
    ]);
    try {
      let activeId = sessionId;
      if (!activeId) {
        const created = await createChatSession(abort.signal);
        if (version.current !== currentVersion) return;
        activeId = created.id;
        setSessionId(activeId); rememberSession(activeId);
        setSessions((current) => [created, ...current]);
      }
      await streamAnswer({ query: trimmed, filters: {}, top_k: 10, session_id: activeId }, (event) => {
        if (version.current !== currentVersion) return;
        if (event.event === 'status') {
          setStage(event.data.stage === 'retrieving' ? '正在检索证据' : '正在生成回答');
        } else if (event.event === 'evidence') {
          patchMessage(id, (message) => ({ ...message, evidence: event.data.supporting_evidence }));
        } else if (event.event === 'delta') {
          patchMessage(id, (message) => ({ ...message, text: message.text + event.data.text }));
        } else if (event.event === 'reset') {
          patchMessage(id, (message) => ({ ...message, text: event.data.text, generation: event.data.generation }));
        } else if (event.event === 'done') {
          patchMessage(id, (message) => ({ ...message, text: event.data.answer,
            evidence: event.data.supporting_evidence, generation: event.data.generation, state: 'completed' }));
        }
      }, abort.signal);
    } catch (caught) {
      if (version.current === currentVersion) {
        patchMessage(id, (message) => ({ ...message, state: abort.signal.aborted ? 'cancelled' : 'error' }));
        if (!abort.signal.aborted) setError(formatErrorMessage('生成答案失败', caught));
      }
    } finally {
      if (version.current === currentVersion) {
        busy.current = false; setLoading(false);
        try {
          const items = await listChatSessions();
          if (version.current === currentVersion) { setSessions(items); setHistoryError(''); }
        } catch (caught) {
          if (version.current === currentVersion) setHistoryError(formatErrorMessage('刷新历史对话失败', caught));
        }
      }
    }
  }

  return (
    <section className="chat-page">
      <header className="chat-page-header">
        <div>
          <p className="eyebrow">AI 问答</p>
          <h1>和无人机知识库对话</h1>
          <p className="muted">基于检索证据回答，支持连续追问。</p>
        </div>
        <div className="chat-session-controls">
          <label className="sr-only" htmlFor="chat-session">历史对话</label>
          <select id="chat-session" value={sessionId} disabled={loading || restoring}
            onChange={(event) => void selectSession(event.target.value)}>
            <option value="">新对话</option>
            {sessions.map((session) => <option key={session.id} value={session.id}>{session.title}</option>)}
          </select>
          <button className="chat-clear-button" type="button" disabled={loading || restoring}
            onClick={() => void selectSession('')}>新建对话</button>
        </div>
      </header>

      <div className="chat-transcript" aria-live="polite" aria-busy={loading || restoring}>
        {historyError ? <p className="chat-error" role="status">{historyError}</p> : null}
        {restoring ? <p role="status">正在加载历史对话…</p> : messages.length ? messages.map((message) => (
          <article className={`chat-message chat-message-${message.role}`} key={message.id}>
            <div className="chat-avatar" aria-hidden="true">{message.role === 'user' ? '你' : 'AI'}</div>
            <div className="chat-message-body">
              <p className="chat-role">{message.role === 'user' ? '你的问题' : answerLabel(message)}</p>
              <p className="chat-message-text">{message.text || (message.state === 'pending' ? stage : '')}</p>
              {message.generation?.status === 'fallback' ? (
                <p className="chat-fallback">本次未能生成模型答案，以下为检索到的原文摘录。</p>
              ) : null}
              {message.evidence?.length ? (
                <details className="chat-evidence">
                  <summary>查看 {message.evidence.length} 条支持证据</summary>
                  <div className="chat-evidence-list">
                    {message.evidence.map((evidence, index) => (
                      <div key={`${message.id}-${evidence.chunk_id}`}>
                        <p className="chat-citation">[{index + 1}]</p>
                        <EvidenceCard evidence={evidence} onOpenContent={onOpenContent} />
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </div>
          </article>
        )) : (
          <div className="chat-empty-state">
            <div className="chat-empty-mark" aria-hidden="true">✦</div>
            <h2>从一个问题开始</h2>
            <p>检索已入库的内容，基于证据回答；同一对话内可以继续追问。</p>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="chat-composer">
        <label className="sr-only" htmlFor="chat-question">输入问题</label>
        <textarea id="chat-question" value={input} onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); void sendQuestion();
            }
          }} placeholder="问问已入库的数据…" rows={1} />
        {loading ? <button type="button" onClick={() => controller.current?.abort()}>停止生成</button> : (
          <button type="button" disabled={!input.trim() || restoring} onClick={() => void sendQuestion()}>发送</button>
        )}
        {error ? <p className="chat-error" role="alert">{error}</p> : null}
        <p className="chat-hint">Enter 发送 · Shift + Enter 换行 · 对话自动保存</p>
      </div>
    </section>
  );
}
