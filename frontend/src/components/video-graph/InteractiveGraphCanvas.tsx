import { useEffect, useRef, useState } from 'react';
import type { Graph, GraphData, LayoutOptions, NodeData, EdgeData, IElementEvent } from '@antv/g6';
import type { VideoGraphData, VideoGraphEdge, VideoGraphNode } from '../../api/types';
import { DARK_ENTITY_COLORS, DEFAULT_GRAPH_APPEARANCE, ENTITY_LABELS, PREDICATE_LABELS } from './presentation';
import type { GraphAppearance } from './presentation';
import { parallelEdgeStyles } from './data';
import { atMinimumZoom, fitDefaultViewport, INITIAL_ZOOM_RANGE } from './viewport';

export type GraphDimension = '2d' | '3d';
type Props = {
  graph: VideoGraphData; focusId: string; selectedEdge: string; expandingId: string;
  onFocus: (node: VideoGraphNode) => void;
  onExpand: (node: VideoGraphNode) => Promise<void>;
  onEdge: (edge: VideoGraphEdge) => void;
  activeNodeId?: string; appearance?: GraphAppearance;
  onSelect?: (node: VideoGraphNode | null) => void;
  onHover?: (node: VideoGraphNode | null) => void;
  dimension?: GraphDimension;
  onUnavailable?: (message: string) => void;
};
function forceLayoutOptions(appearance = DEFAULT_GRAPH_APPEARANCE, is3D = false): LayoutOptions {
  if (is3D) return { type: 'd3-force-3d', preLayout: true, iterations: 180, numDimensions: 3,
    link: { distance: appearance.linkDistance }, manyBody: { strength: -appearance.repulsion },
    collide: { radius: 18, strength: 0.8 }, center: { x: 0, y: 0, z: 0 } };
  return { type: 'd3-force', preLayout: true, iterations: 220, linkDistance: appearance.linkDistance,
        nodeStrength: -appearance.repulsion, collide: { radius: 32, strength: 0.8 }, preventOverlap: true,
        x: { strength: 0.05 }, y: { strength: 0.12 } };
}

function graphData(data: VideoGraphData, instance?: Graph, is3D = false): GraphData {
  const curves = parallelEdgeStyles(data.edges);
  const degree = new Map<string, number>();
  for (const edge of data.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const existing = new Set(instance?.getNodeData().map((node) => node.id));
  return {
    nodes: data.nodes.map((node, i) => {
      const position = instance && existing.has(node.id) ? instance.getElementPosition(node.id) : null;
      const angle = i * Math.PI * (3 - Math.sqrt(5));
      const radius = Math.sqrt(i + 1) * 42;
      return { id: node.id, data: { ...node, degree: degree.get(node.id) ?? 0 },
        style: { x: position?.[0] ?? 500 + Math.cos(angle) * radius,
          y: position?.[1] ?? 300 + Math.sin(angle) * radius,
          ...(is3D ? { z: position?.[2] ?? Math.sin(i * 1.7) * radius * 0.6 } : {}) } };
    }),
    edges: data.edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target,
      data: { ...edge }, style: curves.get(edge.id) })),
  };
}

