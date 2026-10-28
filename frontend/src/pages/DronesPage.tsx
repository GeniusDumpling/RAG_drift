import { useEffect, useMemo, useState } from 'react';

import { getDrone, listDrones } from '../api/client';
import type { DroneBrand, DroneCatalog, DroneDetail } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';
import './DronesPage.css';

const BRANDS: { id: DroneBrand; name: string }[] = [
  { id: 'dji', name: 'DJI 大疆' },
  { id: 'parrot', name: 'Parrot' },
];

type CatalogState = { brand: DroneBrand; data?: DroneCatalog; error?: string };
type DetailState = { brand: DroneBrand; slug: string; data?: DroneDetail; error?: string };

export function DronesPage() {
  const [brand, setBrand] = useState<DroneBrand>('dji');
  const [catalogState, setCatalogState] = useState<CatalogState | null>(null);
  const catalog = catalogState?.brand === brand ? catalogState.data : null;
  const catalogError = catalogState?.brand === brand ? catalogState.error : '';
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [selectedSlug, setSelectedSlug] = useState('');
  const [detailState, setDetailState] = useState<DetailState | null>(null);
  const [detailReload, setDetailReload] = useState(0);
  const [parameterQuery, setParameterQuery] = useState('');
  const [groupIndex, setGroupIndex] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setCatalogState({ brand });
    listDrones(controller.signal, brand).then((result) => {
      if (!controller.signal.aborted) setCatalogState({ brand, data: result });
    }).catch((error) => {
      if (!controller.signal.aborted) {
        setCatalogState({ brand, error: formatErrorMessage('无法加载无人机信息', error) });
      }
    });
    return () => controller.abort();
  }, [reload, brand]);

  function selectBrand(next: DroneBrand) {
    if (next === brand) return;
    setBrand(next);
    setQuery('');
    setCategory('');
    setSelectedSlug('');
    setParameterQuery('');
    setGroupIndex('');
  }

  const categories = useMemo(
    () => [...new Set(catalog?.items.map((item) => item.category) ?? [])], [catalog],
  );
  const models = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase().replace(/[\s-]+/g, '');
    return catalog?.items.filter((item) => (
      (!category || item.category === category)
      && `${item.name}${item.slug}`.toLocaleLowerCase().replace(/[\s-]+/g, '').includes(keyword)
    )) ?? [];
  }, [catalog, query, category]);
  // Derive the effective selection immediately so filtering never shows a
  // previous model's parameters next to a different set of search results.
  const activeSlug = models.some((model) => model.slug === selectedSlug)
    ? selectedSlug : models[0]?.slug ?? '';

  useEffect(() => {
    const controller = new AbortController();
    setDetailState({ brand, slug: activeSlug });
    setParameterQuery('');
    setGroupIndex('');
    if (activeSlug) {
      getDrone(activeSlug, controller.signal, brand).then((result) => {
        if (!controller.signal.aborted) setDetailState({ brand, slug: activeSlug, data: result });
      }).catch((error) => {
        if (!controller.signal.aborted) {
          setDetailState({ brand, slug: activeSlug, error: formatErrorMessage('无法加载技术参数', error) });
        }
      });
    }
    return () => controller.abort();
  }, [activeSlug, detailReload, brand]);

  const currentDetail = detailState?.brand === brand && detailState.slug === activeSlug
    ? detailState : null;
  const activeDetail = currentDetail?.data?.slug === activeSlug ? currentDetail.data : null;
  const detailError = currentDetail?.error;
  const parameterKeyword = parameterQuery.trim().toLocaleLowerCase();
  const groups = (activeDetail?.groups ?? []).map((group, index) => ({
    ...group, index,
    parameters: group.parameters.filter((parameter) => (
      `${group.name}\n${parameter.name}\n${parameter.value}`.toLocaleLowerCase().includes(parameterKeyword)
    )),
  })).filter((group) => group.parameters.length > 0 && (groupIndex === '' || String(group.index) === groupIndex));
  const sourceHref = safeExternalHref(activeDetail?.source_url);

  return (
    <section className="drones-page" aria-label="无人机信息">
      <header className="page-title drone-page-title">
        <div>
          <p className="eyebrow">DRONE CATALOG</p>
          <h1>无人机信息</h1>
          <p className="muted">按品牌浏览已收集的无人机型号与技术参数。</p>
        </div>
        <div className="drone-brand-switch" role="group" aria-label="无人机品牌">
          {BRANDS.map((item) => <button key={item.id} type="button"
            aria-pressed={brand === item.id} onClick={() => selectBrand(item.id)}>
            {item.name}
          </button>)}
        </div>
      </header>

      {catalogError ? (
        <div className="card">
          <p className="error-text" role="alert">{catalogError}</p>
          <button type="button" onClick={() => setReload((value) => value + 1)}>重新加载</button>
        </div>
      ) : null}
      {!catalog && !catalogError ? <p className="status-line" role="status">正在加载无人机型号…</p> : null}

      {catalog ? <>
        {catalog.total === 0 ? <p className="empty-state">尚未收集无人机型号。</p> : (
          <div className="drone-layout">
            <aside className="drone-models" aria-label="无人机型号列表">
              <div className="drone-model-controls">
                <h2>无人机型号 <span className="drone-model-count">{models.length} / {catalog.total}</span></h2>
                <label className="sr-only" htmlFor="drone-model-search">搜索无人机型号</label>
                <input id="drone-model-search" type="search" placeholder={brand === 'parrot' ? '搜索型号，如 ANAFI、UKR' : '搜索型号，如 Mavic、T50'} value={query} onChange={(event) => setQuery(event.target.value)} />
                <label className="sr-only" htmlFor="drone-category">无人机类型</label>
                <select id="drone-category" value={category} onChange={(event) => setCategory(event.target.value)}>
                  <option value="">全部类型</option>
                  {categories.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </div>
              <div className="drone-model-list">
                {models.map((model) => (
                  <button className={`drone-model${model.slug === activeSlug ? ' selected' : ''}`} type="button" key={model.slug} aria-pressed={model.slug === activeSlug} onClick={() => setSelectedSlug(model.slug)}>
                    <strong>{model.name}</strong>
                    <span className="drone-model-meta">{model.category}</span>
                  </button>
                ))}
                {models.length === 0 ? <p className="drone-list-empty">未找到匹配型号，请调整搜索或类型。</p> : null}
              </div>
            </aside>

            <section className="drone-detail" aria-label="无人机技术参数">
              {!activeSlug ? <p className="empty-state">选择一个无人机型号查看技术参数。</p> : null}
              {activeSlug && detailError ? <div className="card">
                <p className="error-text" role="alert">{detailError}</p>
                <button type="button" onClick={() => setDetailReload((value) => value + 1)}>重试技术参数</button>
              </div> : null}
              {activeSlug && !activeDetail && !detailError ? <p className="status-line" role="status">正在加载技术参数…</p> : null}
              {activeDetail ? <>
                <header className="drone-detail-header">
                  <span className="badge">{activeDetail.category}</span>
                  <h2>{activeDetail.name}</h2>
                  {activeDetail.official_version || activeDetail.updated_at || activeDetail.collected_at ? (
                    <div className="drone-source-meta">
                      {activeDetail.official_version ? <span>资料版本 {activeDetail.official_version}</span> : null}
                      {activeDetail.updated_at ? <span>更新于 <time dateTime={activeDetail.updated_at}>{activeDetail.updated_at}</time></span> : null}
                      {activeDetail.collected_at ? <span title={activeDetail.collected_at}>采集于 <time dateTime={activeDetail.collected_at}>{activeDetail.collected_at.slice(0, 10)}</time></span> : null}
                    </div>
                  ) : null}
                  {sourceHref ? <a className="drone-source" href={sourceHref} target="_blank" rel="noreferrer">查看官方来源 ↗</a> : null}
                </header>
                {activeDetail.note ? <p className="drone-note" role="note">{activeDetail.note}</p> : null}
                <div className="drone-parameter-controls">
                  <div><label htmlFor="drone-parameter-search">搜索技术参数</label><input id="drone-parameter-search" type="search" placeholder="搜索参数名称或内容，如重量、电池" value={parameterQuery} onChange={(event) => setParameterQuery(event.target.value)} /></div>
                  <div><label htmlFor="drone-parameter-group">参数分组</label><select id="drone-parameter-group" value={groupIndex} onChange={(event) => setGroupIndex(event.target.value)}>
                    <option value="">全部分组</option>
                    {activeDetail.groups.map((group, index) => <option value={index} key={index}>{group.name}</option>)}
                  </select></div>
                </div>
                {groups.map((group) => <section className="drone-parameter-group" key={group.index} aria-label={group.name}>
                  <h3>{group.name}</h3>
                  <dl className="drone-parameters">
                    {group.parameters.map((parameter, index) => <div key={index}><dt>{parameter.name}</dt><dd>{parameter.value || '未提供'}</dd></div>)}
                  </dl>
                </section>)}
                {groups.length === 0 ? <p className="empty-state">{activeDetail.parameter_count === 0 ? '该型号暂未提供技术参数。' : '未找到匹配的技术参数，请调整搜索或分组。'}</p> : null}
              </> : null}
            </section>
          </div>
        )}
      </> : null}
    </section>
  );
}
