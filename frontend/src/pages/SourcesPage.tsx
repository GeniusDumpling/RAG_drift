import { useEffect, useRef, useState } from 'react';

import { listScheduledTasks, triggerScheduledTask, type ScheduledTask } from '../api/client';
import { formatErrorMessage } from '../utils/errors';

const busy = (task: ScheduledTask) => ['active', 'activating', 'deactivating'].includes(task.service_state);

export function SourcesPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string[]>([]);
  const pendingRef = useRef(new Set<string>());
  const alive = useRef(false);

  async function refresh() {
    try {
      const response = await listScheduledTasks();
      if (alive.current) { setTasks(response.items); setError(''); }
    } catch (reason) {
      if (alive.current) setError(formatErrorMessage('无法加载定时采集任务', reason));
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, []);

  async function trigger(task: ScheduledTask) {
    if (busy(task) || pendingRef.current.has(task.id) || !token.trim()) return;
    pendingRef.current.add(task.id);
    setPending([...pendingRef.current]);
    setMessage('');
    try {
      const result = await triggerScheduledTask(task.id, token.trim());
      if (alive.current) setMessage(result.status === 'already_running'
        ? `${task.name} 正在运行，未重复启动。`
        : `已提交 ${task.name}，后台执行中；不代表采集已成功。`);
      await refresh();
    } catch (reason) {
      if (alive.current) setMessage(formatErrorMessage('手动采集失败', reason));
    } finally {
      pendingRef.current.delete(task.id);
      if (alive.current) setPending([...pendingRef.current]);
    }
  }

  return <section>
    <div className="page-title">
      <p className="eyebrow">控制面板</p>
      <h1>数据源</h1>
      <p className="muted">系统定时采集任务 · 每 10 秒刷新 · 手动采集不改变定时周期</p>
    </div>
    <div className="card">
      <label htmlFor="scheduler-token">采集操作口令</label>
      <input id="scheduler-token" type="password" autoComplete="off" value={token}
        onChange={(event) => setToken(event.target.value)} placeholder="输入管理员配置的操作口令" />
      <button type="button" onClick={() => void refresh()}>刷新任务</button>
      <p className="muted">口令仅保存在当前页面内存中。进程退出成功不等于业务入库成功。</p>
    </div>
    {error && <p className="card error-text" role="alert">{error}</p>}
    {message && <p className="card status-line" role="status">{message}</p>}
    {loading && <p role="status">正在加载定时采集任务...</p>}
    {!loading && !error && tasks.length === 0 && <p className="card">当前未安装定时采集任务。</p>}
    <div className="grid two-column">
      {tasks.map((task) => <article className="card" key={task.id}>
        <div className="card-header"><h2>{task.name}</h2>
          <span className="badge">{busy(task) ? '运行中' : task.service_state}</span></div>
        <p className="muted">{task.timer}</p>
        <dl className="kv-grid">
          <div><dt>调度范围</dt><dd>{task.scope === 'user' ? '用户级' : '系统级'}</dd></div>
          <div><dt>定时器状态</dt><dd>{task.timer_state} · {task.enabled}</dd></div>
          <div><dt>执行规则</dt><dd>{task.schedule || '—'}</dd></div>
          <div><dt>下次执行</dt><dd>{task.next_run || '—'}</dd></div>
          <div><dt>上次启动</dt><dd>{task.last_run || '—'}</dd></div>
          <div><dt>上次进程结果</dt><dd>{task.last_result || '—'} / exit {task.exit_code || '—'}</dd></div>
        </dl>
        <button type="button" aria-label={`手动采集 ${task.name}`}
          disabled={!!error || !task.available || busy(task) || pending.includes(task.id) || !token.trim()}
          aria-busy={pending.includes(task.id)} onClick={() => void trigger(task)}>
          {pending.includes(task.id) ? '提交中...' : busy(task) ? '采集中...' : '手动采集'}
        </button>
      </article>)}
    </div>
  </section>;
}
