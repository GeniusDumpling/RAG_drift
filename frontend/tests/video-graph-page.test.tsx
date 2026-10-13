import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VideoGraphPage } from '../src/pages/VideoGraphPage';

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const entityNodes = [
  { id: 'px4', label: 'PX4', kind: 'entity', entity_type: 'software', content_item_id: null },
  { id: 'gps', label: 'GPS', kind: 'entity', entity_type: 'technology', content_item_id: null },
];
const relation = {
  id: 'relation-1', source: 'px4', target: 'gps', predicate: 'uses', kind: 'relation',
  evidence_count: 1, assertions: { asserted: 1, uncertain: 0, negated: 0 }, qualifiers: {},
};

function mockApi({ error = false, summaryOnly = false } = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/video-graph/overview') return Promise.resolve(response({ videos: 1, active_videos: 1, entities: 2, relations: summaryOnly ? 0 : 1, evidence: 1, builds: {} }));
    if (url.pathname === '/contents') return Promise.resolve(response({ items: [{ id: 'video-1', title: '视频甲', item_type: 'video_description' }], total: 1, limit: 10, offset: 0 }));
    if (url.pathname === '/video-graph/graph') {
      if (error) return Promise.resolve(response({ detail: '视频知识图谱尚未初始化，请执行 alembic upgrade head' }, 503));
      const videoId = url.searchParams.get('content_item_id');
      return Promise.resolve(response({ nodes: videoId ? [{ id: `video:${videoId}`, label: '视频甲', kind: 'video', content_item_id: videoId, entity_type: null }, ...entityNodes] : entityNodes,
        edges: summaryOnly ? [] : [relation], truncated: false }));
    }
    if (url.pathname.endsWith('/status')) return Promise.resolve(response({ status: 'success', withdrawn: false, active_build_id: 'build-1', source_changed: false, attempts: 1, error: null,
      transcript_chunks: summaryOnly ? 0 : 1, summary_only: summaryOnly, assessment: { relevant: true, reason: '无人机技术视频' } }));
    if (url.pathname.endsWith('/evidence')) return Promise.resolve(response({ items: [{ id: 'ev-1', relation_id: relation.id, content_item_id: 'video-1', chunk_id: 'chunk-1', title: '视频甲',
      video_url: 'https://www.youtube.com/watch?v=abc&t=10', quote: 'PX4 uses GPS.', start_seconds: 10, end_seconds: 30,
      assertion: 'asserted', confidence: 0.8, caption_source: 'manual', extractor_version: 'v1' }], total: 1, limit: 20, offset: 0 }));
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('VideoGraphPage', () => {
  it('shows relation quotes, video time links and content navigation', async () => {
    mockApi();
    const openContent = vi.fn();
    render(<VideoGraphPage onOpenContent={openContent} />);
    fireEvent.click(await screen.findByRole('button', { name: /PX4 → GPS/ }));
    expect(await screen.findByText('PX4 uses GPS.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /观看视频 0:10/ })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=abc&t=10');
    fireEvent.click(screen.getByRole('button', { name: '内容详情' }));
    expect(openContent).toHaveBeenCalledWith('video-1');
  });

  it('keeps graph and relation evidence scoped to the selected video', async () => {
    const fetchMock = mockApi();
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '视频甲' }));
    expect(await screen.findByText('处理完成')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /PX4 → GPS/ }));
    await screen.findByText('PX4 uses GPS.');
    const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost'));
    expect(urls.some((url) => url.pathname === '/video-graph/graph' && url.searchParams.get('content_item_id') === 'video-1')).toBe(true);
    expect(urls.some((url) => url.pathname.endsWith('/evidence') && url.searchParams.get('content_item_id') === 'video-1')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'PX4', exact: true }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('entity_id=px4'))).toBe(true));
  });

  it('explains summary-only videos without displaying technical relations', async () => {
    mockApi({ summaryOnly: true });
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '视频甲' }));
    expect(await screen.findByText('仅有摘要，展示实体提及。')).toBeInTheDocument();
    expect(screen.getByText('暂无字幕支持的关系。')).toBeInTheDocument();
  });

  it('surfaces migration errors instead of showing a successful empty graph', async () => {
    mockApi({ error: true });
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('alembic upgrade head');
  });
});
