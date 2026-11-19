import type { VideoGraphEdge, VideoGraphNode } from '../../api/types';
import { entityLayerIndex, lensSchemaFor, nodeLensLayerIndex } from './presentation';
import type { KnowledgeLensId } from './presentation';

export type LayoutNode = { id: string; x: number; y: number; radius: number; item: VideoGraphNode };
export type LayerBounds = { left: number; right: number; top: number; bottom: number };
export type ColumnBand = {
  index: number;
  label: string;
  summary: string;
  count: number;
  arrowAfter?: '→' | '←';
  x: number;
  width: number;
  top: number;
  bottom: number;
  tint: string;
};
export type RadialGuide = {
  innerRadius: number;
  outerRadius: number;
  innerTint: string;
  outerTint: string;
  innerLabel: string;
  outerLabel: string;
  innerCount: number;
  outerCount: number;
};

/** Plan without mutating live positions, so every lens can share one camera envelope. */
export function planKnowledgeLayout(
  nodes: LayoutNode[], edges: VideoGraphEdge[], counts: Map<string, number>,
  activeLens: KnowledgeLensId, matchedIds: Set<string> | null,
) {
  const schema = lensSchemaFor(activeLens);
  const focalNodes = matchedIds ? nodes.filter(n => matchedIds.has(n.id)) : nodes;
  const contextNodes = matchedIds ? nodes.filter(n => !matchedIds.has(n.id)) : [];
  const contextSet = new Set(contextNodes.map(n => n.id));

  const layerTargets = new Map<string, { x: number; y: number }>();
  const layerBounds = new Map<string, LayerBounds>();
  const ringBounds = new Map<string, number>();
  const nextBands: ColumnBand[] = [];
  let nextRadial: RadialGuide | null = null;
  let contextRingRadius = 560;

  if (focalNodes.length) {
    if (schema.geometry === 'columns' || schema.geometry === 'converge') {
      const buckets = new Map<number, LayoutNode[]>();
      for (const node of focalNodes) {
        const layerIdx = nodeLensLayerIndex(node.item, activeLens);
        const list = buckets.get(layerIdx) ?? [];
        list.push(node);
        buckets.set(layerIdx, list);
      }
      const activeLayerIndices = [...buckets.keys()].sort((a, b) => a - b);
      const layerPlans = activeLayerIndices.map((layerIdx) => {
        const colNodes = buckets.get(layerIdx)!;
        colNodes.sort((a, b) => (counts.get(b.id) ?? 0) - (counts.get(a.id) ?? 0) || a.id.localeCompare(b.id));
        const count = colNodes.length;
        const maxRadius = Math.max(22, ...colNodes.map(n => n.radius));
        const subCols = count <= 6 ? 1 : count <= 16 ? 2 : count <= 36 ? 3 : count <= 64 ? 4 : Math.min(6, Math.ceil(Math.sqrt(count / 3)));
        const rows = Math.ceil(count / subCols);
        const subColGap = Math.max(140, maxRadius * 2 + 52);
        const stepY = Math.max(116, maxRadius * 2 + 48);
        const padX = maxRadius + 46;
        const padY = maxRadius + 46;
        const bandWidth = Math.max(220, (subCols - 1) * subColGap + padX * 2);
        const staggerY = subCols > 1 ? stepY * 0.24 : 0;
        const height = Math.max(220, (rows - 1) * stepY + staggerY + padY * 2);
        return { layerIdx, colNodes, subCols, rows, subColGap, stepY, padX, padY, staggerY, bandWidth, height };
      });
      const maxHeight = Math.max(220, ...layerPlans.map(p => p.height));
      const interLayerGap = Math.max(380, Math.round(maxHeight * 0.28));
      const totalWidth = layerPlans.reduce((sum, p) => sum + p.bandWidth, 0) + Math.max(0, layerPlans.length - 1) * interLayerGap;
      contextRingRadius = Math.max(totalWidth, maxHeight) * 0.62 + 120;

      let cursorX = -totalWidth / 2;
      for (const plan of layerPlans) {
        const centerX = cursorX + plan.bandWidth / 2;
        cursorX += plan.bandWidth + interLayerGap;
        const contentSpanY = (plan.rows - 1) * plan.stepY + plan.staggerY;
        const startY = -contentSpanY / 2;
        const boxLeft = centerX - plan.bandWidth / 2;
        const boxRight = centerX + plan.bandWidth / 2;
        const boxTop = startY - plan.padY;
        const boxBottom = startY + contentSpanY + plan.padY;

        plan.colNodes.forEach((node, idx) => {
          const row = Math.floor(idx / plan.subCols);
          const colInRow = idx % plan.subCols;
          const itemsInRow = row === plan.rows - 1 ? plan.colNodes.length - row * plan.subCols : plan.subCols;
          const xOffset = (colInRow - (itemsInRow - 1) / 2) * plan.subColGap;
          const yStagger = (colInRow % 2 === 1 && itemsInRow > 1 ? plan.staggerY : 0);
          const x = centerX + xOffset;
          const y = startY + row * plan.stepY + yStagger;
          layerTargets.set(node.id, { x, y });
          layerBounds.set(node.id, { left: boxLeft, right: boxRight, top: boxTop, bottom: boxBottom });
        });
        const meta = schema.layers.find(layer => layer.index === plan.layerIdx) ?? schema.layers[0];
        nextBands.push({
          index: plan.layerIdx,
          label: meta.label,
          summary: meta.summary,
          count: plan.colNodes.length,
          arrowAfter: meta.flowArrowAfter,
          x: centerX,
          width: plan.bandWidth,
          top: boxTop,
          bottom: boxBottom,
          tint: meta.bandTint,
        });
      }
    } else {
      // 'radial' (ecosystem) or 'force-radial' (all in structured mode)
      const sortedFocal = [...focalNodes].sort((a, b) => (counts.get(b.id) ?? 0) - (counts.get(a.id) ?? 0) || a.id.localeCompare(b.id));
      let innerNodes: LayoutNode[];
      let outerNodes: LayoutNode[];
      if (schema.geometry === 'radial') {
        innerNodes = sortedFocal.filter(n => nodeLensLayerIndex(n.item, activeLens) === 0);
        outerNodes = sortedFocal.filter(n => nodeLensLayerIndex(n.item, activeLens) !== 0);
        if (!innerNodes.length) {
          const split = Math.max(1, Math.ceil(sortedFocal.length * 0.3));
          innerNodes = sortedFocal.slice(0, split);
          outerNodes = sortedFocal.slice(split);
        }
      } else {
        const hubCount = Math.min(12, Math.max(1, Math.ceil(sortedFocal.length * 0.2)));
        innerNodes = sortedFocal.slice(0, hubCount);
        outerNodes = sortedFocal.slice(hubCount);
      }

      const innerMaxR = Math.max(22, ...innerNodes.map(n => n.radius));
      const innerRadius = innerNodes.length <= 1
        ? 0
        : Math.max(140, Math.ceil((innerNodes.length * (innerMaxR * 2 + 56)) / (2 * Math.PI)));
      const innerAngles = new Map<string, number>();
      innerNodes.forEach((node, idx) => {
        const angle = (idx / Math.max(1, innerNodes.length)) * Math.PI * 2 - Math.PI / 2;
        innerAngles.set(node.id, angle);
        const x = Math.cos(angle) * innerRadius;
        const y = Math.sin(angle) * innerRadius;
        layerTargets.set(node.id, { x, y });
      });

      const outerMaxR = Math.max(22, ...outerNodes.map(n => n.radius));
      const innerSet = new Set(innerNodes.map(n => n.id));
      const outerWithAngle = outerNodes.map((node, idx) => {
        let sumSin = 0;
        let sumCos = 0;
        let hits = 0;
        for (const edge of edges) {
          const other = edge.source === node.id ? edge.target : edge.target === node.id ? edge.source : '';
          if (other && innerSet.has(other)) {
            const a = innerAngles.get(other)!;
            sumSin += Math.sin(a);
            sumCos += Math.cos(a);
            hits++;
          }
        }
        const categoryBias = schema.geometry === 'force-radial'
          ? (entityLayerIndex(node.item.entity_type, node.item.kind) / 4) * Math.PI * 2
          : (idx / Math.max(1, outerNodes.length)) * Math.PI * 2;
        const preferred = hits > 0 ? Math.atan2(sumSin, sumCos) : categoryBias;
        return { node, preferred };
      });
      outerWithAngle.sort((a, b) => a.preferred - b.preferred || a.node.id.localeCompare(b.node.id));

      const outerBaseRadius = innerRadius + Math.max(260, innerRadius * 0.72);
      const minArc = outerMaxR * 2 + 48;
      const maxPerSingleRing = Math.max(8, Math.floor((2 * Math.PI * outerBaseRadius) / minArc));
      const splitTwoRings = outerWithAngle.length > Math.max(24, maxPerSingleRing);
      const ring1 = splitTwoRings ? outerWithAngle.filter((_, i) => i % 2 === 0) : outerWithAngle;
      const ring2 = splitTwoRings ? outerWithAngle.filter((_, i) => i % 2 === 1) : [];
      const r1 = Math.max(outerBaseRadius, Math.ceil((ring1.length * minArc) / (2 * Math.PI)));
      const r2 = ring2.length ? Math.max(r1 + outerMaxR * 2 + 90, Math.ceil((ring2.length * minArc) / (2 * Math.PI))) : r1;

      const placeRing = (items: typeof outerWithAngle, ringRadius: number, phaseOffset: number) => {
        items.forEach(({ node }, idx) => {
          const angle = (idx / Math.max(1, items.length)) * Math.PI * 2 - Math.PI / 2 + phaseOffset;
          const x = Math.cos(angle) * ringRadius;
          const y = Math.sin(angle) * ringRadius;
          layerTargets.set(node.id, { x, y });
          });
      };
      placeRing(ring1, r1, 0);
      if (ring2.length) placeRing(ring2, r2, Math.PI / Math.max(1, ring2.length));

      const maxOuter = outerNodes.length ? r2 : innerRadius;
      contextRingRadius = maxOuter + 240;
      for (const node of innerNodes) ringBounds.set(node.id, Math.max(110, innerRadius + 68));
      for (const node of outerNodes) ringBounds.set(node.id, maxOuter + 68);
      nextRadial = {
        innerRadius: Math.max(110, innerRadius + 68),
        outerRadius: maxOuter + 68,
        innerTint: schema.geometry === 'radial' ? schema.layers[0].bandTint : 'rgba(139, 104, 178, 0.035)',
        outerTint: schema.geometry === 'radial' ? schema.layers[1].bandTint : 'rgba(90, 120, 170, 0.02)',
        innerLabel: schema.geometry === 'radial' ? schema.layers[0].label : '核心高关联枢纽',
        outerLabel: schema.geometry === 'radial' ? schema.layers[1].label : '外围本体分簇',
        innerCount: innerNodes.length,
        outerCount: outerNodes.length,
      };
    }

  }
  if (contextNodes.length) {
    contextNodes.forEach((node, idx) => {
      const angle = (idx / contextNodes.length) * Math.PI * 2 - Math.PI / 2;
      const x = Math.cos(angle) * contextRingRadius;
      const y = Math.sin(angle) * contextRingRadius;
      layerTargets.set(node.id, { x, y });
    });
  }
  return { targets: layerTargets, bounds: layerBounds, ringBounds, bands: nextBands, radial: nextRadial,
    contextRingRadius, contextSet, focalNodes };
}

