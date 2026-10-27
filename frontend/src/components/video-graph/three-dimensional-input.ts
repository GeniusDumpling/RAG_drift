import type { Graph } from '@antv/g6';
import { ENTITY_LABELS, PREDICATE_LABELS } from './presentation';

type Hit = { id: string; kind: 'node' | 'edge'; x: number; y: number; depth: number; radius: number };
type Callbacks = {
  select: (id: string | null) => void; edge: (id: string) => void;
  expand: (id: string) => void; hover: (id: string | null) => void;
  selected: () => string; showLabels: () => boolean; error: () => void;
};

/** Project all three coordinates: G's general canvas2Viewport utility drops Z. */
export function project3D(graph: Graph, position: ArrayLike<number>) {
  const camera = graph.getCanvas().getCamera();
  const multiply = (matrix: ArrayLike<number>, vector: number[]) => [0, 1, 2, 3].map((row) =>
    matrix[row] * vector[0] + matrix[row + 4] * vector[1]
    + matrix[row + 8] * vector[2] + matrix[row + 12] * vector[3]);
  const clip = multiply(camera.getPerspective(), multiply(camera.getViewTransform(), [...Array.from(position), 1]));
  const [width, height] = graph.getSize();
  return { x: (clip[0] / clip[3] + 1) * width / 2,
    y: (1 - clip[1] / clip[3]) * height / 2, depth: clip[3] };
}

/** Convert screen movement with the projection's actual axis signs (G flips Y). */
export function screenDelta3D(
  graph: Graph, anchor: ArrayLike<number>, dx: number, dy: number,
): [number, number, number] {
  const camera = graph.getCanvas().getCamera();
  const matrix = camera.getWorldTransform(), projection = camera.getPerspective();
  const eye = camera.getPosition();
  const depth = Math.max(1, Math.abs((eye[0] - anchor[0]) * matrix[8]
    + (eye[1] - anchor[1]) * matrix[9] + (eye[2] - (anchor[2] ?? 0)) * matrix[10]));
  const [width, height] = graph.getSize();
  const horizontal = dx * 2 * depth / (projection[0] * width);
  const vertical = -dy * 2 * depth / (projection[5] * height);
  return [
    matrix[0] * horizontal + matrix[4] * vertical,
    matrix[1] * horizontal + matrix[5] * vertical,
    matrix[2] * horizontal + matrix[6] * vertical,
  ];
}

/** G6's zoom landmarks rebuild a world-up camera; keep the free orbit orientation. */
export async function zoom3D(graph: Graph, zoom: number): Promise<void> {
  const camera = graph.getCanvas().getCamera();
  const orientation = new Float32Array(camera.getWorldTransform());
  await graph.zoomTo(zoom, false);
  if (!graph.destroyed) camera.setMatrix(orientation);
}

/** Move the orbit pivot to a node without changing zoom, distance or orientation. */
export function focusNode3D(graph: Graph, id: string): void {
  const target = graph.getElementPosition(id);
  const camera = graph.getCanvas().getCamera();
  const focal = camera.getFocalPoint();
  if (Math.hypot(target[0] - focal[0], target[1] - focal[1], (target[2] ?? 0) - focal[2]) < 0.001) return;
  const orientation = new Float32Array(camera.getWorldTransform());
  const distance = camera.getDistance();
  const eye = [0, 1, 2].map((axis) => (target[axis] ?? 0) + orientation[8 + axis] * distance);
  camera.setPosition(eye[0], eye[1], eye[2]);
  camera.setFocalPoint(target[0], target[1], target[2] ?? 0);
  // setFocalPoint rebuilds a world-up basis, so restore the original even when inverted.
  orientation[12] = eye[0]; orientation[13] = eye[1]; orientation[14] = eye[2];
  camera.setMatrix(orientation);
}

