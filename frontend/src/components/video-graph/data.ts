import type { VideoGraphData, VideoGraphEdge } from '../../api/types';
import { DESKTOP_GRAPH_LIMITS } from './limits';

/** Precompute parallel curves once instead of scanning all edges during every hover redraw. */
export function parallelEdgeStyles(edges: VideoGraphEdge[]) {
  const groups = new Map<string, VideoGraphEdge[]>();
  for (const edge of edges) {
    const key = JSON.stringify([edge.source, edge.target].sort());
    const group = groups.get(key) ?? [];
    group.push(edge); groups.set(key, group);
  }
  const styles = new Map<string, { curveOffset: number; loopDist?: number }>();
  for (const group of groups.values()) group.forEach((edge, index) => {
    const direction = edge.source > edge.target ? -1 : 1;
    styles.set(edge.id, {
      curveOffset: (index - (group.length - 1) / 2) * 24 * direction,
      ...(edge.source === edge.target ? { loopDist: 35 + index * 24 } : {}),
    });
  });
  return styles;
}

/** Merge one-hop results without dropping the neighborhood the reader already opened. */
export function mergeGraphData(previous: VideoGraphData, incoming: VideoGraphData, maxNodes = DESKTOP_GRAPH_LIMITS.maxNodes, maxEdges = DESKTOP_GRAPH_LIMITS.maxEdges): VideoGraphData {
  const nodes = new Map(previous.nodes.map((node) => [node.id, node]));
  let truncated = previous.truncated || incoming.truncated;
  const candidates = new Map(incoming.nodes.map((node) => [node.id, node]));
  const edges = new Map(previous.edges.map((edge) => [edge.id, edge]));
  // Add complete relation pairs together, so exhausting edges cannot leave isolated nodes.
  // Reserve room for new neighbors before filling it with links between existing nodes.
  for (const edge of incoming.edges) {
    const missing = [...new Set([edge.source, edge.target])].filter((id) => !nodes.has(id));
    if (!missing.length) continue;
    if (missing.some((id) => !candidates.has(id)) || nodes.size + missing.length > maxNodes
      || (!edges.has(edge.id) && edges.size >= maxEdges)) { truncated = true; continue; }
    for (const id of missing) nodes.set(id, candidates.get(id)!);
    edges.set(edge.id, edge);
  }
  for (const edge of incoming.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)
      || (!edges.has(edge.id) && edges.size >= maxEdges)) { truncated = true; continue; }
    edges.set(edge.id, edge);
  }
  if (incoming.nodes.some((node) => !nodes.has(node.id))) truncated = true;
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated };
}
