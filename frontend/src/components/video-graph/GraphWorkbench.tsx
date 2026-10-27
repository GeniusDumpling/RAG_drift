import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { VideoGraphData, VideoGraphEdge, VideoGraphNode, VideoGraphOverview } from '../../api/types';
import { InteractiveGraphCanvas } from './InteractiveGraphCanvas';
import type { GraphDimension } from './InteractiveGraphCanvas';
import { GraphIcon } from './GraphIcon';
import './obsidian-graph.css';
import { ASSERTION_LABELS, DARK_ENTITY_COLORS, DEFAULT_GRAPH_APPEARANCE, ENTITY_LABELS, PREDICATE_LABELS } from './presentation';
import type { GraphAppearance } from './presentation';
import { MIN_GRAPH_COMPONENT_SIZE } from './limits';

type Props = {
  graph: VideoGraphData | null; overview: VideoGraphOverview | null;
  query: string; onQuery: (query: string) => void; onSearch: (event: FormEvent) => void;
  entityType: string; onEntityType: (value: string) => void;
  assertion: string; onAssertion: (value: string) => void;
  hops: number; onHops: (value: number) => void; focusId: string; onClearFocus: () => void;
  selectedEdge: VideoGraphEdge | null; onEdge: (edge: VideoGraphEdge) => void; onClearEdge: () => void;
  onFocus: (node: VideoGraphNode) => void; onExpand: (node: VideoGraphNode) => Promise<void>;
  expandingId: string; loading: boolean; error: string; expansionError: string; expansionNotice?: string;
  onRefresh: () => void; evidencePanel: ReactNode;
};

const format = (value: number | undefined) => value === undefined ? '—' : value.toLocaleString('zh-CN');

