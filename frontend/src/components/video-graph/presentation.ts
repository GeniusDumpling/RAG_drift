export const ENTITY_LABELS: Record<string, string> = {
  organization: '企业／品牌', product: '产品', component: '零部件', software: '软件',
  protocol: '协议', technology: '技术', method: '方法', fault: '故障', country: '国家', region: '地区',
  person: '人物', event: '事件',
  data: '数据', document: '资料',
  product_family: '产品系列',
};
export const PREDICATE_LABELS: Record<string, string> = {
  uses: '使用', contains: '包含', compatible_with: '兼容', applied_to: '应用于',
  reports_fault: '报告故障', mitigates: '缓解', mentions: '提及', used_in: '用于地区', located_in: '位于',
  manufactured_by: '制造商', developed_by: '开发者', installed_on: '安装于',
  distributed_via: '分发渠道', organizes: '举办', affiliated_with: '任职／隶属',
  supports: '支持功能／协议', connected_to: '连接', controls: '控制',
  packaged_with: '包装随附', offered_by: '产品归属',
  has_model: '系列型号', equipped_with: '编队装备',
};
export const ASSERTION_LABELS: Record<string, string> = { asserted: '陈述', uncertain: '推测', negated: '否定' };

export const ENTITY_COLORS: Record<string, string> = {
  organization: '#8665cf', product: '#3d76d6', component: '#dc9950', software: '#3a9b88',
  protocol: '#6596b8', technology: '#55a7bf', method: '#ad76b7', fault: '#db7373',
  country: '#88a249', region: '#b5a25b', video: '#4a9c84',
  person: '#b57b53', event: '#ab8b44',
  data: '#7886af', document: '#7799a1',
  product_family: '#9477ba',
};

export const DARK_ENTITY_COLORS: Record<string, string> = {
  organization: '#b39cdb', product: '#8ca7d7', component: '#c5aa85', software: '#7db3bd',
  protocol: '#8c9dc7', technology: '#87b5a5', method: '#bd95b9', fault: '#cf929b',
  country: '#a3b481', region: '#bfa28f', video: '#8fbca4',
  person: '#d2ad8d', event: '#cbb77e',
  data: '#a4afd0', document: '#a3c4cb',
  product_family: '#bba4d5',
};
export type GraphAppearance = {
  showLabels: boolean; showArrows: boolean; nodeScale: number; linkScale: number;
  repulsion: number; linkDistance: number;
};

/** Logarithmic growth keeps hubs readable without flattening their size at a fixed count. */
export function relationNodeSize(relationCount: number, is3D: boolean, nodeScale: number): number {
  return (is3D ? 4 : 1) * (7 + Math.log2(1 + Math.max(0, relationCount)) * 2.6) * nodeScale;
}

export const DEFAULT_GRAPH_APPEARANCE: GraphAppearance = {
  showLabels: true, showArrows: false, nodeScale: 1, linkScale: 1,
  repulsion: 190, linkDistance: 110,
};
