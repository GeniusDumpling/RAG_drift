import type { VideoGraphData, VideoGraphEdge } from '../../api/types';
import { DESKTOP_GRAPH_LIMITS } from './limits';
import { KNOWLEDGE_LENSES } from './presentation';
import type { KnowledgeLensId } from './presentation';

/** Keep one component by node count, including after neighborhood merges and collapses. */
export function largestGraphComponent(graph: VideoGraphData): VideoGraphData {
  const adjacent = new Map(graph.nodes.map(node => [node.id, new Set<string>()]));
  const validEdges = graph.edges.filter(edge => adjacent.has(edge.source) && adjacent.has(edge.target));
  for (const edge of validEdges) {
    adjacent.get(edge.source)!.add(edge.target);
    adjacent.get(edge.target)!.add(edge.source);
  }
  const visited = new Set<string>();
  let largest = new Set<string>(), largestKey = '';
  for (const node of graph.nodes) {
    if (visited.has(node.id)) continue;
    const component = new Set([node.id]), stack = [node.id];
    let key = node.id;
    visited.add(node.id);
    while (stack.length) {
      for (const neighbor of adjacent.get(stack.pop()!)!) {
        if (visited.has(neighbor)) continue;
        visited.add(neighbor); component.add(neighbor); stack.push(neighbor);
        if (neighbor < key) key = neighbor;
      }
    }
    if (component.size > largest.size || component.size === largest.size && key < largestKey) {
      largest = component; largestKey = key;
    }
  }
  if (largest.size === graph.nodes.length && validEdges.length === graph.edges.length) return graph;
  const edges = validEdges.filter(edge => largest.has(edge.source) && largest.has(edge.target));
  return { nodes: graph.nodes.filter(node => largest.has(node.id)), edges,
    truncated: graph.truncated || largest.size < graph.nodes.length || edges.length < graph.edges.length };
}

/** Count visible relations, including parallel/reversed edges and each self-loop once. */
export function nodeRelationCounts(edges: VideoGraphEdge[]) {
  const counts = new Map<string, number>();
  for (const edge of edges) {
    if (edge.kind !== 'relation') continue;
    counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1);
    if (edge.target !== edge.source) counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1);
  }
  return counts;
}

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

/** Filter the loaded graph by a knowledge lens, keeping only edges matching the lens and their endpoints. */
export function filterGraphByLens(graph: VideoGraphData, lensId: KnowledgeLensId): VideoGraphData {
  if (lensId === 'all') return graph;
  const lens = KNOWLEDGE_LENSES.find((item) => item.id === lensId);
  if (!lens?.predicates) return graph;
  const allowed = new Set(lens.predicates);
  const edges = graph.edges.filter((edge) => allowed.has(edge.predicate));
  const nodeIds = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  return {
    nodes: graph.nodes.filter((node) => nodeIds.has(node.id)),
    edges,
    truncated: graph.truncated,
  };
}

export type KnowledgePathStep = {
  edge: VideoGraphEdge;
  fromId: string;
  toId: string;
  direction: 'out' | 'in';
};

export type KnowledgePathResult = {
  nodeIds: string[];
  edgeIds: string[];
  steps: KnowledgePathStep[];
};

/** Find the shortest knowledge chain (up to maxHops) between two entities in the visible graph. */
export function findShortestKnowledgePath(
  graph: VideoGraphData,
  startId: string,
  targetId: string,
  maxHops = 4,
): KnowledgePathResult | null {
  if (!startId || !targetId || startId === targetId) return null;
  const nodeSet = new Set(graph.nodes.map((node) => node.id));
  if (!nodeSet.has(startId) || !nodeSet.has(targetId)) return null;

  const adjacency = new Map<string, Array<{ nextId: string; step: KnowledgePathStep }>>();
  const sortedEdges = [...graph.edges].sort((a, b) => b.evidence_count - a.evidence_count || a.id.localeCompare(b.id));
  for (const edge of sortedEdges) {
    if (edge.source === edge.target || !nodeSet.has(edge.source) || !nodeSet.has(edge.target)) continue;
    const outList = adjacency.get(edge.source) ?? [];
    outList.push({ nextId: edge.target, step: { edge, fromId: edge.source, toId: edge.target, direction: 'out' } });
    adjacency.set(edge.source, outList);
    const inList = adjacency.get(edge.target) ?? [];
    inList.push({ nextId: edge.source, step: { edge, fromId: edge.target, toId: edge.source, direction: 'in' } });
    adjacency.set(edge.target, inList);
  }

  const visited = new Set([startId]);
  const parent = new Map<string, { prevId: string; step: KnowledgePathStep }>();
  const queue: Array<{ id: string; depth: number }> = [{ id: startId, depth: 0 }];

  while (queue.length) {
    const current = queue.shift()!;
    if (current.depth >= maxHops) continue;
    for (const neighbor of adjacency.get(current.id) ?? []) {
      if (visited.has(neighbor.nextId)) continue;
      visited.add(neighbor.nextId);
      parent.set(neighbor.nextId, { prevId: current.id, step: neighbor.step });
      if (neighbor.nextId === targetId) {
        const steps: KnowledgePathStep[] = [];
        const nodeIds = [targetId];
        let cursor = targetId;
        while (cursor !== startId) {
          const link = parent.get(cursor)!;
          steps.unshift(link.step);
          nodeIds.unshift(link.prevId);
          cursor = link.prevId;
        }
        return { nodeIds, edgeIds: steps.map((step) => step.edge.id), steps };
      }
      queue.push({ id: neighbor.nextId, depth: current.depth + 1 });
    }
  }
  return null;
}
