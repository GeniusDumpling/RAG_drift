import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { VideoGraphData, VideoGraphEdge, VideoGraphNode } from '../src/api/types';
import { BrowserGraphCanvas } from '../src/components/video-graph/BrowserGraphCanvas';
import { BrowserMotion, readableEdgeLabel } from '../src/components/video-graph/browser-motion';
import { filterGraphByLens, findShortestKnowledgePath, lensMatchSets } from '../src/components/video-graph/data';
import { GraphWorkbench } from '../src/components/video-graph/GraphWorkbench';
import {
  classifyEdgeSemantics,
  DEFAULT_GRAPH_APPEARANCE,
  formatEdgeCaption,
  isLensFocalNode,
  lensEdgeColor,
  lensSchemaFor,
  nodeLensLayerIndex,
} from '../src/components/video-graph/presentation';

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

  it('provides lens-specific schemas, layer mappings, focal roles and edge colors including forum types', () => {
    expect(lensSchemaFor('lineage').geometry).toBe('columns');
    expect(lensSchemaFor('diagnosis').geometry).toBe('converge');
    expect(lensSchemaFor('ecosystem').geometry).toBe('radial');
    expect(lensSchemaFor('all').geometry).toBe('force-radial');

    // Diagnosis converges product/component -> fault/state <- method/instruction
    expect(nodeLensLayerIndex({ entity_type: 'product', kind: 'entity' }, 'diagnosis')).toBe(0);
    expect(nodeLensLayerIndex({ entity_type: 'state', kind: 'entity' }, 'diagnosis')).toBe(1);
    expect(nodeLensLayerIndex({ entity_type: 'instruction', kind: 'entity' }, 'diagnosis')).toBe(2);

    // Lineage orders brand -> family -> product -> component
    expect(nodeLensLayerIndex({ entity_type: 'organization', kind: 'entity' }, 'lineage')).toBe(0);
    expect(nodeLensLayerIndex({ entity_type: 'product_family', kind: 'entity' }, 'lineage')).toBe(1);
    expect(nodeLensLayerIndex({ entity_type: 'product', kind: 'entity' }, 'lineage')).toBe(2);
    expect(nodeLensLayerIndex({ entity_type: 'component', kind: 'entity' }, 'lineage')).toBe(3);

    // Lens edge colors highlight diagnosis fault vs remedy while preserving disputed/negated semantics
    expect(lensEdgeColor(makeEdge('f', 'a', 'b', 'indicates'), 'diagnosis')).toBe('#d46a6a');
    expect(lensEdgeColor(makeEdge('r', 'a', 'b', 'instruction'), 'diagnosis')).toBe('#3d9b84');
    expect(lensEdgeColor(makeEdge('d', 'a', 'b', 'reports_fault', { assertions: { asserted: 1, negated: 1 } }), 'diagnosis')).toBe('#c97a2b');
    expect(lensEdgeColor(makeEdge('c', 'a', 'b', 'compatible_with'), 'ecosystem')).toBe('#3f9b86');

    expect(isLensFocalNode({ entity_type: 'fault', kind: 'entity' }, 'diagnosis').focal).toBe(true);
    expect(isLensFocalNode({ entity_type: 'protocol', kind: 'entity' }, 'ecosystem').focal).toBe(true);
    expect(isLensFocalNode({ entity_type: 'product', kind: 'entity' }, 'diagnosis').focal).toBe(false);
  });

  it('filters graphs by knowledge lens, computes context match sets and finds multi-hop bridge paths', () => {
    const diagnosis = filterGraphByLens(richGraph, 'diagnosis');
    expect(diagnosis.edges.map((e) => e.id)).toEqual(['e-fault', 'e-mitigate']);
    expect(diagnosis.nodes.map((n) => n.id)).toEqual(['gimbal', 'drift', 'calib']);

    const match = lensMatchSets(richGraph, 'diagnosis');
    expect(match).not.toBeNull();
    expect([...match!.nodeIds]).toEqual(['gimbal', 'drift', 'calib']);
    expect([...match!.edgeIds]).toEqual(['e-fault', 'e-mitigate']);
    expect(lensMatchSets(richGraph, 'all')).toBeNull();

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

describe('2D canvas borderless lens guides, radial rings and context dimming', () => {
  it('renders borderless column bands with top stage ruler instead of dashed boxes', async () => {
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
        activeLens="diagnosis"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
        highlightedPath={{ nodeIds: new Set(['mavic', 'gimbal', 'drift']), edgeIds: new Set(['e-comp', 'e-fault']) }}
      />,
    );

    await screen.findByRole('button', { name: '实体 大疆' });
    // No old dashed lane boxes
    expect(container.querySelectorAll('.browser-layer-lane')).toHaveLength(0);
    expect(container.querySelectorAll('.browser-layer-band rect[stroke-dasharray]')).toHaveLength(0);
    expect(container.querySelectorAll('.browser-layer-band')).toHaveLength(3);

    // Top ruler shows diagnosis converge direction
    const ruler = screen.getByLabelText('视角结构标尺');
    expect(ruler).toHaveTextContent('产品与部件');
    expect(ruler).toHaveTextContent('故障与状态现象');
    expect(ruler).toHaveTextContent('缓解与操作建议');

    // Focal ring on fault node
    const driftNode = container.querySelector('[data-node-id="drift"]')!;
    expect(driftNode.querySelector('.browser-focal-ring')).not.toBeNull();

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
        activeLens="ecosystem"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );
    expect(container.querySelectorAll('.browser-layer-band')).toHaveLength(0);
    expect(container.querySelector('.browser-radial-guide')).not.toBeNull();
    expect(screen.getByLabelText('视角结构标尺')).toHaveTextContent('内圈：技术／协议／软件');

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
        activeLens="all"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'force' }}
      />,
    );
    expect(container.querySelectorAll('.browser-layer-band')).toHaveLength(0);
    expect(container.querySelector('.browser-radial-guide')).toBeNull();
    expect(screen.queryByLabelText('视角结构标尺')).not.toBeInTheDocument();
  });

  it('spreads dense lineage layered graphs into wide multi-column bands with every node strictly inside', async () => {
    const types = ['organization', 'product_family', 'product', 'component'];
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
        activeLens="lineage"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    await screen.findByRole('button', { name: '实体 节点 0' });
    const bandRects = [...container.querySelectorAll('.browser-layer-band rect:first-child')].map((el) => ({
      x: Number(el.getAttribute('x')),
      y: Number(el.getAttribute('y')),
      width: Number(el.getAttribute('width')),
      height: Number(el.getAttribute('height')),
    }));
    expect(bandRects).toHaveLength(4);
    for (let i = 1; i < bandRects.length; i++) {
      const gap = bandRects[i].x - (bandRects[i - 1].x + bandRects[i - 1].width);
      expect(gap).toBeGreaterThanOrEqual(350);
    }
    const totalWidth = bandRects[3].x + bandRects[3].width - bandRects[0].x;
    expect(totalWidth).toBeGreaterThan(bandRects[0].height);

    for (let i = 0; i < denseNodes.length; i++) {
      const nodeEl = container.querySelector(`[data-node-id="n${i}"]`)!;
      const match = /translate\(([-\d.]+),([-\d.]+)\)/.exec(nodeEl.getAttribute('transform') ?? '');
      expect(match).not.toBeNull();
      const cx = Number(match![1]);
      const cy = Number(match![2]);
      const r = Number(nodeEl.querySelector('circle:not([data-pin])')?.getAttribute('r'));
      const box = bandRects[i % 4];
      expect(cx - r).toBeGreaterThanOrEqual(box.x);
      expect(cx + r).toBeLessThanOrEqual(box.x + box.width);
      expect(cy - r).toBeGreaterThanOrEqual(box.y);
      expect(cy + r).toBeLessThanOrEqual(box.y + box.height);
    }
  });

  it('preserves the camera, releases pins and keeps nodes in their new layers after lens migration', async () => {
    const handlers = { graph: richGraph, focusId: '', selectedEdge: '', expandingId: '',
      onFocus: vi.fn(), onExpand: vi.fn(async () => {}), onEdge: vi.fn() };
    const { container, rerender } = render(<BrowserGraphCanvas {...handlers} activeLens="all" />);
    const node = await screen.findByRole('button', { name: '实体 三轴云台' });
    fireEvent.click(node);
    fireEvent.click(screen.getByRole('button', { name: '固定位置' }));
    await waitFor(() => expect(node).toHaveAttribute('data-pinned', 'true'));
    const world = container.querySelector('.browser-graph-svg > g')!;
    const camera = world.getAttribute('transform');
    const before = node.getAttribute('transform');
    rerender(<BrowserGraphCanvas {...handlers} activeLens="lineage" lensMatch={lensMatchSets(richGraph, 'lineage')}
      appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }} />);
    expect(node.getAttribute('transform')).toBe(before);
    await waitFor(() => expect(node).toHaveAttribute('data-pinned', 'false'));
    await new Promise(resolve => setTimeout(resolve, 1150));
    expect(world.getAttribute('transform')).toBe(camera);
    expect(node.getAttribute('transform')).not.toBe(before);
    for (const el of container.querySelectorAll('[data-node-id]')) {
      const item = richGraph.nodes.find(n => n.id === el.getAttribute('data-node-id'))!;
      if (!lensMatchSets(richGraph, 'lineage')!.nodeIds.has(item.id)) continue;
      const band = container.querySelector(`.browser-layer-band[data-layer-index="${nodeLensLayerIndex(item, 'lineage')}"] rect`)!;
      const [x, y] = el.getAttribute('transform')!.match(/[-\d.]+/g)!.map(Number);
      const radius = Number(el.querySelector('circle:not(.browser-focal-ring)')!.getAttribute('r'));
      expect(x - radius).toBeGreaterThanOrEqual(Number(band.getAttribute('x')));
      expect(x + radius).toBeLessThanOrEqual(Number(band.getAttribute('x')) + Number(band.getAttribute('width')));
      expect(y - radius).toBeGreaterThanOrEqual(Number(band.getAttribute('y')));
      expect(y + radius).toBeLessThanOrEqual(Number(band.getAttribute('y')) + Number(band.getAttribute('height')));
    }
  });

  it('preserves surviving node positions when switching knowledge lenses so nodes migrate smoothly', async () => {
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
        activeLens="lineage"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    const gimbalNode = await screen.findByRole('button', { name: '实体 三轴云台' });
    const beforeTransform = gimbalNode.getAttribute('transform');
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
        activeLens="diagnosis"
        appearance={{ ...DEFAULT_GRAPH_APPEARANCE, layoutMode: 'layered' }}
      />,
    );

    const afterGimbalNode = screen.getByRole('button', { name: '实体 三轴云台' });
    expect(afterGimbalNode.getAttribute('transform')).toBe(beforeTransform);
  });
});

