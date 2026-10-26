import type { Graph } from '@antv/g6';

const MAX_ZOOM = 8;
export const INITIAL_ZOOM_RANGE: [number, number] = [0.000001, MAX_ZOOM];

export function atMinimumZoom(graph: Graph): boolean {
  const minimum = graph.getZoomRange()?.[0] ?? INITIAL_ZOOM_RANGE[0];
  return graph.getZoom() <= minimum * (1 + 1e-6);
}

/** Recalculate the overview before locking its scale as the zoom-out boundary. */
export async function fitDefaultViewport(graph: Graph, preserveViewport = false): Promise<void> {
  const previousZoom = graph.getZoom();
  const wasAtMinimum = atMinimumZoom(graph);
  const [width, height] = graph.getSize();
  const center = preserveViewport ? graph.getCanvasByViewport([width / 2, height / 2]) : null;
  // An old minimum must not prevent fitting a larger graph or a smaller canvas.
  graph.setZoomRange([...INITIAL_ZOOM_RANGE]);
  await graph.fitView({}, false);
  if (graph.destroyed) return;
  const minimum = graph.getZoom();
  const maximum = Math.max(MAX_ZOOM, minimum * 8);
  graph.setZoomRange([minimum, maximum]);
  if (preserveViewport && !wasAtMinimum && previousZoom > minimum && center) {
    await graph.zoomTo(Math.min(previousZoom, maximum), false);
    if (graph.destroyed) return;
    const [x, y] = graph.getViewportByCanvas(center);
    await graph.translateBy([width / 2 - x, height / 2 - y], false);
  }
}