export function InteractiveGraphCanvas(props: Props) {
  const is3D = props.dimension === '3d';
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<Graph | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const drawn = useRef<VideoGraphData | null>(null);
  const extension3D = useRef<typeof import('./three-dimensional') | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(true);
  const [canZoomOut, setCanZoomOut] = useState(false);
  const [error, setError] = useState('');
  const [localSelectedId, setSelectedId] = useState('');
  const selectedId = props.activeNodeId ?? localSelectedId;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const appearance = props.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const drawnForces = useRef(`${appearance.repulsion}:${appearance.linkDistance}`);
  const styleKey = (config: GraphAppearance) => [config.showLabels, config.showArrows, config.nodeScale, config.linkScale].join(':');
  const drawnAppearance = useRef(styleKey(appearance));
  const selectedNode = props.graph.nodes.find((node) => node.id === selectedId);

  function perform(action: (graph: Graph) => Promise<unknown>) {
    queue.current = queue.current.then(async () => {
      const graph = instance.current;
      if (graph && !graph.destroyed) await action(graph);
    }).catch(() => {
      if (instance.current && !instance.current.destroyed) {
        if (is3D && latest.current.onUnavailable) latest.current.onUnavailable('3D 图谱暂时不可用，已切换为 2D，可再次点击 3D 重试。');
        else { setBusy(false); setError('交互画布暂时不可用，请重新加载页面重试。'); }
      }
    });
  }

  async function fitOverview(graph: Graph, preserveViewport = false) {
    if (is3D && extension3D.current) await extension3D.current.fitViewport3D(graph, preserveViewport);
    else await fitDefaultViewport(graph, preserveViewport);
    if (!graph.destroyed) setCanZoomOut(!atMinimumZoom(graph));
  }

  async function focusNode(graph: Graph, id: string) {
    if (is3D && extension3D.current) extension3D.current.focusNode3D(graph, id);
    else {
      const point = graph.getViewportByCanvas(graph.getElementPosition(id));
      const [width, height] = graph.getSize();
      await graph.translateBy([width / 2 - point[0], height / 2 - point[1]], false);
    }
  }

  useEffect(() => {
    let disposed = false;
    let graph: Graph | null = null;
    let observer: ResizeObserver | null = null;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    let disposeInput3D: (() => void) | undefined;
    let disposeDoubleClick2D: (() => void) | undefined;
    let lastNodeClick: { id: string; x: number; y: number; time: number } | null = null;
    async function initialize() {
      try {
        const { Graph: G6Graph, NodeEvent, EdgeEvent, CanvasEvent, GraphEvent } = await import('@antv/g6');
        if (is3D) {
          const probe = document.createElement('canvas');
          const context = probe.getContext('webgl2') ?? probe.getContext('webgl');
          if (!context) throw new Error('WebGL unavailable');
          context.getExtension('WEBGL_lose_context')?.loseContext();
          extension3D.current = await import('./three-dimensional');
        }
        if (disposed || !host.current) return;
        const initial = latest.current.graph;
        drawnAppearance.current = styleKey(latest.current.appearance ?? DEFAULT_GRAPH_APPEARANCE);
        graph = new G6Graph({
          ...(is3D ? extension3D.current?.options3D() : {}),
          container: host.current,
          width: host.current.clientWidth || 1000, height: host.current.clientHeight || 580,
          animation: false, padding: 48, zoomRange: [...INITIAL_ZOOM_RANGE],
          data: graphData(initial, undefined, is3D), layout: forceLayoutOptions(latest.current.appearance, is3D),
          node: {
            type: is3D ? 'graph-sphere' : 'circle',
            style: (node: NodeData) => {
              const config = latest.current.appearance ?? DEFAULT_GRAPH_APPEARANCE;
              return {
                size: (is3D ? 4 : 1) * (7 + Math.min(11, Math.log2(1 + Number(node.data?.degree ?? 0)) * 2.6)) * config.nodeScale,
                ...(is3D ? { materialType: 'phong', latitudeBands: 12, longitudeBands: 12,
                  materialShininess: 24 } : {}),
                fill: DARK_ENTITY_COLORS[String(node.data?.entity_type ?? node.data?.kind)] ?? '#9998a8',
                // Explicit opacity restores brightness when G6 removes inactive state.
                stroke: '#ffffff18', lineWidth: 0.6, opacity: 1, cursor: 'grab',
                labelText: is3D ? '' : String(node.data?.label ?? ''), labelPlacement: 'bottom',
                labelOffsetY: 6, labelFontSize: 11, labelFontWeight: 400,
                labelFill: '#605d6d', labelOpacity: config.showLabels ? 0.9 : 0,
                labelWordWrap: false, labelMaxWidth: 132,
                labelBackground: false, halo: false,
              };
            },
            state: {
              selected: { fill: '#c6a8ed', stroke: '#d6bbf7', lineWidth: 1.5, halo: !is3D, haloStroke: '#a78bd5', haloLineWidth: 12, haloStrokeOpacity: 0.15, labelFill: '#695080', labelOpacity: 1 },
              active: { fill: '#c4a4ee', halo: !is3D, haloStroke: '#b99ae3', haloLineWidth: 6, haloStrokeOpacity: 0.12, labelFill: '#72578b', labelOpacity: 1 },
              inactive: { opacity: 0.55 },
            },
          },
          edge: {
            type: is3D ? 'graph-line-3d' : 'quadratic',
            style: (edge: EdgeData) => {
              const config = latest.current.appearance ?? DEFAULT_GRAPH_APPEARANCE;
              return { stroke: is3D ? '#89808e' : '#383842', strokeOpacity: is3D ? 0.55 : 0.75, opacity: 1, zIndex: 0,
                lineWidth: (0.65 + Math.min(0.8, Math.log2(1 + Number(edge.data?.evidence_count ?? 0)) * 0.24)) * config.linkScale,
                endArrow: config.showArrows, endArrowSize: 4, cursor: 'pointer',
                lineDash: edge.data?.kind === 'mention' ? [4, 4] : undefined,
                labelText: is3D ? '' : PREDICATE_LABELS[String(edge.data?.predicate)] ?? String(edge.data?.predicate ?? ''),
                labelFontSize: 10, labelFill: '#72578b', labelOpacity: 0,
                labelBackground: true, labelBackgroundFill: '#ffffff', labelPadding: [3, 5] };
            },
            state: {
              selected: { stroke: '#bd9be8', strokeOpacity: 1, lineWidth: 1.7, labelOpacity: 1, zIndex: 3 },
              active: { stroke: '#a58acb', strokeOpacity: 0.95, lineWidth: 1.2, labelOpacity: 1, zIndex: 2 },
              hovered: { stroke: '#cfadf7', strokeOpacity: 1, opacity: 1, lineWidth: 2.2, labelOpacity: 1, zIndex: 4 },
              inactive: { opacity: 0.35 },
            },
          },
          behaviors: is3D ? [] : ['drag-canvas', 'zoom-canvas', { type: 'drag-element', animation: false },
            { type: 'hover-activate', key: 'node-neighborhood-hover', degree: 1, direction: 'both',
              inactiveState: initial.nodes.length > 1000 ? undefined : 'inactive', animation: false,
              enable: (event: IElementEvent) => event.targetType === 'node',
              onHover: (event: IElementEvent) => latest.current.onHover?.(latest.current.graph.nodes.find((node) => node.id === event.target.id) ?? null),
              onHoverEnd: () => latest.current.onHover?.(null) },
            { type: 'hover-activate', key: 'edge-hover', degree: 0, state: 'hovered', animation: false,
              enable: (event: IElementEvent) => event.targetType === 'edge' }],
          plugins: [...(is3D ? [{ type: 'graph-light-3d', ambient: { intensity: 4.5 },
            directional: { direction: [-1, -1, 2], intensity: 3 } }] : []),
            { type: 'tooltip', onOpenChange: () => {},
            getContent: async (_event: IElementEvent, items: Array<NodeData | EdgeData>) => {
              const item = items[0];
              const box = document.createElement('div'); box.className = 'graph-node-tooltip';
              const title = document.createElement('strong');
              title.textContent = String(item?.data?.label ?? PREDICATE_LABELS[String(item?.data?.predicate)] ?? '关系');
              const description = document.createElement('p');
              description.textContent = item?.data?.kind === 'entity'
                ? `${ENTITY_LABELS[String(item.data.entity_type)] ?? '实体'} · 双击展开邻居`
                : `${item?.data?.evidence_count ?? 0} 条证据 · 点击查看原文`;
              box.append(title, description); return box;
            } }],
        });
        instance.current = graph;
        graph.on(GraphEvent.AFTER_TRANSFORM, () => {
          // G6 emits an initial transform before its viewport controller is assigned.
          if (!disposed && graph?.rendered && !graph.destroyed) setCanZoomOut(!atMinimumZoom(graph));
        });
        graph.on(NodeEvent.CLICK, (event: IElementEvent) => {
          if (event.detail !== 2 || !lastNodeClick) lastNodeClick = {
            id: event.target.id, x: event.clientX, y: event.clientY, time: event.timeStamp,
          };
          setSelectedId(event.target.id);
          latest.current.onSelect?.(latest.current.graph.nodes.find((node) => node.id === event.target.id) ?? null);
          perform((current) => focusNode(current, event.target.id));
        });
        graph.on(CanvasEvent.CLICK, () => { setSelectedId(''); latest.current.onSelect?.(null); });
        graph.on(NodeEvent.DBLCLICK, (event: IElementEvent) => {
          const node = latest.current.graph.nodes.find((item) => item.id === event.target.id);
          if (node) void latest.current.onExpand(node);
        });
        graph.on(EdgeEvent.CLICK, (event: IElementEvent) => {
          const edge = latest.current.graph.edges.find((item) => item.id === event.target.id);
          if (edge) latest.current.onEdge(edge);
        });
        if (!is3D) {
          // The first click centers the node away from the second click. Catch
          // the DOM double click before G6 routes it to the now-empty canvas.
          const element = host.current;
          const doubleClick = (event: MouseEvent) => {
            if (!lastNodeClick || event.timeStamp - lastNodeClick.time > 500
              || Math.hypot(event.clientX - lastNodeClick.x, event.clientY - lastNodeClick.y) > 5) return;
            const node = latest.current.graph.nodes.find((item) => item.id === lastNodeClick!.id);
            if (!node) return;
            event.stopImmediatePropagation();
            setSelectedId(node.id); latest.current.onSelect?.(node);
            void latest.current.onExpand(node);
            lastNodeClick = null;
          };
          element.addEventListener('dblclick', doubleClick, true);
          disposeDoubleClick2D = () => element.removeEventListener('dblclick', doubleClick, true);
        }
        drawn.current = initial;
        await graph.render();
        if (disposed || graph.destroyed) return;
        await fitOverview(graph);
        if (disposed) return;
        if (is3D && extension3D.current) {
          disposeInput3D = extension3D.current.bindInput3D(graph, host.current!, {
            select: (id) => {
              setSelectedId(id ?? '');
              latest.current.onSelect?.(latest.current.graph.nodes.find((node) => node.id === id) ?? null);
            },
            edge: (id) => {
              const edge = latest.current.graph.edges.find((item) => item.id === id);
              if (edge) latest.current.onEdge(edge);
            },
            expand: (id) => {
              const node = latest.current.graph.nodes.find((item) => item.id === id);
              if (node) void latest.current.onExpand(node);
            },
            hover: (id) => latest.current.onHover?.(latest.current.graph.nodes.find((node) => node.id === id) ?? null),
            selected: () => selectedIdRef.current,
            showLabels: () => !!latest.current.appearance?.showLabels,
            error: () => latest.current.onUnavailable?.('3D 图谱暂时不可用，已切换为 2D，可再次点击 3D 重试。'),
          });
        }
        setReady(true); setBusy(false);
        if (typeof ResizeObserver !== 'undefined') {
          observer = new ResizeObserver(() => {
            if (!host.current || !graph || graph.destroyed || host.current.clientWidth <= 0) return;
            const [width, height] = graph.getSize();
            if (width === host.current.clientWidth && height === host.current.clientHeight) return;
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => perform(async (current) => {
              if (!host.current || host.current.clientWidth <= 0) return;
              const camera = current.getCanvas().getCamera();
              const orientation = is3D ? new Float32Array(camera.getWorldTransform()) : null;
              current.setSize(host.current.clientWidth, host.current.clientHeight);
              // G's canvas resize reconstructs a world-up camera; retain a view
              // that has crossed a pole before recalculating the zoom boundary.
              if (orientation) camera.setMatrix(orientation);
              await fitOverview(current, true);
            }), 100);
          });
          observer.observe(host.current);
        }
      } catch {
        if (!disposed) {
          if (is3D && latest.current.onUnavailable) latest.current.onUnavailable('当前设备暂时无法显示 3D 图谱，已切换为 2D，可再次点击 3D 重试。');
          else { setBusy(false); setError('交互画布加载失败，请重新加载页面重试。'); }
        }
      }
    }
    void initialize();
    return () => {
      disposed = true; observer?.disconnect();
      clearTimeout(resizeTimer);
      disposeInput3D?.();
      disposeDoubleClick2D?.();
      instance.current = null;
      if (graph && !graph.destroyed) graph.destroy();
    };
  }, []);

  useEffect(() => {
    if (!ready || (drawn.current === props.graph && drawnForces.current === `${appearance.repulsion}:${appearance.linkDistance}`)) return;
    setBusy(true);
    perform(async (graph) => {
      graph.setData(graphData(props.graph, graph, is3D));
      graph.setLayout(forceLayoutOptions(appearance, is3D));
      drawnForces.current = `${appearance.repulsion}:${appearance.linkDistance}`;
      drawn.current = props.graph;
      await graph.render();
      if (graph.destroyed) return;
      await fitOverview(graph);
      if (!graph.destroyed) setBusy(false);
    });
  }, [props.graph, ready, appearance.repulsion, appearance.linkDistance]);

  useEffect(() => {
    if (!ready || busy || !selectedId || !props.graph.nodes.some((node) => node.id === selectedId)) return;
    // Also center selections from the data panel and after a scene/layout rebuild.
    perform((graph) => focusNode(graph, selectedId));
  }, [ready, busy, selectedId, props.graph]);

  useEffect(() => {
    if (!ready) return;
    perform(async (graph) => {
      const states: Record<string, string[]> = {};
      for (const element of [...props.graph.nodes, ...props.graph.edges]) {
        const selected = element.kind === 'relation' || element.kind === 'mention'
          ? element.id === props.selectedEdge : element.id === selectedId || element.id === props.focusId;
        const current = graph.getElementState(element.id);
        if (current.includes('selected') !== selected)
          states[element.id] = selected ? [...current, 'selected'] : current.filter((state) => state !== 'selected');
      }
      if (Object.keys(states).length) await graph.setElementState(states, false);
    });
  }, [ready, props.graph, props.selectedEdge, props.focusId, selectedId, busy]);

  useEffect(() => {
    if (ready && drawnAppearance.current !== styleKey(appearance)) perform(async (graph) => {
      // G6 skips drawing unchanged data; refreshing the mapper applies the latest
      // display controls without running layout or moving dragged nodes.
      graph.setNode(graph.getOptions().node ?? {});
      await graph.draw();
      drawnAppearance.current = styleKey(appearance);
    });
  }, [ready, appearance.showLabels, appearance.showArrows, appearance.nodeScale, appearance.linkScale]);

  const kinds = [...new Set(props.graph.nodes.map((node) => node.entity_type ?? node.kind))];
  return <div className="interactive-graph-shell">
    <div className="interactive-graph-controls">
      <div className="graph-viewport-controls">
        <button type="button" aria-label="放大图谱" disabled={!ready || busy} onClick={() => perform((g) => is3D && extension3D.current ? extension3D.current.zoom3D(g, g.getZoom() * 1.25) : g.zoomBy(1.25))}><span aria-hidden="true">＋</span></button>
        <button type="button" aria-label="缩小图谱" disabled={!ready || busy || !canZoomOut} onClick={() => perform((g) => is3D && extension3D.current ? extension3D.current.zoom3D(g, g.getZoom() * 0.8) : g.zoomBy(0.8))}><span aria-hidden="true">−</span></button>
        <button type="button" disabled={!ready || busy} onClick={() => perform((g) => fitOverview(g))}>适应画布</button>
        <button type="button" disabled={!ready || busy} onClick={() => {
          setBusy(true); perform(async (g) => { await g.layout(forceLayoutOptions(appearance, is3D)); if (!g.destroyed) { await fitOverview(g); setBusy(false); } });
        }}>重新布局</button>
      </div>
    </div>
    {error ? <div className="graph-canvas-error" role="alert"><p>{error}</p>
      <button type="button" onClick={() => window.location.reload()}>重新加载页面</button></div> : null}
    <div className="g6-graph-host" ref={host} role="img" aria-label="可拖拽的视频知识图谱"
      aria-busy={busy} data-ready={ready} data-dimension={is3D ? '3d' : '2d'} />
    {busy && !error ? <div className="graph-loading-overlay" role="status">正在整理图谱布局…</div> : null}
    <div className="graph-interaction-footer">
      <div className="graph-legend">{kinds.map((kind) => <span key={kind}>
        <i style={{ background: DARK_ENTITY_COLORS[kind] ?? '#7293bf' }} />{ENTITY_LABELS[kind] ?? '视频'}</span>)}</div>
      <small>拖拽移动 · 滚轮缩放 · 悬停高亮 · 双击展开</small>
    </div>
    {!props.onSelect ? <div className="graph-entity-inspector">
      <label>定位实体<select aria-label="定位图谱实体" value={selectedNode?.id ?? ''} onChange={(event) => {
        setSelectedId(event.target.value);
        if (event.target.value) perform((g) => focusNode(g, event.target.value));
      }}><option value="">选择或点击图中实体</option>
        {props.graph.nodes.filter((node) => node.kind === 'entity').map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}
      </select></label>
      {selectedNode ? <><div className="graph-selected-summary"><strong>{selectedNode.label}</strong>
        <span>{ENTITY_LABELS[selectedNode.entity_type ?? ''] ?? '实体'}</span></div>
        <button type="button" disabled={!!props.expandingId} onClick={() => void props.onExpand(selectedNode)}>
          {props.expandingId === selectedNode.id ? '正在展开…' : '展开邻居'}</button>
        <button type="button" onClick={() => props.onFocus(selectedNode)}>聚焦此实体</button></> :
        <span className="muted">选择实体查看详情，展开邻居时保留已有节点。</span>}
    </div> : null}
  </div>;
}
