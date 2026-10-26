export type GraphLimits = {
  initialNodes: number; initialEdges: number; maxNodes: number; maxEdges: number;
};

export const MIN_GRAPH_COMPONENT_SIZE = 5;

export const DESKTOP_GRAPH_LIMITS: GraphLimits = {
  initialNodes: 300, initialEdges: 500, maxNodes: 500, maxEdges: 1000,
};
export const MOBILE_GRAPH_LIMITS: GraphLimits = {
  initialNodes: 150, initialEdges: 300, maxNodes: 150, maxEdges: 300,
};

export function graphLimitsForWidth(width: number): GraphLimits {
  return width <= 680 ? MOBILE_GRAPH_LIMITS : DESKTOP_GRAPH_LIMITS;
}
