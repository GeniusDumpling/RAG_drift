import { useRef, useState } from 'react';

import { answer } from '../api/client';
import type { EvidenceObject, SearchRequest } from '../api/types';
import { EvidenceCard } from '../components/EvidenceCard';
import { formatErrorMessage } from '../utils/errors';

type ChatMessage = {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  evidence?: EvidenceObject[];
};

type AiChatPageProps = {
  initialQuery?: string;
  onOpenContent?: (contentItemId: string) => void;
};

const STARTERS = ['视频中展示了哪些操作？', '该来源有哪些关键结论？', '请总结与问题相关的证据。'];

export function AiChatPage({ initialQuery = '', onOpenContent }: AiChatPageProps) {
  const [input, setInput] = useState(initialQuery);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const nextMessageId = useRef(1);

  function addMessage(message: Omit<ChatMessage, 'id'>) {
    setMessages((current) => [...current, { ...message, id: nextMessageId.current++ }]);
  }

  async function sendQuestion(question = input) {
    const trimmed = question.trim();
    if (!trimmed || loading) {
      return;
    }
    setInput('');
    setError('');
    addMessage({ role: 'user', text: trimmed });
    setLoading(true);
    try {
      const request: SearchRequest = { query: trimmed, filters: {}, top_k: 10 };
      const response = await answer(request);
      addMessage({ role: 'assistant', text: response.answer, evidence: response.supporting_evidence });
    } catch (caught) {
      setError(formatErrorMessage('生成答案失败', caught));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="chat-page">
      <header className="chat-page-header">
        <div>
          <p className="eyebrow">AI 问答</p>
          <h1>和情报数据库对话</h1>
          <p className="muted">回答由 DeepSeek 基于检索证据生成。</p>
        </div>
        {messages.length ? (
          <button className="chat-clear-button" type="button" onClick={() => setMessages([])}>
            清空对话
          </button>
        ) : null}
      </header>

      <div className="chat-transcript" aria-live="polite">
        {messages.length ? (
          messages.map((message) => (
            <article className={`chat-message chat-message-${message.role}`} key={message.id}>
              <div className="chat-avatar" aria-hidden="true">{message.role === 'user' ? '你' : 'AI'}</div>
              <div className="chat-message-body">
                <p className="chat-role">{message.role === 'user' ? '你的问题' : '基于证据的回答'}</p>
                <p className="chat-message-text">{message.text}</p>
                {message.evidence?.length ? (
                  <details className="chat-evidence">
                    <summary>查看 {message.evidence.length} 条支持证据</summary>
                    <div className="chat-evidence-list">
                      {message.evidence.map((evidence) => (
                        <EvidenceCard
                          evidence={evidence}
                          key={`${message.id}-${evidence.chunk_id}`}
                          onOpenContent={onOpenContent}
                        />
                      ))}
                    </div>
                  </details>
                ) : null}
              </div>
            </article>
          ))
        ) : (
          <div className="chat-empty-state">
            <div className="chat-empty-mark" aria-hidden="true">✦</div>
            <h2>从一个问题开始</h2>
            <p>我会检索已入库的内容，再基于证据生成引用明确的回答。</p>
            <div className="chat-starters">
              {STARTERS.map((starter) => (
                <button key={starter} type="button" onClick={() => void sendQuestion(starter)}>
                  {starter}
                </button>
              ))}
            </div>
          </div>
        )}
        {loading ? (
          <article className="chat-message chat-message-assistant">
            <div className="chat-avatar" aria-hidden="true">AI</div>
            <div className="chat-thinking"><span /> <span /> <span /> 正在检索证据并生成回答</div>
          </article>
        ) : null}
      </div>

      <div className="chat-composer">
        <label className="sr-only" htmlFor="chat-question">输入问题</label>
        <textarea
          id="chat-question"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void sendQuestion();
            }
          }}
          placeholder="问问已入库的数据…"
          rows={1}
        />
        <button type="button" disabled={!input.trim() || loading} onClick={() => void sendQuestion()}>
          发送
        </button>
        {error ? <p className="chat-error" role="alert">{error}</p> : null}
        <p className="chat-hint">Enter 发送 · Shift + Enter 换行 · 回答仅基于检索证据</p>
      </div>
    </section>
  );
}
