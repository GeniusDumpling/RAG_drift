import { ChangeEvent, useEffect, useMemo, useState } from 'react';

import { getRun, getRunEvents, listRuns } from '../api/client';
import type { CrawlRun, CrawlRunEvent, Page } from '../api/types';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

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
  ['discovered_count', 'Discovered'],
  ['fetched_count', 'Fetched'],
  ['parsed_count', 'Parsed'],
  ['extracted_count', 'Extracted'],
  ['deduped_count', 'Deduped'],
  ['chunked_count', 'Chunked'],
  ['embedded_count', 'Embedded'],
  ['error_count', 'Errors'],
];

export function RunDetailPage() {
  const [runs, setRuns] = useState<Page<CrawlRun>>();
  const [selectedRunId, setSelectedRunId] = useState('');
  const [run, setRun] = useState<CrawlRun>();
  const [events, setEvents] = useState<Page<CrawlRunEvent>>();

  useEffect(() => {
    let ignore = false;
    listRuns(25)
      .then((page) => {
        if (!ignore) {
          setRuns(page);
          setSelectedRunId((current) => current || page.items[0]?.id || '');
        }
      })
      .catch(() => {
        if (!ignore) {
          setRuns(undefined);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setRun(undefined);
      setEvents(undefined);
      return;
    }

    let ignore = false;
    Promise.all([getRun(selectedRunId), getRunEvents(selectedRunId).catch(() => undefined)])
      .then(([runDetail, runEvents]) => {
        if (!ignore) {
          setRun(runDetail);
          setEvents(runEvents);
        }
      })
      .catch(() => {
        if (!ignore) {
          setRun(undefined);
          setEvents(undefined);
        }
      });
    return () => {
      ignore = true;
    };
  }, [selectedRunId]);

  const agentTraceEvents = useMemo(
    () => (events?.items ?? []).filter((event) => Object.keys(event.agent_trace_json).length > 0),
    [events],
  );

  function handleRunChange(event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) {
    setSelectedRunId(event.target.value);
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">Execution trace</p>
        <h1>Run Detail</h1>
        <p className="muted">Inspect crawl counters, event chronology, related content, and agent trace payloads.</p>
      </div>

      <div className="card controls-card">
        <label htmlFor="run-selector">Run</label>
        {runs?.items.length ? (
          <select id="run-selector" value={selectedRunId} onChange={handleRunChange}>
            {runs.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.status} · {item.id}
              </option>
            ))}
          </select>
        ) : (
          <input
            id="run-selector"
            value={selectedRunId}
            onChange={handleRunChange}
            placeholder="Paste crawl run id"
          />
        )}
      </div>

      {run ? (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{run.id}</h2>
              <p className="muted compact">
                Source {run.source_site_id} · Job {run.crawl_job_id}
              </p>
            </div>
            <span className="badge">{run.status}</span>
          </div>
          <dl className="kv-grid">
            <div>
              <dt>Trigger</dt>
              <dd>{run.trigger_type}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{run.execution_mode}</dd>
            </div>
            <div>
              <dt>Seed URL</dt>
              <dd>{run.seed_url || 'n/a'}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{run.started_at ? new Date(run.started_at).toLocaleString() : 'n/a'}</dd>
            </div>
            <div>
              <dt>Finished</dt>
              <dd>{run.finished_at ? new Date(run.finished_at).toLocaleString() : 'n/a'}</dd>
            </div>
            <div>
              <dt>Error</dt>
              <dd>{run.error_message || 'none'}</dd>
            </div>
          </dl>
        </section>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}

      <section className="card">
        <h2>Stage Counters</h2>
        {run ? (
          <div className="grid counter-grid">
            {COUNTER_FIELDS.map(([field, label]) => (
              <div className="counter" key={String(field)}>
                <span className="muted">{label}</span>
                <strong>{run[field]}</strong>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">{EMPTY_STATE_COPY}</p>
        )}
      </section>

      <section className="card">
        <h2>Event Timeline</h2>
        {events?.items.length ? (
          <ol className="timeline">
            {events.items.map((event) => (
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
                    {new Date(event.created_at).toLocaleString()} · {event.related_url || 'no related url'}
                  </p>
                  {Object.keys(event.counters_json).length ? (
                    <pre>{JSON.stringify(event.counters_json, null, 2)}</pre>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">{EMPTY_STATE_COPY}</p>
        )}
      </section>

      <section className="card">
        <h2>Agent Trace Summary</h2>
        {agentTraceEvents.length ? (
          agentTraceEvents.map((event) => (
            <pre key={event.id}>{JSON.stringify(event.agent_trace_json, null, 2)}</pre>
          ))
        ) : (
          <p className="muted">{EMPTY_STATE_COPY}</p>
        )}
      </section>
    </section>
  );
}
