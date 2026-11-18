import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { VideoGraphData, VideoGraphEdge, VideoGraphNode, VideoGraphOverview } from '../../api/types';
import { InteractiveGraphCanvas } from './InteractiveGraphCanvas';
import type { GraphDimension } from './InteractiveGraphCanvas';
import { GraphIcon } from './GraphIcon';
import './obsidian-graph.css';
import {
  classifyEdgeSemantics,
  DARK_ENTITY_COLORS,
  DEFAULT_GRAPH_APPEARANCE,
  ENTITY_COLORS,
  ENTITY_LABELS,
  KNOWLEDGE_LENSES,
  ONTOLOGY_LAYERS,
  PREDICATE_LABELS,
} from './presentation';
import type { GraphAppearance, GraphLayoutMode, KnowledgeLensId } from './presentation';
import { filterGraphByLens, findShortestKnowledgePath } from './data';
import { MIN_GRAPH_COMPONENT_SIZE } from './limits';
import { searchVideoGraphEntities } from '../../api/client';

type Props = {
  graph: VideoGraphData | null; overview: VideoGraphOverview | null;
  query: string; onQuery: (query: string) => void; onSearch: (event: FormEvent) => void;
  entityType: string; onEntityType: (value: string) => void;
  hops: number; onHops: (value: number) => void; focusId: string; onClearFocus: () => void;
  selectedEdge: VideoGraphEdge | null; onEdge: (edge: VideoGraphEdge) => void; onClearEdge: () => void;
  onFocus: (node: VideoGraphNode) => void; onExpand: (node: VideoGraphNode) => Promise<void>;
  expandingId: string; loading: boolean; error: string; expansionError: string; expansionNotice?: string;
  onRefresh: () => void; evidencePanel: ReactNode;
  expandedIds?: string[]; onCollapse?: (node: VideoGraphNode) => void;
};

const format = (value: number | undefined) => value === undefined ? '—' : value.toLocaleString('zh-CN');

