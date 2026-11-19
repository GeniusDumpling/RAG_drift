import { useEffect, useLayoutEffect, useId, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceRadial, forceSimulation, forceX, forceY } from 'd3-force';
import type { Simulation, SimulationNodeDatum, SimulationLinkDatum } from 'd3-force';
import type { VideoGraphNode } from '../../api/types';
import type { GraphCanvasProps } from './InteractiveGraphCanvas';
import { nodeRelationCounts, parallelEdgeStyles } from './data';
import { containLayerNode, containRadialNode, fitLayoutToPanorama, planKnowledgeLayout } from './knowledge-layout';
import type { ColumnBand, LayerBounds, RadialGuide } from './knowledge-layout';
import {
  classifyEdgeSemantics,
  DEFAULT_GRAPH_APPEARANCE,
  ENTITY_COLORS,
  ENTITY_LABELS,
  formatEdgeCaption,
  isLensFocalNode,
  lensEdgeColor,
  lensSchemaFor,
  nodeLensLayerIndex,
  PREDICATE_LABELS,
} from './presentation';
import { browserNodeRadius } from './browser-geometry';
import { BrowserMotion } from './browser-motion';
import './browser-graph.css';

type Node = SimulationNodeDatum & { id: string; x: number; y: number; radius: number; item: VideoGraphNode };
type View = { x: number; y: number; k: number };
type Style = { color?: string; scale?: number; caption?: 'name' | 'type' | 'id' | 'none' };
type Hidden = { nodes: Set<string>; edges: Set<string> };

