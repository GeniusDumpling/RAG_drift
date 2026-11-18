import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { VideoGraphData, VideoGraphEdge, VideoGraphNode } from '../src/api/types';
import { BrowserGraphCanvas } from '../src/components/video-graph/BrowserGraphCanvas';
import { BrowserMotion, readableEdgeLabel } from '../src/components/video-graph/browser-motion';
import { filterGraphByLens, findShortestKnowledgePath } from '../src/components/video-graph/data';
import { GraphWorkbench } from '../src/components/video-graph/GraphWorkbench';
import { classifyEdgeSemantics, DEFAULT_GRAPH_APPEARANCE, formatEdgeCaption } from '../src/components/video-graph/presentation';

vi.mock('../src/api/client', () => ({
  searchVideoGraphEntities: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })),
}));

const makeNode = (id: string, label: string, entity_type: string): VideoGraphNode => ({
  id, label, kind: 'entity', entity_type, content_item_id: null,
});

const makeEdge = (
  id: string,
  source: string,
  target: string,
  predicate: string,
  extra: Partial<VideoGraphEdge> = {},
): VideoGraphEdge => ({
  id,
  source,
  target,
  predicate,
  kind: 'relation',
  evidence_count: 2,
  assertions: { asserted: 2 },
  qualifiers: {},
  ...extra,
});

const richGraph: VideoGraphData = {
  nodes: [
    makeNode('dji', '大疆', 'organization'),
    makeNode('mavic', 'Mavic 3', 'product'),
    makeNode('gimbal', '三轴云台', 'component'),
    makeNode('drift', '云台漂移', 'fault'),
    makeNode('calib', 'IMU 校准', 'method'),
    makeNode('ocusync', 'OcuSync 3', 'protocol'),
  ],
  edges: [
    makeEdge('e-brand', 'mavic', 'dji', 'offered_by'),
    makeEdge('e-comp', 'mavic', 'gimbal', 'contains'),
    makeEdge('e-fault', 'gimbal', 'drift', 'reports_fault', {
      assertions: { asserted: 2, negated: 1 },
      qualifiers: { firmware: 'v01.00.09' },
      evidence_count: 3,
    }),
    makeEdge('e-mitigate', 'calib', 'drift', 'mitigates', {
      assertions: { uncertain: 1 },
      evidence_count: 1,
    }),
    makeEdge('e-proto', 'mavic', 'ocusync', 'uses', {
      assertions: { negated: 2 },
      evidence_count: 2,
    }),
  ],
  truncated: false,
};

const originalWidth = window.innerWidth;
beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
});

describe('knowledge graph semantic organization helpers', () => {
  it('classifies edge assertions and formats semantic captions with conditions', () => {
    const disputed = classifyEdgeSemantics(richGraph.edges[2]);
    expect(disputed.status).toBe('disputed');
    expect(disputed.statusLabel).toBe('存在争议');
    expect(disputed.qualifierCount).toBe(1);
    expect(formatEdgeCaption('报告故障', disputed)).toBe('报告故障 [争议·条件×1]');

    const uncertain = classifyEdgeSemantics(richGraph.edges[3]);
    expect(uncertain.status).toBe('uncertain');
    expect(formatEdgeCaption('缓解', uncertain)).toBe('缓解 [推测]');

    const negated = classifyEdgeSemantics(richGraph.edges[4]);
    expect(negated.status).toBe('negated');
    expect(formatEdgeCaption('使用', negated)).toBe('使用 [否定]');
  });

  it('filters graphs by knowledge lens and finds multi-hop bridge paths', () => {
    const diagnosis = filterGraphByLens(richGraph, 'diagnosis');
    expect(diagnosis.edges.map((e) => e.id)).toEqual(['e-fault', 'e-mitigate']);
    expect(diagnosis.nodes.map((n) => n.id)).toEqual(['gimbal', 'drift', 'calib']);

    const lineage = filterGraphByLens(richGraph, 'lineage');
    expect(lineage.edges.map((e) => e.id)).toEqual(['e-brand', 'e-comp']);

    const path = findShortestKnowledgePath(richGraph, 'mavic', 'calib');
    expect(path).not.toBeNull();
    expect(path!.nodeIds).toEqual(['mavic', 'gimbal', 'drift', 'calib']);
    expect(path!.steps.map((step) => step.direction)).toEqual(['out', 'out', 'in']);
  });

  it('shows edge predicate labels at readable zoom when enabled and hides them when disabled or zoomed out', () => {
    const ns = 'http://www.w3.org/2000/svg';
    const world = document.createElementNS(ns, 'g');
    const edgeEl = document.createElementNS(ns, 'g');
    world.append(edgeEl);
    const a = { id: 'a', x: 0, y: 0, radius: 22 };
    const b = { id: 'b', x: 240, y: 0, radius: 22 };
    expect(readableEdgeLabel(a, b, 1)).toBe(true);
    expect(readableEdgeLabel(a, b, 0.2)).toBe(false);

    const motion = new BrowserMotion(world);
    motion.edges = [{ source: a, target: b, element: edgeEl, label: '报告故障', offset: 0, loop: 0, emphasized: false, showLabel: true }];
    motion.paint({ x: 0, y: 0, k: 1 }, true);
    expect(edgeEl.querySelector('text')?.textContent).toBe('报告故障');

    motion.edges[0].showLabel = false;
    motion.paint({ x: 0, y: 0, k: 1 }, false);
    expect(edgeEl.querySelector('text')).toBeNull();
  });
});

