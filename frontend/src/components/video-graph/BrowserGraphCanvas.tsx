import { useEffect, useLayoutEffect, useId, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force';
import type { Simulation, SimulationNodeDatum, SimulationLinkDatum } from 'd3-force';
import type { VideoGraphNode } from '../../api/types';
import type { GraphCanvasProps } from './InteractiveGraphCanvas';
import { nodeRelationCounts, parallelEdgeStyles } from './data';
import { DEFAULT_GRAPH_APPEARANCE, ENTITY_COLORS, ENTITY_LABELS, PREDICATE_LABELS } from './presentation';
import { browserNodeRadius } from './browser-geometry';
import { BrowserMotion } from './browser-motion';
import './browser-graph.css';

type Node = SimulationNodeDatum & { id: string; x: number; y: number; radius: number; item: VideoGraphNode };
type View = { x: number; y: number; k: number };
type Style = { color?: string; scale?: number; caption?: 'name' | 'type' | 'id' | 'none' };
type Hidden = { nodes: Set<string>; edges: Set<string> };

export function BrowserGraphCanvas(props: GraphCanvasProps) {
  const appearance = props.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const uid = useId().replace(/:/g, '');
  const svg = useRef<SVGSVGElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const nodeCache = useRef(new Map<string, Node>());
  const simulation = useRef<Simulation<Node, undefined>>();
  const frame = useRef(0);
  const [sceneNodes, setSceneNodes] = useState<Node[]>([]);
  const [, updateControls] = useState(0);
  const world = useRef<SVGGElement>(null);
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
  const [find, setFind] = useState('');
  const [notice, setNotice] = useState('');
  const drag = useRef<{ id?: string; x: number; y: number; nodeX?: number; nodeY?: number; moved: boolean; view: View; pointer: number; capture: Element }>();
  const moved = useRef(false);
  const counts = useMemo(() => nodeRelationCounts(props.graph.edges), [props.graph]);
  const visibleNodes = useMemo(() => props.graph.nodes.filter(n => !hidden.nodes.has(n.id)), [props.graph, hidden]);
  const edges = useMemo(() => {
    const ids = new Set(visibleNodes.map(n => n.id));
    return props.graph.edges.filter(e => !hidden.edges.has(e.id) && ids.has(e.source) && ids.has(e.target));
  }, [props.graph, hidden, visibleNodes]);
  const curves = useMemo(() => parallelEdgeStyles(edges), [edges]);
  const nodeScales = JSON.stringify(Object.entries(styles).filter(([key, value]) => key.startsWith('node:') && value.scale !== undefined).map(([key, value]) => [key, value.scale]));
  const active = hovered || selectedId;
  const neighbors = new Set([active, ...edges.filter(e => e.source === active || e.target === active).flatMap(e => [e.source, e.target])]);

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
  function fit() {
    const nodes = nodesRef.current;
    if (!nodes.length) return;
    const left = Math.min(...nodes.map(n => n.x - n.radius)), right = Math.max(...nodes.map(n => n.x + n.radius));
    const top = Math.min(...nodes.map(n => n.y - n.radius - 60)), bottom = Math.max(...nodes.map(n => n.y + n.radius));
    const k = Math.min(1.5, (size.width - 110) / Math.max(1, right - left), (size.height - 150) / Math.max(1, bottom - top));
    setView({ k: Math.max(0.05, k), x: size.width / 2 - (left + right) / 2 * k, y: size.height / 2 - (top + bottom) / 2 * k });
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
    return () => { observer.disconnect(); element.removeEventListener('wheel', wheel); };
  }, []);
  useEffect(() => {
    const previous = nodeCache.current;
    const nodes = visibleNodes.map((item, i): Node => {
      const old = previous.get(item.id);
      const neighborId = props.graph.edges.find(e => e.source === item.id && previous.has(e.target) || e.target === item.id && previous.has(e.source));
      const anchor = neighborId ? previous.get(neighborId.source === item.id ? neighborId.target : neighborId.source) : undefined;
      return { ...old, id: item.id, item, x: old?.x ?? (anchor?.x ?? 0) + Math.cos(i * 2.4) * 100,
        y: old?.y ?? (anchor?.y ?? 0) + Math.sin(i * 2.4) * 100,
        radius: browserNodeRadius(counts.get(item.id) ?? 0, appearance.nodeScale * (styles['node:' + item.entity_type]?.scale ?? 1)) };
    });
    nodesRef.current = nodes;
    setSceneNodes(nodes);
    for (const node of nodes) nodeCache.current.set(node.id, node);
    simulation.current?.stop();
    const links: SimulationLinkDatum<Node>[] = edges.filter(e => e.source !== e.target).map(e => ({ source: e.source, target: e.target }));
    const sim = forceSimulation(nodes).stop()
      .force('link', forceLink<Node, SimulationLinkDatum<Node>>(links).id(n => n.id).distance(link => {
        const a = link.source as Node, b = link.target as Node;
        return a.radius + b.radius + appearance.linkDistance;
      }).strength(0.15))
      .force('charge', forceManyBody<Node>().strength(-appearance.repulsion * 3))
      .force('collide', forceCollide<Node>().radius(n => n.radius + 16).iterations(3))
      .force('center', forceCenter(0, 0).strength(0.03));
    const firstLayout = !simulation.current;
    simulation.current = sim;
    if (firstLayout) { sim.tick(160); fit(); }
    sim.on('tick', scheduleDraw).alpha(firstLayout ? 0.08 : 0.25).restart();
    scheduleDraw();
    return () => { sim.stop(); cancelAnimationFrame(frame.current); frame.current = 0; };
  }, [props.graph, hidden, appearance.nodeScale, appearance.repulsion, appearance.linkDistance, nodeScales]);
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
      const node = byId.get(state.id);
      if (!node) return;
      node.x = node.fx = state.nodeX! + dx / state.view.k;
      node.y = node.fy = state.nodeY! + dy / state.view.k;
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
  async function download(format: 'svg' | 'png') {
    try {
      paintRef.current(true);
      const copy = svg.current!.cloneNode(true) as SVGSVGElement;
      copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      copy.setAttribute('width', String(size.width)); copy.setAttribute('height', String(size.height));
      copy.querySelectorAll('.browser-node-actions').forEach(n => n.remove());
      const blob = new Blob([new XMLSerializer().serializeToString(copy)], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      let output = url;
      try {
        if (format === 'png') {
          const image = new Image(); image.src = url; await image.decode();
          const canvas = document.createElement('canvas'); canvas.width = size.width * 2; canvas.height = size.height * 2;
          const context = canvas.getContext('2d'); if (!context) throw Error('Canvas unavailable');
          context.drawImage(image, 0, 0, canvas.width, canvas.height); output = canvas.toDataURL('image/png');
        }
        const link = document.createElement('a'); link.download = 'knowledge-graph.' + format; link.href = output; link.click();
      } finally { URL.revokeObjectURL(url); }
    } catch { setNotice('导出失败，请重试。'); }
  }
  paintRef.current = (geometry) => {
    motion.current?.paint(viewRef.current, geometry);
    const selected = byId.get(selectedId);
    if (selected && toolbar.current) {
      const v = viewRef.current;
      toolbar.current.style.left = `${Math.max(8, Math.min(size.width - 285, selected.x * v.k + v.x - 130))}px`;
      toolbar.current.style.top = `${Math.max(78, Math.min(size.height - 70, (selected.y - selected.radius) * v.k + v.y - 42))}px`;
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
      return [{ node, element, label, enabled: appearance.showLabels, emphasized: node.id === selectedId || node.id === hovered }];
    });
    motion.current.edges = edges.flatMap(edge => {
      const element = edgeElements.get(edge.id), source = byId.get(edge.source), target = byId.get(edge.target);
      if (!element || !source || !target) return [];
      const config = styles['edge:' + edge.predicate] ?? {}, curve = curves.get(edge.id);
      const label = config.caption === 'none' ? '' : config.caption === 'id' ? edge.id : config.caption === 'type' ? edge.predicate : PREDICATE_LABELS[edge.predicate] ?? edge.predicate;
      return [{ element, source, target, label, offset: curve?.curveOffset ?? 0, loop: ((curve?.loopDist ?? 35) - 35) / 24, emphasized: edge.id === props.selectedEdge || edge.id === hoveredEdge }];
    });
    paintRef.current(true);
  });
  const selected = byId.get(selectedId);
  const menuNode = menu?.node ? byId.get(menu.node) : null;
  const types = [...new Set(visibleNodes.map(n => n.entity_type ?? 'video'))];
  const predicates = [...new Set(edges.map(e => e.predicate))];
  const styleValue = styles[styleKey] ?? {};
  const expanded = props.expandedIds?.includes(selectedId);
  function actions(node: Node) {
    return <><button type="button" disabled={!!props.expandingId} onClick={() => void props.onExpand(node.item)}>{props.expandedIds?.includes(node.id) ? '收起邻居' : '展开邻居'}</button>
      <button type="button" onClick={() => pin(node)}>{node.fx != null ? '解除固定' : '固定位置'}</button>
      <button type="button" onClick={() => dismiss(node.id)}>隐藏节点</button></>;
  }
  return <div className="browser-graph interactive-graph-shell" aria-label="Neo4j 风格二维图谱">
    <div className="browser-overview" aria-label="图谱类型与样式">
      <span className="browser-overview-heading">节点 <b>{nodes.length}</b></span>
      {types.map(type => <button type="button" key={type} aria-label={`设置${ENTITY_LABELS[type] ?? type}节点样式`} onClick={() => setStyleKey(styleKey === 'node:' + type ? '' : 'node:' + type)} style={{ borderColor: styles['node:' + type]?.color ?? ENTITY_COLORS[type] ?? '#a5abb6' }}>
        <i style={{ background: styles['node:' + type]?.color ?? ENTITY_COLORS[type] ?? '#a5abb6' }} />{ENTITY_LABELS[type] ?? type} <small>{visibleNodes.filter(n => (n.entity_type ?? 'video') === type).length}</small></button>)}
      <span className="browser-overview-heading">关系 <b>{edges.length}</b></span>
      {predicates.map(type => <button type="button" key={type} onClick={() => setStyleKey(styleKey === 'edge:' + type ? '' : 'edge:' + type)}>{PREDICATE_LABELS[type] ?? type} <small>{edges.filter(e => e.predicate === type).length}</small></button>)}
    </div>
    {styleKey ? <div className="browser-style-panel" aria-label="类型样式设置"><strong>{styleKey.startsWith('node:') ? '节点样式' : '关系样式'}</strong>
      <label>颜色<input type="color" value={styleValue.color ?? (styleKey.startsWith('node:') ? ENTITY_COLORS[styleKey.slice(5)] ?? '#a5abb6' : '#8b929b')} onChange={e => style(styleKey, { color: e.target.value })} /></label>
      <label>{styleKey.startsWith('node:') ? '大小' : '线宽'}<input aria-label="类型尺寸" type="range" min="0.6" max="2" step="0.1" value={styleValue.scale ?? 1} onChange={e => style(styleKey, { scale: Number(e.target.value) })} /></label>
      <label>显示文字<select value={styleValue.caption ?? 'name'} onChange={e => style(styleKey, { caption: e.target.value as Style['caption'] })}>
        <option value="name">名称</option><option value="type">类型</option><option value="id">ID</option><option value="none">不显示</option></select></label>
      <button type="button" onClick={() => setStyleKey('')}>关闭样式设置</button></div> : null}
    <svg ref={svg} className="browser-graph-svg" data-ready="true" data-dimension="2d" aria-label="可拖拽的二维知识图谱" role="group"
      onPointerDown={e => begin(e)} onPointerMove={move} onPointerUp={e => { if (drag.current?.id && drag.current.moved) updateControls(value => value + 1); drag.current?.capture.releasePointerCapture(e.pointerId); drag.current = undefined; }}
      onPointerCancel={() => { drag.current = undefined; }} onClick={() => { if (!moved.current) select(null); }} onContextMenu={e => context(e)}>
      <rect width="100%" height="100%" fill="#ffffff" />
      <defs><marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><path d="M0,0 L10,5 L0,10 Z" fill="context-stroke" /></marker></defs>
      <g ref={world}>
        {edges.map(edge => {
          const a = byId.get(edge.source), b = byId.get(edge.target); if (!a || !b) return null;
          const config = styles['edge:' + edge.predicate] ?? {};
          const label = config.caption === 'none' ? '' : config.caption === 'id' ? edge.id : config.caption === 'type' ? edge.predicate : PREDICATE_LABELS[edge.predicate] ?? edge.predicate;
          const chosen = props.selectedEdge === edge.id;
          const color = chosen ? '#2879bd' : config.color ?? '#9199a3';
          return <g key={edge.id} data-edge-id={edge.id} role="button" tabIndex={0} aria-label={`${a.item.label} ${label} ${b.item.label}`}
            opacity={active && !neighbors.has(edge.source) && !neighbors.has(edge.target) ? 0.2 : 1}
            onClick={e => { e.stopPropagation(); if (!moved.current) props.onEdge(edge); }} onContextMenu={e => context(e, undefined, edge.id)}
            onPointerEnter={() => { if (!drag.current) setHoveredEdge(edge.id); }} onPointerLeave={() => setHoveredEdge('')}
            onKeyDown={e => { if (e.key === 'Enter') props.onEdge(edge); }} style={{ cursor: 'pointer' }}>
            <path stroke="transparent" strokeWidth="18" fill="none" />
            <path stroke={color} strokeWidth={(chosen ? 3 : 1.5) * appearance.linkScale * (config.scale ?? 1)} fill="none" markerEnd={appearance.showArrows ? `url(#${uid}-arrow)` : undefined} />

          </g>;
        })}
        {nodes.map(node => {
          const config = styles['node:' + node.item.entity_type] ?? {};
          const color = config.color ?? ENTITY_COLORS[node.item.entity_type ?? 'video'] ?? '#8795aa';
          const label = config.caption === 'none' ? '' : config.caption === 'type' ? ENTITY_LABELS[node.item.entity_type ?? ''] ?? '实体' : config.caption === 'id' ? node.id : node.item.label;
          const match = !find || node.item.label.toLocaleLowerCase().includes(find.toLocaleLowerCase());
          const chosen = node.id === selectedId;
          return <g key={node.id} data-node-id={node.id} data-relation-count={counts.get(node.id) ?? 0}
            role="button" tabIndex={0} aria-label={`实体 ${node.item.label}`} style={{ cursor: 'grab' }} opacity={!match || active && !neighbors.has(node.id) ? 0.22 : 1}
            onPointerDown={e => begin(e, node)} onClick={e => { e.stopPropagation(); if (!moved.current) select(node); }} onDoubleClick={e => { e.stopPropagation(); void props.onExpand(node.item); }}
            onContextMenu={e => context(e, node.id)} onPointerEnter={() => { if (!drag.current) { setHovered(node.id); props.onHover?.(node.item); } }}
            onPointerLeave={() => { setHovered(''); props.onHover?.(null); }} onKeyDown={e => { if (e.key === 'Enter') select(node); }}>
            <title>{node.item.label} · {counts.get(node.id) ?? 0} 条关系{node.fx != null ? ' · 已固定' : ''}</title>
            {(chosen || node.id === hovered) ? <circle r={node.radius + 6} fill="none" stroke="#a8c8e4" strokeWidth={chosen ? 6 : 3} /> : null}
            <circle r={node.radius} fill={color} stroke="#ffffff" strokeWidth="2" />
            <circle data-pin="true" cx={node.radius * 0.7} cy={-node.radius * 0.7} r="4" fill="#253b50" stroke="#fff" strokeWidth="1.5" />
          </g>;
        })}
      </g>
    </svg>
    {selected && !menu ? <div ref={toolbar} className="browser-node-actions" aria-label={`${selected.item.label}节点操作`} style={{ left: Math.max(8, Math.min(size.width - 285, selected.x * view.k + view.x - 130)), top: Math.max(78, Math.min(size.height - 70, (selected.y - selected.radius) * view.k + view.y - 42)) }}>{actions(selected)}</div> : null}
    {menu ? <div className="browser-context-menu" role="menu" style={{ left: menu.x, top: menu.y }}>
      {menuNode ? actions(menuNode) : menu.edge ? <button type="button" onClick={() => dismiss(undefined, menu.edge)}>隐藏关系</button> : null}
      <button type="button" disabled={!history.current.length} onClick={undo}>撤销隐藏</button>
      <button type="button" onClick={() => { history.current.push(hidden); setHidden({ nodes: new Set(), edges: new Set() }); setMenu(null); }}>显示已隐藏元素</button>
      <button type="button" onClick={() => setMenu(null)}>关闭菜单</button>
    </div> : null}
    <div className="browser-graph-tools"><label>查找<input aria-label="在当前画布查找" value={find} onChange={e => setFind(e.target.value)} placeholder="高亮实体…" /></label>
      <button type="button" disabled={!history.current.length} onClick={undo}>撤销隐藏</button>
      <button type="button" onClick={() => void download('svg')}>导出 SVG</button><button type="button" onClick={() => void download('png')}>导出 PNG</button>
    </div>
    <div className="interactive-graph-controls"><div className="graph-viewport-controls">
      <button type="button" aria-label="放大图谱" onClick={() => zoom(1.25)}>＋</button><button type="button" aria-label="缩小图谱" onClick={() => zoom(0.8)}>−</button>
      <button type="button" onClick={fit}>适应画布</button><button type="button" onClick={() => simulation.current?.alpha(0.8).restart()}>重新布局</button>
    </div></div>
    {notice ? <p className="browser-export-notice" role="alert">{notice}</p> : null}
    {!nodes.length ? <p className="browser-empty">当前元素已隐藏，可撤销隐藏或刷新图谱。</p> : null}
    <span className="sr-only">{expanded ? '选中节点已展开' : ''}</span>
  </div>;
}