describe('GraphWorkbench lens-driven organization, context toggle, schema sync and path exploration', () => {
  it('keeps the panorama in every lens, syncs schema and explores paths', async () => {
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
    const displayDetails = container.querySelector('.ow-display-settings') as HTMLDetailsElement;
    const forceDetails = container.querySelector('.ow-force-settings') as HTMLDetailsElement;
    expect(displayDetails).not.toHaveAttribute('open');
    expect(forceDetails).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('显示'));
    expect(displayDetails).toHaveAttribute('open');
    expect(screen.getByLabelText('知识组织结构')).toBeInTheDocument();
    expect(screen.getByLabelText('关系语义图例')).toBeInTheDocument();
    expect(screen.getByLabelText('画布关系语义说明')).toBeInTheDocument();

    // Initially 'all' lens uses 自然力导向
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'force');
    expect(screen.queryByRole('group', { name: '视角范围模式' })).not.toBeInTheDocument();

    // Switching to 故障排查 automatically switches to 视角结构 (converge) and updates Schema
    fireEvent.click(screen.getByRole('button', { name: /故障排查/ }));
    expect(container.querySelector('.ow-canvas-caption')).toHaveTextContent('故障排查·3 个实体节点·2 条关系');
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'layered');
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-lens-geometry', 'converge');
    expect(container.querySelectorAll('.browser-layer-band')).toHaveLength(3);
    expect(screen.getByLabelText('知识组织结构')).toHaveTextContent('因果收敛流');
    expect(screen.getByLabelText('关系语义图例')).toHaveTextContent('触发故障／状态');

    // Panorama is always present; the old focus/context controls are removed.
    expect(screen.queryByRole('button', { name: '精简聚焦' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '视角范围模式' })).not.toBeInTheDocument();
    expect(container.querySelector('.ow-canvas-caption')).toHaveTextContent('（含全景背景）');
    expect(container.querySelectorAll('[data-node-id]')).toHaveLength(richGraph.nodes.length);
    expect(container.querySelectorAll('[data-edge-id]')).toHaveLength(richGraph.edges.length);
    expect(container.querySelector('[data-node-id="dji"]')).toHaveAttribute('opacity', '0.14');
    expect(container.querySelector('[data-node-id="drift"]')).toHaveAttribute('opacity', '1');

    // Manual override back to 自然力导向
    fireEvent.click(screen.getByRole('button', { name: '自然力导向' }));
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'force');

    // Switch to 技术生态 -> auto switches to 视角结构 radial dual-ring
    fireEvent.click(screen.getByRole('button', { name: /技术生态/ }));
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'layered');
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-lens-geometry', 'radial');
    expect(container.querySelector('.browser-radial-guide')).not.toBeNull();
    expect(screen.getByLabelText('知识组织结构')).toHaveTextContent('双圈辐射生态');

    // Switch back to 全景网络 -> auto restores 自然力导向
    fireEvent.click(screen.getByRole('button', { name: /全景网络/ }));
    expect(container.querySelector('.browser-graph-svg')).toHaveAttribute('data-layout', 'force');
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
