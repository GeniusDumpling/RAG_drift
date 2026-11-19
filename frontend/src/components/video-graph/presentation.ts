import type { VideoGraphEdge } from '../../api/types';

export const ENTITY_LABELS: Record<string, string> = {
  organization: '企业／品牌', product: '产品', component: '零部件', software: '软件',
  protocol: '协议', technology: '技术', method: '方法', fault: '故障', country: '国家', region: '地区',
  person: '人物', event: '事件',
  data: '数据', document: '资料',
  product_family: '产品系列',
  state: '状态陈述', instruction: '操作与建议', attribute: '参数值', fact: '技术陈述',
};

export const PREDICATE_LABELS: Record<string, string> = {
  uses: '使用', contains: '包含', compatible_with: '兼容', applied_to: '应用于',
  reports_fault: '报告故障', mitigates: '缓解', mentions: '提及', used_in: '用于地区', located_in: '位于',
  manufactured_by: '制造商', developed_by: '开发者', installed_on: '安装于',
  distributed_via: '分发渠道', organizes: '举办', affiliated_with: '任职／隶属',
  supports: '支持功能／协议', connected_to: '连接', controls: '控制',
  packaged_with: '包装随附', offered_by: '产品归属',
  has_model: '系列型号', equipped_with: '编队装备',
  indicates: '报告状态', instruction: '操作', has_parameter: '参数值',
};

export const ASSERTION_LABELS: Record<string, string> = { asserted: '陈述', uncertain: '推测', negated: '否定' };

export const ENTITY_COLORS: Record<string, string> = {
  organization: '#8665cf', product: '#3d76d6', component: '#dc9950', software: '#3a9b88',
  protocol: '#6596b8', technology: '#55a7bf', method: '#ad76b7', fault: '#db7373',
  country: '#88a249', region: '#b5a25b', video: '#4a9c84',
  person: '#b57b53', event: '#ab8b44',
  data: '#7886af', document: '#7799a1',
  product_family: '#9477ba',
  state: '#cf7c78', instruction: '#41967d', attribute: '#6e88b5', fact: '#827aa8',
};

export const DARK_ENTITY_COLORS: Record<string, string> = {
  organization: '#b39cdb', product: '#8ca7d7', component: '#c5aa85', software: '#7db3bd',
  protocol: '#8c9dc7', technology: '#87b5a5', method: '#bd95b9', fault: '#cf929b',
  country: '#a3b481', region: '#bfa28f', video: '#8fbca4',
  person: '#d2ad8d', event: '#cbb77e',
  data: '#a4afd0', document: '#a3c4cb',
  product_family: '#bba4d5',
  state: '#d89b98', instruction: '#85bfa9', attribute: '#9cb1d6', fact: '#ada6cf',
};

export type GraphLayoutMode = 'force' | 'layered';
export type LensContextMode = 'focus' | 'dim-context';
export type LensGeometry = 'force-radial' | 'columns' | 'converge' | 'radial';

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
    predicates: ['reports_fault', 'mitigates', 'applied_to', 'installed_on', 'indicates', 'instruction'],
  },
  {
    id: 'ecosystem',
    label: '技术生态',
    description: '产品 → 使用／支持／兼容 → 技术协议与软件',
    predicates: ['uses', 'supports', 'compatible_with', 'connected_to', 'controls', 'developed_by', 'applied_to', 'has_parameter'],
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
    types: ['component', 'software', 'protocol', 'technology', 'attribute'],
  },
  {
    id: 'diagnostics',
    index: 3,
    label: '故障与对策',
    summary: '问题现象、方法与适用区域',
    typicalChain: '产品/部件 —[报告故障]→ 故障 ←[缓解]— 方法',
    types: ['fault', 'method', 'country', 'region', 'state', 'instruction', 'fact'],
  },
];

export type LensLayerDef = OntologyLayer & {
  bandTint: string;
  flowArrowAfter?: '→' | '←';
};

export type LensSchema = {
  id: KnowledgeLensId;
  subtitle: string;
  geometry: LensGeometry;
  rulerHint: string;
  focalTypes: string[];
  focalRingColor: string;
  layers: LensLayerDef[];
  edgeLegend?: Array<{ label: string; color: string }>;
};

