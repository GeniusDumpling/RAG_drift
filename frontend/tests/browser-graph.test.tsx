import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserGraphCanvas } from '../src/components/video-graph/BrowserGraphCanvas';
import type { VideoGraphData } from '../src/api/types';

const graph: VideoGraphData = {
  nodes: ['Alpha', 'Beta', 'Gamma'].map(id => ({ id, label: id, kind: 'entity', entity_type: 'technology', content_item_id: null })),
  edges: [['a', 'Alpha', 'Beta'], ['b', 'Beta', 'Alpha'], ['c', 'Alpha', 'Alpha'], ['d', 'Beta', 'Gamma']].map(([id, source, target]) => ({
    id, source, target, predicate: 'uses', kind: 'relation', evidence_count: 1, assertions: {}, qualifiers: {},
  })), truncated: false,
};
const handlers = () => ({ graph, focusId: '', selectedEdge: '', expandingId: '', onGlobal: vi.fn(), onFocus: vi.fn(), onExpand: vi.fn(async () => {}), onEdge: vi.fn(), onSelect: vi.fn() });
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Browser 2D exploration', () => {
  it('renders each directed relation, reversed relation and self-loop independently with in-circle captions', async () => {
    const { container } = render(<BrowserGraphCanvas {...handlers()} />);
    await screen.findByRole('button', { name: '实体 Alpha' });
    expect(container.querySelectorAll('[data-edge-id]')).toHaveLength(4);
    const types = screen.getByLabelText('图谱类型与样式');
    expect(within(types).queryByText('关系')).not.toBeInTheDocument();
    expect(within(types).queryByRole('button', { name: /使用/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出 SVG' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出 PNG' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('在当前画布查找')).not.toBeInTheDocument();
    expect(within(screen.getByRole('button', { name: '实体 Alpha' })).getByText('Alpha')).toBeInTheDocument();
    const sizes = [...container.querySelectorAll('[data-node-id]')].map(n => Number(n.querySelector('circle')?.getAttribute('r')));
    expect(sizes[0]).toBeGreaterThan(sizes[2]);
  });
  it('selects properties and offers pin/unpin, expand, dismiss and undo without modifying source data', async () => {
    const props = handlers();
    render(<BrowserGraphCanvas {...props} />);
    const node = await screen.findByRole('button', { name: '实体 Alpha' });
    fireEvent.click(node);
    expect(props.onSelect).toHaveBeenCalledWith(graph.nodes[0]);
    fireEvent.click(screen.getByRole('button', { name: '固定位置' }));
    await waitFor(() => expect(node).toHaveAttribute('data-pinned', 'true'));
    fireEvent.click(screen.getByRole('button', { name: '解除固定' }));
    await waitFor(() => expect(node).toHaveAttribute('data-pinned', 'false'));
    fireEvent.click(screen.getByRole('button', { name: '展开邻居' }));
    expect(props.onExpand).toHaveBeenCalledWith(graph.nodes[0]);
    fireEvent.click(screen.getByRole('button', { name: '固定位置' }));
    await waitFor(() => expect(node).toHaveAttribute('data-pinned', 'true'));
    const position = node.getAttribute('transform');
    fireEvent.click(screen.getByRole('button', { name: '隐藏节点' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '实体 Alpha' })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '撤销隐藏' }));
    const restored = await screen.findByRole('button', { name: '实体 Alpha' });
    expect(restored).toHaveAttribute('data-pinned', 'true');
    expect(restored).toHaveAttribute('transform', position);
    expect(graph.nodes).toHaveLength(3); expect(graph.edges).toHaveLength(4);
  });
  it('routes edge selection to evidence and supports dismissing just the edge', async () => {
    const props = handlers();
    const { container } = render(<BrowserGraphCanvas {...props} />);
    await screen.findByRole('button', { name: '实体 Alpha' });
    const edge = container.querySelector('[data-edge-id="a"]')!;
    fireEvent.click(edge);
    expect(props.onEdge).toHaveBeenCalledWith(graph.edges[0]);
    fireEvent.contextMenu(edge);
    fireEvent.click(screen.getByRole('button', { name: '隐藏关系' }));
    expect(container.querySelector('[data-edge-id="a"]')).toBeNull();
    expect(container.querySelectorAll('[data-node-id]')).toHaveLength(3);
  });
  it('places global graph after relayout and invokes the global reset handler', async () => {
    const props = handlers(); render(<BrowserGraphCanvas {...props} />);
    await screen.findByRole('button', { name: '实体 Alpha' });
    const button = screen.getByRole('button', { name: '全局图谱', exact: true });
    expect(button.previousElementSibling).toHaveTextContent('重新布局');
    fireEvent.click(button); expect(props.onGlobal).toHaveBeenCalledOnce();
  });

});
