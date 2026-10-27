import { describe, expect, it } from 'vitest';
import type { Graph } from '@antv/g6';
import { project3D, screenDelta3D } from '../src/components/video-graph/three-dimensional-input';

const graph = {
  getSize: () => [1000, 600],
  getCanvas: () => ({ getCamera: () => ({
    // A perspective camera looking along negative Z, with a 90 degree vertical field of view.
    getPerspective: () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, -1, 0, 0, -2, 0],
    getViewTransform: () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
  }) }),
} as unknown as Graph;

describe('3D pointer and label projection', () => {
  it('projects the viewing axis to the canvas center', () => {
    expect(project3D(graph, [0, 0, -10])).toEqual({ x: 500, y: 300, depth: 10 });
  });

  it('uses depth when locating nodes, rather than projecting every node onto Z=0', () => {
    expect(project3D(graph, [5, 0, -10]).x).toBe(750);
    expect(project3D(graph, new Float32Array([5, 0, -20])).x).toBe(625);
  });

  it('maps upward camera coordinates to screen coordinates and identifies points behind the camera', () => {
    expect(project3D(graph, [0, 5, -10]).y).toBe(150);
    expect(project3D(graph, [0, 0, 10]).depth).toBeLessThan(0);
  });
});

describe('3D screen drag direction', () => {
  it.each([false, true])('tracks the pointer for node dragging and panning with rotated camera=%s', (rotated) => {
    const yaw = rotated ? Math.PI / 3 : 0, roll = rotated ? Math.PI / 6 : 0;
    const right = [Math.cos(yaw) * Math.cos(roll), Math.sin(roll), -Math.sin(yaw) * Math.cos(roll)];
    const up = [-Math.cos(yaw) * Math.sin(roll), Math.cos(roll), Math.sin(yaw) * Math.sin(roll)];
    const normal = [Math.sin(yaw), 0, Math.cos(yaw)];
    const anchor = normal.map((value) => -value * 10);
    for (const ySign of [-1, 1]) for (const zoom of [1, 2]) {
      let eye = [0, 0, 0];
      const fixture = {
        getSize: () => [1000, 600],
        getCanvas: () => ({ getCamera: () => ({
          getPerspective: () => [zoom, 0, 0, 0, 0, ySign * zoom, 0, 0, 0, 0, -1, -1, 0, 0, -2, 0],
          getPosition: () => eye,
          getWorldTransform: () => [...right, 0, ...up, 0, ...normal, 0, ...eye, 1],
          getViewTransform: () => [right[0], up[0], normal[0], 0, right[1], up[1], normal[1], 0,
            right[2], up[2], normal[2], 0,
            -eye.reduce((sum, value, axis) => sum + value * right[axis], 0),
            -eye.reduce((sum, value, axis) => sum + value * up[axis], 0),
            -eye.reduce((sum, value, axis) => sum + value * normal[axis], 0), 1],
        }) }),
      } as unknown as Graph;
      for (const [dx, dy] of [[50, 30], [-50, 30], [50, -30], [-50, -30]]) {
        eye = [0, 0, 0];
        const before = project3D(fixture, anchor);
        const delta = screenDelta3D(fixture, anchor, dx, dy);
        const dragged = project3D(fixture, anchor.map((value, axis) => value + delta[axis]));
        expect(dragged.x - before.x).toBeCloseTo(dx, 8);
        expect(dragged.y - before.y).toBeCloseTo(dy, 8);
        eye = delta.map((value) => -value);
        const panned = project3D(fixture, anchor);
        expect(panned.x - before.x).toBeCloseTo(dx, 8);
        expect(panned.y - before.y).toBeCloseTo(dy, 8);
      }
    }
  });
});
