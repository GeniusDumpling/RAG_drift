import type { EvidenceObject } from '../api/types';
import { itemTypeLabel, safeExternalHref } from '../utils/links';

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return 'n/a';
  }
  return score.toFixed(3);
}

type EvidenceCardProps = {
  evidence: EvidenceObject;
  onOpenContent?: (contentItemId: string) => void;
};

export function EvidenceCard({ evidence, onOpenContent }: EvidenceCardProps) {
  const canonicalHref = safeExternalHref(evidence.canonical_url);
  const videoHref = evidence.video_url ? safeExternalHref(evidence.video_url) : null;

  return (
    <article className="card evidence-card">
      <div className="card-header">
        <div>
          <h3>{evidence.title || '未命名 Evidence'}</h3>
          <p className="muted compact">
            {evidence.source_site_name} · {itemTypeLabel(evidence.item_type)}
          </p>
        </div>
        <span className="badge">{evidence.matched_by}</span>
      </div>
      <p>{evidence.snippet}</p>
      {evidence.thread_summary ? <p className="muted">Thread 线程：{evidence.thread_summary}</p> : null}
      {videoHref ? (
        <video controls preload="metadata" src={videoHref} data-testid="video-evidence" className="video-player">
          当前浏览器不支持视频播放。
        </video>
      ) : null}
      {evidence.description_text ? (
        <details className="video-description">
          <summary>查看完整视频描述</summary>
          <p>{evidence.description_text}</p>
        </details>
      ) : null}
      <dl className="kv-grid">
        <div>
          <dt>评分</dt>
          <dd>{formatScore(evidence.score)}</dd>
        </div>
        <div>
          <dt>Vector</dt>
          <dd>{formatScore(evidence.vector_score)}</dd>
        </div>
        <div>
          <dt>Keyword</dt>
          <dd>{formatScore(evidence.keyword_score)}</dd>
        </div>
        <div>
          <dt>Content item</dt>
          <dd>
            {onOpenContent ? (
              <button
                aria-label={`打开 Content item ${evidence.content_item_id}`}
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
          <dt>Source</dt>
          <dd>{evidence.source_site_id}</dd>
        </div>
        <div>
          <dt>Raw page</dt>
          <dd>{evidence.raw_page_id}</dd>
        </div>
        <div>
          <dt>Chunk</dt>
          <dd>{evidence.chunk_id}</dd>
        </div>
      </dl>
      {canonicalHref ? (
        <a href={canonicalHref} target="_blank" rel="noreferrer">
          {evidence.canonical_url}
        </a>
      ) : (
        <span className="muted">{evidence.canonical_url}</span>
      )}
    </article>
  );
}