/** Screen-space picking avoids synchronous GPU readback on every pointer movement. */
export function bindInput3D(graph: Graph, host: HTMLElement, callbacks: Callbacks): () => void {
  const surface = document.createElement('div');
  surface.className = 'graph-3d-input';
  const labels = document.createElement('div');
  labels.className = 'graph-3d-labels'; labels.setAttribute('aria-hidden', 'true');
  const tooltip = document.createElement('div');
  tooltip.className = 'graph-3d-tooltip graph-node-tooltip'; tooltip.hidden = true;
  const title = document.createElement('strong');
  const description = document.createElement('small');
  tooltip.append(title, description); surface.append(labels, tooltip); host.append(surface);
  let disposed = false;
  let hovered: Hit | null = null;
  let frame = 0;
  let queue = Promise.resolve();
  let pressed: { x: number; y: number; startX: number; startY: number; hit: Hit | null; moved: boolean } | null = null;
  let lastNodeClick: { id: string; x: number; y: number; time: number } | null = null;
  const names = new Map<string, HTMLSpanElement>();

  function projectedNodes(): Hit[] {
    const scale = Math.abs(graph.getCanvas().getCamera().getPerspective()[5]) * graph.getSize()[1] / 2;
    return graph.getNodeData().map((node) => {
      const point = project3D(graph, graph.getElementPosition(node.id));
      const size = graph.getElementRenderStyle(node.id).size;
      const radius = (Array.isArray(size) ? size[0] : Number(size)) / 2 * scale / point.depth;
      return { ...point, radius, id: node.id, kind: 'node' as const };
    }).filter((node) => node.depth > 0);
  }
  function hitTest(x: number, y: number): Hit | null {
    const nodes = projectedNodes();
    const node = nodes.filter((item) => Math.hypot(item.x - x, item.y - y) <= Math.max(5, item.radius))
      .sort((a, b) => a.depth - b.depth)[0];
    if (node) return node;
    const positions = new Map(nodes.map((item) => [item.id, item]));
    let closest: Hit | null = null;
    let distance = 4;
    for (const edge of graph.getEdgeData()) {
      if (!edge.id) continue;
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if (!a || !b) continue;
      const length = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
      if (length < 1) continue;
      const t = Math.max(0, Math.min(1, ((x - a.x) * (b.x - a.x) + (y - a.y) * (b.y - a.y)) / length));
      const gap = Math.hypot(x - a.x - t * (b.x - a.x), y - a.y - t * (b.y - a.y));
      if (gap < distance) {
        distance = gap;
        closest = { id: edge.id, kind: 'edge', x, y, radius: 0, depth: a.depth * (1 - t) + b.depth * t };
      }
    }
    return closest;
  }
  function updateLabels() {
    if (disposed || graph.destroyed || !graph.rendered) return;
    const visible = new Set<string>();
    for (const node of graph.getNodeData()) {
      const states = graph.getElementState(node.id);
      if (!callbacks.showLabels() && !states.includes('selected') && hovered?.id !== node.id) continue;
      const point = project3D(graph, graph.getElementPosition(node.id));
      if (point.depth <= 0) continue;
      visible.add(node.id);
      let name = names.get(node.id);
      if (!name) { name = document.createElement('span'); labels.append(name); names.set(node.id, name); }
      name.textContent = String(node.data?.label ?? '');
      name.style.transform = `translate(${point.x}px, ${point.y + 12}px) translateX(-50%)`;
      name.hidden = false;
    }
    for (const [id, name] of names) if (!visible.has(id)) name.hidden = true;
  }
  function scheduleLabels() { cancelAnimationFrame(frame); frame = requestAnimationFrame(updateLabels); }
  function hover(hit: Hit | null, x = 0, y = 0) {
    surface.style.cursor = hit?.kind === 'node' ? 'grab' : hit ? 'pointer' : 'grab';
    if (hit) {
      const data = hit.kind === 'node' ? graph.getNodeData(hit.id) : graph.getEdgeData(hit.id);
      title.textContent = String(data.data?.label ?? PREDICATE_LABELS[String(data.data?.predicate)] ?? '关系');
      description.textContent = hit.kind === 'node'
        ? `${ENTITY_LABELS[String(data.data?.entity_type)] ?? '实体'} · 拖拽移动，双击展开`
        : `${data.data?.evidence_count ?? 0} 条证据 · 点击查看原文`;
      tooltip.style.left = `${Math.max(8, Math.min(host.clientWidth - 200, x + 14))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(host.clientHeight - 50, y + 18))}px`;
    }
    tooltip.hidden = !hit;
    if (hovered?.id === hit?.id) return;
    hovered = hit;
    callbacks.hover(hit?.kind === 'node' ? hit.id : null);
    const activeNodes = new Set<string>();
    const activeEdges = new Set<string>();
    if (hit?.kind === 'node') {
      activeNodes.add(hit.id);
      for (const edge of graph.getRelatedEdgesData(hit.id)) {
        if (!edge.id) continue;
        activeEdges.add(edge.id); activeNodes.add(edge.source); activeNodes.add(edge.target);
      }
    }
    const highlightedEdge = hit?.kind === 'edge' ? hit.id : '';
    queue = queue.then(async () => {
      if (disposed || graph.destroyed) return;
      const states: Record<string, string[]> = {};
      for (const item of [...graph.getNodeData(), ...graph.getEdgeData()]) {
        if (!item.id) continue;
        const previous = graph.getElementState(item.id);
        const next = previous.filter((state) => !['active', 'hovered', 'inactive'].includes(state));
        if (activeNodes.has(item.id) || activeEdges.has(item.id)) next.push('active');
        if (highlightedEdge === item.id) next.push('hovered');
        if (next.join() !== previous.join()) states[item.id] = next;
      }
      if (Object.keys(states).length) await graph.setElementState(states, false);
      scheduleLabels();
    }).catch(() => { if (!disposed) callbacks.error(); });
  }
  function position(event: PointerEvent | MouseEvent) {
    const rect = surface.getBoundingClientRect();
    return [event.clientX - rect.x, event.clientY - rect.y];
  }
  const down = (event: PointerEvent) => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    const [x, y] = position(event);
    pressed = { x, y, startX: x, startY: y, hit: hitTest(x, y), moved: false };
    surface.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent) => {
    event.stopPropagation();
    const [x, y] = position(event);
    if (!pressed) { hover(hitTest(x, y), x, y); return; }
    const dx = x - pressed.x, dy = y - pressed.y;
    pressed.x = x; pressed.y = y;
    if (Math.hypot(x - pressed.startX, y - pressed.startY) > 3) pressed.moved = true;
    if (!pressed.moved) return;
    tooltip.hidden = true;
    const camera = graph.getCanvas().getCamera();
    const eye = [...camera.getPosition()], focal = [...camera.getFocalPoint()];
    if (pressed.hit?.kind === 'node' || event.shiftKey) {
      const node = pressed.hit?.kind === 'node' ? graph.getElementPosition(pressed.hit.id) : focal;
      const delta = screenDelta3D(graph, node, dx, dy);
      if (pressed.hit?.kind === 'node') {
        const id = pressed.hit.id;
        queue = queue.then(async () => {
          if (!disposed && !graph.destroyed) await graph.translateElementBy(id, delta, false);
        }).catch(() => { if (!disposed) callbacks.error(); });
      } else {
        // Looking at a focal point reconstructs a world-up camera. Restore the
        // existing basis so panning beyond a pole keeps the flipped view intact.
        const orientation = new Float32Array(camera.getWorldTransform());
        camera.setPosition(eye[0] - delta[0], eye[1] - delta[1], eye[2] - delta[2]);
        camera.setFocalPoint(focal[0] - delta[0], focal[1] - delta[1], focal[2] - delta[2]);
        orientation[12] = eye[0] - delta[0];
        orientation[13] = eye[1] - delta[1];
        orientation[14] = eye[2] - delta[2];
        camera.setMatrix(orientation);
      }
    } else {
      // A selected node may have been dragged or the view panned since selection.
      const selected = callbacks.selected();
      if (selected) focusNode3D(graph, selected);
      camera.rotate(dx * 0.35, -dy * 0.35, 0);
    }
    scheduleLabels();
  };
  const up = (event: PointerEvent) => {
    event.stopPropagation();
    if (!pressed) return;
    if (!pressed.moved && event.type !== 'pointercancel') {
      // Centering moves the sphere away from the pointer between the two clicks.
      const repeated = lastNodeClick && event.timeStamp - lastNodeClick.time < 500
        && Math.hypot(pressed.x - lastNodeClick.x, pressed.y - lastNodeClick.y) < 5;
      const nodeId = repeated ? lastNodeClick!.id : pressed.hit?.kind === 'node' ? pressed.hit.id : '';
      if (nodeId) {
        focusNode3D(graph, nodeId);
        callbacks.select(nodeId);
        lastNodeClick = { id: nodeId, x: pressed.x, y: pressed.y, time: event.timeStamp };
      }
      else {
        lastNodeClick = null;
        if (pressed.hit) callbacks.edge(pressed.hit.id);
        else callbacks.select(null);
      }
    }
    pressed = null;
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    scheduleLabels();
  };
  const leave = () => { if (!pressed) hover(null); };
  const doubleClick = (event: MouseEvent) => {
    const [x, y] = position(event);
    const recent = lastNodeClick && event.timeStamp - lastNodeClick.time < 500
      && Math.hypot(x - lastNodeClick.x, y - lastNodeClick.y) < 5;
    const hit = recent ? { kind: 'node', id: lastNodeClick!.id } : hitTest(x, y);
    if (hit?.kind === 'node') callbacks.expand(hit.id);
    lastNodeClick = null;
  };
  const wheel = (event: WheelEvent) => {
    event.preventDefault(); event.stopPropagation();
    const [minimum, maximum] = graph.getZoomRange() ?? [1, 8];
    const zoom = graph.getZoom() * Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.005);
    void zoom3D(graph, Math.max(minimum, Math.min(maximum, zoom)));
  };
  surface.addEventListener('pointerdown', down);
  surface.addEventListener('pointermove', move);
  surface.addEventListener('pointerup', up);
  surface.addEventListener('pointercancel', up);
  surface.addEventListener('pointerleave', leave);
  surface.addEventListener('dblclick', doubleClick);
  surface.addEventListener('wheel', wheel, { passive: false });
  graph.on('afterdraw', scheduleLabels); graph.on('aftertransform', scheduleLabels);
  scheduleLabels();
  return () => {
    disposed = true; cancelAnimationFrame(frame);
    graph.off('afterdraw', scheduleLabels); graph.off('aftertransform', scheduleLabels);
    surface.remove(); names.clear();
  };
}