export function GraphWorkbench(props: Props) {
  const [dimension, setDimension] = useState<GraphDimension>(() => {
    try {
      const saved = localStorage.getItem('video-graph-dimension');
      if (saved === '2d' || saved === '3d') return saved;
    } catch { /* Storage can be disabled by the browser. */ }
    return window.innerWidth <= 680 ? '2d' : '3d';
  });
  const [dimensionNotice, setDimensionNotice] = useState('');
  const [labels3D, setLabels3D] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 1200);
  const [panelTab, setPanelTab] = useState<'overview' | 'links'>('overview');
  const [selectedId, setSelectedId] = useState('');
  const [hoveredId, setHoveredId] = useState('');
  const [direction, setDirection] = useState<'all' | 'in' | 'out'>('all');
  const [appearance, setAppearance] = useState<GraphAppearance>({ ...DEFAULT_GRAPH_APPEARANCE });
  const [appliedForces, setAppliedForces] = useState({ repulsion: DEFAULT_GRAPH_APPEARANCE.repulsion, linkDistance: DEFAULT_GRAPH_APPEARANCE.linkDistance });
  useEffect(() => {
    const timer = setTimeout(() => setAppliedForces({ repulsion: appearance.repulsion, linkDistance: appearance.linkDistance }), 220);
    return () => clearTimeout(timer);
  }, [appearance.repulsion, appearance.linkDistance]);
  const search = useRef<HTMLInputElement>(null);
  const nodes = props.graph?.nodes ?? [];
  const edges = props.graph?.edges ?? [];
  // Recreate the WebGL scene when its buffers change size. The official extension
  // caches sphere geometry and cannot safely reuse all resized instance buffers.
  const sceneKey = useMemo(() => props.graph
    ? [...props.graph.nodes.map((node) => node.id), ...props.graph.edges.map((edge) => edge.id)].join(':')
    : '', [props.graph]);
  const selected = nodes.find((node) => node.id === selectedId);
  const hovered = nodes.find((node) => node.id === hoveredId);
  const labels = new Map(nodes.map((node) => [node.id, node.label]));
  const related = edges.filter((edge) => edge.source === selectedId || edge.target === selectedId);
  const incoming = related.filter((edge) => edge.target === selectedId);
  const outgoing = related.filter((edge) => edge.source === selectedId);
  const visibleLinks = direction === 'in' ? incoming : direction === 'out' ? outgoing : related;
  const groups = useMemo(() => Object.entries(ENTITY_LABELS).map(([type, label]) => ({
    type, label, count: nodes.filter((node) => node.entity_type === type).length,
  })).filter((group) => group.count > 0), [props.graph]);

  function selectNode(node: VideoGraphNode | null) {
    setSelectedId(node?.id ?? ''); props.onClearEdge();
    if (node) { setPanelTab('links'); setPanelOpen(true); }
  }
  function selectEdge(edge: VideoGraphEdge) {
    setSelectedId(''); props.onEdge(edge); setPanelTab('links'); setPanelOpen(true);
  }
  function configure<K extends keyof GraphAppearance>(key: K, value: GraphAppearance[K]) {
    if (key === 'showLabels' && dimension === '3d') { setLabels3D(Boolean(value)); return; }
    setAppearance((previous) => ({ ...previous, [key]: value }));
  }
  function changeDimension(value: GraphDimension, message = '') {
    setHoveredId(''); setDimensionNotice(message); setDimension(value);
    try { localStorage.setItem('video-graph-dimension', value); } catch { /* Optional preference. */ }
  }
  const showLabels = dimension === '3d' ? labels3D : appearance.showLabels;
  useEffect(() => {
    if (selectedId && !nodes.some((node) => node.id === selectedId)) setSelectedId('');
  }, [props.graph, selectedId]);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.key === '/' && !target?.closest('input,textarea,select,[contenteditable="true"]')) {
        event.preventDefault(); search.current?.focus();
      }
    }
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, []);

  return <section className="obsidian-workbench" aria-label="知识图谱工作台">
    <div className="ow-toolbar">
      <div className="ow-breadcrumb"><GraphIcon name="graph" size={16} /><span className="ow-scope-label">图谱范围</span><span>/</span>
        <button type="button" onClick={props.onClearFocus} aria-label="查看全局图谱">{props.focusId ? '局部图谱' : '全局图谱'}<GraphIcon name="chevron" size={12} /></button>
      </div>
      <div className="ow-dimension-switch" role="group" aria-label="图谱维度">
        <button type="button" aria-pressed={dimension === '2d'} onClick={() => changeDimension('2d')} title="平面图谱">2D</button>
        <button type="button" aria-pressed={dimension === '3d'} onClick={() => changeDimension('3d')} title="三维图谱，可旋转查看">3D</button>
      </div>
      <form className="ow-search" aria-label="图谱搜索" onSubmit={props.onSearch}>
        <GraphIcon name="search" size={16} /><label className="sr-only" htmlFor="graph-entity-search">实体名称</label>
        <input id="graph-entity-search" ref={search} value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder="搜索实体、技术或产品…" autoComplete="off" />
        {props.query ? <button type="button" aria-label="清空搜索输入" onClick={() => props.onQuery('')}><GraphIcon name="close" size={13} /></button> : <kbd>/</kbd>}
        <button className="sr-only" type="submit">搜索实体</button>
      </form>
      <div className="ow-tools">
        <span className="ow-connection"><i className={props.overview ? '' : 'is-pending'} />{props.overview ? '已连接' : '读取中'}</span>
        <button type="button" aria-label="刷新图谱" title="刷新图谱" disabled={props.loading} onClick={props.onRefresh}><GraphIcon name="refresh" size={16} /></button>
        <button type="button" aria-label={panelOpen ? '收起数据面板' : '打开数据面板'} title="数据面板" aria-expanded={panelOpen} className={panelOpen ? 'is-active' : ''} onClick={() => setPanelOpen((value) => !value)}><GraphIcon name="panel" size={17} /></button>
      </div>
    </div>

    <div className={`ow-body${panelOpen ? '' : ' panel-collapsed'}`}>
      <section className="ow-graph-stage" aria-label="图谱画布区域">
        <div className="ow-canvas-caption"><span className="ow-caption-icon"><GraphIcon name="link" size={13} /></span>
          {props.focusId ? '实体邻域' : '知识网络'}<span className="ow-caption-dot">·</span><span>{format(nodes.length)} 个实体节点</span></div>
        {props.loading ? <div className="ow-loading" role="status"><div className="ow-loading-orbit" /><span>正在连接知识网络…</span></div> : null}
        {props.error ? <div className="ow-stage-message" role="alert"><GraphIcon name="info" size={24} /><p>{props.error}</p><button onClick={props.onRefresh}>重新加载</button></div> : null}
        {props.graph && nodes.length === 0 ? <div className="ow-stage-message"><GraphIcon name="search" size={28} /><h2>没有符合展示条件的图谱</h2><p>当前范围内未找到至少 {MIN_GRAPH_COMPONENT_SIZE} 个节点的连通子图，请尝试其他关键词或调整筛选条件。</p></div> : null}
        {props.graph && nodes.length > 0 ? <InteractiveGraphCanvas
          key={dimension === '3d' ? `${dimension}:${sceneKey}` : dimension} dimension={dimension}
          onUnavailable={(message) => changeDimension('2d', message)} graph={props.graph} focusId={props.focusId}
          selectedEdge={props.selectedEdge?.id ?? ''} expandingId={props.expandingId}
          activeNodeId={selectedId} appearance={{ ...appearance, ...appliedForces, showLabels }} onSelect={selectNode}
          onHover={(node) => setHoveredId(node?.id ?? '')}
          onFocus={props.onFocus} onExpand={props.onExpand} onEdge={selectEdge} /> : null}
        {props.expansionError ? <div className="ow-canvas-notice" role="alert">{props.expansionError}</div> : null}
        {props.expansionNotice ? <div className="ow-canvas-notice" role="status">{props.expansionNotice}</div> : null}
        {props.expandingId ? <div className="ow-canvas-notice" role="status">正在展开邻居，保留已有节点…</div> : null}
        {dimensionNotice ? <div className="ow-canvas-notice" role="status">{dimensionNotice}</div> : null}
        <div className="ow-stage-hint"><span>{dimension === '3d' ? '拖拽空白旋转' : '拖拽节点'}</span><b>·</b><span>滚轮缩放</span><b>·</b><span>{dimension === '3d' ? 'Shift + 拖拽平移' : '悬停查看关联'}</span><b>·</b><span>双击展开</span></div>
      </section>

      <aside className="ow-data-panel" aria-label="图谱数据面板" hidden={!panelOpen}>
        <div className="ow-panel-heading"><span><GraphIcon name="panel" size={16} />数据面板</span><button type="button" aria-label="关闭数据面板" onClick={() => setPanelOpen(false)}><GraphIcon name="close" size={15} /></button></div>
        <div className="ow-panel-tabs" role="tablist" aria-label="数据面板内容">
          <button type="button" role="tab" aria-selected={panelTab === 'overview'} onClick={() => setPanelTab('overview')}>数据总览</button>
          <button type="button" role="tab" aria-selected={panelTab === 'links'} onClick={() => setPanelTab('links')}>关联详情{selected || props.selectedEdge ? <i /> : null}</button>
        </div>
        <div className="ow-panel-scroll">
          {panelTab === 'overview' ? <>
            <div className="ow-metrics" aria-label="全局有效图谱统计">
              {[['实体', props.overview?.entities], ['关系', props.overview?.relations], ['已构图视频', props.overview?.active_videos], ['字幕证据', props.overview?.evidence]].map(([label, value]) => <div key={label}><small>{label}</small><strong>{format(value as number | undefined)}</strong></div>)}
            </div>
            <div className="ow-processing-status"><span><i />{format(props.overview?.builds.pending ?? 0)} 待处理</span><span>{format(props.overview?.partial_videos ?? 0)} 部分完成</span></div>
            <section className="ow-panel-section"><h2><GraphIcon name="search" size={14} />筛选</h2>
              <p className="ow-muted">优先展示关联密集的区域，隐藏少于 {MIN_GRAPH_COMPONENT_SIZE} 个节点的独立子图；全局统计包含全部数据。</p>
              <label>实体类型<select value={props.entityType} onChange={(event) => props.onEntityType(event.target.value)}><option value="">全部类型</option>{Object.entries(ENTITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label>陈述状态<select value={props.assertion} onChange={(event) => props.onAssertion(event.target.value)}><option value="">全部状态</option>{Object.entries(ASSERTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label>展开层数<select value={props.hops} onChange={(event) => props.onHops(Number(event.target.value))}><option value={1}>1 跳邻居</option><option value={2}>2 跳邻居</option></select></label>
              {props.focusId ? <button type="button" className="ow-subtle-button" onClick={props.onClearFocus}>取消实体聚焦</button> : null}
            </section>
            <section className="ow-panel-section"><h2><GraphIcon name="layers" size={14} />当前视图分组<span>{groups.length}</span></h2>
              <div className="ow-groups">{groups.map((group) => <button type="button" key={group.type} className={props.entityType === group.type ? 'is-active' : ''} onClick={() => props.onEntityType(props.entityType === group.type ? '' : group.type)}>
                <i style={{ background: DARK_ENTITY_COLORS[group.type] }} /><span>{group.label}</span><small>{group.count}</small></button>)}</div>
            </section>
            <section className="ow-panel-section"><h2><GraphIcon name="settings" size={14} />显示</h2>
              <label className="ow-toggle">常显实体名称<input type="checkbox" checked={showLabels} onChange={(event) => configure('showLabels', event.target.checked)} /><span /></label>
              <label className="ow-toggle">显示关系方向<input type="checkbox" checked={appearance.showArrows} onChange={(event) => configure('showArrows', event.target.checked)} /><span /></label>
              <label className="ow-slider" htmlFor="graph-node-scale">节点大小<output>{Math.round(appearance.nodeScale * 100)}%</output><input id="graph-node-scale" type="range" min="0.6" max="2" step="0.1" value={appearance.nodeScale} onChange={(event) => configure('nodeScale', Number(event.target.value))} /></label>
              <label className="ow-slider" htmlFor="graph-link-scale">连线粗细<output>{Math.round(appearance.linkScale * 100)}%</output><input id="graph-link-scale" type="range" min="0.5" max="2" step="0.1" value={appearance.linkScale} onChange={(event) => configure('linkScale', Number(event.target.value))} /></label>
            </section>
            <details className="ow-panel-section ow-force-settings"><summary><GraphIcon name="settings" size={14} />布局引力</summary>
              <label className="ow-slider" htmlFor="graph-repulsion">节点斥力<output>{appearance.repulsion}</output><input id="graph-repulsion" type="range" min="80" max="420" step="10" value={appearance.repulsion} onChange={(event) => configure('repulsion', Number(event.target.value))} /></label>
              <label className="ow-slider" htmlFor="graph-link-distance">连接距离<output>{appearance.linkDistance}</output><input id="graph-link-distance" type="range" min="60" max="180" step="10" value={appearance.linkDistance} onChange={(event) => configure('linkDistance', Number(event.target.value))} /></label>
              <button className="ow-subtle-button" type="button" onClick={() => setAppearance({ ...DEFAULT_GRAPH_APPEARANCE })}>恢复默认显示</button>
            </details>
          </> : <>
            <section className="ow-panel-section ow-locator"><label>定位实体<select aria-label="定位图谱实体" value={selectedId} onChange={(event) => selectNode(nodes.find((node) => node.id === event.target.value) ?? null)}><option value="">选择或点击图中实体</option>{nodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label></section>
            {selected ? <section className="ow-panel-section ow-entity-details">
              <span className="ow-entity-type"><i style={{ background: DARK_ENTITY_COLORS[selected.entity_type ?? ''] }} />{ENTITY_LABELS[selected.entity_type ?? ''] ?? '实体'}</span>
              <h2>{selected.label}</h2><p>当前视图中的双向关联</p>
              <div className="ow-link-counters"><span><strong>{incoming.length}</strong>入链</span><span><strong>{outgoing.length}</strong>出链</span><span><strong>{new Set(related.map((e) => e.source === selected.id ? e.target : e.source)).size}</strong>邻居</span></div>
              <div className="ow-node-actions"><button type="button" disabled={!!props.expandingId} onClick={() => void props.onExpand(selected)}><GraphIcon name="expand" size={14} />{props.expandingId === selected.id ? '正在展开…' : '展开邻居'}</button><button type="button" onClick={() => props.onFocus(selected)}>聚焦此实体</button></div>
              <div className="ow-link-directions" aria-label="关联方向">{([['all', '全部'], ['in', '入链'], ['out', '出链']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={direction === value} onClick={() => setDirection(value)}>{label}</button>)}</div>
              <div className="ow-related-links">{visibleLinks.map((edge) => <button key={edge.id} type="button" onClick={() => selectEdge(edge)}><GraphIcon name="link" size={14} /><span><strong>{labels.get(edge.source === selected.id ? edge.target : edge.source)}</strong><small>{edge.target === selected.id ? '入链' : '出链'} · {PREDICATE_LABELS[edge.predicate] ?? edge.predicate}</small></span><small>{edge.evidence_count}</small></button>)}</div>
              {!visibleLinks.length ? <p className="ow-muted">此范围内没有关联，可展开邻居继续探索。</p> : null}
            </section> : props.selectedEdge ? <section className="ow-panel-section ow-selected-relation"><span className="ow-entity-type"><GraphIcon name="link" size={13} />关系证据</span><h2>{labels.get(props.selectedEdge.source)}<span>→</span>{labels.get(props.selectedEdge.target)}</h2><p>{PREDICATE_LABELS[props.selectedEdge.predicate] ?? props.selectedEdge.predicate} · {props.selectedEdge.evidence_count} 条证据</p>{Object.keys(props.selectedEdge.qualifiers).length ? <dl>{Object.entries(props.selectedEdge.qualifiers).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl> : null}</section> : <div className="ow-selection-empty"><GraphIcon name="graph" size={34} /><h2>每个连接，都是一条线索</h2><p>点击实体查看入链与出链，<br />点击连线追溯字幕原文。</p></div>}
            {props.selectedEdge ? <div className="ow-evidence-panel">{props.evidencePanel}</div> : null}
            {!selected && !props.selectedEdge ? <section className="ow-panel-section ow-link-directory" aria-label="关系列表"><h2>当前视图关系<span>{edges.filter((e) => e.kind === 'relation').length}</span></h2>{!edges.some((edge) => edge.kind === 'relation') ? <p className="ow-muted">暂无字幕支持的关系。</p> : null}{edges.filter((e) => e.kind === 'relation').map((edge) => <button key={edge.id} type="button" onClick={() => selectEdge(edge)}><span>{labels.get(edge.source)} → {labels.get(edge.target)}</span><small>{PREDICATE_LABELS[edge.predicate]} · {edge.evidence_count}</small></button>)}</section> : null}
          </>}
        </div>
        <div className="ow-panel-bottom"><GraphIcon name="info" size={13} /><span>字幕陈述 · 未经独立事实验证</span></div>
      </aside>
    </div>
    <footer className="ow-statusbar"><div><span className="ow-status-dot" />{props.loading ? '正在读取图谱' : `${format(nodes.length)} 个节点` }<span>／</span>{format(edges.length)} 条连线{props.graph?.truncated ? <small>· 局部视图</small> : null}</div><div>{hovered ? <span className="ow-hover-name">{hovered.label}</span> : <span>Neo4j 知识网络</span>}<span className="ow-status-separator" /><span>只读探索</span></div></footer>
  </section>;
}