export function GraphWorkbench(props: Props) {
  const [dimension, setDimension] = useState<GraphDimension>(() => {
    try {
      const saved = localStorage.getItem('video-graph-dimension');
      if (saved === '2d' || saved === '3d') return saved;
    } catch { /* Storage can be disabled by the browser. */ }
    return '2d';
  });
  const [dimensionNotice, setDimensionNotice] = useState('');
  const [canvasEpoch, setCanvasEpoch] = useState(0);
  const [labels3D, setLabels3D] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => window.innerWidth > 1200);
  const [panelTab, setPanelTab] = useState<'overview' | 'links'>('overview');
  const [activeLens, setActiveLens] = useState<KnowledgeLensId>('all');
  const [selectedId, setSelectedId] = useState('');
  const [pathTargetId, setPathTargetId] = useState('');
  const [searchSelection, setSearchSelection] = useState<VideoGraphNode | null>(null);
  const [direction, setDirection] = useState<'all' | 'in' | 'out'>('all');
  const [appearance, setAppearance] = useState<GraphAppearance>({ ...DEFAULT_GRAPH_APPEARANCE });
  const [appliedForces, setAppliedForces] = useState({ repulsion: DEFAULT_GRAPH_APPEARANCE.repulsion, linkDistance: DEFAULT_GRAPH_APPEARANCE.linkDistance });
  useEffect(() => {
    const timer = setTimeout(() => setAppliedForces({ repulsion: appearance.repulsion, linkDistance: appearance.linkDistance }), 220);
    return () => clearTimeout(timer);
  }, [appearance.repulsion, appearance.linkDistance]);
  const search = useRef<HTMLInputElement>(null);
  const focusSearch = useRef(false);
  const [submittedSearch, setSubmittedSearch] = useState('');
  const [searchVersion, setSearchVersion] = useState(0);
  const [searchResults, setSearchResults] = useState<VideoGraphNode[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  useEffect(() => {
    let ignore = false;
    setSearchResults([]); setSearchError('');
    if (!submittedSearch) { setSearchLoading(false); return; }
    setSearchLoading(true);
    searchVideoGraphEntities(submittedSearch, props.entityType || undefined)
      .then(value => { if (!ignore) setSearchResults(value.items.map(item => ({ id: item.id, label: item.name, entity_type: item.entity_type, kind: 'entity', content_item_id: null }))); })
      .catch(() => { if (!ignore) setSearchError('无法搜索实体，请重试。'); })
      .finally(() => { if (!ignore) setSearchLoading(false); });
    return () => { ignore = true; };
  }, [submittedSearch, searchVersion, props.entityType]);
  function openSearch() {
    focusSearch.current = true; setPanelOpen(true); setPanelTab('links');
    search.current?.focus();
  }
  useEffect(() => {
    if (panelOpen && panelTab === 'links' && focusSearch.current) { search.current?.focus(); focusSearch.current = false; }
  }, [panelOpen, panelTab]);
  function searchEntities(event: FormEvent) {
    event.preventDefault();
    setSubmittedSearch(props.query.trim()); setSearchVersion(value => value + 1); setSelectedId(''); setPathTargetId(''); setSearchSelection(null); props.onClearEdge(); props.onSearch(event);
  }
  function resetGlobal() {
    setActiveLens('all'); setSelectedId(''); setPathTargetId(''); setSearchSelection(null); setSubmittedSearch(''); setSearchResults([]); setSearchError('');
    props.onQuery(''); props.onClearEdge(); props.onEntityType(''); props.onHops(1);
    setCanvasEpoch(value => value + 1); props.onClearFocus();
  }
  function changeEntityType(value: string) {
    setActiveLens('all'); setSelectedId(''); setPathTargetId(''); setSearchSelection(null); setSubmittedSearch(''); setSearchResults([]); setSearchError('');
    props.onClearEdge(); setCanvasEpoch(value => value + 1); props.onEntityType(value);
  }
  function changeLens(lensId: KnowledgeLensId) {
    setActiveLens(lensId); setPathTargetId('');
    props.onClearEdge();
  }

  const displayedGraph = useMemo(
    () => (props.graph ? filterGraphByLens(props.graph, activeLens) : null),
    [props.graph, activeLens],
  );
  const lensCounts = useMemo(() => {
    const map = new Map<KnowledgeLensId, number>();
    for (const lens of KNOWLEDGE_LENSES) {
      if (!props.graph) { map.set(lens.id, 0); continue; }
      if (!lens.predicates) { map.set(lens.id, props.graph.edges.length); continue; }
      const allowed = new Set(lens.predicates);
      map.set(lens.id, props.graph.edges.filter(edge => allowed.has(edge.predicate)).length);
    }
    return map;
  }, [props.graph]);

  const nodes = displayedGraph?.nodes ?? [];
  const edges = displayedGraph?.edges ?? [];
  // Recreate the WebGL scene when its buffers change size. The official extension
  // caches sphere geometry and cannot safely reuse all resized instance buffers.
  const sceneKey = useMemo(() => displayedGraph
    ? [...displayedGraph.nodes.map((node) => node.id), ...displayedGraph.edges.map((edge) => edge.id)].join(':')
    : '', [displayedGraph]);
  const selected = nodes.find((node) => node.id === selectedId) ?? (searchSelection?.id === selectedId ? searchSelection : null);
  const labels = new Map(nodes.map((node) => [node.id, node.label]));
  const related = edges.filter((edge) => edge.source === selectedId || edge.target === selectedId);
  const incoming = related.filter((edge) => edge.target === selectedId);
  const outgoing = related.filter((edge) => edge.source === selectedId);
  const visibleLinks = direction === 'in' ? incoming : direction === 'out' ? outgoing : related;

  const knowledgePath = useMemo(
    () => (displayedGraph && selected && pathTargetId ? findShortestKnowledgePath(displayedGraph, selected.id, pathTargetId) : null),
    [displayedGraph, selected, pathTargetId],
  );
  const highlightedPath = useMemo(
    () => (knowledgePath ? { nodeIds: new Set(knowledgePath.nodeIds), edgeIds: new Set(knowledgePath.edgeIds) } : null),
    [knowledgePath],
  );
  const pathCandidates = useMemo(
    () => nodes.filter((node) => node.id !== selectedId).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN')),
    [nodes, selectedId],
  );
  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of nodes) {
      const key = node.entity_type ?? node.kind;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [nodes]);
  const hasNonAssertedOrQualifiedEdges = useMemo(
    () => edges.some((edge) => {
      const s = classifyEdgeSemantics(edge);
      return s.status !== 'asserted' || s.qualifierCount > 0;
    }),
    [edges],
  );

  function selectNode(node: VideoGraphNode | null) {
    setSearchSelection(node && !nodes.some(item => item.id === node.id) ? node : null);
    setSelectedId(node?.id ?? ''); setPathTargetId(''); props.onClearEdge();
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
    setDimensionNotice(message); setDimension(value);
    try { localStorage.setItem('video-graph-dimension', value); } catch { /* Optional preference. */ }
  }
  const showLabels = dimension === '3d' ? labels3D : appearance.showLabels;
  const layoutMode: GraphLayoutMode = appearance.layoutMode ?? 'force';
  function exploreNode(node: VideoGraphNode) {
    if (dimension === '2d' && props.expandedIds?.includes(node.id) && props.onCollapse) props.onCollapse(node);
    else return props.onExpand(node);
    return Promise.resolve();
  }
  useEffect(() => {
    if (selectedId && !nodes.some((node) => node.id === selectedId) && searchSelection?.id !== selectedId) {
      setSelectedId(''); setPathTargetId('');
    }
  }, [displayedGraph, selectedId, searchSelection, nodes]);
  useEffect(() => {
    if (pathTargetId && !nodes.some((node) => node.id === pathTargetId)) setPathTargetId('');
  }, [displayedGraph, pathTargetId, nodes]);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      const target = event.target instanceof Element ? event.target : null;
      if (event.key === '/' && !target?.closest('input,textarea,select,[contenteditable="true"]')) {
        event.preventDefault(); openSearch();
      }
    }
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, []);

  const activeLensMeta = KNOWLEDGE_LENSES.find((item) => item.id === activeLens) ?? KNOWLEDGE_LENSES[0];
  const selectedEdgeSemantics = props.selectedEdge ? classifyEdgeSemantics(props.selectedEdge) : null;

  return <section className="obsidian-workbench" aria-label="知识图谱工作台">
    <div className="ow-toolbar">
      <div className="ow-breadcrumb"><GraphIcon name="graph" size={16} /><span className="ow-scope-label">图谱范围</span><span>/</span>
        <button type="button" onClick={props.onClearFocus} aria-label="查看全局图谱">{props.focusId ? '局部图谱' : '全局图谱'}<GraphIcon name="chevron" size={12} /></button>
      </div>
      <div className="ow-toolbar-switches">
        {dimension === '2d' ? <div className="ow-dimension-switch ow-layout-switch" role="group" aria-label="知识组织布局">
          <button type="button" aria-pressed={layoutMode === 'force'} onClick={() => configure('layoutMode', 'force')} title="自由聚簇网络">力导向</button>
          <button type="button" aria-pressed={layoutMode === 'layered'} onClick={() => configure('layoutMode', 'layered')} title="按来源/品牌 → 产品 → 部件技术 → 故障对策分层排布">层级流</button>
        </div> : null}
        <div className="ow-dimension-switch" role="group" aria-label="图谱维度">
          <button type="button" aria-pressed={dimension === '2d'} onClick={() => changeDimension('2d')} title="平面图谱">2D</button>
          <button type="button" aria-pressed={dimension === '3d'} onClick={() => changeDimension('3d')} title="三维图谱，可旋转查看">3D</button>
        </div>
      </div>
      <div className="ow-tools">
        <span className="ow-connection"><i className={props.overview ? '' : 'is-pending'} />{props.overview ? '已连接' : '读取中'}</span>
        <button type="button" aria-label="刷新图谱" title="刷新图谱" disabled={props.loading} onClick={props.onRefresh}><GraphIcon name="refresh" size={16} /></button>
        <button type="button" aria-label={panelOpen ? '收起数据面板' : '打开数据面板'} title="数据面板" aria-expanded={panelOpen} className={panelOpen ? 'is-active' : ''} onClick={() => setPanelOpen((value) => !value)}><GraphIcon name="panel" size={17} /></button>
      </div>
    </div>

    <div className="ow-lens-strip" role="group" aria-label="知识视角">
      <span className="ow-lens-heading"><GraphIcon name="layers" size={13} />知识视角</span>
      <div className="ow-lens-pills">
        {KNOWLEDGE_LENSES.map((lens) => (
          <button
            key={lens.id}
            type="button"
            aria-pressed={activeLens === lens.id}
            title={lens.description}
            onClick={() => changeLens(lens.id)}
          >
            <span>{lens.label}</span>
            {props.graph ? <small>{format(lensCounts.get(lens.id) ?? 0)}</small> : null}
          </button>
        ))}
      </div>
      <span className="ow-lens-desc">{activeLensMeta.description}</span>
    </div>

    <div className={`ow-body${panelOpen ? '' : ' panel-collapsed'}`}>
      <section className="ow-graph-stage" aria-label="图谱画布区域">
        <div className="ow-canvas-caption"><span className="ow-caption-icon"><GraphIcon name="link" size={13} /></span>
          {props.focusId ? '实体邻域' : activeLensMeta.label}<span className="ow-caption-dot">·</span><span>{format(nodes.length)} 个实体节点</span><span className="ow-caption-dot">·</span><span>{format(edges.length)} 条关系</span></div>
        {props.loading ? <div className="ow-loading" role="status"><div className="ow-loading-orbit" /><span>正在连接知识网络…</span></div> : null}
        {props.error ? <div className="ow-stage-message" role="alert"><GraphIcon name="info" size={24} /><p>{props.error}</p><button onClick={props.onRefresh}>重新加载</button></div> : null}
        {props.graph && props.graph.nodes.length === 0 ? <div className="ow-stage-message"><GraphIcon name="search" size={28} /><h2>没有符合展示条件的图谱</h2><p>当前范围内未找到至少 {MIN_GRAPH_COMPONENT_SIZE} 个节点的连通子图，请尝试其他关键词或调整筛选条件。</p></div> : null}
        {props.graph && props.graph.nodes.length > 0 && displayedGraph && nodes.length === 0 ? <div className="ow-stage-message"><GraphIcon name="layers" size={28} /><h2>当前子图暂无“{activeLensMeta.label}”关系</h2><p>{activeLensMeta.description}。可切换回全景网络，或搜索并展开相关实体。</p><button type="button" onClick={() => changeLens('all')}>切换回全景网络</button></div> : null}
        {displayedGraph && nodes.length > 0 ? <InteractiveGraphCanvas
          key={dimension === '3d' ? `${dimension}:${sceneKey}` : `${dimension}:${canvasEpoch}`} dimension={dimension}
          onUnavailable={(message) => changeDimension('2d', message)} graph={displayedGraph} focusId={props.focusId}
          selectedEdge={props.selectedEdge?.id ?? ''} expandingId={props.expandingId}
          activeNodeId={selectedId} appearance={{ ...appearance, ...appliedForces, showLabels }} onSelect={selectNode}
          onFocus={props.onFocus} onExpand={exploreNode} expandedIds={props.expandedIds} onEdge={selectEdge} onGlobal={resetGlobal}
          highlightedPath={highlightedPath} /> : null}
        {props.expansionError ? <div className="ow-canvas-notice" role="alert">{props.expansionError}</div> : null}
        {props.expansionNotice ? <div className="ow-canvas-notice" role="status">{props.expansionNotice}</div> : null}
        {props.expandingId ? <div className="ow-canvas-notice" role="status">正在展开邻居，保留已有节点…</div> : null}
        {dimensionNotice ? <div className="ow-canvas-notice" role="status">{dimensionNotice}</div> : null}
        {hasNonAssertedOrQualifiedEdges ? <div className="ow-semantic-strip" aria-label="画布关系语义说明">
          <span><i className="is-solid" />陈述</span>
          <span><i className="is-dashed" />推测</span>
          <span><i className="is-negated" />否定</span>
          <span><i className="is-disputed" />争议</span>
          <span>[条件×N] 含限定条件</span>
        </div> : null}
        <div className="ow-stage-hint"><span>{dimension === '3d' ? '拖拽空白旋转' : '拖拽节点固定位置'}</span><b>·</b><span>滚轮缩放</span><b>·</b><span>{dimension === '3d' ? 'Shift + 拖拽平移' : '右键操作'}</span><b>·</b><span>{dimension === '3d' ? '双击展开' : '双击展开／收起'}</span></div>
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
              <label>实体类型<select value={props.entityType} onChange={(event) => changeEntityType(event.target.value)}><option value="">全部类型</option>{Object.entries(ENTITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label>展开层数<select value={props.hops} onChange={(event) => props.onHops(Number(event.target.value))}><option value={1}>1 跳邻居</option><option value={2}>2 跳邻居</option></select></label>
              {props.focusId ? <button type="button" className="ow-subtle-button" onClick={props.onClearFocus}>取消实体聚焦</button> : null}
            </section>
            <section className="ow-panel-section ow-schema-section" aria-label="知识组织结构">
              <h2><GraphIcon name="layers" size={14} />知识组织结构<span>本体分层</span></h2>
              <div className="ow-schema-layers">
                {ONTOLOGY_LAYERS.map((layer) => {
                  const presentTypes = layer.types.filter((type) => (typeCounts.get(type) ?? 0) > 0);
                  const visibleTypes = presentTypes.length ? presentTypes : layer.types.filter((type) => ENTITY_LABELS[type]);
                  return (
                    <div key={layer.id} className="ow-schema-layer">
                      <div className="ow-schema-layer-head">
                        <strong>{layer.label}</strong>
                        <small>{layer.summary}</small>
                      </div>
                      <p className="ow-schema-chain">{layer.typicalChain}</p>
                      <div className="ow-schema-chips">
                        {visibleTypes.map((type) => {
                          const count = typeCounts.get(type) ?? 0;
                          const isSelected = props.entityType === type;
                          return (
                            <button
                              key={type}
                              type="button"
                              className={isSelected ? 'is-active' : ''}
                              onClick={() => changeEntityType(isSelected ? '' : type)}
                              title={`按${ENTITY_LABELS[type] ?? type}筛选`}
                            >
                              <i style={{ background: ENTITY_COLORS[type] ?? '#8795aa' }} />
                              <span>{ENTITY_LABELS[type] ?? type}</span>
                              {count > 0 ? <small>{count}</small> : null}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="ow-schema-legend" aria-label="关系语义图例">
                <span><i className="is-solid" />陈述</span>
                <span><i className="is-dashed" />推测</span>
                <span><i className="is-negated" />否定</span>
                <span><i className="is-disputed" />存在争议</span>
                <span>[条件×N] 适用条件</span>
              </div>
            </section>
            <section className="ow-panel-section"><h2><GraphIcon name="settings" size={14} />显示</h2>
              <label className="ow-toggle">常显实体名称<input type="checkbox" checked={showLabels} onChange={(event) => configure('showLabels', event.target.checked)} /><span /></label>
              <label className="ow-toggle">显示关系方向<input type="checkbox" checked={appearance.showArrows} onChange={(event) => configure('showArrows', event.target.checked)} /><span /></label>
              <label className="ow-slider" htmlFor="graph-node-scale">节点大小<output>{Math.round(appearance.nodeScale * 100)}%</output><input id="graph-node-scale" type="range" min="0.6" max="2" step="0.1" value={appearance.nodeScale} onChange={(event) => configure('nodeScale', Number(event.target.value))} /></label>
              <label className="ow-slider" htmlFor="graph-link-scale">连线粗细<output>{Math.round(appearance.linkScale * 100)}%</output><input id="graph-link-scale" type="range" min="0.5" max="2" step="0.1" value={appearance.linkScale} onChange={(event) => configure('linkScale', Number(event.target.value))} /></label>
            </section>
            <details className="ow-panel-section ow-force-settings"><summary><GraphIcon name="settings" size={14} />布局引力</summary>
              <label className="ow-slider" htmlFor="graph-repulsion">节点斥力<output>{appearance.repulsion}</output><input id="graph-repulsion" type="range" min="80" max="420" step="10" value={appearance.repulsion} onChange={(event) => configure('repulsion', Number(event.target.value))} /></label>
              <button className="ow-subtle-button" type="button" onClick={() => setAppearance({ ...DEFAULT_GRAPH_APPEARANCE })}>恢复默认显示</button>
            </details>
          </> : <>
            <section className="ow-panel-section ow-entity-search" aria-label="关联详情实体搜索">
              <h2><GraphIcon name="search" size={14} />搜索实体</h2>
              <form className="ow-search ow-detail-search" aria-label="图谱搜索" onSubmit={searchEntities}>
                <GraphIcon name="search" size={16} /><label className="sr-only" htmlFor="graph-entity-search">实体名称</label>
                <input id="graph-entity-search" ref={search} value={props.query} onChange={(event) => props.onQuery(event.target.value)} placeholder="搜索实体、技术或产品…" autoComplete="off" />
                {props.query ? <button type="button" aria-label="清空搜索输入" onClick={() => { props.onQuery(''); setSubmittedSearch(''); setSelectedId(''); setPathTargetId(''); setSearchSelection(null); props.onClearEdge(); props.onClearFocus(); }}><GraphIcon name="close" size={13} /></button> : <kbd>/</kbd>}
                <button type="submit">搜索实体</button>
              </form>
              {submittedSearch && searchLoading ? <p role="status">正在搜索实体…</p> : null}
              {searchError ? <p role="alert">{searchError}</p> : null}
              {submittedSearch && submittedSearch === props.query.trim() && !searchLoading && !searchError ? <div className="ow-entity-results" aria-label="实体搜索结果">
                {searchResults.map(node => <button type="button" key={node.id} onClick={() => selectNode(node)}><span>{node.label}</span><small>{ENTITY_LABELS[node.entity_type ?? ''] ?? '实体'}</small></button>)}
                {!searchResults.length ? <p className="ow-muted">未找到匹配实体，请尝试其他关键词。</p> : null}
              </div> : null}
            </section>
            {selected ? <section className="ow-panel-section ow-entity-details">
              <span className="ow-entity-type"><i style={{ background: DARK_ENTITY_COLORS[selected.entity_type ?? ''] }} />{ENTITY_LABELS[selected.entity_type ?? ''] ?? '实体'}</span>
              <h2>{selected.label}</h2><p>当前视图中的双向关联</p>
              <div className="ow-link-counters"><span><strong>{incoming.length}</strong>入链</span><span><strong>{outgoing.length}</strong>出链</span><span><strong>{new Set(related.map((e) => e.source === selected.id ? e.target : e.source)).size}</strong>邻居</span></div>
              <div className="ow-node-actions"><button type="button" disabled={!!props.expandingId} onClick={() => void exploreNode(selected)}><GraphIcon name="expand" size={14} />{props.expandingId === selected.id ? '正在展开…' : dimension === '2d' && props.expandedIds?.includes(selected.id) ? '收起邻居' : '展开邻居'}</button><button type="button" onClick={() => props.onFocus(selected)}>聚焦此实体</button></div>
              {pathCandidates.length > 0 && nodes.some((n) => n.id === selected.id) ? <div className="ow-path-explorer" aria-label="关联路径探索">
                <label className="ow-path-picker">
                  <span>关联路径至</span>
                  <select aria-label="探索关联路径" value={pathTargetId} onChange={(event) => setPathTargetId(event.target.value)}>
                    <option value="">选择另一实体发现路径…</option>
                    {pathCandidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
                    ))}
                  </select>
                </label>
                {pathTargetId ? (
                  knowledgePath ? <div className="ow-path-chain">
                    <div className="ow-path-chain-head">
                      <strong>{knowledgePath.steps.length} 跳知识链</strong>
                      <button type="button" className="ow-subtle-button" onClick={() => setPathTargetId('')}>清除路径</button>
                    </div>
                    {knowledgePath.steps.map((step, index) => {
                      const s = classifyEdgeSemantics(step.edge);
                      const predicate = PREDICATE_LABELS[step.edge.predicate] ?? step.edge.predicate;
                      return (
                        <button key={`${step.edge.id}:${index}`} type="button" className="ow-path-step" onClick={() => selectEdge(step.edge)}>
                          <span>{labels.get(step.fromId) ?? step.fromId}</span>
                          <small>{step.direction === 'out' ? `—[${predicate}]→` : `←[${predicate}]—`} · {step.edge.evidence_count} 证据{s.status !== 'asserted' ? ` · ${s.statusLabel}` : ''}</small>
                          <span>{labels.get(step.toId) ?? step.toId}</span>
                        </button>
                      );
                    })}
                  </div> : <p className="ow-muted">当前 4 跳内未连通到目标实体，可切换全景网络或展开邻居后重试。</p>
                ) : null}
              </div> : null}
              <div className="ow-link-directions" aria-label="关联方向">{([['all', '全部'], ['in', '入链'], ['out', '出链']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={direction === value} onClick={() => setDirection(value)}>{label}</button>)}</div>
              <div className="ow-related-links">{visibleLinks.map((edge) => {
                const s = classifyEdgeSemantics(edge);
                return <button key={edge.id} type="button" onClick={() => selectEdge(edge)}>
                  <GraphIcon name="link" size={14} />
                  <span>
                    <strong>{labels.get(edge.source === selected.id ? edge.target : edge.source)}</strong>
                    <small>
                      {edge.target === selected.id ? '入链' : '出链'} · {PREDICATE_LABELS[edge.predicate] ?? edge.predicate}
                      {s.status !== 'asserted' ? <em className={`ow-assertion-pill is-${s.status}`}>{s.statusLabel}</em> : null}
                      {s.qualifierCount > 0 ? <em className="ow-qualifier-pill">条件×{s.qualifierCount}</em> : null}
                    </small>
                  </span>
                  <small>{edge.evidence_count}</small>
                </button>;
              })}</div>
              {!visibleLinks.length ? <p className="ow-muted">此范围内没有关联，可展开邻居继续探索。</p> : null}
            </section> : props.selectedEdge && selectedEdgeSemantics ? <section className="ow-panel-section ow-selected-relation">
              <div className="ow-relation-header">
                <span className="ow-entity-type"><GraphIcon name="link" size={13} />关系证据</span>
                <span className={`ow-assertion-pill is-${selectedEdgeSemantics.status}`}>{selectedEdgeSemantics.statusLabel}</span>
              </div>
              <h2>{labels.get(props.selectedEdge.source)}<span>→</span>{labels.get(props.selectedEdge.target)}</h2>
              <p>{PREDICATE_LABELS[props.selectedEdge.predicate] ?? props.selectedEdge.predicate} · {props.selectedEdge.evidence_count} 条证据</p>
              <div className="ow-assertion-breakdown" aria-label="证据态度分布">
                {selectedEdgeSemantics.assertedCount > 0 ? <span className="is-asserted">肯定 {selectedEdgeSemantics.assertedCount}</span> : null}
                {selectedEdgeSemantics.uncertainCount > 0 ? <span className="is-uncertain">推测 {selectedEdgeSemantics.uncertainCount}</span> : null}
                {selectedEdgeSemantics.negatedCount > 0 ? <span className="is-negated">否定 {selectedEdgeSemantics.negatedCount}</span> : null}
              </div>
              {selectedEdgeSemantics.status === 'disputed' ? <p className="ow-dispute-note">该关系同时存在肯定与否定记录，请结合下方适用条件与原句判断。</p> : null}
              {Object.keys(props.selectedEdge.qualifiers).length ? <div className="ow-qualifiers-box">
                <strong>适用条件与限定</strong>
                <dl>{Object.entries(props.selectedEdge.qualifiers).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
              </div> : null}
            </section> : <div className="ow-selection-empty"><GraphIcon name="graph" size={34} /><h2>选择实体或关系</h2><p>点击图谱中的节点或关系查看详情，也可在上方搜索实体。</p></div>}
            {props.selectedEdge ? <div className="ow-evidence-panel">{props.evidencePanel}</div> : null}

          </>}
        </div>
      </aside>
    </div>
  </section>;
}
