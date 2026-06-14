import { useEffect, useMemo, useState } from 'react';

import { listJobs, listSources, triggerJob } from '../api/client';
import type { CrawlJob, Page, SourceSite } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

export function SourcesPage() {
  const [sources, setSources] = useState<Page<SourceSite>>();
  const [jobs, setJobs] = useState<Page<CrawlJob>>();
  const [message, setMessage] = useState('');
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    let ignore = false;
    setLoadError('');
    Promise.allSettled([listSources(), listJobs()]).then(([sourceResult, jobResult]) => {
      if (ignore) {
        return;
      }
      const firstFailure = [sourceResult, jobResult].find(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      );
      setSources(sourceResult.status === 'fulfilled' ? sourceResult.value : undefined);
      setJobs(jobResult.status === 'fulfilled' ? jobResult.value : undefined);
      setLoadError(firstFailure ? formatErrorMessage('Unable to load sources data', firstFailure.reason) : '');
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

  async function handleTrigger(job: CrawlJob) {
    setMessage(`Triggering ${job.name}...`);
    try {
      const run = await triggerJob(job.id);
      setMessage(`Queued run ${run.id} for ${job.name}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to trigger job.');
    }
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">Control plane</p>
        <h1>Sources</h1>
        <p className="muted">Registered source sites, crawl jobs, and manual trigger controls.</p>
      </div>

      {message ? <p className="card status-line">{message}</p> : null}
      {loadError ? (
        <p className="card error-text" role="alert">
          {loadError}
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
                  <span className="badge">{source.active ? 'active' : 'inactive'}</span>
                </div>
                <dl className="kv-grid">
                  <div>
                    <dt>Type</dt>
                    <dd>{source.site_type}</dd>
                  </div>
                  <div>
                    <dt>Fetch mode</dt>
                    <dd>{source.fetch_mode}</dd>
                  </div>
                  <div>
                    <dt>Language</dt>
                    <dd>{source.default_language || 'n/a'}</dd>
                  </div>
                  <div>
                    <dt>Domains</dt>
                    <dd>{source.allowed_domains.join(', ') || 'n/a'}</dd>
                  </div>
                </dl>

                <h3>Jobs</h3>
                {sourceJobs.length ? (
                  <ul className="dense-list">
                    {sourceJobs.map((job) => (
                      <li key={job.id}>
                        <span>
                          <strong>{job.name}</strong> · {job.parser_profile} · max {job.max_pages}
                        </span>
                        <span className="badge">{job.enabled ? 'enabled' : 'disabled'}</span>
                        <button type="button" onClick={() => void handleTrigger(job)} disabled={!job.enabled}>
                          Trigger
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">
                    {jobs ? 'No crawl jobs configured for this source.' : 'Job data unavailable while the API request is failing.'}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      ) : loadError ? (
        <p className="card muted">Source data unavailable while the API request is failing.</p>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}
    </section>
  );
}
