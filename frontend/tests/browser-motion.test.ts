import { describe, expect, it } from 'vitest';
import { BrowserMotion, readableLabel } from '../src/components/video-graph/browser-motion';

it('uses screen diameter and screen font size rather than zoom alone', () => {
  expect(readableLabel(10, 1, 11)).toBe(false);
  expect(readableLabel(50, 0.5, 11)).toBe(false);
  expect(readableLabel(22, 1, 11)).toBe(true);
  expect(readableLabel(40, 0.75, 10)).toBe(true);
});
describe('motion labels', () => {
  it('removes unreadable text, restores labels and preserves focused names without a React render', () => {
    const ns = 'http://www.w3.org/2000/svg';
    const world = document.createElementNS(ns, 'g'), group = document.createElementNS(ns, 'g'); world.append(group);
    const motion = new BrowserMotion(world);
    const node = { id: 'a', x: 1, y: 2, radius: 25 };
    motion.nodes = [{ node, element: group, label: 'Alpha', enabled: true, emphasized: false }];
    motion.paint({ x: 0, y: 0, k: 0.1 }, true);
    expect(group.querySelector('text')).toBeNull();
    motion.paint({ x: 0, y: 0, k: 1 }, false);
    expect(group.querySelector('text')?.textContent).toBe('Alpha');
    motion.nodes[0].enabled = false;
    motion.paint({ x: 0, y: 0, k: 1 }, false);
    expect(group.querySelector('text')).toBeNull();
    motion.nodes[0].enabled = true;
    motion.paint({ x: 0, y: 0, k: 0.1 }, false);
    expect(group.querySelector('text')).toBeNull();
    motion.nodes[0].emphasized = true;
    motion.paint({ x: 0, y: 0, k: 0.1 }, false);
    expect(group.querySelector('text')?.getAttribute('font-size')).toBe('100');
    expect(group.querySelector('text')?.textContent).toBe('Alpha');
    motion.edges = [{ source: node, target: { ...node, id: 'b', x: 200 }, element: document.createElementNS(ns, 'g'), label: 'uses', offset: 0, loop: 0, emphasized: false }];
    motion.paint({ x: 0, y: 0, k: 0.1 }, true);
    expect(motion.edges[0].element.querySelector('text')).toBeNull();
    motion.edges[0].emphasized = true;
    motion.paint({ x: 0, y: 0, k: 0.1 }, false);
    expect(motion.edges[0].element.querySelector('text')?.textContent).toBe('uses');
  });
});