describe('2D canvas semantic encoding and layered organization', () => {
  it('renders assertion status styles, layered ontology lanes and path highlighting', async () => {
    const { container, rerender } = render(
      <BrowserGraphCanvas
        graph={richGraph}
        focusId=""
        selectedEdge=""
        expandingId=""
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        onEdge={vi.fn()}
        onGlobal={vi.fn()}
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
        highlightedPath={{ nodeIds: new Set(['mavic', 'gimbal', 'drift']), edgeIds: new Set(['e-comp', 'e-fault']) }}
      />,
    );

    await screen.findByRole('button', { name: '实体 大疆' });
    const lanes = [...container.querySelectorAll('.browser-layer-lane')].map((el) => el.textContent);
    expect(lanes).toEqual(['来源与品牌', '产品主体', '部件与技术', '故障与对策']);

    const faultEdge = container.querySelector('[data-edge-id="e-fault"]')!;
    expect(faultEdge).toHaveAttribute('data-assertion-status', 'disputed');
    expect(faultEdge).toHaveAttribute('data-qualifier-count', '1');
    expect(faultEdge).toHaveAttribute('opacity', '1');

    const brandEdge = container.querySelector('[data-edge-id="e-brand"]')!;
    expect(brandEdge).toHaveAttribute('opacity', '0.14');

    const mitigatePath = container.querySelector('[data-edge-id="e-mitigate"] path:not([stroke="transparent"])')!;
    expect(mitigatePath.getAttribute('stroke-dasharray')).toBe('6 4');

    rerender(
      <BrowserGraphCanvas
        graph={richGraph}
        focusId=""
        selectedEdge=""
        expandingId=""
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        onEdge={vi.fn()}
        onGlobal={vi.fn()}
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'force' }}
      />,
    );
    expect(container.querySelectorAll('.browser-layer-lane')).toHaveLength(0);
  });

  it('spreads dense layered graphs into wide multi-column bands with generous inter-layer spacing', async () => {
    const types = ['organization', 'product', 'component', 'fault'];
    const denseNodes = Array.from({ length: 120 }, (_, i) => makeNode(`n${i}`, `节点 ${i}`, types[i % 4]));
    const denseEdges = denseNodes.slice(1).map((n, i) => makeEdge(`e${i}`, denseNodes[i].id, n.id, 'contains'));
    const { container } = render(
      <BrowserGraphCanvas
        graph={{ nodes: denseNodes, edges: denseEdges, truncated: false }}
        focusId=""
        selectedEdge=""
        expandingId=""
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        onEdge={vi.fn()}
        onGlobal={vi.fn()}
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    await screen.findByRole('button', { name: '实体 节点 0' });
    const laneRects = [...container.querySelectorAll('.browser-layer-lane rect:first-child')].map((el) => ({
      x: Number(el.getAttribute('x')),
      y: Number(el.getAttribute('y')),
      width: Number(el.getAttribute('width')),
      height: Number(el.getAttribute('height')),
    }));
    expect(laneRects).toHaveLength(4);
    for (let i = 1; i < laneRects.length; i++) {
      const gap = laneRects[i].x - (laneRects[i - 1].x + laneRects[i - 1].width);
      expect(gap).toBeGreaterThanOrEqual(350);
    }
    const totalWidth = laneRects[3].x + laneRects[3].width - laneRects[0].x;
    expect(totalWidth).toBeGreaterThan(laneRects[0].height);

    // Every node circle must remain completely inside its corresponding ontology layer box.
    for (let i = 0; i < denseNodes.length; i++) {
      const nodeEl = container.querySelector(`[data-node-id="n${i}"]`)!;
      const match = /translate\(([-\d.]+),([-\d.]+)\)/.exec(nodeEl.getAttribute('transform') ?? '');
      expect(match).not.toBeNull();
      const cx = Number(match![1]);
      const cy = Number(match![2]);
      const r = Number(nodeEl.querySelector('circle:not([data-pin])')?.getAttribute('r'));
      const box = laneRects[i % 4];
      expect(cx - r).toBeGreaterThanOrEqual(box.x);
      expect(cx + r).toBeLessThanOrEqual(box.x + box.width);
      expect(cy - r).toBeGreaterThanOrEqual(box.y);
      expect(cy + r).toBeLessThanOrEqual(box.y + box.height);
    }
  });

  it('preserves surviving node positions when switching knowledge lenses in layered mode so nodes migrate smoothly', async () => {
    const { rerender } = render(
      <BrowserGraphCanvas
        graph={richGraph}
        focusId=""
        selectedEdge=""
        expandingId=""
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        onEdge={vi.fn()}
        onGlobal={vi.fn()}
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    const driftNode = await screen.findByRole('button', { name: '实体 云台漂移' });
    const beforeTransform = driftNode.getAttribute('transform');
    expect(beforeTransform).toBeTruthy();

    rerender(
      <BrowserGraphCanvas
        graph={filterGraphByLens(richGraph, 'diagnosis')}
        focusId=""
        selectedEdge=""
        expandingId=""
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        onEdge={vi.fn()}
        onGlobal={vi.fn()}
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    const afterDriftNode = screen.getByRole('button', { name: '实体 云台漂移' });
    expect(afterDriftNode.getAttribute('transform')).toBe(beforeTransform);
  });
});

describe('GraphWorkbench knowledge lens, schema map and path exploration', () => {
  it('switches knowledge lenses, layout mode, schema filters and explores cross-entity paths', async () => {
    const onEntityType = vi.fn();
    const onEdge = vi.fn();
    const { container, rerender } = render(
      <GraphWorkbench
        graph={richGraph}
        overview={{ videos: 5, active_videos: 4, entities: 6, relations: 5, evidence: 10, builds: {} }}
        query=""
        onQuery={vi.fn()}
        onSearch={vi.fn()}
        entityType=""
        onEntityType={onEntityType}
        hops={1}
        onHops={vi.fn()}
        focusId=""
        onClearFocus={vi.fn()}
        selectedEdge={null}
        onEdge={onEdge}
        onClearEdge={vi.fn()}
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        expandingId=""
        loading={false}
        error=""
        expansionError=""
        onRefresh={vi.fn()}
        evidencePanel={<blockquote>测试字幕证据</blockquote>}
      />,
    );

    expect(DEFAULT_GRAPH_APPEARANCE.showRelationLabels).toBe(false);
    expect(screen.queryByLabelText('显示关系谓词')).not.toBeInTheDocument();
    expect(screen.getByLabelText('知识组织结构')).toBeInTheDocument();
    expect(screen.getByLabelText('关系语义图例')).toBeInTheDocument();
    expect(screen.getByLabelText('画布关系语义说明')).toBeInTheDocument();

    // Switch layout mode to layered
    fireEvent.click(screen.getByRole('button', { name: '层级流' }));
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'layered');
    expect(container.querySelectorAll('.browser-layer-lane')).toHaveLength(4);

    // Switch to diagnosis lens
    fireEvent.click(screen.getByRole('button', { name: /故障排查/ }));
    expect(container.querySelector('.ow-canvas-caption')).toHaveTextContent('故障排查·3 个实体节点·2 条关系');

    // Switch to all lens and click a schema chip to filter by entity type
    fireEvent.click(screen.getByRole('button', { name: /全景网络/ }));
    const schema = screen.getByLabelText('知识组织结构');
    fireEvent.click(within(schema).getByRole('button', { name: /零部件/ }));
    expect(onEntityType).toHaveBeenCalledWith('component');

    // Select an entity and discover path to another entity
    fireEvent.click(await screen.findByRole('button', { name: '实体 Mavic 3' }));
    fireEvent.change(screen.getByLabelText('探索关联路径'), { target: { value: 'calib' } });
    expect(screen.getByText('3 跳知识链')).toBeInTheDocument();
    const stepButtons = container.querySelectorAll('.ow-path-step');
    expect(stepButtons).toHaveLength(3);

    // Clicking a path step selects that edge and shows assertion breakdown & qualifiers
    fireEvent.click(stepButtons[1]);
    expect(onEdge).toHaveBeenCalledWith(richGraph.edges[2]);
    rerender(
      <GraphWorkbench
        graph={richGraph}
        overview={{ videos: 5, active_videos: 4, entities: 6, relations: 5, evidence: 10, builds: {} }}
        query=""
        onQuery={vi.fn()}
        onSearch={vi.fn()}
        entityType=""
        onEntityType={onEntityType}
        hops={1}
        onHops={vi.fn()}
        focusId=""
        onClearFocus={vi.fn()}
        selectedEdge={richGraph.edges[2]}
        onEdge={onEdge}
        onClearEdge={vi.fn()}
        onFocus={vi.fn()}
        onExpand={vi.fn(async () => {})}
        expandingId=""
        loading={false}
        error=""
        expansionError=""
        onRefresh={vi.fn()}
        evidencePanel={<blockquote>测试字幕证据</blockquote>}
      />,
    );
    expect(screen.getByLabelText('证据态度分布')).toHaveTextContent('肯定 2否定 1');
    expect(screen.getByText(/同时存在肯定与否定记录/)).toBeInTheDocument();
    expect(screen.getByText('适用条件与限定')).toBeInTheDocument();
    expect(screen.getByText('v01.00.09')).toBeInTheDocument();
  });
});
