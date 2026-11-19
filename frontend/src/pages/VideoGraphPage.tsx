import { FormEvent, useEffect, useRef, useState } from 'react';

import { getVideoGraph, getVideoGraphEvidence, getVideoGraphOverview, getForumGraph, getForumGraphEvidence, getForumGraphOverview, searchForumGraphEntities } from '../api/client';
import type { Page, ForumGraphEvidence, VideoGraphData, VideoGraphEdge, VideoGraphEvidence, VideoGraphNode, VideoGraphOverview } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';
import { GraphWorkbench } from '../components/video-graph/GraphWorkbench';
import { largestGraphComponent, mergeGraphData } from '../components/video-graph/data';
import { graphLimitsForWidth, MIN_GRAPH_COMPONENT_SIZE } from '../components/video-graph/limits';
import { ASSERTION_LABELS } from '../components/video-graph/presentation';


function secondsLabel(value: number | null): string {
  if (value === null) return '时间未知';
  const seconds = Math.floor(value);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}


export function VideoGraphPage({ onOpenContent, source = 'video' }: { onOpenContent: (id: string) => void; source?: 'video' | 'forum' }) {
  const forum = source === 'forum';
  const loadGraph = forum ? getForumGraph : getVideoGraph;
  const loadOverview = forum ? getForumGraphOverview : getVideoGraphOverview;
  const loadEvidence = forum ? getForumGraphEvidence : getVideoGraphEvidence;
  const minimumComponentSize = forum ? 0 : MIN_GRAPH_COMPONENT_SIZE;
  const visibleGraph = forum ? (value: VideoGraphData) => value : largestGraphComponent;
  const [limits, setLimits] = useState(() => graphLimitsForWidth(window.innerWidth));
  useEffect(() => {
    const resize = () => setLimits(graphLimitsForWidth(window.innerWidth));
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const [expandingId, setExpandingId] = useState('');
  const [expansionError, setExpansionError] = useState('');
  const [expansionNotice, setExpansionNotice] = useState('');
  const scope = useRef(0);
  const expansionBusy = useRef(false);
  const baseGraph = useRef<VideoGraphData | null>(null);
  const expansions = useRef(new Map<string, VideoGraphData>());
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [overview, setOverview] = useState<VideoGraphOverview | null>(null);
  const [graph, setGraph] = useState<VideoGraphData | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [entityId, setEntityId] = useState('');
  const [entityType, setEntityType] = useState('');
  const [hops, setHops] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [selectedEdge, setSelectedEdge] = useState<VideoGraphEdge | null>(null);
  const [evidence, setEvidence] = useState<Page<VideoGraphEvidence | ForumGraphEvidence> | null>(null);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [error, setError] = useState('');
  const [evidenceError, setEvidenceError] = useState('');
  const maxNodes = entityType ? Number.POSITIVE_INFINITY : limits.maxNodes;
  const maxEdges = entityType ? Number.POSITIVE_INFINITY : limits.maxEdges;

  useEffect(() => {
    let ignore = false;
    loadOverview().then((value) => { if (!ignore) setOverview(value); }).catch(() => { if (!ignore) setOverview(null); });
    return () => { ignore = true; };
  }, [refresh, loadOverview]);

  useEffect(() => {
    let ignore = false;
    scope.current += 1;
    expansionBusy.current = false;
    baseGraph.current = null; expansions.current.clear(); setExpandedIds([]);
    setExpandingId(''); setExpansionError(''); setExpansionNotice('');
    setLoading(true); setError(''); setGraph(null); setSelectedEdge(null); setEvidence(null); setEvidenceOffset(0);
    loadGraph({ entity_id: entityId || undefined,
      q: appliedQuery || undefined, entity_type: entityId ? undefined : entityType || undefined,
      hops, max_nodes: limits.initialNodes, max_edges: limits.initialEdges,
      full: entityType ? true : undefined,
      min_component_size: minimumComponentSize })
      .then((value) => { if (!ignore) { const visible = visibleGraph(value); baseGraph.current = visible; setGraph(visible); } })
      .catch((reason: unknown) => { if (!ignore) setError(formatErrorMessage('无法加载知识图谱', reason)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; scope.current += 1; };
  }, [entityId, entityType, hops, appliedQuery, refresh, limits, loadGraph, minimumComponentSize]);

  useEffect(() => {
    let ignore = false;
    setEvidence(null); setEvidenceError(''); setEvidenceLoading(false);
    if (selectedEdge?.kind === 'relation') {
      setEvidenceLoading(true);
      loadEvidence(selectedEdge.id, {
        limit: 20, offset: evidenceOffset })
        .then((value) => { if (!ignore) setEvidence(value); })
        .catch((reason: unknown) => { if (!ignore) setEvidenceError(formatErrorMessage('无法加载关系证据', reason)); })
        .finally(() => { if (!ignore) setEvidenceLoading(false); });
    }
    return () => { ignore = true; };
  }, [selectedEdge, evidenceOffset, refresh, loadEvidence]);

  async function expandNode(node: VideoGraphNode) {
    if (expansionBusy.current || node.kind !== 'entity') return;
    expansionBusy.current = true;
    const currentScope = scope.current;
    setExpandingId(node.id); setExpansionError(''); setExpansionNotice('');
    try {
      const incoming = await loadGraph({ entity_id: node.id,
        hops: 1, max_nodes: limits.maxNodes, max_edges: limits.maxEdges,
        full: entityType ? true : undefined, min_component_size: 0 });
      if (scope.current === currentScope && graph) {
        expansions.current.set(node.id, incoming);
        let combined = baseGraph.current ?? graph;
        for (const expansion of expansions.current.values()) combined = mergeGraphData(combined, expansion, maxNodes, maxEdges);
        const merged = visibleGraph(combined);
        setExpandedIds([...expansions.current.keys()]);
        setGraph(merged);
        if (merged.nodes.length >= maxNodes || merged.edges.length >= maxEdges)
          setExpansionNotice(`当前画布最多展示 ${limits.maxNodes} 个节点、${limits.maxEdges} 条关系；可搜索或聚焦实体查看其他内容。`);
      }
    } catch (reason) {
      if (scope.current === currentScope) setExpansionError(formatErrorMessage('无法展开邻居', reason));
    } finally {
      if (scope.current === currentScope) { expansionBusy.current = false; setExpandingId(''); }
    }
  }
  function collapseNode(node: VideoGraphNode) {
    if (expansionBusy.current || !baseGraph.current) return;
    expansions.current.delete(node.id);
    let next = baseGraph.current;
    for (const incoming of expansions.current.values()) next = mergeGraphData(next, incoming, maxNodes, maxEdges);
    next = visibleGraph(next);
    setGraph(next); setExpandedIds([...expansions.current.keys()]);
    setExpansionNotice('');
    if (selectedEdge && !next.edges.some(edge => edge.id === selectedEdge.id)) setSelectedEdge(null);
  }
  function selectEdge(edge: VideoGraphEdge) {
    if (edge.kind !== 'relation') return;
    setSelectedEdge(edge); setEvidenceOffset(0);
  }
  function searchEntities(event: FormEvent) { event.preventDefault(); setAppliedQuery(query.trim()); setEntityId(''); }

  const evidencePanel = (
<section aria-label={forum ? "帖子证据" : "字幕证据"}><h2>{forum ? "帖子证据" : "字幕证据"}</h2>
              {!selectedEdge ? <p className="muted">{forum ? "选择连线查看帖子原文、作者及具体楼层。" : "选择关系查看原文和视频时间点。"}</p> : null}
              {evidenceLoading ? <p className="status-line">正在加载证据…</p> : null}
              {evidenceError ? <p role="alert" className="error-text">{evidenceError}</p> : null}
              {evidence?.items.map((item) => {
                if ('post_url' in item) {
                  const href = safeExternalHref(item.post_url);
                  const conditions = Object.entries(item.qualifiers).filter(([k]) => k !== 'case_id' && k !== 'modality');
                  return <article className="video-graph-evidence" key={item.id}>
                    <strong>{item.title || '论坛主题'}</strong>
                    <p>{item.author} · 第 {item.post_number} 楼 · {ASSERTION_LABELS[item.assertion]}</p>
                    <p>{item.role === 'context' ? '案例上下文；本楼层不是其他楼层操作建议的直接证据。' : item.act === 'recommendation' ? '操作建议，是否有效请核对对应案例反馈。' : '作者的技术陈述，仅适用于原文所述条件。'}</p>
                    {conditions.length ? <p>适用条件：{conditions.map(([k,v]) => `${k.replace(/^context:/, '')}=${String(v)}`).join(' · ')}</p> : null}
                    {item.accepted_answer ? <p>社区已采纳，不代表独立验证。</p> : null}
                    <blockquote>{item.quote}</blockquote>
                    {href ? <a href={href} target="_blank" rel="noreferrer">查看第 {item.post_number} 楼原帖</a> : null}
                    {item.evaluations.length ? item.evaluations.map((feedback, i) => <p key={i}>{feedback.outcome === 'reported_success' ? '本案例作者报告成功' : feedback.outcome === 'reported_failure' ? '本案例作者报告无效' : '本案例反馈结果不明确'}</p>) : item.act === 'recommendation' ? <p>尚无明确验证反馈</p> : null}
                    <button type="button" onClick={() => onOpenContent(item.content_item_id)}>主题详情</button>
                  </article>;
                }
                const href = safeExternalHref(item.video_url);
                const qualifiers = Object.entries(item.qualifiers ?? {});
                return <article className="video-graph-evidence" key={item.id}><strong>{item.title || '视频'}</strong>
                  <p>{ASSERTION_LABELS[item.assertion]} · 抽取把握程度 {Math.round(item.confidence * 100)}% · 字幕来源 {item.caption_source || '未知'}</p>
                  {qualifiers.length ? <p className="video-graph-evidence-qualifiers">适用条件：{qualifiers.map(([k, v]) => `${k}=${v}`).join(' · ')}</p> : null}
                  <blockquote>{item.quote}</blockquote>
                  {href ? <a href={href} target="_blank" rel="noreferrer">观看视频 {secondsLabel(item.start_seconds)} — {secondsLabel(item.end_seconds)}</a> : null}
                  <button type="button" onClick={() => onOpenContent(item.content_item_id)}>内容详情</button>
                </article>;
              })}
              {evidence ? <div className="button-row"><span>共 {evidence.total} 条证据</span>
                <button type="button" disabled={evidenceOffset === 0} onClick={() => setEvidenceOffset(Math.max(0, evidenceOffset - 20))}>上一页证据</button>
                <button type="button" disabled={evidenceOffset + 20 >= evidence.total} onClick={() => setEvidenceOffset(evidenceOffset + 20)}>下一页证据</button>
              </div> : null}
            </section>
  );
  return <section className="video-graph-page" aria-label={forum ? "论坛知识图谱" : "视频知识图谱"}>
    {forum && !loading && !error && graph?.nodes.length === 0 ? <p role="status">论坛图谱尚无可展示数据。请运行论坛构图命令，完成后刷新。</p> : null}
    <GraphWorkbench source={source} entitySearch={forum ? searchForumGraphEntities : undefined} graph={graph} overview={overview}
    query={query} onQuery={setQuery} onSearch={searchEntities}
    entityType={entityType} onEntityType={(value) => { scope.current += 1; setEntityType(value); setEntityId(''); setAppliedQuery(''); setQuery(''); setHops(1); }}
    hops={hops} onHops={setHops}
    focusId={entityId} onClearFocus={() => { scope.current += 1; setEntityId(''); setAppliedQuery(''); setQuery(''); setRefresh((value) => value + 1); }}
    selectedEdge={selectedEdge} onEdge={selectEdge} onClearEdge={() => setSelectedEdge(null)}
    onFocus={(node) => { if (node.kind === 'entity') { setEntityId(node.id); setHops(1); } }} onExpand={expandNode}
    expandingId={expandingId} expandedIds={expandedIds} onCollapse={collapseNode} loading={loading} error={error} expansionError={expansionError} expansionNotice={expansionNotice}
    onRefresh={() => setRefresh((value) => value + 1)}
    evidencePanel={evidencePanel} />
  </section>;

}
