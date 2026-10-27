import { ExtensionCategory, register } from '@antv/g6';
import type { D3ForceLayout, Graph, GraphOptions } from '@antv/g6';
import { D3Force3DLayout, Light, Line3D, Sphere, renderer } from '@antv/g6-extension-3d';
import type { SphereStyleProps } from '@antv/g6-extension-3d';
import { zoom3D } from './three-dimensional-input';

const FOV = 45;
const RADIANS = Math.PI / 180;

/** Share one unit sphere; resizing a node only updates its instance transform. */
class GraphSphere extends Sphere {
  protected getGeometry(attributes: Required<SphereStyleProps>) {
    return super.getGeometry({ ...attributes, radius: 1 });
  }
  protected getKeyStyle(attributes: Required<SphereStyleProps>) {
    const style = super.getKeyStyle(attributes);
    const radius = this.getSize()[0] / 2;
    return { ...style, transform: `scale3d(${radius}, ${radius}, ${radius})` };
  }
}

register(ExtensionCategory.NODE, 'graph-sphere', GraphSphere);
register(ExtensionCategory.EDGE, 'graph-line-3d', Line3D);
// G6's runtime supports the extension's Layout 1.x through its legacy layout adapter.
register(ExtensionCategory.LAYOUT, 'd3-force-3d', D3Force3DLayout as unknown as typeof D3ForceLayout);
register(ExtensionCategory.PLUGIN, 'graph-light-3d', Light);

export function options3D(): Partial<GraphOptions> {
  return { renderer, canvas: { enableMultiLayer: false }, background: '#ffffff' };
}

/** Fit a bounding sphere so rotation cannot push the overview outside the viewport. */
export async function fitViewport3D(graph: Graph, preserveViewport = false): Promise<void> {
  const positions = graph.getNodeData().map((node) => graph.getElementPosition(node.id));
  if (!positions.length) return;
  const minimum = [0, 1, 2].map((axis) => Math.min(...positions.map((p) => p[axis] ?? 0)));
  const maximum = [0, 1, 2].map((axis) => Math.max(...positions.map((p) => p[axis] ?? 0)));
  const center = minimum.map((value, axis) => (value + maximum[axis]) / 2);
  const radius = Math.max(50, ...positions.map((p) => Math.hypot(
    p[0] - center[0], p[1] - center[1], (p[2] ?? 0) - center[2],
  ))) + 24;
  const [width, height] = graph.getSize();
  const aspect = width / Math.max(1, height);
  const halfAngle = Math.atan(Math.tan(FOV * RADIANS / 2) * Math.min(1, aspect));
  const distance = radius / Math.sin(halfAngle) * 1.08;
  const camera = graph.getCanvas().getCamera();
  const orientation = new Float32Array(camera.getWorldTransform());
  const previousZoom = graph.getZoom();
  const eye = camera.getPosition();
  const target = preserveViewport ? [...camera.getFocalPoint()] : center;
  const direction = preserveViewport ? Array.from(eye, (value, axis) => value - target[axis]) : [0, 0, 1];
  const length = Math.hypot(...direction) || 1;
  // EXPLORING uses quaternion rotations and can cross both poles; ORBITING stops at 90°.
  camera.setType(1);
  camera.setWorldRotation(false);
  camera.setPerspective(0.1, Math.max(10000, distance * 10), FOV, aspect);
  camera.setProjectionMode(1);
  camera.setPosition(target[0] + direction[0] / length * distance,
    target[1] + direction[1] / length * distance, target[2] + direction[2] / length * distance);
  camera.setFocalPoint(target[0], target[1], target[2]);
  if (preserveViewport) {
    const position = camera.getPosition();
    orientation[12] = position[0]; orientation[13] = position[1]; orientation[14] = position[2];
    camera.setMatrix(orientation);
  }
  if (!preserveViewport) { camera.setRoll(0); camera.rotate(-18, 12, 0); }
  graph.setZoomRange([1, 8]);
  await zoom3D(graph, preserveViewport ? Math.max(1, Math.min(8, previousZoom)) : 1);
}

export { bindInput3D, focusNode3D, zoom3D } from './three-dimensional-input';
