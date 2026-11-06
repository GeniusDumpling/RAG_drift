import { describe, expect, it } from 'vitest';
import type { VideoGraphEdge } from '../src/api/types';
import { nodeRelationCounts } from '../src/components/video-graph/data';
import { relationNodeSize } from '../src/components/video-graph/presentation';

const edge = (id: string, source: string, target: string, kind: VideoGraphEdge['kind'] = 'relation'): VideoGraphEdge => ({
  id, source, target, kind, predicate: 'uses', evidence_count: 100, assertions: {}, qualifiers: {},
});

describe('relation-based node sizes', () => {
  it('counts incoming, outgoing, parallel and self-loop relations independently of evidence or mentions', () => {
    const counts = nodeRelationCounts([
      edge('r1', 'a', 'b'), edge('r2', 'a', 'b'), edge('r3', 'b', 'a'),
      edge('r4', 'a', 'a'), edge('m1', 'video', 'a', 'mention'),
    ]);
    expect(counts.get('a')).toBe(4);
    expect(counts.get('b')).toBe(3);
    expect(counts.has('video')).toBe(false);
  });

  it.each([false, true])('keeps higher relation counts larger, including beyond the former cap (3D: %s)', (is3D) => {
    const counts = [0, 1, 5, 18, 20, 50, 100, 500, 1000];
    const sizes = counts.map((count) => relationNodeSize(count, is3D, 1));
    expect(sizes[0]).toBeGreaterThan(0);
    for (let i = 1; i < sizes.length; i++) expect(sizes[i]).toBeGreaterThan(sizes[i - 1]);
    expect(relationNodeSize(50, is3D, 1.7)).toBeCloseTo(relationNodeSize(50, is3D, 1) * 1.7);
  });
});
