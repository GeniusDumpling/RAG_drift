import type { EvidenceObject } from '../api/types';
import { safeExternalHref } from '../utils/links';

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) {
    return 'n/a';
  }
  return score.toFixed(3);
}

type EvidenceCardProps = {
  evidence: EvidenceObject;
};

export function EvidenceCard({ evidence }: EvidenceCardProps) {
  const canonicalHref = safeExternalHref(evidence.canonical_url);

  return (
    <article className="card evidence-card">
      <div className="card-header">
        <div>
          <h3>{evidence.title || 'Untitled evidence'}</h3>
          <p className="muted compact">
            {evidence.source_site_name} · {evidence.item_type}
          </p>
        </div>
        <span className="badge">{evidence.matched_by}</span>
      </div>
      <p>{evidence.snippet}</p>
      {evidence.thread_summary ? <p className="muted">Thread: {evidence.thread_summary}</p> : null}
      <dl className="kv-grid">
        <div>
          <dt>Score</dt>
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
          <dd>{evidence.content_item_id}</dd>
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
