import { ChangeEvent, useEffect, useMemo, useState } from 'react';

import { getRun, getRunEvents, listRuns } from '../api/client';
import type { CrawlRun, CrawlRunEvent, Page } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';
const RUN_UNAVAILABLE_COPY = 'Run 数据暂不可用：API 请求失败。';
const EVENTS_UNAVAILABLE_COPY = 'Run 事件暂不可用：API 请求失败。';
const LOADING_RUN_COPY = '正在加载 Run 数据...';
const LOADING_EVENTS_COPY = '正在加载 Run 事件...';

type CounterField =
  | 'discovered_count'
  | 'fetched_count'
  | 'parsed_count'
  | 'extracted_count'
  | 'deduped_count'
  | 'chunked_count'
  | 'embedded_count'
  | 'error_count';

const COUNTER_FIELDS: Array<[CounterField, string]> = [
  ['discovered_count', '已发现'],
  ['fetched_count', '已抓取'],
  ['parsed_count', '已解析'],
  ['extracted_count', '已抽取'],
  ['deduped_count', '去重'],
  ['chunked_count', 'Chunks'],
  ['embedded_count', '已向量化'],
  ['error_count', '错误'],
];

type RunDetailPageProps = {
  selectedRunId?: string;
};

export function RunDetailPage({ selectedRunId: externallySelectedRunId = '' }: RunDetailPageProps = {}) {
  const [runs, setRuns] = useState<Page<CrawlRun>>();
  const [activeRunId, setActiveRunId] = useState(externallySelectedRunId);
  const [run, setRun] = useState<CrawlRun>();
  const [events, setEvents] = useState<Page<CrawlRunEvent>>();
  const [runListError, setRunListError] = useState('');
  const [runDetailError, setRunDetailError] = useState('');
  const [eventError, setEventError] = useState('');
  const [runListLoading, setRunListLoading] = useState(true);
  const [runDetailLoading, setRunDetailLoading] = useState(Boolean(externallySelectedRunId));
  const [eventLoading, setEventLoading] = useState(Boolean(externallySelectedRunId));

  useEffect(() => {
    let ignore = false;
    setRunListError('');
    setRunListLoading(true);
    listRuns(25)
      .then((page) => {
        if (!ignore) {
          setRuns(page);
          setActiveRunId((current) => current || externallySelectedRunId || page.items[0]?.id || '');
        }
      })
      .catch((caught) => {
        if (!ignore) {
          setRuns(undefined);
          setRunListError(formatErrorMessage('无法加载 Runs', caught));
        }
      })
      .finally(() => {
        if (!ignore) {
          setRunListLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (externallySelectedRunId) {
      setActiveRunId(externallySelectedRunId);
    }
  }, [externallySelectedRunId]);

  useEffect(() => {
    setRunDetailError('');
    setEventError('');
    if (!activeRunId) {
      setRun(undefined);
      setEvents(undefined);
      setRunDetailLoading(false);
      setEventLoading(false);
      return;
    }

    let ignore = false;
    setRun(undefined);
    setEvents(undefined);
    setRunDetailLoading(true);
    setEventLoading(true);
    Promise.allSettled([getRun(activeRunId), getRunEvents(activeRunId)]).then(([runResult, eventsResult]) => {
      if (ignore) {
        return;
      }
      if (runResult.status === 'fulfilled') {
        setRun(runResult.value);
      } else {
        setRun(undefined);
        setRunDetailError(formatErrorMessage('无法加载 Run 详情', runResult.reason));
      }

      if (eventsResult.status === 'fulfilled') {
        setEvents(eventsResult.value);
      } else {
        setEvents(undefined);
        setEventError(formatErrorMessage('无法加载 Run 事件', eventsResult.reason));
      }
      setRunDetailLoading(false);
      setEventLoading(false);
    });
    return () => {
      ignore = true;
    };
  }, [activeRunId]);

  const displayedRun = run?.id === activeRunId ? run : undefined;
  const displayedEvents = events?.items.length && events.items.some((event) => event.crawl_run_id !== activeRunId)
    ? undefined
    : events;
  const agentTraceEvents = useMemo(
    () => (displayedEvents?.items ?? []).filter((event) => Object.keys(event.agent_trace_json).length > 0),
    [displayedEvents],
  );
  const runOptions = useMemo(() => {
    const loadedRuns = runs?.items ?? [];
    if (!loadedRuns.length) {
      return [];
    }

    const options = loadedRuns.map((item) => ({
      id: item.id,
      label: `${item.status} · ${item.id}`,
    }));
    if (activeRunId && !loadedRuns.some((item) => item.id === activeRunId)) {
      const selectedRun = displayedRun;
      return [
        {
          id: activeRunId,
          label: `${selectedRun?.status ?? 'selected'} · ${activeRunId}`,
        },
        ...options,
      ];
    }
    return options;
  }, [activeRunId, displayedRun, runs]);

  function handleRunChange(event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) {
    setActiveRunId(event.target.value);
  }

  const runLoadFailed = Boolean(runListError || runDetailError);
  const runSelectionPending = Boolean(activeRunId && !displayedRun && !runDetailError);
  const loading = runListLoading || runDetailLoading || eventLoading || runSelectionPending;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">执行追踪</p>
        <h1>运行详情</h1>
        <p className="muted">查看爬取计数、事件时间线、关联内容和智能体追踪。</p>
      </div>

      <div className="card controls-card">
        <label htmlFor="run-selector">Run</label>
        {runOptions.length ? (
          <select id="run-selector" value={activeRunId} onChange={handleRunChange}>
            {runOptions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            id="run-selector"
            value={activeRunId}
            onChange={handleRunChange}
            placeholder="粘贴 crawl run id"
          />
        )}
      </div>

      {runListError ? (
        <p className="card error-text" role="alert">
          {runListError}
        </p>
      ) : null}
      {runDetailError ? (
        <p className="card error-text" role="alert">
          {runDetailError}
        </p>
      ) : null}
      {eventError ? (
        <p className="card error-text" role="alert">
          {eventError}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {LOADING_RUN_COPY}
        </p>
      ) : null}

      {displayedRun ? (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{displayedRun.id}</h2>
              <p className="muted compact">
                Source {displayedRun.source_site_id} · Job {displayedRun.crawl_job_id}
              </p>
            </div>
            <span className="badge">{displayedRun.status}</span>
          </div>
          <dl className="kv-grid">
            <div>
              <dt>触发方式</dt>
              <dd>{displayedRun.trigger_type}</dd>
            </div>
            <div>
              <dt>执行模式</dt>
              <dd>{displayedRun.execution_mode}</dd>
            </div>
            <div>
              <dt>Seed URL</dt>
              <dd>{displayedRun.seed_url || 'n/a'}</dd>
            </div>
            <div>
              <dt>开始时间 Started</dt>
              <dd>{displayedRun.started_at ? new Date(displayedRun.started_at).toLocaleString() : 'n/a'}</dd>
            </div>
            <div>
              <dt>结束时间 Finished</dt>
              <dd>{displayedRun.finished_at ? new Date(displayedRun.finished_at).toLocaleString() : 'n/a'}</dd>
            </div>
            <div>
              <dt>错误</dt>
              <dd>{displayedRun.error_message || '无'}</dd>
            </div>
          </dl>
        </section>
      ) : loading ? null : runLoadFailed ? (
        <p className="card muted">{RUN_UNAVAILABLE_COPY}</p>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}

      <section className="card">
        <h2>阶段计数</h2>
        {displayedRun ? (
          <div className="grid counter-grid">
            {COUNTER_FIELDS.map(([field, label]) => (
              <div className="counter" key={String(field)}>
                <span className="muted">{label}</span>
                <strong>{displayedRun[field]}</strong>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">{loading ? LOADING_RUN_COPY : runLoadFailed ? RUN_UNAVAILABLE_COPY : EMPTY_STATE_COPY}</p>
        )}
      </section>

      <section className="card">
        <h2>事件时间线</h2>
        {displayedEvents?.items.length ? (
          <ol className="timeline">
            {displayedEvents.items.map((event) => (
              <li key={event.id}>
                <div className="timeline-marker" />
                <div>
                  <div className="card-header compact-header">
                    <strong>
                      {event.stage} / {event.event_type}
                    </strong>
                    <span className="badge">{event.level}</span>
                  </div>
                  <p>{event.message}</p>
                  <p className="muted compact">
                    {new Date(event.created_at).toLocaleString()} · {event.related_url || '无关联 URL'}
                  </p>
                  {Object.keys(event.counters_json).length ? (
                    <pre>{JSON.stringify(event.counters_json, null, 2)}</pre>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">
            {loading
              ? eventLoading
                ? LOADING_EVENTS_COPY
                : LOADING_RUN_COPY
              : eventError
                ? EVENTS_UNAVAILABLE_COPY
                : runLoadFailed
                  ? RUN_UNAVAILABLE_COPY
                  : EMPTY_STATE_COPY}
          </p>
        )}
      </section>

      <section className="card">
        <h2>智能体摘要</h2>
        {agentTraceEvents.length ? (
          agentTraceEvents.map((event) => (
            <pre key={event.id}>{JSON.stringify(event.agent_trace_json, null, 2)}</pre>
          ))
        ) : (
          <p className="muted">
            {loading
              ? eventLoading
                ? LOADING_EVENTS_COPY
                : LOADING_RUN_COPY
              : eventError
                ? EVENTS_UNAVAILABLE_COPY
                : runLoadFailed
                  ? RUN_UNAVAILABLE_COPY
                  : EMPTY_STATE_COPY}
          </p>
        )}
      </section>
    </section>
  );
}
