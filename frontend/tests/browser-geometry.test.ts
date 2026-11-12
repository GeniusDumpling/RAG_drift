import { describe, expect, it } from 'vitest';
import { browserCaption, browserNodeRadius, browserRelationship } from '../src/components/video-graph/browser-geometry';

describe('Neo4j-style geometry', () => {
  it('clips arrows at the circumference and keeps captions upright in either direction', () => {
    const a = { x: 0, y: 0, radius: 25 }, b = { x: 200, y: 0, radius: 35 };
    expect(browserRelationship(a, b, 0).path).toBe('M25,0 Q100,0 165,0');
    expect(browserRelationship(b, a, 0).angle).toBe(0);
    const up = browserRelationship(a, b, 24), down = browserRelationship(a, b, -24);
    expect(up.y).toBeGreaterThan(0); expect(down.y).toBeLessThan(0);
    expect(up.path).not.toBe(down.path);
  });
  it('keeps self-loops visible and distinct instead of routing through the center', () => {
    const a = { x: 40, y: 60, radius: 30 };
    const first = browserRelationship(a, a, 0), second = browserRelationship(a, a, 0, 1);
    expect(first.path).toContain(' C'); expect(first.path).not.toBe(second.path);
    expect(first.y).toBeLessThan(a.y - a.radius); expect(second.y).toBeLessThan(first.y);
    expect(first.path).not.toContain('NaN');
  });
  it('fits multilingual captions and retains monotonic relationship-based node sizes', () => {
    expect(browserCaption('中文无人机飞行控制系统名称很长', 22).length).toBeLessThanOrEqual(3);
    expect(browserCaption('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 22).join('')).toContain('…');
    expect(browserCaption('DJI', 22)).toEqual(['DJI']);
    expect(browserCaption('很长的名称需要在小节点中截断', 13)).toHaveLength(1);
    expect(browserNodeRadius(100)).toBeGreaterThan(browserNodeRadius(20));
    expect(browserNodeRadius(20, 2)).toBe(browserNodeRadius(20) * 2);
  });
});
