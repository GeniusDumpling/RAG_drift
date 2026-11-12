import { browserCaption, browserRelationship } from './browser-geometry';

export type MotionNode = { id: string; x: number; y: number; radius: number; fx?: number | null };
export type MotionView = { x: number; y: number; k: number };
type NodeEntry = { node: MotionNode; element: SVGGElement; label: string; enabled: boolean; emphasized: boolean; text?: SVGTextElement | null; pin?: Element | null };
type EdgeEntry = { source: MotionNode; target: MotionNode; element: SVGGElement; label: string; offset: number; loop: number; emphasized: boolean; text?: SVGTextElement | null; paths?: NodeListOf<SVGPathElement> };
const NS = 'http://www.w3.org/2000/svg';

export function readableLabel(radius: number, zoom: number, fontSize: number) {
  return radius * 2 * zoom >= 28 && fontSize * zoom >= 7;
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
      const visible = label && (entry.emphasized || entry.enabled && readableLabel(node.radius, view.k, 11));
      let text = entry.text === undefined ? entry.text = element.querySelector<SVGTextElement>('[data-motion-label]') : entry.text;
      if (!visible) { text?.remove(); entry.text = null; continue; }
      const font = entry.emphasized ? Math.max(11, 10 / view.k) : 11;
      const focus = entry.emphasized && !readableLabel(node.radius, view.k, 11);
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
          cached = { key, lines: entry.emphasized && !readableLabel(node.radius, view.k, 11) ? [label] : browserCaption(label, node.radius) };
          this.captions.set(node.id, cached);
        }
        text.replaceChildren(...cached.lines.map((line, i) => {
          const span = document.createElementNS(NS, 'tspan'); span.setAttribute('x', '0');
          span.setAttribute('y', String((i - (cached!.lines.length - 1) / 2) * 13 + 4)); span.textContent = line; return span;
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
      const visible = label && (entry.emphasized || readableLabel(Math.min(source.radius, target.radius), view.k, 10));
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
      attr(text, 'font-size', String(entry.emphasized ? Math.max(10, 10 / view.k) : 10));
      attr(text, 'fill', entry.emphasized ? '#2879bd' : '#5c6470');
      if (!geometry && !created) continue;
      geometryValue ??= browserRelationship(source, target, entry.offset, entry.loop);
      attr(text, 'x', String(geometryValue.x)); attr(text, 'y', String(geometryValue.y));
      attr(text, 'transform', `rotate(${geometryValue.angle},${geometryValue.x},${geometryValue.y})`);
    }
  }
}
