import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VideoGraphData, VideoGraphNode } from '../src/api/types';
import { mergeGraphData, parallelEdgeStyles } from '../src/components/video-graph/data';
import { graphLimitsForWidth } from '../src/components/video-graph/limits';
import { VideoGraphPage } from '../src/pages/VideoGraphPage';

vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: ({ graph, onExpand }: {
    graph: VideoGraphData; onExpand: (node: VideoGraphNode) => void;
  }) => <div aria-label="交互画布测试替身">
    <span>{graph.nodes.map((node) => node.label).join(',')}</span>
    <button onClick={() => onExpand(graph.nodes[0])}>展开测试实体</button>
  </div>,
}));

const node = (id: string): VideoGraphNode => ({ id, label: id, kind: 'entity', entity_type: 'technology', content_item_id: null });
const edge = (id: string, source: string, target: string) => ({ id, source, target, predicate: 'uses', kind: 'relation' as const, evidence_count: 1, assertions: { asserted: 1 }, qualifiers: {} });
const base: VideoGraphData = { nodes: [node('PX4'), node('GPS')], edges: [edge('r1', 'PX4', 'GPS')], truncated: false };
const next: VideoGraphData = { nodes: [node('PX4'), node('RTK')], edges: [edge('r2', 'PX4', 'RTK')], truncated: false };
function response(body: unknown) { return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }); }
function mockApi(expansion?: () => Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.endsWith('/overview')) return Promise.resolve(response({ videos: 1, active_videos: 1, entities: 3, relations: 2, evidence: 2, builds: {} }));
    if (url.pathname.endsWith('/graph')) return url.searchParams.has('entity_id')
      ? expansion?.() ?? Promise.resolve(response(next)) : Promise.resolve(response(base));
    return Promise.reject(new Error('Unexpected request'));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
const originalWidth = window.innerWidth;
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth }); });

