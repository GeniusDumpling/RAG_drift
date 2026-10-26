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
  for (const node of incoming.nodes) {
    if (nodes.has(node.id) || nodes.size < maxNodes) nodes.set(node.id, node);
    else truncated = true;
  }
  const edges = new Map(previous.edges.map((edge) => [edge.id, edge]));
  for (const edge of incoming.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) { truncated = true; continue; }
    if (edges.has(edge.id) || edges.size < maxEdges) edges.set(edge.id, edge);
    else truncated = true;
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated };
}
