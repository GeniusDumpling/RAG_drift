import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphWorkbench } from '../src/components/video-graph/GraphWorkbench';
import type { VideoGraphData, VideoGraphNode } from '../src/api/types';
import type { GraphAppearance } from '../src/components/video-graph/presentation';

vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: ({ graph, appearance, onSelect, dimension, onUnavailable }: {
    graph: VideoGraphData; appearance: GraphAppearance; onSelect: (node: VideoGraphNode) => void;
    dimension: '2d' | '3d'; onUnavailable: (message: string) => void;
  }) => <div><button onClick={() => onSelect(graph.nodes[0])}>选中 PX4 节点</button>
    <output data-testid="appearance">{JSON.stringify(appearance)}</output><output data-testid="dimension">{dimension}</output>
    <button onClick={() => onUnavailable('3D 暂时不可用，已切换为 2D。')}>模拟三维不可用</button></div>,
}));
const node = (id: string): VideoGraphNode => ({ id, label: id, kind: 'entity', entity_type: 'software', content_item_id: null });
const edges = [
  { id: 'out', source: 'PX4', target: 'GPS', predicate: 'uses', kind: 'relation' as const, evidence_count: 2, assertions: { asserted: 2 }, qualifiers: {} },
  { id: 'in', source: 'RTK', target: 'PX4', predicate: 'compatible_with', kind: 'relation' as const, evidence_count: 1, assertions: { asserted: 1 }, qualifiers: {} },
];
function props() {
  return {
    graph: { nodes: ['PX4', 'GPS', 'RTK'].map(node), edges, truncated: false },
    overview: { videos: 10, active_videos: 6, entities: 100, relations: 120, evidence: 300, builds: {} },
    query: '', onQuery: vi.fn(), onSearch: vi.fn(), entityType: '', onEntityType: vi.fn(),
    assertion: '', onAssertion: vi.fn(), hops: 1, onHops: vi.fn(), focusId: '', onClearFocus: vi.fn(),
    selectedEdge: null, onEdge: vi.fn(), onClearEdge: vi.fn(), onFocus: vi.fn(),
    onExpand: vi.fn(async () => {}), expandingId: '', loading: false, error: '', expansionError: '',
    onRefresh: vi.fn(), evidencePanel: <blockquote>PX4 uses GPS.</blockquote>,
  };
}
const originalWidth = window.innerWidth;
beforeEach(() => { localStorage.clear(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 }); });
afterEach(() => { cleanup(); localStorage.clear(); Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth }); });

describe('Obsidian-style graph workbench', () => {
  it('separates incoming and outgoing links and opens the selected relation evidence', () => {
    const handlers = props();
    const { container, rerender } = render(<GraphWorkbench {...handlers} />);
    fireEvent.click(screen.getByRole('button', { name: '选中 PX4 节点' }));
    expect(container.querySelector('.ow-link-counters')).toHaveTextContent('1入链1出链2邻居');
    fireEvent.click(screen.getByRole('button', { name: '入链', exact: true }));
    const links = container.querySelector('.ow-related-links') as HTMLElement;
    expect(within(links).getByRole('button', { name: /RTK/ })).toBeInTheDocument();
    expect(within(links).queryByRole('button', { name: /GPS/ })).not.toBeInTheDocument();
    fireEvent.click(within(links).getByRole('button', { name: /RTK/ }));
    expect(handlers.onEdge).toHaveBeenCalledWith(edges[1]);
    rerender(<GraphWorkbench {...handlers} selectedEdge={edges[1]} />);
    expect(screen.getByText('PX4 uses GPS.')).toBeInTheDocument();
  });

  it('applies display controls and distinguishes global totals from the displayed subgraph', () => {
    render(<GraphWorkbench {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: '2D', exact: true }));
    expect(screen.getByLabelText('全局有效图谱统计')).toHaveTextContent('实体100');
    expect(document.querySelector('.ow-statusbar')).toHaveTextContent('3 个节点');
    fireEvent.click(screen.getByLabelText('常显实体名称'));
    fireEvent.click(screen.getByLabelText('显示关系方向'));
    fireEvent.change(screen.getByRole('slider', { name: /节点大小/ }), { target: { value: '1.7' } });
    expect(JSON.parse(screen.getByTestId('appearance').textContent ?? '{}')).toMatchObject({ showLabels: false, showArrows: true, nodeScale: 1.7 });
  });

  it('toggles the data panel without creating separate page navigation', () => {
    const handlers = props();
    render(<GraphWorkbench {...handlers} />);
    fireEvent.click(screen.getByRole('button', { name: '收起数据面板' }));
    expect(screen.getByLabelText('图谱数据面板')).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '打开数据面板' }));
    expect(screen.getByLabelText('图谱数据面板')).toBeVisible();
    expect(screen.queryByRole('navigation', { name: '图谱顶部导航' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /经典视图/ })).not.toBeInTheDocument();
  });

  it('switches dimensions while retaining selection, data and separate label settings', () => {
    const handlers = props();
    render(<GraphWorkbench {...handlers} />);
    expect(screen.getByTestId('dimension')).toHaveTextContent('3d');
    expect(screen.getByLabelText('常显实体名称')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '选中 PX4 节点' }));
    fireEvent.click(screen.getByRole('button', { name: '2D', exact: true }));
    expect(screen.getByTestId('dimension')).toHaveTextContent('2d');
    expect(document.querySelector('.ow-entity-details')).toHaveTextContent('PX4');
    expect(document.querySelector('.ow-statusbar')).toHaveTextContent('3 个节点');
    fireEvent.click(screen.getByRole('tab', { name: '数据总览' }));
    expect(screen.getByLabelText('常显实体名称')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '3D', exact: true }));
    expect(screen.getByLabelText('常显实体名称')).not.toBeChecked();
    expect(handlers.onRefresh).not.toHaveBeenCalled();
    expect(handlers.onClearFocus).not.toHaveBeenCalled();
  });

  it('starts mobile in 2D and remembers an explicitly chosen dimension', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    const view = render(<GraphWorkbench {...props()} />);
    expect(screen.getByTestId('dimension')).toHaveTextContent('2d');
    fireEvent.click(screen.getByRole('button', { name: '3D', exact: true }));
    view.unmount();
    render(<GraphWorkbench {...props()} />);
    expect(screen.getByTestId('dimension')).toHaveTextContent('3d');
    expect(screen.getByRole('button', { name: '3D', exact: true })).toHaveAttribute('aria-pressed', 'true');
  });

  it('falls back to the existing 2D canvas when 3D cannot initialize', () => {
    render(<GraphWorkbench {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: '模拟三维不可用' }));
    expect(screen.getByTestId('dimension')).toHaveTextContent('2d');
    expect(screen.getByText('3D 暂时不可用，已切换为 2D。')).toHaveAttribute('role', 'status');
    expect(document.querySelector('.ow-statusbar')).toHaveTextContent('3 个节点');
  });
});
