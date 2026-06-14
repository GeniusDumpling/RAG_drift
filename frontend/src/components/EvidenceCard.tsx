import type { EvidenceObject } from '../api/types';

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
          <dt>Raw page</dt>
          <dd>{evidence.raw_page_id}</dd>
        </div>
      </dl>
      <a href={evidence.canonical_url} target="_blank" rel="noreferrer">
        {evidence.canonical_url}
      </a>
    </article>
  );
}
