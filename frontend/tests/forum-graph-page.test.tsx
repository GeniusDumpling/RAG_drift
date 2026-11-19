import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { VideoGraphPage } from '../src/pages/VideoGraphPage';
import type { VideoGraphData, VideoGraphEdge } from '../src/api/types';

vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: ({ graph, onEdge }: { graph: VideoGraphData; onEdge: (edge: VideoGraphEdge) => void }) => (
    <div>{graph.nodes.map(n => <span key={n.id}>{n.label}</span>)}
      {graph.edges.map(e => <button key={e.id} onClick={() => onEdge(e)}>选择论坛事实</button>)}
    </div>
  ),
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

function mockApi(empty = false) {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    calls.push(url.pathname);
    let data: unknown;
    if (url.pathname === '/forum-graph/overview') data = { topics: empty ? 0 : 1, active_topics: empty ? 0 : 1,
      entities: 1, facts: 1, evidence: 1, builds: {}, unavailable_topics: 0 };
    else if (url.pathname === '/forum-graph/graph') {
      expect(url.searchParams.get('min_component_size')).toBe('0');
      data = { nodes: empty ? [] : [
        { id: 'device', label: 'PX4', kind: 'entity', entity_type: 'software', content_item_id: null },
        { id: 'fact:case', label: 'GPS drift', kind: 'fact', entity_type: 'state', content_item_id: null },
      ], edges: empty ? [] : [{ id: 'case', source: 'device', target: 'fact:case', predicate: 'indicates', kind: 'relation',
        evidence_count: 1, assertions: { asserted: 1 }, qualifiers: {} }], truncated: false };
    } else if (url.pathname === '/forum-graph/facts/case/evidence') data = { items: [{
      id: 'proof', fact_id: 'case', content_item_id: 'topic-id', title: 'GPS issue', quote: 'GPS drift',
      post_url: 'https://discuss.px4.io/t/7/2', post_id: 102, post_number: 2, author: 'reporter',
      role: 'claim', act: 'observation', assertion: 'asserted', qualifiers: { 'context:firmware': '1.14' },
      evaluations: [], accepted_answer: false,
    }], total: 1, offset: 0, limit: 20 };
    else return Promise.reject(new Error(`Unexpected ${url}`));
    return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  }));
  return calls;
}

describe('forum graph page', () => {
  it('uses forum endpoints and preserves small case graphs and exact floor citations', async () => {
    const calls = mockApi();
    const open = vi.fn();
    render(<VideoGraphPage source="forum" onOpenContent={open} />);
    fireEvent.click(await screen.findByRole('button', { name: '选择论坛事实' }));
    expect(await screen.findByText(/reporter · 第 2 楼/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看第 2 楼原帖' })).toHaveAttribute('href', 'https://discuss.px4.io/t/7/2');
    expect(screen.queryByText(/字幕来源/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '主题详情' }));
    expect(open).toHaveBeenCalledWith('topic-id');
    expect(calls.every(path => path.startsWith('/forum-graph/'))).toBe(true);
  });
  it('shows an explicit empty state before building forum topics', async () => {
    mockApi(true);
    render(<VideoGraphPage source="forum" onOpenContent={() => undefined} />);
    expect(await screen.findByText(/论坛图谱尚无可展示数据/)).toBeInTheDocument();
  });
});
