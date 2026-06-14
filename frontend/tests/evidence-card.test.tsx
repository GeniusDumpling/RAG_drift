import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { EvidenceObject } from '../src/api/types';
import { EvidenceCard } from '../src/components/EvidenceCard';

const evidence: EvidenceObject = {
  chunk_id: 'chunk-1',
  content_item_id: 'content-1',
  raw_page_id: 'raw-1',
  source_site_id: 'source-1',
  title: 'Disable telemetry guide',
  snippet: 'Follow the device settings path to disable telemetry.',
  canonical_url: 'https://example.test/docs/telemetry',
  source_site_name: 'Example Docs',
  author_name: 'Ops Writer',
  published_at: '2025-12-01T00:00:00Z',
  item_type: 'doc_page',
  score: 0.91,
  vector_score: 0.88,
  keyword_score: 0.72,
  matched_by: 'hybrid',
  thread_summary: null,
};

describe('EvidenceCard', () => {
  it('renders a safe canonical URL as an external link', () => {
    render(<EvidenceCard evidence={evidence} />);

    expect(screen.getByRole('link', { name: evidence.canonical_url })).toHaveAttribute('href', evidence.canonical_url);
  });

  it('renders an unsafe canonical URL as plain text instead of a link', () => {
    const unsafeUrl = 'javascript:alert(1)';

    render(<EvidenceCard evidence={{ ...evidence, canonical_url: unsafeUrl }} />);

    expect(screen.queryByRole('link', { name: unsafeUrl })).not.toBeInTheDocument();
    expect(screen.getByText(unsafeUrl)).toBeInTheDocument();
  });
});