/** Include node radii and highlights, not just centers, when containing a layer. */
export function containLayerNode<T extends { x: number; y: number; radius: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null }>(node: T, bounds: LayerBounds) {
  const margin = node.radius + 10;
  const x = Math.max(bounds.left + margin, Math.min(bounds.right - margin, node.x));
  const y = Math.max(bounds.top + margin, Math.min(bounds.bottom - margin, node.y));
  if (x !== node.x) { node.x = x; node.vx = 0; if (node.fx != null) node.fx = x; }
  if (y !== node.y) { node.y = y; node.vy = 0; if (node.fy != null) node.fy = y; }
}

export function containRadialNode<T extends { x: number; y: number; radius: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null }>(node: T, radius: number) {
  const distance = Math.hypot(node.x, node.y);
  const limit = Math.max(0, radius - node.radius - 10);
  if (distance <= limit || distance === 0) return;
  node.x *= limit / distance; node.y *= limit / distance;
  node.vx = 0; node.vy = 0;
  if (node.fx != null) node.fx = node.x;
  if (node.fy != null) node.fy = node.y;
}

/** Fit the semantic foreground into the original panorama; background keeps its anchors. */
export function fitLayoutToPanorama(
  plan: ReturnType<typeof planKnowledgeLayout>, frame: LayerBounds,
  panorama: Map<string, { x: number; y: number }>,
) {
  const nodeBands = new Map(plan.focalNodes.map(node => {
    const bounds = plan.bounds.get(node.id);
    return [node.id, bounds ? plan.bands.find(b => Math.abs(b.x - (bounds.left + bounds.right) / 2) < 0.01) : undefined];
  }));
  const centerX = (frame.left + frame.right) / 2, centerY = (frame.top + frame.bottom) / 2;
  const width = frame.right - frame.left, height = frame.bottom - frame.top;
  const points = plan.focalNodes.flatMap(node => {
    const target = plan.targets.get(node.id);
    return target ? [{ ...target, radius: node.radius + 30 }] : [];
  });
  const left = Math.min(...points.map(n => n.x - n.radius), ...plan.bands.map(b => b.x - b.width / 2));
  const right = Math.max(...points.map(n => n.x + n.radius), ...plan.bands.map(b => b.x + b.width / 2));
  const top = Math.min(...points.map(n => n.y - n.radius), ...plan.bands.map(b => b.top));
  const bottom = Math.max(...points.map(n => n.y + n.radius), ...plan.bands.map(b => b.bottom));
  let sx = width * 0.82 / Math.max(1, right - left), sy = height * 0.78 / Math.max(1, bottom - top);
  if (plan.radial) {
    // Circles stay circular and centered on the graph's origin.
    sx = sy = Math.min(width * 0.82, height * 0.78) / Math.max(1, plan.radial.outerRadius * 2);
  }
  const cx = plan.radial ? 0 : (left + right) / 2, cy = plan.radial ? 0 : (top + bottom) / 2;
  const point = (p: { x: number; y: number }) => ({ x: (p.x - cx) * sx + centerX, y: (p.y - cy) * sy + centerY });
  // Radial guides are centered at the origin, so keep their destination frame centered there.
  if (plan.radial) {
    for (const node of plan.focalNodes) {
      const target = plan.targets.get(node.id)!;
      plan.targets.set(node.id, { x: target.x * sx, y: target.y * sy });
    }
    for (const [id, radius] of plan.ringBounds) plan.ringBounds.set(id, radius * sx);
    plan.radial.innerRadius *= sx; plan.radial.outerRadius *= sx;
  } else if (points.length) {
    for (const node of plan.focalNodes) plan.targets.set(node.id, point(plan.targets.get(node.id)!));
    for (const band of plan.bands) {
      const a = point({ x: band.x - band.width / 2, y: band.top });
      const b = point({ x: band.x + band.width / 2, y: band.bottom });
      const maxRadius = Math.max(0, ...plan.focalNodes.filter(n => nodeBands.get(n.id) === band).map(n => n.radius));
      band.x = (a.x + b.x) / 2; band.width = Math.max(b.x - a.x, maxRadius * 2 + 24);
      band.top = a.y; band.bottom = Math.max(b.y, a.y + maxRadius * 2 + 24);
    }
    for (const [id, bounds] of plan.bounds) {
      const band = nodeBands.get(id);
      if (band) {
        plan.bounds.set(id, { left: band.x - band.width / 2, right: band.x + band.width / 2, top: band.top, bottom: band.bottom });
        continue;
      }
      const a = point({ x: bounds.left, y: bounds.top }), b = point({ x: bounds.right, y: bounds.bottom });
      plan.bounds.set(id, { left: a.x, right: b.x, top: a.y, bottom: b.y });
    }
  }
  for (const id of plan.contextSet) {
    const anchor = panorama.get(id);
    if (anchor) plan.targets.set(id, { ...anchor });
  }
  return plan;
}