export const LENS_SCHEMAS: Record<KnowledgeLensId, LensSchema> = {
  all: {
    id: 'all',
    subtitle: '本体分层',
    geometry: 'force-radial',
    rulerHint: '内圈：高关联核心枢纽 · 外圈：按本体类别聚簇',
    focalTypes: [],
    focalRingColor: '#8b68b2',
    layers: [
      { ...ONTOLOGY_LAYERS[0], bandTint: 'rgba(134, 101, 207, 0.03)' },
      { ...ONTOLOGY_LAYERS[1], bandTint: 'rgba(61, 118, 214, 0.03)' },
      { ...ONTOLOGY_LAYERS[2], bandTint: 'rgba(85, 167, 191, 0.03)' },
      { ...ONTOLOGY_LAYERS[3], bandTint: 'rgba(219, 115, 115, 0.03)' },
    ],
  },
  lineage: {
    id: 'lineage',
    subtitle: '4 级谱系流',
    geometry: 'columns',
    rulerHint: '企业／品牌 → 产品系列 → 产品型号 → 零部件与随附',
    focalTypes: ['organization', 'product_family'],
    focalRingColor: '#8665cf',
    layers: [
      {
        id: 'lineage-brand',
        index: 0,
        label: '企业／品牌',
        summary: '品牌方、制造商与信源',
        typicalChain: '企业/品牌 ←[产品归属/制造商]— 产品系列与型号',
        types: ['organization', 'document', 'data', 'video'],
        bandTint: 'rgba(134, 101, 207, 0.035)',
        flowArrowAfter: '→',
      },
      {
        id: 'lineage-family',
        index: 1,
        label: '产品系列',
        summary: '品牌旗下的产品线谱系',
        typicalChain: '产品系列 —[系列型号]→ 具体产品型号',
        types: ['product_family'],
        bandTint: 'rgba(148, 119, 186, 0.028)',
        flowArrowAfter: '→',
      },
      {
        id: 'lineage-product',
        index: 2,
        label: '产品型号',
        summary: '具体整机、套装与装备',
        typicalChain: '产品型号 —[包含/包装随附]→ 零部件与配件',
        types: ['product', 'person', 'event'],
        bandTint: 'rgba(61, 118, 214, 0.035)',
        flowArrowAfter: '→',
      },
      {
        id: 'lineage-component',
        index: 3,
        label: '零部件与随附',
        summary: '硬件组件、模块与附属配置',
        typicalChain: '零部件被整机包含或包装随附',
        types: ['component', 'attribute', 'software', 'protocol', 'technology', 'method', 'fault', 'country', 'region', 'state', 'instruction', 'fact'],
        bandTint: 'rgba(220, 153, 80, 0.035)',
      },
    ],
  },
  diagnosis: {
    id: 'diagnosis',
    subtitle: '因果收敛流',
    geometry: 'converge',
    rulerHint: '产品与部件 → 故障／状态现象 ← 缓解与操作建议',
    focalTypes: ['fault', 'state'],
    focalRingColor: '#db7373',
    edgeLegend: [
      { label: '触发故障／状态', color: '#d46a6a' },
      { label: '缓解／操作建议', color: '#3d9b84' },
    ],
    layers: [
      {
        id: 'diag-subject',
        index: 0,
        label: '产品与部件',
        summary: '发生异常的机型、部件或参数',
        typicalChain: '产品/部件 —[报告故障/报告状态]→ 故障现象',
        types: ['product', 'component', 'product_family', 'organization', 'protocol', 'technology', 'attribute', 'person', 'event', 'country', 'region'],
        bandTint: 'rgba(61, 118, 214, 0.03)',
        flowArrowAfter: '→',
      },
      {
        id: 'diag-fault',
        index: 1,
        label: '故障与状态现象',
        summary: '问题表征、状态陈述与技术事实',
        typicalChain: '故障居中收敛多源异常报告与解决对策',
        types: ['fault', 'state', 'fact'],
        bandTint: 'rgba(219, 115, 115, 0.045)',
        flowArrowAfter: '←',
      },
      {
        id: 'diag-remedy',
        index: 2,
        label: '缓解与操作建议',
        summary: '排查方法、固件软件与操作指引',
        typicalChain: '方法/操作建议/软件 —[缓解/操作]→ 故障现象',
        types: ['method', 'instruction', 'software', 'document', 'data', 'video'],
        bandTint: 'rgba(58, 155, 136, 0.035)',
      },
    ],
  },
  ecosystem: {
    id: 'ecosystem',
    subtitle: '双圈辐射生态',
    geometry: 'radial',
    rulerHint: '内圈：核心技术／协议／软件 · 外圈：支持与兼容产品',
    focalTypes: ['technology', 'protocol', 'software'],
    focalRingColor: '#4a9ab0',
    edgeLegend: [
      { label: '使用／支持／连接', color: '#4a8cad' },
      { label: '兼容适配', color: '#3f9b86' },
    ],
    layers: [
      {
        id: 'eco-core',
        index: 0,
        label: '技术／协议／软件',
        summary: '内圈能力核心与控制协议',
        typicalChain: '协议/软件/技术居于核心，辐射支持与连接关系',
        types: ['technology', 'protocol', 'software', 'method', 'fact'],
        bandTint: 'rgba(74, 154, 176, 0.04)',
      },
      {
        id: 'eco-outer',
        index: 1,
        label: '应用产品与部件',
        summary: '外圈使用、兼容与参数依赖方',
        typicalChain: '产品/部件 —[使用/兼容/连接/参数]→ 核心技术生态',
        types: ['product', 'component', 'product_family', 'organization', 'attribute', 'fault', 'state', 'instruction', 'person', 'event', 'country', 'region', 'document', 'data', 'video'],
        bandTint: 'rgba(61, 118, 214, 0.025)',
      },
    ],
  },
};

