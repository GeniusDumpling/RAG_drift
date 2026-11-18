import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { VideoGraphData, VideoGraphNode } from '../src/api/types';
import { largestGraphComponent, mergeGraphData, parallelEdgeStyles } from '../src/components/video-graph/data';
import { graphLimitsForWidth } from '../src/components/video-graph/limits';
import { VideoGraphPage } from '../src/pages/VideoGraphPage';

vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: ({ graph, onExpand }: {
    graph: VideoGraphData; onExpand: (node: VideoGraphNode) => void;
  }) => <div aria-label="交互画布测试替身">
    <span>{graph.nodes.map((node) => node.label).join(',')}</span>
    <output data-testid="graph-data">{JSON.stringify(graph)}</output>
    <button onClick={() => onExpand(graph.nodes[0])}>展开测试实体</button>
    {graph.nodes.some(node => node.id === 'RTK') ? <button onClick={() => onExpand(graph.nodes.find(node => node.id === 'RTK')!)}>展开 RTK 分支</button> : null}
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
    expect(graphRequests[0].searchParams.get('min_component_size')).toBe('5');
    expect(graphRequests[1].searchParams.get('min_component_size')).toBe('0');
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

  it('retains all category seeds and cross-type neighbors when expanding and collapsing beyond the global budget', async () => {
    const products = Array.from({ length: 605 }, (_, i) => ({ ...node(`p${i}`), entity_type: 'product' }));
    const category: VideoGraphData = { nodes: [...products, node('RTK')],
      edges: [...products.slice(1).map((n, i) => edge(`p${i}`, products[i].id, n.id)), edge('root', 'p0', 'RTK')], truncated: false };
    const expanded: VideoGraphData = { nodes: [products[0], node('GPS')], edges: [edge('added', 'p0', 'GPS')], truncated: false };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      return Promise.resolve(response(url.pathname.endsWith('/overview') ? { builds: {} }
        : url.searchParams.has('entity_id') ? expanded : url.searchParams.has('entity_type') ? category : base));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    await screen.findByRole('button', { name: '展开测试实体' });
    fireEvent.change(screen.getByLabelText('实体类型'), { target: { value: 'product' } });
    const actual = () => JSON.parse(screen.getByTestId('graph-data').textContent ?? '{}') as VideoGraphData;
    await waitFor(() => expect(actual().nodes).toHaveLength(606));
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    await waitFor(() => expect(actual().nodes).toHaveLength(607));
    expect(actual().nodes.filter(n => n.entity_type === 'product')).toHaveLength(605);
    expect(actual().truncated).toBe(false);
    const last = new URL(String(fetchMock.mock.calls.at(-1)?.[0]), 'http://localhost');
    expect(last.searchParams.get('full')).toBe('true');
    expect(last.searchParams.get('hops')).toBe('1');
    expect(last.searchParams.has('entity_type')).toBe(false);
    expect(last.searchParams.has('assertion')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    await waitFor(() => expect(actual().nodes).toHaveLength(606));
    expect(actual().nodes.filter(n => n.entity_type === 'product')).toHaveLength(605);
  });

  it('collapses a neighborhood back to the base graph without requesting data again', async () => {
    const fetchMock = mockApi();
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    expect(await screen.findByText('PX4,GPS,RTK')).toBeInTheDocument();
    const requests = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    expect(await screen.findByText('PX4,GPS')).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(requests);
  });

  it('keeps another expanded branch and all base relationships when collapsing its parent', async () => {
    const branch: VideoGraphData = { nodes: [node('RTK'), node('GPS'), node('Lidar')],
      edges: [edge('r3', 'RTK', 'GPS'), edge('r4', 'RTK', 'Lidar')], truncated: false };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      return Promise.resolve(response(url.pathname.endsWith('/overview') ? { builds: {} }
        : url.searchParams.get('entity_id') === 'RTK' ? branch : url.searchParams.has('entity_id') ? next : base));
    }));
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    fireEvent.click(await screen.findByRole('button', { name: '展开 RTK 分支' }));
    await screen.findByText('PX4,GPS,RTK,Lidar');
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    await waitFor(() => {
      const actual = JSON.parse(screen.getByTestId('graph-data').textContent ?? '{}') as VideoGraphData;
      expect(actual.edges.map(edge => edge.id)).toEqual(['r1', 'r3', 'r4']);
      expect(actual.nodes.map(node => node.id)).toEqual(['PX4', 'GPS', 'RTK', 'Lidar']);
    });
  });

  it('enforces graph bounds while avoiding duplicate or dangling edges', () => {
    const merged = mergeGraphData(base, { ...next, nodes: [...next.nodes, node('extra')], edges: [...next.edges, edge('r3', 'RTK', 'extra'), base.edges[0]] }, 3, 2);
    expect(merged.nodes.map((n) => n.id)).toEqual(['PX4', 'GPS', 'RTK']);
    expect(merged.edges.map((e) => e.id)).toEqual(['r1', 'r2']);
    expect(merged.truncated).toBe(true);
  });

  it('hides detached twenty-node branches after collapse and restores them when reconnected', async () => {
    const mainNodes = ['PX4', 'GPS', ...Array.from({ length: 28 }, (_, i) => `main${i}`)].map(node);
    const main: VideoGraphData = { nodes: mainNodes,
      edges: mainNodes.slice(1).map((n, i) => edge(`main${i}`, mainNodes[i].id, n.id)), truncated: false };
    const branchNodes = ['RTK', ...Array.from({ length: 19 }, (_, i) => `branch${i}`)].map(node);
    const branch: VideoGraphData = { nodes: branchNodes,
      edges: branchNodes.slice(1).map((n, i) => edge(`branch${i}`, 'RTK', n.id)), truncated: false };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      return Promise.resolve(response(url.pathname.endsWith('/overview') ? { builds: {} }
        : url.searchParams.get('entity_id') === 'RTK' ? branch : url.searchParams.has('entity_id') ? next : main));
    }));
    render(<VideoGraphPage onOpenContent={vi.fn()} />);
    const actual = () => JSON.parse(screen.getByTestId('graph-data').textContent ?? '{}') as VideoGraphData;
    fireEvent.click(await screen.findByRole('button', { name: '展开测试实体' }));
    fireEvent.click(await screen.findByRole('button', { name: '展开 RTK 分支' }));
    await waitFor(() => expect(actual().nodes).toHaveLength(50));
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    await waitFor(() => expect(actual().nodes).toHaveLength(30));
    expect(actual().nodes.some(n => n.id === 'RTK')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '展开测试实体' }));
    await waitFor(() => expect(actual().nodes).toHaveLength(50));
  });

  it('retains the largest component by nodes and resolves ties independently of array order', () => {
    const chain = (prefix: string, count: number): VideoGraphData => {
      const nodes = Array.from({ length: count }, (_, i) => node(`${prefix}${i}`));
      return { nodes, edges: nodes.slice(1).map((n, i) => edge(`${prefix}e${i}`, nodes[i].id, n.id)), truncated: false };
    };
    const main = chain('main', 40), twenty = chain('small', 20), fifteen = chain('other', 15);
    const mixed = { nodes: [...fifteen.nodes, ...twenty.nodes, ...main.nodes],
      edges: [...fifteen.edges, ...twenty.edges, ...main.edges], truncated: false };
    const original = JSON.stringify(mixed);
    expect(largestGraphComponent(mixed).nodes).toEqual(main.nodes);
    expect(largestGraphComponent(mixed).edges).toEqual(main.edges);
    expect(JSON.stringify(mixed)).toBe(original);
    const a = chain('a', 20), b = chain('b', 20);
    const tied = { nodes: [...b.nodes, ...a.nodes], edges: [...b.edges, ...a.edges], truncated: false };
    expect(largestGraphComponent(tied).nodes).toEqual(a.nodes);
    tied.nodes.reverse(); tied.edges.reverse();
    expect(new Set(largestGraphComponent(tied).nodes.map(n => n.id))).toEqual(new Set(a.nodes.map(n => n.id)));
    expect(largestGraphComponent({ nodes: [], edges: [], truncated: false }).nodes).toEqual([]);
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