describe('interactive graph exploration', () => {
  it('retains the opened neighborhood and always uses the interactive view despite old preferences', async () => {
    localStorage.setItem('video-graph-renderer', 'classic');
    const fetchMock = mockApi();
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    expect(await screen.findByText('PX4,GPS,RTK')).toBeInTheDocument();
    const graphRequests = fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost'))
      .filter((url) => url.pathname.endsWith('/graph'));
    expect(graphRequests.length).toBeGreaterThanOrEqual(2);
    expect(graphRequests.every((url) => !url.searchParams.has('full'))).toBe(true);
    expect(graphRequests.every((url) => url.searchParams.get('min_component_size') === '5')).toBe(true);
    expect(graphRequests[0].searchParams.get('max_nodes')).toBe('300');
    expect(graphRequests[0].searchParams.get('max_edges')).toBe('500');
    expect(graphRequests[1].searchParams.get('max_nodes')).toBe('500');
    expect(graphRequests[1].searchParams.get('max_edges')).toBe('1000');
    expect(screen.getByLabelText('交互画布测试替身')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /经典视图/ })).not.toBeInTheDocument();
    expect(document.querySelector('svg.video-graph-canvas')).not.toBeInTheDocument();
  });

  it('discards an expansion response if filters changed while it was loading', async () => {
    let resolve: (value: Response) => void = () => {};
    const pending = new Promise<Response>((r) => { resolve = r; });
    mockApi(() => pending);
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    fireEvent.change(screen.getByLabelText('实体类型'), { target: { value: 'technology' } });
    await screen.findByText('PX4,GPS');
    resolve(response(next));
    await waitFor(() => expect(screen.queryByText('PX4,GPS,RTK')).not.toBeInTheDocument());
  });

  it('enforces graph bounds while avoiding duplicate or dangling edges', () => {
    const merged = mergeGraphData(base, { ...next, nodes: [...next.nodes, node('extra')], edges: [...next.edges, edge('r3', 'RTK', 'extra'), base.edges[0]] }, 3, 2);
    expect(merged.nodes.map((n) => n.id)).toEqual(['PX4', 'GPS', 'RTK']);
    expect(merged.edges.map((e) => e.id)).toEqual(['r1', 'r2']);
    expect(merged.truncated).toBe(true);
  });

  it('retains expanded results within the desktop budget', () => {
    const nodes = Array.from({ length: 450 }, (_, i) => node(`n${i}`));
    const edges = nodes.slice(1).map((n, i) => edge(`e${i}`, nodes[i].id, n.id));
    const merged = mergeGraphData({ nodes: nodes.slice(0, 2), edges: edges.slice(0, 1), truncated: false },
      { nodes, edges: [...edges, edges[0]], truncated: false });
    expect(merged.nodes).toHaveLength(450);
    expect(merged.edges).toHaveLength(449);
    expect(merged.truncated).toBe(false);
  });

  it('does not add isolated expansion nodes when the edge budget is exhausted', () => {
    const merged = mergeGraphData(base, {
      nodes: [node('PX4'), node('RTK'), node('unused')],
      edges: [edge('r2', 'PX4', 'RTK')], truncated: false,
    }, 20, 1);
    expect(merged.nodes).toEqual(base.nodes);
    expect(merged.edges).toEqual(base.edges);
    expect(merged.truncated).toBe(true);
  });

  it('caps desktop expansion and never adds a relation with a missing endpoint', () => {
    const nodes = Array.from({ length: 700 }, (_, i) => node(`n${i}`));
    const edges = nodes.flatMap((n, i) => [edge(`e${i}`, n.id, nodes[(i + 1) % nodes.length].id),
      edge(`f${i}`, n.id, nodes[(i + 2) % nodes.length].id), edge(`g${i}`, n.id, nodes[(i + 3) % nodes.length].id)]);
    const merged = mergeGraphData({ nodes: [], edges: [], truncated: false }, { nodes, edges, truncated: false });
    expect(merged.nodes).toHaveLength(500);
    expect(merged.edges).toHaveLength(1000);
    expect(merged.truncated).toBe(true);
    const ids = new Set(merged.nodes.map((n) => n.id));
    expect(merged.edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true);
    const mobile = graphLimitsForWidth(390);
    const compact = mergeGraphData(base, { nodes, edges, truncated: false }, mobile.maxNodes, mobile.maxEdges);
    expect(compact.nodes).toHaveLength(150);
    expect(compact.edges).toHaveLength(300);
  });

  it('uses mobile limits after crossing the responsive breakpoint', async () => {
    const fetchMock = mockApi();
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    await screen.findByRole('button', { name: '展开测试实体' });
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    fireEvent(window, new Event('resize'));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('max_nodes=150') && String(input).includes('max_edges=300'))).toBe(true));
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    await screen.findByText('PX4,GPS,RTK');
    const last = new URL(String(fetchMock.mock.calls.at(-1)?.[0]), 'http://localhost');
    expect(last.searchParams.get('max_nodes')).toBe('150');
    expect(last.searchParams.get('max_edges')).toBe('300');
    expect(graphLimitsForWidth(680).initialNodes).toBe(150);
    expect(graphLimitsForWidth(681).initialNodes).toBe(300);
  });

  it('keeps parallel, reversed and self-loop relations distinct without merging evidence', () => {
    const edges = [edge('a', 'A', 'B'), edge('b', 'B', 'A'), edge('c', 'A', 'B'),
      edge('loop1', 'A', 'A'), edge('loop2', 'A', 'A'), edge('single', 'B', 'C')];
    const styles = parallelEdgeStyles(edges);
    expect(styles.size).toBe(edges.length);
    const offsets = edges.slice(0, 3).map((e) => styles.get(e.id)!.curveOffset * (e.source > e.target ? -1 : 1));
    expect(new Set(offsets).size).toBe(3);
    expect(styles.get('single')!.curveOffset).toBe(0);
    expect(styles.get('loop1')!.loopDist).not.toBe(styles.get('loop2')!.loopDist);
  });
});