export function BrowserGraphCanvas(props: GraphCanvasProps) {
  const appearance = props.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const layoutMode = appearance.layoutMode ?? 'force';
  const activeLens = props.activeLens ?? 'all';
  const lensContextMode = props.lensContextMode ?? 'dim-context';
  const lensMatch = props.lensMatch ?? null;
  const isContextDimmed = Boolean(lensMatch && lensContextMode === 'dim-context');
  const schema = lensSchemaFor(activeLens);

  const uid = useId().replace(/:/g, '');
  const svg = useRef<SVGSVGElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const nodeCache = useRef(new Map<string, Node>());
  const panorama = useRef(new Map<string, { x: number; y: number }>());
  const layoutFrame = useRef<LayerBounds>();
  const simulation = useRef<Simulation<Node, undefined>>();
  const previousLayoutMode = useRef(layoutMode);
  const previousLens = useRef(activeLens);
  const previousContextMode = useRef(lensContextMode);
  const frame = useRef(0);
  const transitionFrame = useRef(0);
  const layerBoundsRef = useRef(new Map<string, LayerBounds>());
  const ringBoundsRef = useRef(new Map<string, number>());
  const [sceneNodes, setSceneNodes] = useState<Node[]>([]);
  const [bands, setBands] = useState<ColumnBand[]>([]);
  const [radialGuide, setRadialGuide] = useState<RadialGuide | null>(null);
  const [, updateControls] = useState(0);
  const world = useRef<SVGGElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const motion = useRef<BrowserMotion>();
  const toolbar = useRef<HTMLDivElement>(null);
  const paintRef = useRef<(geometry: boolean) => void>(() => {});
  const geometryDirty = useRef(false);
  const [size, setSize] = useState({ width: 1000, height: 700 });
  const viewRef = useRef<View>({ x: 500, y: 350, k: 1 });
  const view = viewRef.current;
  const [localSelection, setLocalSelection] = useState('');
  const selectedId = props.activeNodeId ?? localSelection;
  const [hovered, setHovered] = useState('');
  const [hoveredEdge, setHoveredEdge] = useState('');
  const [menu, setMenu] = useState<{ x: number; y: number; node?: string; edge?: string } | null>(null);
  const [hidden, setHidden] = useState<Hidden>({ nodes: new Set(), edges: new Set() });
  const history = useRef<Hidden[]>([]);
  const [styles, setStyles] = useState<Record<string, Style>>({});
  const [styleKey, setStyleKey] = useState('');
  const drag = useRef<{ id?: string; x: number; y: number; nodeX?: number; nodeY?: number; moved: boolean; view: View; pointer: number; capture: Element }>();
  const moved = useRef(false);
  const counts = useMemo(() => nodeRelationCounts(props.graph.edges), [props.graph]);
  const visibleNodes = useMemo(() => props.graph.nodes.filter(n => !hidden.nodes.has(n.id)), [props.graph, hidden]);
  const edges = useMemo(() => {
    const ids = new Set(visibleNodes.map(n => n.id));
    return props.graph.edges.filter(e => !hidden.edges.has(e.id) && ids.has(e.source) && ids.has(e.target));
  }, [props.graph, hidden, visibleNodes]);
  const curves = useMemo(() => parallelEdgeStyles(edges), [edges]);
  const edgeSemantics = useMemo(() => new Map(edges.map(edge => [edge.id, classifyEdgeSemantics(edge)])), [edges]);
  const nodeScales = JSON.stringify(Object.entries(styles).filter(([key, value]) => key.startsWith('node:') && value.scale !== undefined).map(([key, value]) => [key, value.scale]));
  const active = hovered || selectedId;
  const pathFocus = props.highlightedPath && props.highlightedPath.nodeIds.size > 0 ? props.highlightedPath : null;
  const neighbors = new Set([active, ...edges.filter(e => e.source === active || e.target === active).flatMap(e => [e.source, e.target])]);

  function cancelTransition() {
    if (transitionFrame.current) cancelAnimationFrame(transitionFrame.current);
    transitionFrame.current = 0;
  }
  function scheduleDraw(geometry = true) {
    geometryDirty.current ||= geometry;
    if (!frame.current) frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const dirty = geometryDirty.current; geometryDirty.current = false;
      paintRef.current(dirty);
    });
  }
  function setView(next: View | ((value: View) => View)) {
    viewRef.current = typeof next === 'function' ? next(viewRef.current) : next;
    scheduleDraw(false);
  }
  function computeFitView(points?: Array<{ x: number; y: number; radius: number }>): View {
    const source = points && points.length ? points : nodesRef.current;
    if (!source.length) return viewRef.current;
    const lanePadX = layoutMode === 'layered' ? 90 : 0;
    const left = Math.min(...source.map(n => n.x - n.radius)) - lanePadX;
    const right = Math.max(...source.map(n => n.x + n.radius)) + lanePadX;
    const top = Math.min(...source.map(n => n.y - n.radius - (layoutMode === 'layered' ? 128 : 60)));
    const bottom = Math.max(...source.map(n => n.y + n.radius)) + (layoutMode === 'layered' ? 40 : 0);
    const k = Math.min(1.5, (size.width - 110) / Math.max(1, right - left), (size.height - 150) / Math.max(1, bottom - top));
    const safeK = Math.max(0.05, k);
    return { k: safeK, x: size.width / 2 - (left + right) / 2 * safeK, y: size.height / 2 - (top + bottom) / 2 * safeK };
  }
  function fit() {
    setView(computeFitView());
  }
  function zoom(factor: number, x = size.width / 2, y = size.height / 2) {
    setView(v => {
      const k = Math.max(0.05, Math.min(8, v.k * factor));
      return { k, x: x - (x - v.x) * k / v.k, y: y - (y - v.y) * k / v.k };
    });
  }
  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    const measure = () => setSize({ width: element.clientWidth || 1000, height: element.clientHeight || 700 });
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = element.getBoundingClientRect();
      zoom(Math.exp(-event.deltaY * 0.0015), event.clientX - box.left, event.clientY - box.top);
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => { observer.disconnect(); element.removeEventListener('wheel', wheel); cancelTransition(); };
  }, []);

  useEffect(() => {
    const previous = nodeCache.current;
    const firstLayout = !simulation.current;
    const modeChanged = previousLayoutMode.current !== layoutMode;
    const lensChanged = previousLens.current !== activeLens;
    const contextChanged = previousContextMode.current !== lensContextMode;
    previousLayoutMode.current = layoutMode;
    previousLens.current = activeLens;
    previousContextMode.current = lensContextMode;
    if (modeChanged || lensChanged || contextChanged) for (const node of previous.values()) {
      node.fx = null; node.fy = null; node.vx = 0; node.vy = 0;
    }

    const nodes = visibleNodes.map((item, i): Node => {
      const old = previous.get(item.id);
      const neighborId = props.graph.edges.find(e => e.source === item.id && previous.has(e.target) || e.target === item.id && previous.has(e.source));
      const anchor = neighborId ? previous.get(neighborId.source === item.id ? neighborId.target : neighborId.source) : undefined;
      return {
        ...old,
        id: item.id,
        item,
        x: old?.x ?? (anchor?.x ?? 0) + Math.cos(i * 2.4) * 100,
        y: old?.y ?? (anchor?.y ?? 0) + Math.sin(i * 2.4) * 100,
        radius: browserNodeRadius(counts.get(item.id) ?? 0, appearance.nodeScale * (styles['node:' + item.entity_type]?.scale ?? 1)),
      };
    });

    const plan = planKnowledgeLayout(nodes, edges, counts, activeLens, isContextDimmed ? lensMatch!.nodeIds : null);
    // Added nodes acquire panorama anchors without moving existing background nodes.
    if (!firstLayout) for (const node of nodes) if (!panorama.current.has(node.id)) {
      panorama.current.set(node.id, { x: node.x, y: node.y });
    }
    if (layoutFrame.current) fitLayoutToPanorama(plan, layoutFrame.current, panorama.current);
    if (activeLens === 'all' && layoutMode === 'force' && !firstLayout) for (const node of nodes) {
      const anchor = panorama.current.get(node.id);
      if (anchor) plan.targets.set(node.id, { ...anchor });
    }
    const { targets: layerTargets, bounds: layerBounds, ringBounds, bands: nextBands, radial: nextRadial,
      contextSet } = plan;
    layerBoundsRef.current = layoutMode === 'layered' ? layerBounds : new Map();
    ringBoundsRef.current = layoutMode === 'layered' ? ringBounds : new Map();
    for (const node of nodes) {
      if (modeChanged || lensChanged || contextChanged) {
        node.fx = null; node.fy = null; node.vx = 0; node.vy = 0;
      }
      const target = layerTargets.get(node.id);
      if (layoutMode === 'layered' && target && (firstLayout || !previous.has(node.id))) {
        node.x = target.x; node.y = target.y;
      }
    }

    setBands(nextBands);
    setRadialGuide(nextRadial);
    nodesRef.current = nodes;
    setSceneNodes(nodes);
    for (const node of nodes) nodeCache.current.set(node.id, node);

    simulation.current?.stop();
    const links: SimulationLinkDatum<Node>[] = edges.filter(e => e.source !== e.target).map(e => ({ source: e.source, target: e.target }));
    const sim = forceSimulation(nodes).stop()
      .force('collide', forceCollide<Node>().radius(n => n.radius + 14).iterations(3));

    if (layoutMode === 'layered') {
      sim
        .force('link', forceLink<Node, SimulationLinkDatum<Node>>(links).id(n => n.id).distance(link => {
          const a = link.source as Node, b = link.target as Node;
          return a.radius + b.radius + appearance.linkDistance;
        }).strength(0.02))
        .force('charge', forceManyBody<Node>().strength(-appearance.repulsion * 0.25))
        .force('layerX', forceX<Node>(n => layerTargets.get(n.id)?.x ?? 0).strength(0.15))
        .force('layerY', forceY<Node>(n => layerTargets.get(n.id)?.y ?? 0).strength(0.12));


    } else {
      // Natural force-directed mode with subtle lens-specific directional/radial forces
      sim
        .force('link', forceLink<Node, SimulationLinkDatum<Node>>(links).id(n => n.id).distance(link => {
          const a = link.source as Node, b = link.target as Node;
          return a.radius + b.radius + appearance.linkDistance;
        }).strength(0.15))
        .force('charge', forceManyBody<Node>().strength(-appearance.repulsion * 3))
        .force('center', forceCenter(0, 0).strength(0.03));

      if (activeLens === 'lineage') {
        sim.force('lensX', forceX<Node>((n) => {
          if (contextSet.has(n.id)) return 0;
          const idx = nodeLensLayerIndex(n.item, 'lineage');
          return (idx - 1.5) * 260;
        }).strength(n => contextSet.has(n.id) ? 0 : 0.06));
      } else if (activeLens === 'diagnosis') {
        sim.force('lensX', forceX<Node>((n) => {
          if (contextSet.has(n.id)) return 0;
          const idx = nodeLensLayerIndex(n.item, 'diagnosis');
          return idx === 0 ? -280 : idx === 1 ? 0 : 280;
        }).strength(n => contextSet.has(n.id) ? 0 : (nodeLensLayerIndex(n.item, 'diagnosis') === 1 ? 0.08 : 0.045)));
      } else if (activeLens === 'ecosystem') {
        sim.force('lensRadial', forceRadial<Node>((n) => {
          if (contextSet.has(n.id)) return 0;
          return nodeLensLayerIndex(n.item, 'ecosystem') === 0 ? 70 : 320;
        }, 0, 0).strength(n => contextSet.has(n.id) ? 0 : (nodeLensLayerIndex(n.item, 'ecosystem') === 0 ? 0.08 : 0.05)));
      }
      if (contextSet.size) {
        sim.force('contextX', forceX<Node>(n => panorama.current.get(n.id)?.x ?? n.x).strength(n => contextSet.has(n.id) ? 0.15 : 0));
        sim.force('contextY', forceY<Node>(n => panorama.current.get(n.id)?.y ?? n.y).strength(n => contextSet.has(n.id) ? 0.15 : 0));
      }
    }

    simulation.current = sim;
    const constrain = () => {
      if (layoutMode === 'layered') for (const node of nodes) {
        const bounds = layerBounds.get(node.id);
        if (bounds) containLayerNode(node, bounds);
        const ring = ringBounds.get(node.id);
        if (ring !== undefined) containRadialNode(node, ring);
      }
    };
    const tick = () => { constrain(); scheduleDraw(); };
    sim.on('tick', tick).on('end', tick);
    if (firstLayout) {
      sim.tick(160);
      constrain();
      // The original panorama defines one frame for every semantic layout.
      // Lens changes preserve this camera, including any subsequent user zoom/pan.
      for (const node of nodes) panorama.current.set(node.id, { x: node.x, y: node.y });
      const halfWidth = Math.max(700, ...nodes.map(n => Math.abs(n.x) + n.radius + 80));
      const halfHeight = Math.max(350, ...nodes.map(n => Math.abs(n.y) + n.radius + 80));
      layoutFrame.current = { left: -halfWidth, right: halfWidth, top: -halfHeight, bottom: halfHeight };
      setView(computeFitView([{ x: -halfWidth, y: -halfHeight, radius: 0 }, { x: halfWidth, y: halfHeight, radius: 0 }]));
      sim.alpha(0.08).restart();
    } else if (modeChanged || lensChanged || contextChanged) {
      cancelTransition();
      const starts = new Map(nodes.map(n => [n.id, { x: n.x, y: n.y }]));
      let started: number | undefined;
      const migrate = (now: number) => {
        started ??= now;
        const progress = Math.min(1, (now - started) / 650);
        const eased = 1 - Math.pow(1 - progress, 3);
        for (const node of nodes) {
          const target = layerTargets.get(node.id), start = starts.get(node.id);
          if (!target || !start || node.fx != null) continue;
          node.x = start.x + (target.x - start.x) * eased;
          node.y = start.y + (target.y - start.y) * eased;
        }
        scheduleDraw();
        if (progress < 1) transitionFrame.current = requestAnimationFrame(migrate);
        else { transitionFrame.current = 0; constrain(); sim.alpha(0.12).restart(); }
      };
      transitionFrame.current = requestAnimationFrame(migrate);
    } else {
      constrain(); sim.alpha(0.22).restart();
    }
    scheduleDraw();
    return () => { sim.stop(); cancelTransition(); cancelAnimationFrame(frame.current); frame.current = 0; };
  }, [props.graph, hidden, appearance.nodeScale, appearance.repulsion, appearance.linkDistance, layoutMode, activeLens, lensContextMode, nodeScales]);

  // Resizing the data panel preserves the observed graph center and scale.
  const previousSize = useRef(size);
  useEffect(() => {
    const old = previousSize.current; previousSize.current = size;
    setView(v => ({ ...v, x: v.x + (size.width - old.width) / 2, y: v.y + (size.height - old.height) / 2 }));
  }, [size]);
  const nodes = sceneNodes;
  const byId = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);

  function select(node: Node | null) {
    setLocalSelection(node?.id ?? ''); props.onSelect?.(node?.item ?? null); setMenu(null);
  }
  function dismiss(node?: string, edge?: string) {
    history.current.push(hidden);
    setHidden({ nodes: new Set([...hidden.nodes, ...(node ? [node] : [])]), edges: new Set([...hidden.edges, ...(edge ? [edge] : [])]) });
    if (node === selectedId || edge === props.selectedEdge) select(null);
    setMenu(null);
  }
  function undo() { const previous = history.current.pop(); if (previous) setHidden(previous); setMenu(null); }
  function pin(node: Node) {
    if (node.fx != null) { node.fx = null; node.fy = null; simulation.current?.alpha(0.2).restart(); }
    else { node.fx = node.x; node.fy = node.y; }
    scheduleDraw(); setMenu(null); updateControls(value => value + 1);
  }
  function begin(event: ReactPointerEvent, node?: Node) {
    if (event.button !== 0 || (event.target as Element).closest('button')) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id: node?.id, x: event.clientX, y: event.clientY, nodeX: node?.x, nodeY: node?.y, view: { ...viewRef.current }, moved: false, pointer: event.pointerId, capture: event.currentTarget };
    moved.current = false; setMenu(null);
  }
  function move(event: ReactPointerEvent) {
    const state = drag.current;
    if (!state || state.pointer !== event.pointerId) return;
    const dx = event.clientX - state.x, dy = event.clientY - state.y;
    if (Math.hypot(dx, dy) < 3 && !state.moved) return;
    state.moved = true; moved.current = true;
    if (state.id) {
      cancelTransition();
      const node = byId.get(state.id);
      if (!node) return;
      node.x = node.fx = state.nodeX! + dx / state.view.k;
      node.y = node.fy = state.nodeY! + dy / state.view.k;
      const bounds = layerBoundsRef.current.get(node.id);
      if (bounds) containLayerNode(node, bounds);
      const ring = ringBoundsRef.current.get(node.id);
      if (ring !== undefined) containRadialNode(node, ring);
      simulation.current?.alpha(0.12).restart(); scheduleDraw();
    } else setView({ ...state.view, x: state.view.x + dx, y: state.view.y + dy });
  }
  function context(event: React.MouseEvent, node?: string, edge?: string) {
    event.preventDefault(); event.stopPropagation();
    const box = svg.current!.getBoundingClientRect();
    setMenu({ x: Math.min(size.width - 175, Math.max(0, event.clientX - box.left)),
      y: Math.min(size.height - 230, Math.max(65, event.clientY - box.top)), node, edge });
  }
  function style(key: string, patch: Style) { setStyles(s => ({ ...s, [key]: { ...s[key], ...patch } })); }

  paintRef.current = (geometry) => {
    const v = viewRef.current;
    motion.current?.paint(v, geometry);
    const selected = byId.get(selectedId);
    if (selected && toolbar.current) {
      toolbar.current.style.left = `${Math.max(8, Math.min(size.width - 285, selected.x * v.k + v.x - 130))}px`;
      toolbar.current.style.top = `${Math.max(78, Math.min(size.height - 70, (selected.y - selected.radius) * v.k + v.y - 42))}px`;
    }
    if (rulerRef.current && bands.length > 0) {
      const items = Array.from(rulerRef.current.querySelectorAll<HTMLElement>('.browser-ruler-item'));
      const screenXs = bands.map(band => band.x * v.k + v.x);
      let minGap = Number.POSITIVE_INFINITY;
      for (let i = 1; i < screenXs.length; i++) minGap = Math.min(minGap, screenXs[i] - screenXs[i - 1]);
      const inRange = screenXs.filter(x => x >= 60 && x <= size.width - 60).length >= Math.min(2, screenXs.length);
      if (bands.length > 1 && minGap >= 150 && inRange) {
        rulerRef.current.dataset.mode = 'tracked';
        items.forEach((item, idx) => {
          const sx = screenXs[idx];
          if (sx !== undefined) item.style.left = `${Math.max(65, Math.min(size.width - 105, sx))}px`;
        });
      } else {
        rulerRef.current.dataset.mode = 'compact';
        items.forEach((item) => { item.style.left = ''; });
      }
    }
  };

  useLayoutEffect(() => {
    if (!world.current) return;
    motion.current ??= new BrowserMotion(world.current);
    const nodeElements = new Map(Array.from(world.current.querySelectorAll<SVGGElement>('[data-node-id]'), element => [element.dataset.nodeId, element]));
    const edgeElements = new Map(Array.from(world.current.querySelectorAll<SVGGElement>('[data-edge-id]'), element => [element.dataset.edgeId, element]));
    motion.current.nodes = nodes.flatMap(node => {
      const element = nodeElements.get(node.id); if (!element) return [];
      const config = styles['node:' + node.item.entity_type] ?? {};
      const label = config.caption === 'none' ? '' : config.caption === 'type' ? ENTITY_LABELS[node.item.entity_type ?? ''] ?? '实体' : config.caption === 'id' ? node.id : node.item.label;
      const isMatchedNode = !lensMatch || lensMatch.nodeIds.has(node.id);
      return [{
        node,
        element,
        label,
        enabled: appearance.showLabels && (!isContextDimmed || isMatchedNode),
        emphasized: node.id === selectedId || node.id === hovered || Boolean(pathFocus?.nodeIds.has(node.id)),
      }];
    });
    motion.current.edges = edges.flatMap(edge => {
      const element = edgeElements.get(edge.id), source = byId.get(edge.source), target = byId.get(edge.target);
      if (!element || !source || !target) return [];
      const config = styles['edge:' + edge.predicate] ?? {}, curve = curves.get(edge.id);
      const semantics = edgeSemantics.get(edge.id) ?? classifyEdgeSemantics(edge);
      const baseLabel = PREDICATE_LABELS[edge.predicate] ?? edge.predicate;
      const label = config.caption === 'none' ? '' : config.caption === 'id' ? edge.id : config.caption === 'type' ? edge.predicate : formatEdgeCaption(baseLabel, semantics);
      return [{
        element,
        source,
        target,
        label,
        offset: curve?.curveOffset ?? 0,
        loop: ((curve?.loopDist ?? 35) - 35) / 24,
        emphasized: edge.id === hoveredEdge || edge.id === props.selectedEdge || Boolean(pathFocus?.edgeIds.has(edge.id)),
        showLabel: Boolean(appearance.showRelationLabels),
        defaultColor: semantics.status === 'asserted' ? lensEdgeColor(edge, activeLens) : semantics.strokeColor,
      }];
    });
    paintRef.current(true);
  });
  const selected = byId.get(selectedId);
  const menuNode = menu?.node ? byId.get(menu.node) : null;
  const types = [...new Set(visibleNodes.map(n => n.entity_type ?? 'video'))];
  const styleValue = styles[styleKey] ?? {};
  const expanded = props.expandedIds?.includes(selectedId);
  function actions(node: Node) {
    return <><button type="button" disabled={!!props.expandingId} onClick={() => void props.onExpand(node.item)}>{props.expandedIds?.includes(node.id) ? '收起邻居' : '展开邻居'}</button>
      <button type="button" onClick={() => pin(node)}>{node.fx != null ? '解除固定' : '固定位置'}</button>
      <button type="button" onClick={() => dismiss(node.id)}>隐藏节点</button></>;
  }
  return <div className="browser-graph interactive-graph-shell" aria-label="Neo4j 风格二维图谱">
    <div className="browser-top-hud">
      <div className="browser-overview" aria-label="图谱类型与样式">
        <span className="browser-overview-heading">节点 <b>{nodes.length}</b></span>
        {types.map(type => <button type="button" key={type} aria-label={`设置${ENTITY_LABELS[type] ?? type}节点样式`} onClick={() => setStyleKey(styleKey === 'node:' + type ? '' : 'node:' + type)} style={{ borderColor: styles['node:' + type]?.color ?? ENTITY_COLORS[type] ?? '#a5abb6' }}>
          <i style={{ background: styles['node:' + type]?.color ?? ENTITY_COLORS[type] ?? '#a5abb6' }} />{ENTITY_LABELS[type] ?? type} <small>{visibleNodes.filter(n => (n.entity_type ?? 'video') === type).length}</small></button>)}
      </div>
      {layoutMode === 'layered' && (bands.length > 0 || radialGuide) ? (
        <div ref={rulerRef} className="browser-stage-ruler" data-mode="compact" aria-label="视角结构标尺">
          {bands.length > 0 ? (
            <div className="browser-ruler-items">
              {bands.map((band, idx) => (
                <span key={band.index} className="browser-ruler-item" data-layer-index={band.index}>
                  <span className="browser-ruler-pill">
                    <strong>{band.label}</strong>
                    <small>{band.count}</small>
                  </span>
                  {idx < bands.length - 1 ? <em className="browser-ruler-arrow" aria-hidden="true">{band.arrowAfter ?? '→'}</em> : null}
                </span>
              ))}
            </div>
          ) : radialGuide ? (
            <div className="browser-ruler-items">
              <span className="browser-ruler-pill"><strong>内圈：{radialGuide.innerLabel}</strong><small>{radialGuide.innerCount}</small></span>
              <em className="browser-ruler-arrow" aria-hidden="true">·</em>
              <span className="browser-ruler-pill"><strong>外圈：{radialGuide.outerLabel}</strong><small>{radialGuide.outerCount}</small></span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
    {styleKey ? <div className="browser-style-panel" aria-label="类型样式设置"><strong>{styleKey.startsWith('node:') ? '节点样式' : '关系样式'}</strong>
      <label>颜色<input type="color" value={styleValue.color ?? (styleKey.startsWith('node:') ? ENTITY_COLORS[styleKey.slice(5)] ?? '#a5abb6' : '#8b929b')} onChange={e => style(styleKey, { color: e.target.value })} /></label>
      <label>{styleKey.startsWith('node:') ? '大小' : '线宽'}<input aria-label="类型尺寸" type="range" min="0.6" max="2" step="0.1" value={styleValue.scale ?? 1} onChange={e => style(styleKey, { scale: Number(e.target.value) })} /></label>
      <label>显示文字<select value={styleValue.caption ?? 'name'} onChange={e => style(styleKey, { caption: e.target.value as Style['caption'] })}>
        <option value="name">名称</option><option value="type">类型</option><option value="id">ID</option><option value="none">不显示</option></select></label>
      <button type="button" onClick={() => setStyleKey('')}>关闭样式设置</button></div> : null}
    <svg ref={svg} className="browser-graph-svg" data-ready="true" data-dimension="2d" data-layout={layoutMode} data-lens={activeLens} data-lens-geometry={schema.geometry} data-context-mode={lensContextMode} aria-label="可拖拽的二维知识图谱" role="group"
      onPointerDown={e => begin(e)} onPointerMove={move} onPointerUp={e => { if (drag.current?.id && drag.current.moved) updateControls(value => value + 1); drag.current?.capture.releasePointerCapture(e.pointerId); drag.current = undefined; }}
      onPointerCancel={() => { drag.current = undefined; }} onClick={() => { if (!moved.current) select(null); }} onContextMenu={e => context(e)}>
      <rect width="100%" height="100%" fill="#ffffff" />
      <defs>
        <marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M0,0 L10,5 L0,10 Z" fill="context-stroke" /></marker>
        <linearGradient id={`${uid}-band-fade`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopOpacity="0" stopColor="#ffffff" />
          <stop offset="12%" stopOpacity="1" stopColor="#ffffff" />
          <stop offset="88%" stopOpacity="1" stopColor="#ffffff" />
          <stop offset="100%" stopOpacity="0" stopColor="#ffffff" />
        </linearGradient>
      </defs>
      <g ref={world}>
        {layoutMode === 'layered' && bands.length > 0 ? bands.map(band => (
          <g key={band.index} className="browser-layer-band" data-layer-index={band.index} pointerEvents="none">
            <rect x={band.x - band.width / 2} y={band.top} width={band.width} height={band.bottom - band.top} rx={32} fill={band.tint} />
            <line x1={band.x} y1={band.top + 16} x2={band.x} y2={band.bottom - 16} stroke="#e6e0ef" strokeOpacity="0.5" strokeWidth="1" />
          </g>
        )) : null}
        {layoutMode === 'layered' && radialGuide ? (
          <g className="browser-radial-guide" pointerEvents="none">
            <circle cx={0} cy={0} r={radialGuide.outerRadius} fill={radialGuide.outerTint} stroke="#e4deec" strokeOpacity="0.6" strokeDasharray="3 6" strokeWidth="1.2" />
            <circle cx={0} cy={0} r={radialGuide.innerRadius} fill={radialGuide.innerTint} stroke="#cfdce8" strokeOpacity="0.75" strokeDasharray="3 6" strokeWidth="1.2" />
          </g>
        ) : null}
        {edges.map(edge => {
          const a = byId.get(edge.source), b = byId.get(edge.target); if (!a || !b) return null;
          const config = styles['edge:' + edge.predicate] ?? {};
          const semantics = edgeSemantics.get(edge.id) ?? classifyEdgeSemantics(edge);
          const baseLabel = PREDICATE_LABELS[edge.predicate] ?? edge.predicate;
          const label = config.caption === 'none' ? '' : config.caption === 'id' ? edge.id : config.caption === 'type' ? edge.predicate : formatEdgeCaption(baseLabel, semantics);
          const chosen = props.selectedEdge === edge.id;
          const inPath = Boolean(pathFocus?.edgeIds.has(edge.id));
          const isMatchedEdge = !lensMatch || lensMatch.edgeIds.has(edge.id);
          const color = chosen ? '#2879bd' : inPath ? '#7b529d' : config.color ?? lensEdgeColor(edge, activeLens);
          const dimForPath = pathFocus && !inPath && !chosen;
          const dimForContext = isContextDimmed && !isMatchedEdge && !chosen && !inPath;
          const dimForHover = active && !neighbors.has(edge.source) && !neighbors.has(edge.target);
          const evidenceBoost = 1.4 + Math.min(1.2, Math.log2(1 + Math.max(0, edge.evidence_count)) * 0.35);
          return <g key={edge.id} data-edge-id={edge.id} data-assertion-status={semantics.status} data-qualifier-count={semantics.qualifierCount}
            role="button" tabIndex={0} aria-label={`${a.item.label} ${label} ${b.item.label}`}
            opacity={dimForPath ? 0.14 : dimForContext ? 0.07 : dimForHover ? 0.2 : 1}
            onClick={e => { e.stopPropagation(); if (!moved.current) props.onEdge(edge); }} onContextMenu={e => context(e, undefined, edge.id)}
            onPointerEnter={() => { if (!drag.current) setHoveredEdge(edge.id); }} onPointerLeave={() => setHoveredEdge('')}
            onKeyDown={e => { if (e.key === 'Enter') props.onEdge(edge); }} style={{ cursor: 'pointer' }}>
            <title>{`${a.item.label} → ${baseLabel} → ${b.item.label} · ${edge.evidence_count} 条证据 · ${semantics.statusLabel}${semantics.qualifierCount ? ` · 含 ${semantics.qualifierCount} 项条件` : ''}`}</title>
            <path stroke="transparent" strokeWidth="18" fill="none" />
            <path stroke={color} strokeDasharray={semantics.strokeDasharray}
              strokeWidth={(chosen || inPath ? 3 : evidenceBoost) * appearance.linkScale * (config.scale ?? 1)}
              fill="none" markerEnd={appearance.showArrows ? `url(#${uid}-arrow)` : undefined} />
          </g>;
        })}
        {nodes.map(node => {
          const config = styles['node:' + node.item.entity_type] ?? {};
          const color = config.color ?? ENTITY_COLORS[node.item.entity_type ?? 'video'] ?? '#8795aa';
          const chosen = node.id === selectedId;
          const inPath = Boolean(pathFocus?.nodeIds.has(node.id));
          const isMatchedNode = !lensMatch || lensMatch.nodeIds.has(node.id);
          const focal = isMatchedNode ? isLensFocalNode(node.item, activeLens) : { focal: false, ringColor: '' };
          const dimForPath = pathFocus && !inPath && !chosen && node.id !== hovered;
          const dimForContext = isContextDimmed && !isMatchedNode && !chosen && !inPath && node.id !== hovered;
          const dimForHover = active && !neighbors.has(node.id);
          return <g key={node.id} data-node-id={node.id} data-relation-count={counts.get(node.id) ?? 0}
            data-layer-index={layoutMode === 'layered' && layerBoundsRef.current.has(node.id) ? nodeLensLayerIndex(node.item, activeLens) : undefined}
            role="button" tabIndex={0} aria-label={`实体 ${node.item.label}`} style={{ cursor: 'grab' }}
            opacity={dimForPath ? 0.16 : dimForContext ? 0.14 : dimForHover ? 0.22 : 1}
            onPointerDown={e => begin(e, node)} onClick={e => { e.stopPropagation(); if (!moved.current) select(node); }} onDoubleClick={e => { e.stopPropagation(); void props.onExpand(node.item); }}
            onContextMenu={e => context(e, node.id)} onPointerEnter={() => { if (!drag.current) { setHovered(node.id); props.onHover?.(node.item); } }}
            onPointerLeave={() => { setHovered(''); props.onHover?.(null); }} onKeyDown={e => { if (e.key === 'Enter') select(node); }}>
            <title>{node.item.label} · {counts.get(node.id) ?? 0} 条关系{node.fx != null ? ' · 已固定' : ''}</title>
            {focal.focal ? <circle className="browser-focal-ring" r={node.radius + 5} fill="none" stroke={focal.ringColor} strokeOpacity="0.28" strokeWidth="4" /> : null}
            {(chosen || node.id === hovered || inPath) ? <circle r={node.radius + 6} fill="none" stroke={inPath && !chosen ? '#b99ae3' : '#a8c8e4'} strokeWidth={chosen ? 6 : 3} /> : null}
            <circle r={node.radius} fill={color} stroke="#ffffff" strokeWidth="2" />
            <circle data-pin="true" cx={node.radius * 0.7} cy={-node.radius * 0.7} r="4" fill="#253b50" stroke="#fff" strokeWidth="1.5" />
          </g>;
        })}
      </g>
    </svg>
    {selected && !menu ? <div ref={toolbar} className="browser-node-actions" aria-label={`${selected.item.label}节点操作`} style={{ left: Math.max(8, Math.min(size.width - 285, selected.x * view.k + vOr0(view.x) - 130)), top: Math.max(78, Math.min(size.height - 70, (selected.y - selected.radius) * view.k + view.y - 42)) }}>{actions(selected)}</div> : null}
    {menu ? <div className="browser-context-menu" role="menu" style={{ left: menu.x, top: menu.y }}>
      {menuNode ? actions(menuNode) : menu.edge ? <button type="button" onClick={() => dismiss(undefined, menu.edge)}>隐藏关系</button> : null}
      <button type="button" disabled={!history.current.length} onClick={undo}>撤销隐藏</button>
      <button type="button" onClick={() => { history.current.push(hidden); setHidden({ nodes: new Set(), edges: new Set() }); setMenu(null); }}>显示已隐藏元素</button>
      <button type="button" onClick={() => setMenu(null)}>关闭菜单</button>
    </div> : null}
    {history.current.length ? <div className="browser-graph-tools"><button type="button" onClick={undo}>撤销隐藏</button></div> : null}
    <div className="interactive-graph-controls"><div className="graph-viewport-controls">
      <button type="button" aria-label="放大图谱" onClick={() => zoom(1.25)}>＋</button><button type="button" aria-label="缩小图谱" onClick={() => zoom(0.8)}>−</button>
      <button type="button" onClick={fit}>适应画布</button><button type="button" onClick={() => simulation.current?.alpha(0.8).restart()}>重新布局</button>
      <button type="button" onClick={props.onGlobal}>全局图谱</button>
    </div></div>
    {!nodes.length ? <p className="browser-empty">当前元素已隐藏，可撤销隐藏或刷新图谱。</p> : null}
    <span className="sr-only">{expanded ? '选中节点已展开' : ''}</span>
  </div>;
}

function vOr0(value: number) { return Number.isFinite(value) ? value : 0; }
