import { describe, expect, it, vi } from 'vitest';
import type { Graph } from '@antv/g6';
import { atMinimumZoom, fitDefaultViewport, INITIAL_ZOOM_RANGE } from '../src/components/video-graph/viewport';

function fixture(previousZoom: number, previousMinimum: number, fittedZoom: number) {
  let zoom = previousZoom;
  let range = [previousMinimum, 8];
  const mock = {
    destroyed: false,
    getZoom: () => zoom,
    getZoomRange: () => range,
    getSize: () => [1000, 600],
    getCanvasByViewport: vi.fn(() => [42, 84]),
    getViewportByCanvas: vi.fn(() => [520, 320]),
    setZoomRange: vi.fn((value: number[]) => { range = value; }),
    fitView: vi.fn(async () => { zoom = fittedZoom; }),
    zoomTo: vi.fn(async (value: number) => { zoom = value; }),
    translateBy: vi.fn(async () => {}),
  };
  return { graph: mock as unknown as Graph, mock };
}

describe('graph zoom boundary', () => {
  it('uses the fitted overview as the minimum and releases the old boundary before fitting', async () => {
    const { graph, mock } = fixture(0.5, 0.5, 0.1);
    await fitDefaultViewport(graph);
    expect(mock.setZoomRange.mock.calls).toEqual([[INITIAL_ZOOM_RANGE], [[0.1, 8]]]);
    expect(atMinimumZoom(graph)).toBe(true);
    expect(mock.zoomTo).not.toHaveBeenCalled();
  });

  it('keeps a zoomed detail and its center while recalibrating after canvas resize', async () => {
    const { graph, mock } = fixture(0.8, 0.1, 0.2);
    await fitDefaultViewport(graph, true);
    expect(mock.setZoomRange).toHaveBeenLastCalledWith([0.2, 8]);
    expect(mock.zoomTo).toHaveBeenCalledWith(0.8, false);
    expect(mock.getViewportByCanvas).toHaveBeenCalledWith([42, 84]);
    expect(mock.translateBy).toHaveBeenCalledWith([-20, -20], false);
    expect(atMinimumZoom(graph)).toBe(false);
  });

  it('keeps overview mode after resize and raises a detail below the new minimum', async () => {
    for (const zoom of [0.1, 0.15]) {
      const { graph, mock } = fixture(zoom, 0.1, 0.3);
      await fitDefaultViewport(graph, true);
      expect(graph.getZoom()).toBe(0.3);
      expect(mock.zoomTo).not.toHaveBeenCalled();
      expect(atMinimumZoom(graph)).toBe(true);
    }
  });
});
