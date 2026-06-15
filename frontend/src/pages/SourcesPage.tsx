import { useEffect, useMemo, useRef, useState } from 'react';

import { listJobs, listSources, triggerJob } from '../api/client';
import type { CrawlJob, Page, SourceSite } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';
const LOADING_STATE_COPY = '正在加载数据源...';

export function SourcesPage() {
  const [sources, setSources] = useState<Page<SourceSite>>();
  const [jobs, setJobs] = useState<Page<CrawlJob>>();
  const [message, setMessage] = useState('');
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [pendingJobIds, setPendingJobIds] = useState<Set<string>>(() => new Set());
  const pendingJobIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    let ignore = false;
    setLoadError('');
    setLoading(true);
    Promise.allSettled([listSources(), listJobs()]).then(([sourceResult, jobResult]) => {
      if (ignore) {
        return;
      }
      const firstFailure = [sourceResult, jobResult].find(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      );
      setSources(sourceResult.status === 'fulfilled' ? sourceResult.value : undefined);
      setJobs(jobResult.status === 'fulfilled' ? jobResult.value : undefined);
      setLoadError(firstFailure ? formatErrorMessage('无法加载数据源数据', firstFailure.reason) : '');
      setLoading(false);
    });
    return () => {
      ignore = true;
    };
  }, []);

  const jobsBySource = useMemo(() => {
    const grouped = new Map<string, CrawlJob[]>();
    for (const job of jobs?.items ?? []) {
      const existing = grouped.get(job.source_site_id) ?? [];
      grouped.set(job.source_site_id, [...existing, job]);
    }
    return grouped;
  }, [jobs]);

  function setJobPending(jobId: string, pending: boolean) {
    if (pending) {
      pendingJobIdsRef.current.add(jobId);
    } else {
      pendingJobIdsRef.current.delete(jobId);
    }
    setPendingJobIds(new Set(pendingJobIdsRef.current));
  }

  async function handleTrigger(job: CrawlJob) {
    if (!job.enabled || pendingJobIdsRef.current.has(job.id)) {
      return;
    }

    setJobPending(job.id, true);
    setMessage(`正在触发 ${job.name}...`);
    try {
      const run = await triggerJob(job.id);
      setMessage(`已创建 Run ${run.id}（${job.name}）。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法触发 Job。');
    } finally {
      setJobPending(job.id, false);
    }
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">控制面 Control plane</p>
        <h1>数据源 Sources</h1>
        <p className="muted">管理 Source site、Crawl job 和手动触发。</p>
      </div>

      {message ? <p className="card status-line">{message}</p> : null}
      {loadError ? (
        <p className="card error-text" role="alert">
          {loadError}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {LOADING_STATE_COPY}
        </p>
      ) : null}

      {sources?.items.length ? (
        <div className="grid two-column">
          {sources.items.map((source) => {
            const sourceJobs = jobsBySource.get(source.id) ?? [];
            return (
              <article className="card" key={source.id}>
                <div className="card-header">
                  <div>
                    <h2>{source.name}</h2>
                    <p className="muted compact">{source.base_url}</p>
                  </div>
                  <span className="badge">{source.active ? 'active 活跃' : 'inactive 停用'}</span>
                </div>
                <dl className="kv-grid">
                  <div>
                    <dt>类型 Type</dt>
                    <dd>{source.site_type}</dd>
                  </div>
                  <div>
                    <dt>抓取模式 Fetch mode</dt>
                    <dd>{source.fetch_mode}</dd>
                  </div>
                  <div>
                    <dt>语言 Language</dt>
                    <dd>{source.default_language || 'n/a'}</dd>
                  </div>
                  <div>
                    <dt>域名 Domains</dt>
                    <dd>{source.allowed_domains.join(', ') || 'n/a'}</dd>
                  </div>
                </dl>

                <h3>Jobs 任务</h3>
                {sourceJobs.length ? (
                  <ul className="dense-list">
                    {sourceJobs.map((job) => {
                      const triggerPending = pendingJobIds.has(job.id);
                      return (
                        <li key={job.id}>
                          <span>
                            <strong>{job.name}</strong> · {job.parser_profile} · max {job.max_pages}
                          </span>
                          <span className="badge">{job.enabled ? 'enabled 已启用' : 'disabled 已禁用'}</span>
                          <button
                            type="button"
                            onClick={() => void handleTrigger(job)}
                            disabled={!job.enabled || triggerPending}
                            aria-busy={triggerPending}
                            aria-label={`触发 Trigger ${job.name}`}
                          >
                            触发 Trigger
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="muted">
                    {jobs ? '该 Source 暂无 Crawl job。' : 'Job 数据暂不可用：API 请求失败。'}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      ) : loadError ? (
        <p className="card muted">Source 数据暂不可用：API 请求失败。</p>
      ) : loading ? null : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}
    </section>
  );
}
