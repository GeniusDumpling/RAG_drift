import { FormEvent, useEffect, useRef, useState } from 'react';

import { getVideoGraph, getVideoGraphEvidence, getVideoGraphOverview } from '../api/client';
import type { Page, VideoGraphData, VideoGraphEdge, VideoGraphEvidence, VideoGraphNode, VideoGraphOverview } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';
import { GraphWorkbench } from '../components/video-graph/GraphWorkbench';
import { mergeGraphData } from '../components/video-graph/data';
import { graphLimitsForWidth, MIN_GRAPH_COMPONENT_SIZE } from '../components/video-graph/limits';
import { ASSERTION_LABELS } from '../components/video-graph/presentation';


function secondsLabel(value: number | null): string {
  if (value === null) return '时间未知';
  const seconds = Math.floor(value);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}


export function VideoGraphPage({ onOpenContent }: { onOpenContent: (id: string) => void }) {
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
  const [overview, setOverview] = useState<VideoGraphOverview | null>(null);
  const [graph, setGraph] = useState<VideoGraphData | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [entityId, setEntityId] = useState('');
  const [entityType, setEntityType] = useState('');
  const [assertion, setAssertion] = useState('');
  const [hops, setHops] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [selectedEdge, setSelectedEdge] = useState<VideoGraphEdge | null>(null);
  const [evidence, setEvidence] = useState<Page<VideoGraphEvidence> | null>(null);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [error, setError] = useState('');
  const [evidenceError, setEvidenceError] = useState('');

  useEffect(() => {
    let ignore = false;
    getVideoGraphOverview().then((value) => { if (!ignore) setOverview(value); }).catch(() => { if (!ignore) setOverview(null); });
    return () => { ignore = true; };
  }, [refresh]);

  useEffect(() => {
    let ignore = false;
    scope.current += 1;
    expansionBusy.current = false;
    setExpandingId(''); setExpansionError(''); setExpansionNotice('');
    setLoading(true); setError(''); setGraph(null); setSelectedEdge(null); setEvidence(null); setEvidenceOffset(0);
    getVideoGraph({ entity_id: entityId || undefined,
      q: appliedQuery || undefined, entity_type: entityType || undefined, assertion: assertion || undefined,
      hops, max_nodes: limits.initialNodes, max_edges: limits.initialEdges,
      min_component_size: MIN_GRAPH_COMPONENT_SIZE })
      .then((value) => { if (!ignore) setGraph(value); })
      .catch((reason: unknown) => { if (!ignore) setError(formatErrorMessage('无法加载知识图谱', reason)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; scope.current += 1; };
  }, [entityId, entityType, assertion, hops, appliedQuery, refresh, limits]);

  useEffect(() => {
    let ignore = false;
    setEvidence(null); setEvidenceError(''); setEvidenceLoading(false);
    if (selectedEdge?.kind === 'relation') {
      setEvidenceLoading(true);
      getVideoGraphEvidence(selectedEdge.id, {
        assertion: assertion || undefined, limit: 20, offset: evidenceOffset })
        .then((value) => { if (!ignore) setEvidence(value); })
        .catch((reason: unknown) => { if (!ignore) setEvidenceError(formatErrorMessage('无法加载关系证据', reason)); })
        .finally(() => { if (!ignore) setEvidenceLoading(false); });
    }
    return () => { ignore = true; };
  }, [selectedEdge, assertion, evidenceOffset, refresh]);

  async function expandNode(node: VideoGraphNode) {
    if (expansionBusy.current || node.kind !== 'entity') return;
    expansionBusy.current = true;
    const currentScope = scope.current;
    setExpandingId(node.id); setExpansionError(''); setExpansionNotice('');
    try {
      const incoming = await getVideoGraph({ entity_id: node.id,
        entity_type: entityType || undefined, assertion: assertion || undefined,
        hops: 1, max_nodes: limits.maxNodes, max_edges: limits.maxEdges,
        min_component_size: MIN_GRAPH_COMPONENT_SIZE });
      if (scope.current === currentScope && graph) {
        const merged = mergeGraphData(graph, incoming, limits.maxNodes, limits.maxEdges);
        setGraph(merged);
        if (merged.nodes.length >= limits.maxNodes || merged.edges.length >= limits.maxEdges)
          setExpansionNotice(`当前画布最多展示 ${limits.maxNodes} 个节点、${limits.maxEdges} 条关系；可搜索或聚焦实体查看其他内容。`);
      }
    } catch (reason) {
      if (scope.current === currentScope) setExpansionError(formatErrorMessage('无法展开邻居', reason));
    } finally {
      if (scope.current === currentScope) { expansionBusy.current = false; setExpandingId(''); }
    }
  }
  function selectEdge(edge: VideoGraphEdge) {
    if (edge.kind !== 'relation') return;
    setSelectedEdge(edge); setEvidenceOffset(0);
  }
  function searchEntities(event: FormEvent) { event.preventDefault(); setAppliedQuery(query.trim()); setEntityId(''); }

  const evidencePanel = (
<section aria-label="字幕证据"><h2>字幕证据</h2>
              {!selectedEdge ? <p className="muted">选择关系查看原文和视频时间点。</p> : null}
              {evidenceLoading ? <p className="status-line">正在加载证据…</p> : null}
              {evidenceError ? <p role="alert" className="error-text">{evidenceError}</p> : null}
              {evidence?.items.map((item) => {
                const href = safeExternalHref(item.video_url);
                return <article className="video-graph-evidence" key={item.id}><strong>{item.title || '视频'}</strong>
                  <p>{ASSERTION_LABELS[item.assertion]} · 抽取把握程度 {Math.round(item.confidence * 100)}% · 字幕来源 {item.caption_source || '未知'}</p>
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
  return <section className="video-graph-page" aria-label="视频知识图谱">
    <GraphWorkbench graph={graph} overview={overview}
    query={query} onQuery={setQuery} onSearch={searchEntities}
    entityType={entityType} onEntityType={(value) => { setEntityType(value); setEntityId(''); }}
    assertion={assertion} onAssertion={setAssertion} hops={hops} onHops={setHops}
    focusId={entityId} onClearFocus={() => { setEntityId(''); setAppliedQuery(''); setQuery(''); setRefresh((value) => value + 1); }}
    selectedEdge={selectedEdge} onEdge={selectEdge} onClearEdge={() => setSelectedEdge(null)}
    onFocus={(node) => { if (node.kind === 'entity') setEntityId(node.id); }} onExpand={expandNode}
    expandingId={expandingId} loading={loading} error={error} expansionError={expansionError} expansionNotice={expansionNotice}
    onRefresh={() => setRefresh((value) => value + 1)}
    evidencePanel={evidencePanel} />
  </section>;

}
