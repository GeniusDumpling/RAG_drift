import { browserCaption, browserRelationship, NODE_LABEL_FONT_SIZE, RELATION_LABEL_FONT_SIZE } from './browser-geometry';

export type MotionNode = { id: string; x: number; y: number; radius: number; fx?: number | null };
export type MotionView = { x: number; y: number; k: number };
type NodeEntry = { node: MotionNode; element: SVGGElement; label: string; enabled: boolean; emphasized: boolean; text?: SVGTextElement | null; pin?: Element | null };
type EdgeEntry = { source: MotionNode; target: MotionNode; element: SVGGElement; label: string; offset: number; loop: number; emphasized: boolean; showLabel?: boolean; defaultColor?: string; text?: SVGTextElement | null; paths?: NodeListOf<SVGPathElement> };
const NS = 'http://www.w3.org/2000/svg';

export function readableLabel(radius: number, zoom: number, fontSize: number) {
  return radius * 2 * zoom >= 28 && fontSize * zoom >= 7;
}

export function readableEdgeLabel(source: MotionNode, target: MotionNode, zoom: number) {
  if (zoom < 0.72 || RELATION_LABEL_FONT_SIZE * zoom < 8) return false;
  if (source === target) return source.radius * 2 * zoom >= 36;
  const gap = Math.hypot(target.x - source.x, target.y - source.y) - source.radius - target.radius;
  return gap * zoom >= 54;
}

function attr(element: Element, key: string, value: string) {
  if (element.getAttribute(key) !== value) element.setAttribute(key, value);
}

/** Own animation attributes and labels outside React. React owns topology and interaction state. */
export class BrowserMotion {
  nodes: NodeEntry[] = [];
  edges: EdgeEntry[] = [];
  private captions = new Map<string, { key: string; lines: string[] }>();
  constructor(public world: SVGGElement) {}

  paint(view: MotionView, geometry: boolean) {
    attr(this.world, 'transform', `translate(${view.x},${view.y}) scale(${view.k})`);
    for (const entry of this.nodes) {
      const { node, element, label } = entry;
      if (geometry) {
        attr(element, 'transform', `translate(${node.x},${node.y})`);
        attr(element, 'data-pinned', String(node.fx != null));
        const pin = entry.pin === undefined ? entry.pin = element.querySelector('[data-pin]') : entry.pin;
        if (pin) attr(pin, 'display', node.fx != null ? 'inline' : 'none');
      }
      const visible = label && (entry.emphasized || entry.enabled && readableLabel(node.radius, view.k, NODE_LABEL_FONT_SIZE));
      let text = entry.text === undefined ? entry.text = element.querySelector<SVGTextElement>('[data-motion-label]') : entry.text;
      if (!visible) { text?.remove(); entry.text = null; continue; }
      const font = entry.emphasized ? Math.max(NODE_LABEL_FONT_SIZE, 12 / view.k) : NODE_LABEL_FONT_SIZE;
      const focus = entry.emphasized && !readableLabel(node.radius, view.k, NODE_LABEL_FONT_SIZE);
      const key = `${label}:${node.radius}:${focus}`;
      if (!text || text.getAttribute('data-caption-key') !== key) {
        if (!text) {
          text = document.createElementNS(NS, 'text');
          text.setAttribute('data-motion-label', 'node');
          text.setAttribute('text-anchor', 'middle'); text.setAttribute('fill', '#fff');
          text.setAttribute('font-family', 'Arial,sans-serif'); text.setAttribute('pointer-events', 'none');
          element.append(text); entry.text = text;
        }
        // Focused names remain readable even when the graph is zoomed out.
        let cached = this.captions.get(node.id);
        if (cached?.key !== key) {
          cached = { key, lines: entry.emphasized && !readableLabel(node.radius, view.k, NODE_LABEL_FONT_SIZE) ? [label] : browserCaption(label, node.radius) };
          this.captions.set(node.id, cached);
        }
        text.replaceChildren(...cached.lines.map((line, i) => {
          const span = document.createElementNS(NS, 'tspan'); span.setAttribute('x', '0');
          span.setAttribute('y', String((i - (cached!.lines.length - 1) / 2) * (NODE_LABEL_FONT_SIZE + 2) + NODE_LABEL_FONT_SIZE * 0.35)); span.textContent = line; return span;
        }));
        attr(text, 'data-caption-key', key);
      }
      attr(text!, 'font-size', String(font));
      attr(text!, 'fill', focus ? '#334155' : '#fff');
      attr(text!, 'stroke', focus ? '#fff' : 'none');
      attr(text!, 'stroke-width', focus ? String(3 / view.k) : '0');
      attr(text!, 'paint-order', 'stroke');
    }
    for (const entry of this.edges) {
      const { element, source, target, label } = entry;
      let text = entry.text === undefined ? entry.text = element.querySelector<SVGTextElement>('[data-motion-label]') : entry.text;
      const visible = Boolean(label && (entry.emphasized || (entry.showLabel && readableEdgeLabel(source, target, view.k))));
      let geometryValue: ReturnType<typeof browserRelationship> | undefined;
      if (geometry) {
        geometryValue = browserRelationship(source, target, entry.offset, entry.loop);
        for (const path of entry.paths ??= element.querySelectorAll('path')) attr(path, 'd', geometryValue.path);
      }
      if (!visible) { text?.remove(); entry.text = null; continue; }
      const created = !text;
      if (!text) {
        text = document.createElementNS(NS, 'text'); text.setAttribute('data-motion-label', 'edge');
        for (const [key, value] of Object.entries({ 'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-family': 'Arial,sans-serif', stroke: '#fff', 'stroke-width': '5', 'paint-order': 'stroke', 'stroke-linejoin': 'round', 'pointer-events': 'none' })) text.setAttribute(key, value);
        element.append(text); entry.text = text;
      }
      if (text.textContent !== label) text.textContent = label;
      attr(text, 'font-size', String(entry.emphasized ? Math.max(RELATION_LABEL_FONT_SIZE, 12 / view.k) : Math.max(10, RELATION_LABEL_FONT_SIZE - 1)));
      attr(text, 'fill', entry.emphasized ? '#2879bd' : (entry.defaultColor ?? '#5c6470'));
      if (!geometry && !created) continue;
      geometryValue ??= browserRelationship(source, target, entry.offset, entry.loop);
      attr(text, 'x', String(geometryValue.x)); attr(text, 'y', String(geometryValue.y));
      attr(text, 'transform', `rotate(${geometryValue.angle},${geometryValue.x},${geometryValue.y})`);
    }
  }
}
