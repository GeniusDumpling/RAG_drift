import type { EvidenceObject } from '../api/types';
import { itemTypeLabel, safeExternalHref } from '../utils/links';

const MAX_SNIPPET_LENGTH = 300;

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return 'n/a';
  }
  return score.toFixed(3);
}

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) {
    return text;
  }
  return text.slice(0, maxLen) + '…';
}

type EvidenceCardProps = {
  evidence: EvidenceObject;
  onOpenContent?: (contentItemId: string) => void;
};

export function EvidenceCard({ evidence, onOpenContent }: EvidenceCardProps) {
  const canonicalHref = safeExternalHref(evidence.canonical_url);
  const videoHref = evidence.video_url ? safeExternalHref(evidence.video_url) : null;
  const hasLongSnippet = evidence.snippet.length > MAX_SNIPPET_LENGTH;

  return (
    <article className="card evidence-card">
      <div className="card-header">
        <div>
          <h3>{evidence.title || '未命名内容'}</h3>
          <p className="muted compact">
            {evidence.source_site_name} · {itemTypeLabel(evidence.item_type)}
          </p>
        </div>
        <span className="badge">{evidence.matched_by}</span>
      </div>

      <p className="evidence-snippet">
        {hasLongSnippet ? (
          <>
            {truncate(evidence.snippet, MAX_SNIPPET_LENGTH)}
            <details className="snippet-expand">
              <summary>查看完整文本</summary>
              <p>{evidence.snippet}</p>
            </details>
          </>
        ) : (
          evidence.snippet
        )}
      </p>

      {evidence.thread_summary ? <p className="muted evidence-thread">帖子摘要：{evidence.thread_summary}</p> : null}

      {videoHref ? (
        <div className="video-wrapper">
          <video controls preload="metadata" src={videoHref} data-testid="video-evidence" className="video-player">
            当前浏览器不支持视频播放。
          </video>
        </div>
      ) : null}

      {evidence.description_text ? (
        <details className="video-description">
          <summary>查看完整视频描述</summary>
          <p className="video-description-text">{evidence.description_text}</p>
        </details>
      ) : null}

      <dl className="kv-grid">
        <div>
          <dt>评分</dt>
          <dd>{formatScore(evidence.score)}</dd>
        </div>
        <div>
          <dt>向量</dt>
          <dd>{formatScore(evidence.vector_score)}</dd>
        </div>
        <div>
          <dt>关键词</dt>
          <dd>{formatScore(evidence.keyword_score)}</dd>
        </div>
        <div>
          <dt>内容</dt>
          <dd>
            {onOpenContent ? (
              <button
                aria-label={`打开内容 ${evidence.content_item_id}`}
                className="link-button"
                type="button"
                onClick={() => onOpenContent(evidence.content_item_id)}
              >
                {evidence.content_item_id}
              </button>
            ) : (
              evidence.content_item_id
            )}
          </dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{evidence.source_site_id}</dd>
        </div>
        <div>
          <dt>原始页</dt>
          <dd>{evidence.raw_page_id}</dd>
        </div>
        <div>
          <dt>分块</dt>
          <dd>{evidence.chunk_id}</dd>
        </div>
      </dl>

      {canonicalHref ? (
        <a href={canonicalHref} target="_blank" rel="noreferrer" className="evidence-link">
          {evidence.canonical_url}
        </a>
      ) : (
        <span className="muted">{evidence.canonical_url}</span>
      )}
    </article>
  );
}
