import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { VideoGraphPage } from '../src/pages/VideoGraphPage';
import type { VideoGraphData, VideoGraphNode, VideoGraphEdge } from '../src/api/types';

vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: ({ graph, onFocus, onEdge }: {
    graph: VideoGraphData; onFocus: (node: VideoGraphNode) => void; onEdge: (edge: VideoGraphEdge) => void;
  }) => <div aria-label="交互画布测试替身">
    {graph.nodes.map((node) => <button key={node.id} onClick={() => onFocus(node)}>{node.label}</button>)}
    {graph.edges.map((edge) => <button key={edge.id} onClick={() => onEdge(edge)}>
      {graph.nodes.find((n) => n.id === edge.source)?.label} → {graph.nodes.find((n) => n.id === edge.target)?.label}
    </button>)}
  </div>,
}));

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
    if (url.pathname === '/video-graph/overview') return Promise.resolve(response({ videos: 1, active_videos: 1, entities: 2, relations: summaryOnly ? 0 : 1, evidence: 1, builds: { partial_success: 1 }, partial_videos: 1,
      chunk_coverage: { videos: 1, total_chunks: 10, passed_chunks: 8, skipped_chunks: 2, pending_chunks: 0 } }));
    if (url.pathname === '/video-graph/graph') {
      if (error) return Promise.resolve(response({ detail: '视频知识图谱尚未初始化，请执行 alembic upgrade head' }, 503));
      return Promise.resolve(response({ nodes: summaryOnly ? [] : entityNodes,
        edges: summaryOnly ? [] : [relation], truncated: false }));
    }
    if (url.pathname.endsWith('/evidence')) return Promise.resolve(response({ items: [{ id: 'ev-1', relation_id: relation.id, content_item_id: 'video-1', chunk_id: 'chunk-1', title: '视频甲',
      video_url: 'https://www.youtube.com/watch?v=abc&t=10', quote: 'PX4 uses GPS.', start_seconds: 10, end_seconds: 30,
      assertion: 'asserted', confidence: 0.8, caption_source: 'manual', extractor_version: 'v1' }], total: 1, limit: 20, offset: 0 }));
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const originalWidth = window.innerWidth;
beforeEach(() => { localStorage.setItem('video-graph-renderer', 'classic'); Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 }); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth }); });

describe('VideoGraphPage', () => {
  it('shows relation quotes, video time links and content navigation', async () => {
    mockApi();
    const openContent = vi.fn();
    render(<VideoGraphPage onOpenContent={openContent} />);
    expect(await screen.findByText('1 部分完成')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /PX4 → GPS/ }));
    expect(await screen.findByText('PX4 uses GPS.')).toBeInTheDocument();
    expect(screen.getByLabelText('交互画布测试替身')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /经典视图/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /观看视频 0:10/ })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=abc&t=10');
    fireEvent.click(screen.getByRole('button', { name: '内容详情' }));
    expect(openContent).toHaveBeenCalledWith('video-1');
  });

  it('browses the graph and evidence across all videos while allowing entity focus', async () => {
    const fetchMock = mockApi();
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: /PX4 → GPS/ }));
    await screen.findByText('PX4 uses GPS.');
    const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost'));
    expect(screen.queryByRole('complementary', { name: '按视频浏览' })).not.toBeInTheDocument();
    expect(urls.every((url) => !url.searchParams.has('content_item_id'))).toBe(true);
    expect(urls.some((url) => url.pathname === '/contents' || url.pathname.endsWith('/status'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'PX4', exact: true }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('entity_id=px4'))).toBe(true));
    fireEvent.click(screen.getByRole('tab', { name: '数据总览' }));
    fireEvent.click(screen.getByRole('button', { name: '取消实体聚焦' }));
    await waitFor(() => {
      const last = new URL(String(fetchMock.mock.calls.at(-1)?.[0]), 'http://localhost');
      expect(last.pathname).toBe('/video-graph/graph');
      expect(last.searchParams.has('entity_id')).toBe(false);
      expect(last.searchParams.has('content_item_id')).toBe(false);
    });
  });

  it('explains when only isolated entities or tiny subgraphs match the filters', async () => {
    mockApi({ summaryOnly: true });
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    expect(await screen.findByText('没有符合展示条件的图谱')).toBeInTheDocument();
    expect(screen.getByText(/当前范围内未找到至少 5 个节点/)).toBeInTheDocument();
    expect(screen.queryByLabelText('交互画布测试替身')).not.toBeInTheDocument();
  });

  it('surfaces migration errors instead of showing a successful empty graph', async () => {
    mockApi({ error: true });
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('alembic upgrade head');
  });
});