const LAYER_BY_TYPE = new Map<string, number>(
  ONTOLOGY_LAYERS.flatMap((layer) => layer.types.map((type) => [type, layer.index] as const)),
);

const LENS_LAYER_INDEX_MAPS = new Map<KnowledgeLensId, Map<string, number>>(
  (Object.keys(LENS_SCHEMAS) as KnowledgeLensId[]).map((lensId) => [
    lensId,
    new Map(LENS_SCHEMAS[lensId].layers.flatMap((layer) => layer.types.map((type) => [type, layer.index] as const))),
  ]),
);

export function entityLayerIndex(entityType: string | null | undefined, kind: 'entity' | 'video' | 'fact'): number {
  if (kind === 'video') return 0;
  if (kind === 'fact') return 3;
  return LAYER_BY_TYPE.get(entityType ?? '') ?? 1;
}

export function lensSchemaFor(lensId: KnowledgeLensId): LensSchema {
  return LENS_SCHEMAS[lensId] ?? LENS_SCHEMAS.all;
}

export function nodeLensLayerIndex(
  node: { entity_type?: string | null; kind: 'entity' | 'video' | 'fact' },
  lensId: KnowledgeLensId,
): number {
  const map = LENS_LAYER_INDEX_MAPS.get(lensId);
  if (!map) return entityLayerIndex(node.entity_type, node.kind);
  const byType = node.entity_type ? map.get(node.entity_type) : undefined;
  if (byType !== undefined) return byType;
  if (node.kind === 'video') return map.get('video') ?? 0;
  if (node.kind === 'fact') return map.get('fact') ?? (LENS_SCHEMAS[lensId].layers.length > 1 ? 1 : 0);
  return lensId === 'lineage' ? 2 : lensId === 'diagnosis' ? 0 : lensId === 'ecosystem' ? 1 : 1;
}

export function isLensFocalNode(
  node: { entity_type?: string | null; kind: 'entity' | 'video' | 'fact' },
  lensId: KnowledgeLensId,
): { focal: boolean; ringColor: string } {
  const schema = lensSchemaFor(lensId);
  const typeKey = node.entity_type ?? (node.kind === 'fact' ? 'fact' : node.kind === 'video' ? 'video' : '');
  const focal = schema.focalTypes.length > 0 && schema.focalTypes.includes(typeKey);
  return { focal, ringColor: schema.focalRingColor };
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

export function lensEdgeColor(
  edge: Pick<VideoGraphEdge, 'predicate' | 'kind' | 'assertions' | 'qualifiers'>,
  lensId: KnowledgeLensId,
): string {
  const semantics = classifyEdgeSemantics(edge);
  if (edge.kind === 'mention' || semantics.status !== 'asserted') {
    return semantics.strokeColor;
  }
  if (lensId === 'lineage') return '#6477b8';
  if (lensId === 'diagnosis') {
    return edge.predicate === 'reports_fault' || edge.predicate === 'indicates' ? '#d46a6a' : '#3d9b84';
  }
  if (lensId === 'ecosystem') {
    return edge.predicate === 'compatible_with' ? '#3f9b86' : '#4a8cad';
  }
  return semantics.strokeColor;
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
