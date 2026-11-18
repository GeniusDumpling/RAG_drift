import type { VideoGraphEdge } from '../../api/types';

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

export type GraphLayoutMode = 'force' | 'layered';

export type GraphAppearance = {
  showLabels: boolean;
  showArrows: boolean;
  showRelationLabels?: boolean;
  layoutMode?: GraphLayoutMode;
  nodeScale: number;
  linkScale: number;
  repulsion: number;
  linkDistance: number;
};

/** Logarithmic growth keeps hubs readable without flattening their size at a fixed count. */
export function relationNodeSize(relationCount: number, is3D: boolean, nodeScale: number): number {
  return (is3D ? 4 : 1) * (7 + Math.log2(1 + Math.max(0, relationCount)) * 2.6) * nodeScale;
}

export const DEFAULT_GRAPH_APPEARANCE: GraphAppearance = {
  showLabels: true,
  showArrows: true,
  showRelationLabels: false,
  layoutMode: 'force',
  nodeScale: 1,
  linkScale: 1,
  repulsion: 190,
  linkDistance: 110,
};

export type KnowledgeLensId = 'all' | 'lineage' | 'diagnosis' | 'ecosystem';

export type KnowledgeLens = {
  id: KnowledgeLensId;
  label: string;
  description: string;
  predicates: string[] | null;
};

export const KNOWLEDGE_LENSES: KnowledgeLens[] = [
  {
    id: 'all',
    label: '全景网络',
    description: '完整呈现多维实体与跨层关系',
    predicates: null,
  },
  {
    id: 'lineage',
    label: '产品谱系',
    description: '品牌 → 系列 → 产品 → 零部件构成',
    predicates: ['offered_by', 'has_model', 'contains', 'packaged_with', 'manufactured_by', 'equipped_with'],
  },
  {
    id: 'diagnosis',
    label: '故障排查',
    description: '产品／部件 → 故障现象 → 缓解方法',
    predicates: ['reports_fault', 'mitigates', 'applied_to', 'installed_on'],
  },
  {
    id: 'ecosystem',
    label: '技术生态',
    description: '产品 → 使用／支持／兼容 → 技术协议与软件',
    predicates: ['uses', 'supports', 'compatible_with', 'connected_to', 'controls', 'developed_by', 'applied_to'],
  },
];

export type OntologyLayer = {
  id: string;
  index: number;
  label: string;
  summary: string;
  typicalChain: string;
  types: string[];
};

export const ONTOLOGY_LAYERS: OntologyLayer[] = [
  {
    id: 'sources',
    index: 0,
    label: '来源与品牌',
    summary: '品牌、系列与信息信源',
    typicalChain: '品牌 —[归属/型号]→ 产品 · 资料 —[提及]→ 实体',
    types: ['organization', 'product_family', 'document', 'data', 'video'],
  },
  {
    id: 'products',
    index: 1,
    label: '产品主体',
    summary: '核心机型、人物与活动',
    typicalChain: '产品 —[包含/使用]→ 部件与技术',
    types: ['product', 'person', 'event'],
  },
  {
    id: 'capabilities',
    index: 2,
    label: '部件与技术',
    summary: '零部件、软件、协议与技术',
    typicalChain: '部件/软件 —[支持/兼容/连接]→ 能力网络',
    types: ['component', 'software', 'protocol', 'technology'],
  },
  {
    id: 'diagnostics',
    index: 3,
    label: '故障与对策',
    summary: '问题现象、方法与适用区域',
    typicalChain: '产品/部件 —[报告故障]→ 故障 ←[缓解]— 方法',
    types: ['fault', 'method', 'country', 'region'],
  },
];

const LAYER_BY_TYPE = new Map<string, number>(
  ONTOLOGY_LAYERS.flatMap((layer) => layer.types.map((type) => [type, layer.index] as const)),
);

export function entityLayerIndex(entityType: string | null | undefined, kind: 'entity' | 'video'): number {
  if (kind === 'video') return 0;
  return LAYER_BY_TYPE.get(entityType ?? '') ?? 1;
}

export type EdgeAssertionStatus = 'asserted' | 'uncertain' | 'negated' | 'disputed';

export type EdgeSemantics = {
  status: EdgeAssertionStatus;
  statusLabel: string;
  assertedCount: number;
  uncertainCount: number;
  negatedCount: number;
  qualifierCount: number;
  strokeColor: string;
  strokeDasharray?: string;
};

export function classifyEdgeSemantics(edge: Pick<VideoGraphEdge, 'kind' | 'assertions' | 'qualifiers'>): EdgeSemantics {
  const assertions = edge.assertions ?? {};
  const assertedCount = assertions.asserted ?? 0;
  const uncertainCount = assertions.uncertain ?? 0;
  const negatedCount = assertions.negated ?? 0;
  const qualifierCount = Object.keys(edge.qualifiers ?? {}).length;

  let status: EdgeAssertionStatus = 'asserted';
  let statusLabel = '陈述';
  let strokeColor = '#848c98';
  let strokeDasharray: string | undefined;

  if (edge.kind === 'mention') {
    strokeColor = '#7d93a6';
    strokeDasharray = '4 4';
  } else if (assertedCount > 0 && negatedCount > 0) {
    status = 'disputed';
    statusLabel = '存在争议';
    strokeColor = '#c97a2b';
    strokeDasharray = '7 3 2 3';
  } else if (negatedCount > 0) {
    status = 'negated';
    statusLabel = '否定';
    strokeColor = '#c9515a';
    strokeDasharray = '2 3';
  } else if (uncertainCount > 0 && assertedCount === 0) {
    status = 'uncertain';
    statusLabel = '推测';
    strokeColor = '#6d7e96';
    strokeDasharray = '6 4';
  }

  return {
    status,
    statusLabel,
    assertedCount,
    uncertainCount,
    negatedCount,
    qualifierCount,
    strokeColor,
    strokeDasharray,
  };
}

export function formatEdgeCaption(baseLabel: string, semantics: EdgeSemantics): string {
  if (!baseLabel) return '';
  const tags: string[] = [];
  if (semantics.status === 'disputed') tags.push('争议');
  else if (semantics.status === 'negated') tags.push('否定');
  else if (semantics.status === 'uncertain') tags.push('推测');
  if (semantics.qualifierCount > 0) tags.push(`条件×${semantics.qualifierCount}`);
  return tags.length ? `${baseLabel} [${tags.join('·')}]` : baseLabel;
}
