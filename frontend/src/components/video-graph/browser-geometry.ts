export type Point = { x: number; y: number; radius: number };

/** Clip relationships at the circumference, keeping parallel/reversed relationships separate. */
export function browserRelationship(source: Point, target: Point, offset: number, loopIndex = 0) {
  if (source === target) {
    const r = source.radius, reach = r + 45 + loopIndex * 24;
    const start = { x: source.x - r * 0.7, y: source.y - r * 0.714 };
    const end = { x: source.x + r * 0.7, y: start.y };
    return { path: `M${start.x},${start.y} C${source.x - reach * 1.7},${source.y - reach * 2} ${source.x + reach * 1.7},${source.y - reach * 2} ${end.x},${end.y}`,
      x: source.x, y: source.y - r * 0.1785 - reach * 1.5, angle: 0 };
  }
  const dx = target.x - source.x, dy = target.y - source.y;
  const length = Math.hypot(dx, dy) || 1;
  const control = { x: (source.x + target.x) / 2 - dy / length * offset * 2,
    y: (source.y + target.y) / 2 + dx / length * offset * 2 };
  function endpoint(node: Point) {
    const vx = control.x - node.x, vy = control.y - node.y, distance = Math.hypot(vx, vy) || 1;
    return { x: node.x + vx / distance * node.radius, y: node.y + vy / distance * node.radius };
  }
  const a = endpoint(source), b = endpoint(target);
  let angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
  if (angle > 90) angle -= 180;
  if (angle < -90) angle += 180;
  return { path: `M${a.x},${a.y} Q${control.x},${control.y} ${b.x},${b.y}`,
    x: (a.x + b.x) / 4 + control.x / 2, y: (a.y + b.y) / 4 + control.y / 2, angle };
}

export function browserNodeRadius(count: number, scale = 1) {
  return (22 + 3 * Math.log2(1 + Math.max(0, count))) * scale;
}

/** Wrap CJK and Latin captions inside the circle, rather than outside the node. */
export function browserCaption(text: string, radius: number): string[] {
  const budget = Math.max(2, Math.floor(radius * 1.55 / 6));
  const maxLines = Math.max(1, Math.min(3, Math.floor((radius * 2 - 4) / 13)));
  const lines: string[] = [];
  let line = '', width = 0;
  for (const char of text) {
    const advance = /[\u0000-\u00ff]/.test(char) ? 1 : 1.8;
    if (width + advance > budget && line) {
      lines.push(line); line = ''; width = 0;
      if (lines.length === maxLines) {
        lines[maxLines - 1] = lines[maxLines - 1].slice(0, -1) + '…';
        return lines;
      }
    }
    line += char; width += advance;
  }
  if (line) lines.push(line);
  return lines;
}
