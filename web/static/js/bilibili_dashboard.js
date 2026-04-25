(function () {
  const pageAlert = document.querySelector('[data-page-alert]');
  const accountsBody = document.querySelector('#accounts-table tbody');
  const tasksBody = document.querySelector('#tasks-table tbody');
  const snapshotsBody = document.querySelector('#snapshots-table tbody');
  const runsBody = document.querySelector('#runs-table tbody');
  const eventsLog = document.getElementById('events-log');
  const eventFilterLabel = document.getElementById('event-filter-label');
  const currentRunCard = document.getElementById('current-run-card');
  const currentRunContent = document.getElementById('current-run-content');
  const currentRunNote = document.getElementById('current-run-note');
  const currentRunOverview = document.getElementById('current-run-overview');
  const currentRunResult = document.getElementById('current-run-result');
  const eventsRunResult = document.getElementById('events-run-result');
  const accountsMoreButton = document.getElementById('accounts-more');
  const tasksMoreButton = document.getElementById('tasks-more');
  const snapshotsMoreButton = document.getElementById('snapshots-more');
  const runsMoreButton = document.getElementById('runs-more');
  const eventsMoreButton = document.getElementById('events-more');

  let accounts = [];
  let tasks = [];
  let snapshots = [];
  let runs = [];
  let accountTotal = 0;
  let taskTotal = 0;
  let activeTaskTotal = 0;
  let snapshotTotal = 0;
  let runTotal = 0;
  let eventTotal = 0;
  let lastEventID = 0;
  let selectedRunID = '';
  let currentRunID = '';
  let eventTimer = null;
  let loginPollTimer = null;
  let loginCountdownTimer = null;
  const currentRunCollapsedKey = 'bilibili-current-run-collapsed';
  const pageState = {
    accounts: {nextCursor: '', hasMore: false, loading: false},
    tasks: {nextCursor: '', hasMore: false, loading: false},
    snapshots: {nextCursor: '', hasMore: false, loading: false},
    runs: {nextCursor: '', hasMore: false, loading: false},
    events: {nextCursor: '', hasMore: false, loading: false}
  };

  const taskTypes = {
    align_backfill: '评论对齐补全',
    fetch_snapshot: '评论拉取',
    snapshot_backfill: '快照补全',
    watch_forward_roots: '指定用户根评论转发',
    sync_root_mentions: '根评论用户 @ 补全'
  };

  const eventText = {
    queued: '已加入队列',
    start: '开始执行',
    fetch_start: '开始拉取评论',
    fetch_root_page: '拉取根评论',
    fetch_reply_page: '拉取楼中楼',
    fetch_dialog_page: '拉取对话树',
    right_fetch_start: '开始拉取目标视频评论',
    snapshot_loaded: '已加载源快照',
    snapshot_saved: '快照已保存',
    analyze_summary: '差异分析完成',
    analysis_item: '缺失项',
    plan_ready: '发布计划已生成',
    delayed_publish_resume: '继续延迟发布',
    delayed_publish_saved: '延迟发布已保存',
    pause: '运行已暂停',
    resume: '运行已恢复',
    publish_request: '准备发布',
    post_result: '发布结果',
    mention_users_backoff: '@人数回退',
    cooldown_backoff: '发送退避',
    reconcile_start: '重对账开始',
    reconcile_resume: '重对账后继续',
    reconciled_duplicate: '重对账确认已存在',
    fetch_delay: '等待下一次拉取',
    temp_cleanup_done: '临时快照已清理',
    temp_cleanup_failed: '临时快照清理失败',
    sleep: '等待下一条',
    backfill_finish: '补全完成',
    finish: '运行完成',
    failed: '运行失败',
    skipped: '已跳过',
    blocked: '运行阻塞'
  };

  const cronPresets = {
    '*/30 * * * *': '每 30 分钟',
    '0 * * * *': '每 1 小时',
    '0 */6 * * *': '每 6 小时',
    '0 2 * * *': '每天 02:00'
  };

  function escape(value) {
    return Mimotion.escapeHTML(value == null ? '' : value);
  }

  function parsePayload(payload) {
    if (!payload) return {};
    if (typeof payload === 'string') {
      try {
        return JSON.parse(payload);
      } catch (_) {
        return {};
      }
    }
    return payload;
  }

  function setPageAction(button, state) {
    if (!button) return;
    button.hidden = !state.hasMore;
    button.disabled = !!state.loading;
    button.textContent = state.loading ? '加载中...' : '加载更多';
  }

  function mergeUniqueByID(existing, incoming) {
    const merged = new Map();
    (existing || []).forEach(item => {
      if (item && item.id != null) merged.set(String(item.id), item);
    });
    (incoming || []).forEach(item => {
      if (item && item.id != null) merged.set(String(item.id), item);
    });
    return Array.from(merged.values());
  }

  function taskTypeText(type) {
    return taskTypes[type] || type || '-';
  }

  function isPublishTaskType(type) {
    return ['align_backfill', 'snapshot_backfill', 'watch_forward_roots', 'sync_root_mentions'].includes(type);
  }

  function runStatusText(status) {
    return {
      queued: '排队中',
      running: '运行中',
      stopped: '已停止',
      paused: '已暂停',
      success: '成功',
      no_change: '无变更',
      dry_run: '仅分析',
      partial: '部分完成',
      failed: '失败',
      skipped: '跳过',
      pending: '等待首次运行',
      idle: '未启用'
    }[status] || status || '-';
  }

  function sourceOID(payload) {
    payload = parsePayload(payload);
    return payload.source_oid || payload.left_oid || '';
  }

  function targetOID(payload) {
    payload = parsePayload(payload);
    return payload.target_oid || payload.right_oid || '';
  }

  function parseResult(result) {
    return parsePayload(result);
  }

  function runPayload(run) {
    return parsePayload(run && run.payload);
  }

  function sourceText(payload, result) {
    result = parseResult(result);
    payload = parsePayload(payload);
    if (result.source_snapshot_id) return `源快照 #${result.source_snapshot_id}`;
    if (result.source_oid) return result.source_oid;
    if (payload.snapshot_id) return `源快照 #${payload.snapshot_id}`;
    if (payload.oid) return payload.oid;
    return sourceOID(payload) || '-';
  }

  function targetText(payload, result) {
    result = parseResult(result);
    payload = parsePayload(payload);
    if (result.target_oid) return result.target_oid;
    return targetOID(payload) || '-';
  }

  function storageModeText(mode) {
    return {
      both: 'JSONL + SQLite',
      jsonl: '仅 JSONL',
      sqlite: '仅 SQLite'
    }[String(mode || '').toLowerCase()] || (mode || '-');
  }

  function resultMetricLine(result) {
    result = parseResult(result);
    if (!result || Object.keys(result).length === 0) return '';
    const parts = [];
    if (result.missing_count != null) parts.push(`缺失 ${Number(result.missing_count || 0)}`);
    if (result.planned_count != null) parts.push(`计划 ${Number(result.planned_count || 0)}`);
    if (result.posted_count != null) parts.push(`已发布 ${Number(result.posted_count || 0)}`);
    if (result.retry_pending_count != null && Number(result.retry_pending_count || 0) > 0) parts.push(`待重试 ${Number(result.retry_pending_count || 0)}`);
    if (result.failed_count != null) parts.push(`失败 ${Number(result.failed_count || 0)}`);
    if (result.blocked_count != null) parts.push(`阻塞 ${Number(result.blocked_count || 0)}`);
    if (result.reconciled_duplicate_count != null) parts.push(`重对账 ${Number(result.reconciled_duplicate_count || 0)}`);
    if (result.cooldown_backoff_count != null && Number(result.cooldown_backoff_count || 0) > 0) parts.push(`退避 ${Number(result.cooldown_backoff_count || 0)}`);
    if (result.delayed_publish_enabled) parts.push(`延迟 ${Number(result.delayed_publish_batch_size || 30)} 条/阶段`);
    return parts.join(' | ');
  }

  function normalizeProgress(run, result) {
    result = parseResult(result);
    const progress = result.progress || {};
    const completed = Number(progress.completed_count != null ? progress.completed_count : (run?.step_index || 0));
    const estimated = Number(progress.estimated_total != null ? progress.estimated_total : Math.max(run?.step_total || 0, completed || 0, 1));
    const percent = Number(progress.percent != null ? progress.percent : (estimated > 0 ? Math.min(100, Math.round((completed / estimated) * 100)) : 0));
    return {
      stage: progress.stage || '',
      stage_label: progress.stage_label || '',
      completed_count: completed,
      estimated_total: estimated,
      percent: Number.isFinite(percent) ? percent : 0,
      comment_count: Number(progress.comment_count || 0),
      root_pages_fetched: Number(progress.root_pages_fetched || 0),
      reply_pages_fetched: Number(progress.reply_pages_fetched || 0),
      dialog_pages_fetched: Number(progress.dialog_pages_fetched || 0),
      dialog_target_count: Number(progress.dialog_target_count || 0),
      pending_retry_count: Number(progress.pending_retry_count || result.retry_pending_count || 0)
    };
  }

  function runDelayRangeText(run) {
    if (!run) return '';
    const result = parseResult(run.result);
    const fetchMin = Number(run.fetch_min_delay_ms || 2000);
    const fetchMax = Number(run.fetch_max_delay_ms || 8000);
    const sendMin = Number(result.run_send_min_delay_ms || run.send_min_delay_ms || 2000);
    const sendMax = Number(result.run_send_max_delay_ms || run.send_max_delay_ms || 8000);
    return `抓取 ${fetchMin}-${fetchMax} ms | 发送 ${sendMin}-${sendMax} ms`;
  }

  function renderProgressBlock(run, result) {
    const progress = normalizeProgress(run, result);
    if (!progress.estimated_total && !progress.completed_count) return '';
    const meta = [];
    if (progress.comment_count) meta.push(`已抓取 ${progress.comment_count} 条`);
    if (progress.root_pages_fetched || progress.reply_pages_fetched || progress.dialog_pages_fetched) {
      meta.push(`根页 ${progress.root_pages_fetched} / 楼中楼页 ${progress.reply_pages_fetched} / 对话页 ${progress.dialog_pages_fetched}`);
    }
    if (progress.dialog_target_count) meta.push(`对话目标 ${progress.dialog_target_count}`);
    if (progress.pending_retry_count) meta.push(`待继续发送 ${progress.pending_retry_count}`);
    return `
      <div class="bilibili-progress">
        <div class="bilibili-progress-head">
          <strong>${escape(progress.stage_label || '运行中')}</strong>
          <span>${escape(progress.completed_count)} / ${escape(progress.estimated_total)} · ${escape(progress.percent)}%</span>
        </div>
        <div class="bilibili-progress-bar" aria-hidden="true">
          <span style="width:${Math.max(0, Math.min(100, progress.percent))}%"></span>
        </div>
        <div class="bilibili-progress-meta">
          <span>${escape(runDelayRangeText(run))}</span>
          ${meta.length ? `<span>${escape(meta.join(' | '))}</span>` : ''}
        </div>
      </div>
    `;
  }

  function runSummaryText(run) {
    const result = parseResult(run.result);
    if (result && result.final_reason) return sanitizeCooldownCopy(result.final_reason);
    return sanitizeCooldownCopy(run.message || run.summary || '-');
  }

  function sanitizeCooldownCopy(text) {
    text = String(text || '').trim();
    if (!text) return text;
    if (text.includes('回落到') || text.includes('下次运行将从')) {
      return text.replace(/评论冷却[^；。]*[；;]\s*(下次运行将回落到|下次运行将从)[^。]*/g, '评论冷却退避超过 1 小时上限，已停止本次运行');
    }
    return text;
  }

  function accountStatusText(account) {
    const status = String(account.status || 'pending').toLowerCase();
    if (status === 'valid') return '有效';
    if (status === 'invalid') return '失效，需重新扫码';
    return '待校验';
  }

  function accountStatusBadge(account) {
    const status = String(account.status || 'pending').toLowerCase();
    const label = accountStatusText(account);
    const cls = status === 'valid' ? 'is-valid' : (status === 'invalid' ? 'is-invalid' : 'is-pending');
    return `<span class="bilibili-status-badge ${cls}">${escape(label)}</span>`;
  }

  function accountStatusDetail(account) {
    const detail = String(account.invalid_reason || account.last_validation_message || '').trim();
    if (!detail) return '';
    if (detail === '登录有效' || detail === '有效') return '';
    return detail;
  }

  function delayRangeCompact(min, max) {
    return `${Number(min || 2000)}-${Number(max || 8000)} ms`;
  }

  function shortText(value, max = 70) {
    const text = String(value || '');
    if (text.length <= max) return text;
    return text.slice(0, max - 1) + '...';
  }

  function setEmpty(body, id, message) {
    const empty = document.getElementById(id);
    const wrap = body.closest('.motion-table-wrap');
    const hasRows = body.children.length > 0;
    if (wrap) wrap.style.display = hasRows ? '' : 'none';
    if (!empty) return;
    empty.style.display = hasRows ? 'none' : '';
    if (!hasRows) Mimotion.renderEmpty(empty, message);
  }

  function isMobileViewport() {
    return window.matchMedia('(max-width: 700px)').matches;
  }

  function isCurrentRunCollapsed() {
    const stored = window.localStorage.getItem(currentRunCollapsedKey);
    if (stored === '1') return true;
    if (stored === '0') return false;
    return isMobileViewport();
  }

  function setCurrentRunCollapsed(collapsed) {
    window.localStorage.setItem(currentRunCollapsedKey, collapsed ? '1' : '0');
    currentRunCard.classList.toggle('is-collapsed', collapsed);
    if (currentRunContent) currentRunContent.hidden = collapsed;
    const head = currentRunCard.querySelector('.bilibili-current-run-head');
    if (head) head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }

  function validAccounts() {
    return accounts.filter(item => String(item.status || '').toLowerCase() === 'valid');
  }

  function activeTasks() {
    return tasks.filter(item => item.is_active);
  }

  function runningRuns() {
    return runs.filter(item => ['queued', 'running', 'paused'].includes(String(item.status || '').toLowerCase()));
  }

  function isActiveRunStatus(status) {
    return ['queued', 'running'].includes(String(status || '').toLowerCase());
  }

  function isUserPausedRun(run) {
    return String(run?.status || '').toLowerCase() === 'paused' && String(run?.pause_kind || '') === 'user';
  }

  function runActionButtons(item) {
    const buttons = [`<button class="motion-btn small secondary" data-action="select-run" data-run-id="${escape(item.id)}">查看事件</button>`];
    if (isActiveRunStatus(item.status)) {
      buttons.push(`<button class="motion-btn small secondary" data-action="pause-run" data-id="${escape(item.id)}">暂停</button>`);
      buttons.push(`<button class="motion-btn small danger" data-action="stop-run" data-id="${escape(item.id)}">停止运行</button>`);
    } else if (isUserPausedRun(item)) {
      buttons.push(`<button class="motion-btn small" data-action="resume-run" data-id="${escape(item.id)}">恢复</button>`);
      buttons.push(`<button class="motion-btn small danger" data-action="stop-run" data-id="${escape(item.id)}">停止运行</button>`);
    }
    return buttons.join('');
  }

  function renderCounts() {
    document.getElementById('account-count').textContent = accountTotal || validAccounts().length;
    document.getElementById('task-count').textContent = activeTaskTotal || activeTasks().length || taskTotal;
    document.getElementById('snapshot-count').textContent = snapshotTotal || snapshots.length;
    document.getElementById('run-count').textContent = runningRuns().length || runTotal || runs.length;
  }

  function switchTab(tab) {
    document.querySelectorAll('[data-tab-target]').forEach(button => {
      button.classList.toggle('active', button.dataset.tabTarget === tab);
    });
    document.querySelectorAll('[data-tab-panel]').forEach(panel => {
      panel.classList.toggle('active', panel.dataset.tabPanel === tab);
    });
  }

  function renderCurrentRun() {
    const run = runningRuns()[0];
    if (!run) {
      currentRunID = '';
      currentRunCard.hidden = true;
      if (currentRunResult) currentRunResult.hidden = true;
      return;
    }
    currentRunID = String(run.id || '');
    currentRunCard.hidden = false;
    setCurrentRunCollapsed(isCurrentRunCollapsed());
    currentRunNote.textContent = `${run.display_name || run.task_name || '-'} | ${sourceText(run.payload, run.result)} -> ${targetText(run.payload, run.result)} | ${run.account_name || run.account_id || '-'} | ${run.source_type === 'manual' ? '手动' : '任务'}`;
    currentRunOverview.innerHTML = `
      <span>${Mimotion.statusBadge(run.status || 'idle')}</span>
      ${run.pause_kind === 'delayed_publish' ? '<span class="bilibili-event-chip">延迟等待中</span>' : ''}
      <strong>${escape(taskTypeText(run.task_type))}</strong>
      <span class="bilibili-event-chip">${escape(sourceText(run.payload, run.result))}${run.task_type === 'fetch_snapshot' ? '' : ` -> ${escape(targetText(run.payload, run.result))}`}</span>
      <em>${escape(shortText(runSummaryText(run) || '等待事件更新', 90))}</em>
    `;
    const pauseButton = currentRunCard.querySelector('[data-current-pause]');
    const resumeButton = currentRunCard.querySelector('[data-current-resume]');
    if (pauseButton) pauseButton.hidden = !isActiveRunStatus(run.status);
    if (resumeButton) resumeButton.hidden = !isUserPausedRun(run);
    renderRunResultPanel(currentRunResult, [run]);
  }

  async function loadAccounts(reset = true) {
    const state = pageState.accounts;
    if (!reset && (!state.hasMore || !state.nextCursor)) return;
    if (state.loading) return;
    state.loading = true;
    setPageAction(accountsMoreButton, state);
    try {
      const query = ['limit=20'];
      if (!reset && state.nextCursor) query.push(`cursor=${encodeURIComponent(state.nextCursor)}`);
      const data = await Mimotion.api(`/web/api/bilibili/accounts?${query.join('&')}`);
      const items = data.items || data.accounts || [];
      accounts = reset ? items : mergeUniqueByID(accounts, items);
      state.nextCursor = data.next_cursor || '';
      state.hasMore = Boolean(data.has_more);
      accountTotal = Number(data.total || accounts.length);
      accountsBody.innerHTML = accounts.map(item => {
        const invalid = String(item.status || '').toLowerCase() === 'invalid';
        const statusDetail = accountStatusDetail(item);
        return `<tr class="${invalid ? 'is-invalid' : ''}">
          <td data-label="ID">#${escape(item.id)}</td>
          <td data-label="账号">
            <span class="motion-cell-title bilibili-account-name" title="${escape(item.display_name || item.uname || item.mid)}">${escape(item.display_name || item.uname || item.mid)}</span>
            <span class="motion-cell-sub bilibili-account-meta">${escape(item.uname || '-')} | MID ${escape(item.mid || '-')}</span>
          </td>
          <td data-label="状态">
            ${accountStatusBadge(item)}
            ${statusDetail ? `<span class="motion-cell-sub">${escape(statusDetail)}</span>` : ''}
          </td>
          <td data-label="间隔">
            <div class="bilibili-delay-stack">
              <span class="bilibili-delay-chip">抓取 ${escape(delayRangeCompact(item.fetch_min_delay_ms, item.fetch_max_delay_ms))}</span>
              <span class="bilibili-delay-chip">发送 ${escape(delayRangeCompact(item.send_min_delay_ms, item.send_max_delay_ms))}</span>
            </div>
          </td>
          <td data-label="操作"><div class="motion-inline-actions bilibili-actions-wrap">
            <button class="motion-btn small" data-action="validate-account" data-id="${escape(item.id)}">校验</button>
            <button class="motion-btn small secondary" data-action="edit-account" data-id="${escape(item.id)}">设置</button>
            <button class="motion-btn small ${invalid ? '' : 'secondary'}" data-action="scan-login">${invalid ? '重新扫码' : '扫码'}</button>
            <button class="motion-btn small danger" data-action="delete-account" data-id="${escape(item.id)}">删除</button>
          </div></td>
        </tr>`;
      }).join('');
      setEmpty(accountsBody, 'accounts-empty', '暂无 Bilibili 账号，请先扫码登录');
    } finally {
      state.loading = false;
      setPageAction(accountsMoreButton, state);
    }
  }

  async function loadTasks(reset = true) {
    const state = pageState.tasks;
    if (!reset && (!state.hasMore || !state.nextCursor)) return;
    if (state.loading) return;
    state.loading = true;
    setPageAction(tasksMoreButton, state);
    try {
      const query = ['limit=20'];
      if (!reset && state.nextCursor) query.push(`cursor=${encodeURIComponent(state.nextCursor)}`);
      const data = await Mimotion.api(`/web/api/bilibili/tasks?${query.join('&')}`);
      const items = data.items || data.tasks || [];
      tasks = reset ? items : mergeUniqueByID(tasks, items);
      state.nextCursor = data.next_cursor || '';
      state.hasMore = Boolean(data.has_more);
      taskTotal = Number(data.total || tasks.length);
      activeTaskTotal = Number(data.active_total || activeTasks().length);
      tasksBody.innerHTML = tasks.map(item => {
        const payload = parsePayload(item.payload);
        const status = item.last_run_status || (item.is_active ? 'pending' : 'idle');
        return `<tr>
          <td data-label="ID">#${escape(item.id)}</td>
          <td data-label="任务">
            <span class="motion-cell-title">${escape(item.name)}</span>
            <span class="motion-cell-sub">${escape(taskTypeText(item.task_type))} | 账号 ${escape((item.account_ids || []).join(', ') || '-')}</span>
            <span class="motion-cell-sub">${escape(taskPayloadSummary(item.task_type, payload))}</span>
          </td>
          <td data-label="调度">
            <span class="motion-cell-title">${escape(cronPresets[item.cron_spec] || item.cron_spec || '-')}</span>
            <span class="motion-cell-sub">下次 ${escape(item.next_run_at || '-')}</span>
          </td>
          <td data-label="状态">${Mimotion.statusBadge(status)}</td>
          <td data-label="操作"><div class="motion-inline-actions bilibili-actions-wrap">
            <button class="motion-btn small secondary" data-action="task-actions" data-id="${escape(item.id)}">更多操作</button>
          </div></td>
        </tr>`;
      }).join('');
      setEmpty(tasksBody, 'tasks-empty', '暂无任务，选择上方快捷入口创建');
    } finally {
      state.loading = false;
      setPageAction(tasksMoreButton, state);
    }
  }

  async function loadSnapshots(reset = true) {
    const state = pageState.snapshots;
    if (!reset && (!state.hasMore || !state.nextCursor)) return;
    if (state.loading) return;
    state.loading = true;
    setPageAction(snapshotsMoreButton, state);
    try {
      const query = ['limit=20'];
      if (!reset && state.nextCursor) query.push(`cursor=${encodeURIComponent(state.nextCursor)}`);
      const data = await Mimotion.api(`/web/api/bilibili/snapshots?${query.join('&')}`);
      const items = data.items || data.snapshots || [];
      snapshots = reset ? items : mergeUniqueByID(snapshots, items);
      state.nextCursor = data.next_cursor || '';
      state.hasMore = Boolean(data.has_more);
      snapshotTotal = Number(data.total || snapshots.length);
      snapshotsBody.innerHTML = snapshots.map(item => `<tr>
        <td data-label="ID">#${escape(item.id)}</td>
        <td data-label="视频"><span class="motion-cell-title">av${escape(item.oid)}</span><span class="motion-cell-sub">${escape(item.source_name || '评论快照')}</span></td>
        <td data-label="评论数">${escape(item.comment_count)}<span class="motion-cell-sub">根评论 ${escape(item.root_count)}</span></td>
        <td data-label="存储">
          <span class="bilibili-event-chip">${escape(item.storage_mode || 'both')}</span>
          ${item.has_jsonl ? `<span class="bilibili-event-chip">JSONL</span>` : ''}
          ${item.has_sqlite ? `<span class="bilibili-event-chip">SQLite</span>` : ''}
          ${item.storage_path ? `<span class="motion-cell-sub bilibili-path" title="${escape(item.storage_path)}">${escape(item.storage_path)}</span>` : ''}
        </td>
        <td data-label="操作"><div class="motion-inline-actions bilibili-actions-wrap">
          <button class="motion-btn small" data-action="use-snapshot" data-id="${escape(item.id)}">用于补全</button>
        </div></td>
      </tr>`).join('');
      setEmpty(snapshotsBody, 'snapshots-empty', '暂无快照，可先拉取一个视频的评论');
    } finally {
      state.loading = false;
      setPageAction(snapshotsMoreButton, state);
    }
  }

  async function loadRuns(reset = true) {
    const state = pageState.runs;
    if (!reset && (!state.hasMore || !state.nextCursor)) return;
    if (state.loading) return;
    state.loading = true;
    setPageAction(runsMoreButton, state);
    try {
      const query = ['limit=20'];
      if (!reset && state.nextCursor) query.push(`cursor=${encodeURIComponent(state.nextCursor)}`);
      const data = await Mimotion.api(`/web/api/bilibili/runs?${query.join('&')}`);
      const items = data.items || data.runs || [];
      runs = reset ? items : mergeUniqueByID(runs, items);
      state.nextCursor = data.next_cursor || '';
      state.hasMore = Boolean(data.has_more);
      runTotal = Number(data.total || runs.length);
      runsBody.innerHTML = runs.map(item => `<tr>
        <td data-label="ID"><button class="motion-link-button" data-action="select-run" data-run-id="${escape(item.id)}">#${escape(item.id)}</button></td>
        <td data-label="运行">
          <span class="motion-cell-title">${escape(item.display_name || item.task_name || '-')}</span>
          <span class="motion-cell-sub">${escape(taskTypeText(item.task_type))} | ${escape(item.source_type === 'manual' ? '手动' : '任务')}</span>
          <span class="motion-cell-sub">${escape(runPathSummary(item))}</span>
        </td>
        <td data-label="账号">${escape(item.account_name || item.account_id)}</td>
        <td data-label="状态">${Mimotion.statusBadge(item.status)}${item.pause_kind === 'delayed_publish' ? '<span class="motion-cell-sub">延迟等待中</span>' : ''}</td>
        <td data-label="消息">
          <span class="bilibili-truncate" title="${escape(runSummaryText(item) || '-')}">${escape(shortText(runSummaryText(item) || '-', 90))}</span>
          ${resultMetricLine(item.result) ? `<span class="motion-cell-sub">${escape(resultMetricLine(item.result))}</span>` : ''}
          <span class="motion-cell-sub">${escape(runDelayRangeText(item))}</span>
        </td>
        <td data-label="操作"><div class="motion-inline-actions bilibili-actions-wrap">${runActionButtons(item)}</div></td>
      </tr>`).join('');
      setEmpty(runsBody, 'runs-empty', '暂无运行记录');
      renderCurrentRun();
      renderSelectedRunResult();
    } finally {
      state.loading = false;
      setPageAction(runsMoreButton, state);
    }
  }

  function taskPayloadSummary(type, payload) {
    payload = parsePayload(payload);
    const delayed = payload.delayed_publish_enabled && isPublishTaskType(type) ? ` | 延迟处理 ${payload.delayed_publish_batch_size || 30} 条/阶段` : '';
    if (type === 'align_backfill') return `源 ${sourceOID(payload) || '-'} -> 目标 ${targetOID(payload) || '-'}${delayed}`;
    if (type === 'fetch_snapshot') return `视频 ${payload.oid || '-'} | ${storageModeText(payload.storage_mode || 'both')}`;
    if (type === 'snapshot_backfill') return `源快照 #${payload.snapshot_id || '-'} -> 目标 ${targetOID(payload) || '-'}${delayed}`;
    if (type === 'watch_forward_roots') return `源 ${sourceOID(payload) || '-'} -> 目标 ${targetOID(payload) || '-'} | 用户 ${((payload.monitor_unames || []).join(', ') || '-')}${delayed}`;
    if (type === 'sync_root_mentions') return `源 ${sourceOID(payload) || '-'} -> 目标 ${targetOID(payload) || '-'} | 触发词 ${payload.mention_trigger_text || '全部评论'} | 每条@ ${payload.mention_users_per_message || 10} 人 | 后缀 ${payload.mention_suffix || '-'} | 随机字符 ${payload.mention_random_chars ? '开启' : '关闭'}${delayed}`;
    return '-';
  }

  function runPathSummary(run) {
    const source = sourceText(run.payload, run.result);
    const target = targetText(run.payload, run.result);
    if (run.task_type === 'fetch_snapshot') return `视频 ${source}`;
    return `源 ${source} -> 目标 ${target}`;
  }

  function renderRunResultPanel(target, relatedRuns) {
    if (!target) return;
    const list = (relatedRuns || []).filter(Boolean);
    if (!list.length) {
      target.hidden = true;
      target.innerHTML = '';
      return;
    }
    const primaryRun = list.find(item => {
      const parsed = parseResult(item.result);
      return parsed && Object.keys(parsed).length > 0;
    }) || list[0];
    const result = parseResult(primaryRun.result);
    const aggregate = {
      missing_count: 0,
      missing_root_count: 0,
      missing_reply_count: 0,
      planned_count: 0,
      posted_count: 0,
      skipped_count: 0,
      failed_count: 0,
      blocked_count: 0,
      reconciled_duplicate_count: 0,
      cooldown_backoff_count: 0,
      retry_pending_count: 0
    };
    list.forEach(run => {
      const item = parseResult(run.result);
      aggregate.missing_count += Number(item.missing_count || 0);
      aggregate.missing_root_count += Number(item.missing_root_count || 0);
      aggregate.missing_reply_count += Number(item.missing_reply_count || 0);
      aggregate.planned_count += Number(item.planned_count || 0);
      aggregate.posted_count += Number(item.posted_count || 0);
      aggregate.skipped_count += Number(item.skipped_count || 0);
      aggregate.failed_count += Number(item.failed_count || 0);
      aggregate.blocked_count += Number(item.blocked_count || 0);
      aggregate.reconciled_duplicate_count += Number(item.reconciled_duplicate_count || 0);
      aggregate.cooldown_backoff_count += Number(item.cooldown_backoff_count || 0);
      aggregate.retry_pending_count += Number(item.retry_pending_count || 0);
    });
    const sourceLabel = sourceText(primaryRun.payload, primaryRun.result);
    const targetLabel = targetText(primaryRun.payload, primaryRun.result);
    const displayStatus = result.status || primaryRun.status;
    const failedItems = Array.isArray(result.failed_items) ? result.failed_items : [];
    const blockedItems = Array.isArray(result.blocked_items) ? result.blocked_items : [];
    const reconciledItems = Array.isArray(result.reconciled_items) ? result.reconciled_items : [];
    const analysisItems = Array.isArray(result.analysis_items) ? result.analysis_items : [];
    const items = (result.dry_run || displayStatus === 'dry_run' || displayStatus === 'no_change')
      ? analysisItems
      : [...failedItems, ...blockedItems, ...reconciledItems];
    const displayMode = result.dry_run ? '仅分析' : (primaryRun.task_type === 'fetch_snapshot' ? storageModeText(runPayload(primaryRun).storage_mode || 'both') : '真实发布');
    target.hidden = false;
    target.innerHTML = `
      ${renderProgressBlock(primaryRun, result)}
      <div class="bilibili-run-result-grid">
        <div><span>状态</span><strong>${escape(runStatusText(displayStatus))}</strong></div>
        <div><span>来源</span><strong>${escape(primaryRun.source_type === 'manual' ? '手动' : '任务')}</strong></div>
        <div><span>源</span><strong>${escape(sourceLabel)}</strong></div>
        <div><span>目标</span><strong>${escape(primaryRun.task_type === 'fetch_snapshot' ? '-' : targetLabel)}</strong></div>
        <div><span>缺失</span><strong>${escape(aggregate.missing_count)}</strong></div>
        <div><span>一级评论</span><strong>${escape(aggregate.missing_root_count)}</strong></div>
        <div><span>楼中楼</span><strong>${escape(aggregate.missing_reply_count)}</strong></div>
        <div><span>计划</span><strong>${escape(aggregate.planned_count)}</strong></div>
        <div><span>已发布</span><strong>${escape(aggregate.posted_count)}</strong></div>
        <div><span>待重试</span><strong>${escape(aggregate.retry_pending_count)}</strong></div>
        <div><span>失败</span><strong>${escape(aggregate.failed_count)}</strong></div>
        <div><span>阻塞</span><strong>${escape(aggregate.blocked_count)}</strong></div>
        <div><span>重对账</span><strong>${escape(aggregate.reconciled_duplicate_count)}</strong></div>
        <div><span>冷却退避</span><strong>${escape(aggregate.cooldown_backoff_count)}</strong></div>
        <div><span>模式</span><strong>${escape(displayMode)}</strong></div>
      </div>
      <div class="motion-cell-sub">${escape(sanitizeCooldownCopy(result.final_reason || runSummaryText(primaryRun) || ''))}</div>
      ${items.length ? `<div class="bilibili-run-result-list">${items.map(item => `
        <div class="bilibili-run-result-item">
          <strong>${escape((item.path_messages || []).join(' / ') || item.message || '-')}</strong>
          <span>${escape(item.error ? `失败：${item.error}` : item.blocked ? `阻塞：${item.block_reason || '-'}` : item.response_code === 12051 ? `重对账：${item.response_hint || item.response_message || '重复评论'}` : (item.is_root ? '一级评论' : `父评论：${item.parent_message || '-'}`))}</span>
        </div>
      `).join('')}</div>` : ''}
    `;
  }

  function renderSelectedRunResult() {
    if (!selectedRunID) {
      if (eventsRunResult) {
        eventsRunResult.hidden = true;
        eventsRunResult.innerHTML = '';
      }
      return;
    }
    const relatedRuns = runs.filter(item => String(item.id) === String(selectedRunID));
    renderRunResultPanel(eventsRunResult, relatedRuns);
  }

  async function loadAll() {
    Mimotion.setAlert(pageAlert, '', '');
    try {
      await Promise.all([loadAccounts(true), loadTasks(true), loadSnapshots(true), loadRuns(true)]);
      renderCounts();
      await loadEvents(true);
    } catch (error) {
      Mimotion.setAlert(pageAlert, error.message || '加载失败', 'error');
    }
  }

  async function loadEvents(reset = false) {
    const state = pageState.events;
    if (state.loading) return;
    state.loading = true;
    setPageAction(eventsMoreButton, state);
    let rawEvents = [];
    try {
      const params = [];
      if (selectedRunID) params.push(`account_run_id=${encodeURIComponent(selectedRunID)}`);
      if (reset) {
        params.push('limit=50');
        const data = await Mimotion.api(`/web/api/bilibili/events?${params.join('&')}`);
        rawEvents = data.items || data.events || [];
        eventTotal = Number(data.total || rawEvents.length);
        state.nextCursor = data.next_cursor || '';
        state.hasMore = Boolean(data.has_more);
        lastEventID = rawEvents.reduce((max, event) => Math.max(max, Number(event.id || 0)), 0);
        eventsLog.innerHTML = '';
      } else {
        params.push(`after_id=${lastEventID}`);
        params.push('limit=100');
        const data = await Mimotion.api(`/web/api/bilibili/events?${params.join('&')}`);
        rawEvents = data.items || data.events || [];
        for (const event of rawEvents) {
          lastEventID = Math.max(lastEventID, Number(event.id || 0));
        }
      }
      const events = rawEvents.filter(shouldDisplayEvent);
      if (eventFilterLabel) {
        const prefix = selectedRunID ? `运行 #${selectedRunID} 的事件` : '全部事件';
        eventFilterLabel.textContent = `${prefix} · 历史保留 30 天`;
      }
      renderSelectedRunResult();
      if (events.length && eventsLog.querySelector('.motion-empty')) {
        eventsLog.innerHTML = '';
      }
      if (reset) {
        for (const event of events) {
          eventsLog.append(renderEvent(event));
        }
      } else {
        for (let i = events.length - 1; i >= 0; i -= 1) {
          eventsLog.prepend(renderEvent(events[i]));
        }
      }
      if (!eventsLog.children.length) {
        eventsLog.innerHTML = '<div class="motion-empty">暂无事件</div>';
      }
      if (rawEvents.length) {
        await loadRuns(true);
        if (rawEvents.some(event => ['finish', 'failed'].includes(event.event_type))) {
          await loadTasks(true);
        }
        if (rawEvents.some(event => event.event_type === 'snapshot_saved')) {
          await loadSnapshots(true);
        }
        renderCounts();
      }
    } finally {
      state.loading = false;
      setPageAction(eventsMoreButton, state);
    }
  }

  async function loadMoreEvents() {
    const state = pageState.events;
    if (state.loading || !state.hasMore || !state.nextCursor) return;
    state.loading = true;
    setPageAction(eventsMoreButton, state);
    try {
      const params = ['limit=50', `cursor=${encodeURIComponent(state.nextCursor)}`];
      if (selectedRunID) params.push(`account_run_id=${encodeURIComponent(selectedRunID)}`);
      const data = await Mimotion.api(`/web/api/bilibili/events?${params.join('&')}`);
      const rawEvents = data.items || data.events || [];
      const events = rawEvents.filter(shouldDisplayEvent);
      state.nextCursor = data.next_cursor || '';
      state.hasMore = Boolean(data.has_more);
      eventTotal = Number(data.total || eventTotal);
      if (events.length && eventsLog.querySelector('.motion-empty')) {
        eventsLog.innerHTML = '';
      }
      for (const event of events) {
        eventsLog.append(renderEvent(event));
      }
      if (!eventsLog.children.length) {
        eventsLog.innerHTML = '<div class="motion-empty">暂无事件</div>';
      }
    } finally {
      state.loading = false;
      setPageAction(eventsMoreButton, state);
    }
  }

  function shouldDisplayEvent(event) {
    const type = event && event.event_type;
    if (type !== 'post_result') return true;
    const data = parsePayload(event.data);
    if (data.dry_run) return false;
    if (data.skipped) return true;
    if (data.error) return true;
    if (Number(data.response_code || 0) !== 0) return true;
    return false;
  }

  function renderEvent(event) {
    const item = document.createElement('div');
    const level = event.level || 'info';
    const type = event.event_type || 'info';
    item.className = `bilibili-event ${level}`;
    const data = parsePayload(event.data);
    item.innerHTML = `
      <div class="bilibili-event-marker"></div>
      <div class="bilibili-event-body">
        <div class="bilibili-event-head">
          <strong>${escape(eventText[type] || type)}</strong>
          <span>#${escape(event.id)}</span>
          <em>${escape(event.created_at)}</em>
        </div>
        <p>${escape(event.message || '')}</p>
        ${eventDetails(type, data)}
      </div>
    `;
    return item;
  }

  function eventDetails(type, data) {
    if (!data || typeof data !== 'object') return '';
    if (type === 'analyze_summary') {
      return `<div class="bilibili-run-result-grid">
        <div><span>源</span><strong>${escape(data.source_snapshot_id ? `源快照 #${data.source_snapshot_id}` : (data.source_oid || '-'))}</strong></div>
        <div><span>目标</span><strong>${escape(data.target_oid || '-')}</strong></div>
        <div><span>缺失总数</span><strong>${escape(data.missing_count || 0)}</strong></div>
        <div><span>一级评论</span><strong>${escape(data.missing_root_count || 0)}</strong></div>
        <div><span>楼中楼</span><strong>${escape(data.missing_reply_count || 0)}</strong></div>
        <div><span>模式</span><strong>${escape(data.dry_run ? '仅分析' : '真实发布')}</strong></div>
      </div>`;
    }
    if (type === 'analysis_item') {
      return `<details class="bilibili-event-details">
        <summary>缺失路径</summary>
        <dl>
          <dt>路径</dt><dd>${escape((data.path_messages || []).join(' / ') || '-')}</dd>
          <dt>评论</dt><dd>${escape(data.message || '-')}</dd>
          <dt>父评论</dt><dd>${escape(data.parent_message || '-')}</dd>
          <dt>源评论 ID</dt><dd>${escape(data.left_rpid || data.source_rpid || '-')}</dd>
          <dt>类型</dt><dd>${escape(data.is_root ? '一级评论' : '楼中楼')}</dd>
        </dl>
      </details>`;
    }
    if (type === 'plan_ready') {
      return `<div class="bilibili-run-result-grid">
        <div><span>源</span><strong>${escape(data.source_snapshot_id ? `源快照 #${data.source_snapshot_id}` : (data.source_oid || '-'))}</strong></div>
        <div><span>目标</span><strong>${escape(data.target_oid || '-')}</strong></div>
        <div><span>可执行</span><strong>${escape(data.ready_count || 0)}</strong></div>
        <div><span>阻塞</span><strong>${escape(data.blocked_count || 0)}</strong></div>
        <div><span>模式</span><strong>${escape(data.dry_run ? '仅分析' : '真实发布')}</strong></div>
      </div>`;
    }
    if (type === 'publish_request') {
      return `<details class="bilibili-event-details">
        <summary>发布目标</summary>
        <dl>
          <dt>路径</dt><dd>${escape((data.path_messages || []).join(' / ') || '-')}</dd>
          <dt>评论</dt><dd>${escape(data.message || '-')}</dd>
          <dt>父评论</dt><dd>${escape(data.parent_message || '-')}</dd>
          <dt>目标</dt><dd>root ${escape(data.root == null ? 0 : data.root)} / parent ${escape(data.parent == null ? 0 : data.parent)}</dd>
          <dt>模式</dt><dd>${escape(data.dry_run ? '仅分析' : '真实发布')}</dd>
        </dl>
      </details>`;
    }
    if (type === 'post_result') {
      return `<details class="bilibili-event-details">
        <summary>发布详情</summary>
        <dl>
          <dt>路径</dt><dd>${escape((data.path_messages || []).join(' / ') || '-')}</dd>
          <dt>评论</dt><dd>${escape(data.message || '-')}</dd>
          <dt>父评论</dt><dd>${escape(data.parent_message || '-')}</dd>
          <dt>源评论 ID</dt><dd>${escape(data.left_rpid || data.source_rpid || '-')}</dd>
          <dt>目标</dt><dd>root ${escape(data.root == null ? 0 : data.root)} / parent ${escape(data.parent == null ? 0 : data.parent)} / rpid ${escape(data.rpid || data.planned_rpid || '-')}</dd>
          <dt>结果</dt><dd>${escape(data.skipped ? data.skip_reason : data.error || data.response_hint || data.response_message || '成功')}</dd>
          <dt>响应</dt><dd>${escape(data.response_code || 0)} ${escape(data.response_message || '')}</dd>
          <dt>提示</dt><dd>${escape(data.response_hint || '-')}</dd>
          ${data.cooldown_backoff ? `<dt>本次运行发送区间</dt><dd>${escape((data.run_send_min_delay_ms || data.send_min_delay_ms || 0) + ' - ' + (data.run_send_max_delay_ms || data.send_max_delay_ms || 0) + ' ms')}</dd>` : ''}
        </dl>
      </details>`;
    }
    if (type === 'reconciled_duplicate') {
      return `<details class="bilibili-event-details">
        <summary>重对账详情</summary>
        <dl>
          <dt>路径</dt><dd>${escape((data.path_messages || []).join(' / ') || '-')}</dd>
          <dt>评论</dt><dd>${escape(data.message || '-')}</dd>
          <dt>父评论</dt><dd>${escape(data.parent_message || '-')}</dd>
          <dt>目标</dt><dd>root ${escape(data.root == null ? 0 : data.root)} / parent ${escape(data.parent == null ? 0 : data.parent)}</dd>
          <dt>结果</dt><dd>${escape(data.response_hint || data.response_message || '已存在')}</dd>
        </dl>
      </details>`;
    }
    if (type === 'reconcile_start' || type === 'reconcile_resume') {
      return `<div class="bilibili-run-result-grid">
        <div><span>目标</span><strong>${escape(data.target_oid || '-')}</strong></div>
        <div><span>路径</span><strong>${escape((data.path_messages || []).join(' / ') || '-')}</strong></div>
      </div>`;
    }
    if (type === 'fetch_delay') {
      return `<span class="bilibili-event-chip">${escape(data.delay_ms || 0)} ms</span>`;
    }
    if (type === 'cooldown_backoff') {
      return `<div class="bilibili-run-result-grid">
        <div><span>等待</span><strong>${escape(data.delay_ms || 0)} ms</strong></div>
      </div>`;
    }
    if (type === 'blocked') {
      return `<details class="bilibili-event-details">
        <summary>阻塞详情</summary>
        <dl>
          <dt>路径</dt><dd>${escape((data.path_messages || []).join(' / ') || '-')}</dd>
          <dt>评论</dt><dd>${escape(data.message || '-')}</dd>
          <dt>父评论</dt><dd>${escape(data.parent_message || '-')}</dd>
          <dt>类型</dt><dd>${escape(data.is_root ? '一级评论' : '楼中楼')}</dd>
          <dt>原因</dt><dd>${escape(data.block_reason || '-')}</dd>
        </dl>
      </details>`;
    }
    if (type === 'snapshot_loaded') {
      return `<div class="bilibili-run-result-grid">
        <div><span>源快照</span><strong>${escape(data.source_snapshot_id || '-')}</strong></div>
        <div><span>存储</span><strong>${escape(storageModeText(data.storage_mode || '-'))}</strong></div>
      </div>`;
    }
    if (type === 'sleep') {
      return `<span class="bilibili-event-chip">${escape(data.delay_ms || 0)} ms</span>`;
    }
    if (type === 'snapshot_saved') {
      return `<details class="bilibili-event-details">
        <summary>快照详情</summary>
        <dl>
          <dt>快照</dt><dd>#${escape(data.snapshot_id || '-')}</dd>
          <dt>评论数</dt><dd>${escape(data.comment_count || 0)}</dd>
          <dt>根评论</dt><dd>${escape(data.root_count || 0)}</dd>
          <dt>存储</dt><dd>${escape(storageModeText(data.storage_mode || '-'))}</dd>
          <dt>JSONL</dt><dd>${escape(data.has_jsonl ? '是' : '否')}</dd>
          <dt>SQLite</dt><dd>${escape(data.has_sqlite ? '是' : '否')}</dd>
          <dt>路径</dt><dd>${escape(data.storage_path || '-')}</dd>
        </dl>
      </details>`;
    }
    if (type === 'finish' || type === 'failed' || type === 'skipped') {
      const result = parseResult(data.result);
      return `
        ${renderProgressBlock({fetch_min_delay_ms: 0, fetch_max_delay_ms: 0, send_min_delay_ms: 0, send_max_delay_ms: 0, step_index: data.step_index || 0, step_total: data.step_total || 0}, result)}
        <div class="bilibili-run-result-grid">
        <div><span>状态</span><strong>${escape(runStatusText(data.status || '-'))}</strong></div>
        <div><span>摘要</span><strong>${escape(sanitizeCooldownCopy(data.message || '-'))}</strong></div>
        <div><span>步骤</span><strong>${escape((data.step_index || 0) + ' / ' + (data.step_total || 0))}</strong></div>
        ${result && Object.keys(result).length ? `
          <div><span>源</span><strong>${escape(result.source_snapshot_id ? `源快照 #${result.source_snapshot_id}` : (result.source_oid || '-'))}</strong></div>
          <div><span>目标</span><strong>${escape(result.target_oid || '-')}</strong></div>
          <div><span>缺失</span><strong>${escape(result.missing_count || 0)}</strong></div>
          <div><span>计划</span><strong>${escape(result.planned_count || 0)}</strong></div>
          <div><span>已发布</span><strong>${escape(result.posted_count || 0)}</strong></div>
          <div><span>待重试</span><strong>${escape(result.retry_pending_count || 0)}</strong></div>
          <div><span>失败</span><strong>${escape(result.failed_count || 0)}</strong></div>
          <div><span>阻塞</span><strong>${escape(result.blocked_count || 0)}</strong></div>
          <div><span>重对账</span><strong>${escape(result.reconciled_duplicate_count || 0)}</strong></div>
          <div><span>冷却退避</span><strong>${escape(result.cooldown_backoff_count || 0)}</strong></div>
          <div><span>模式</span><strong>${escape(result.dry_run ? '仅分析' : '真实发布')}</strong></div>
        ` : ''}
      </div>`;
    }
    return '';
  }

  function accountPicker(selectedIDs = []) {
    if (!accounts.length) return '<div class="motion-empty compact">请先扫码登录账号</div>';
    const selected = new Set((selectedIDs || []).map(Number));
    return accounts.map(item => `<label class="motion-check">
      <input type="checkbox" name="account_ids" value="${escape(item.id)}" ${selected.has(Number(item.id)) ? 'checked' : ''}>
      <span>#${escape(item.id)} ${escape(item.display_name || item.uname || item.mid)} | ${escape(accountStatusText(item))}</span>
    </label>`).join('');
  }

  function snapshotOptions(selectedID = 0) {
    if (!snapshots.length) return '<option value="">暂无快照</option>';
    return snapshots.map(item => `<option value="${escape(item.id)}" ${Number(selectedID) === Number(item.id) ? 'selected' : ''}>#${escape(item.id)} av${escape(item.oid)} ${escape(item.comment_count)} 条</option>`).join('');
  }

  function cronSelect(spec = '0 */6 * * *') {
    const known = Object.prototype.hasOwnProperty.call(cronPresets, spec);
    return `<div class="motion-row">
      <label class="motion-label">执行频率
        <select class="motion-select" name="cron_preset" data-cron-preset>
          <option value="*/30 * * * *" ${spec === '*/30 * * * *' ? 'selected' : ''}>每 30 分钟</option>
          <option value="0 * * * *" ${spec === '0 * * * *' ? 'selected' : ''}>每 1 小时</option>
          <option value="0 */6 * * *" ${spec === '0 */6 * * *' ? 'selected' : ''}>每 6 小时</option>
          <option value="0 2 * * *" ${spec === '0 2 * * *' ? 'selected' : ''}>每天 02:00</option>
          <option value="custom" ${known ? '' : 'selected'}>自定义</option>
        </select>
        <span class="bilibili-help">多久自动发起一次运行</span>
      </label>
      <label class="motion-label" data-custom-cron-wrap ${known ? 'hidden' : ''}>Cron 表达式
        <input class="motion-input" name="cron_spec" value="${escape(spec)}" ${known ? 'disabled' : ''}>
        <span class="bilibili-help">仅在选择“自定义”后生效</span>
      </label>
    </div>`;
  }

  function taskFormBody(type = 'align_backfill', options = {}) {
    const task = options.task || {};
    const payload = parsePayload(task.payload);
    const editing = Boolean(task.id);
    const selectedSnapshotID = options.snapshotID || payload.snapshot_id || 0;
    const title = task.name || taskTypeText(type);
    return `
      <form class="motion-form bilibili-task-form" id="bilibili-task-form" data-task-id="${escape(task.id || '')}" data-task-type="${escape(type)}">
        <input type="hidden" name="task_type" value="${escape(type)}">
        <label class="motion-label">任务名<input class="motion-input" name="name" value="${escape(editing ? title : taskTypeText(type))}" required></label>
        ${taskSpecificFields(type, payload, selectedSnapshotID)}
        <details class="motion-details">
          <summary>高级参数</summary>
          ${cronSelect(task.cron_spec || '0 */6 * * *')}
          ${advancedFetchOptions(payload, true)}
          ${delayedPublishOptions(type, payload)}
          <label class="motion-check"><input type="checkbox" name="dry_run" ${payload.dry_run ? 'checked' : ''}>只分析并生成补全计划</label>
          <span class="bilibili-help">不会发表评论，结果会显示在运行详情和事件列表中。</span>
        </details>
        <div class="motion-label">绑定账号<div class="motion-picker">${accountPicker(task.account_ids || [])}</div></div>
        <label class="motion-check"><input type="checkbox" name="is_active" ${editing ? (task.is_active ? 'checked' : '') : 'checked'}>启用定时任务</label>
      </form>`;
  }

  function taskSpecificFields(type, payload, selectedSnapshotID) {
    if (type === 'align_backfill') {
      return `<div class="motion-row">
        <label class="motion-label">源视频号（av/BV）<input class="motion-input" name="source_oid" value="${escape(sourceOID(payload) || '')}" placeholder="av2 或 BV1xx" required></label>
        <label class="motion-label">目标视频号（av/BV）<input class="motion-input" name="target_oid" value="${escape(targetOID(payload) || '')}" placeholder="av123456 或 BV1xx" required></label>
      </div>`;
    }
    if (type === 'fetch_snapshot') {
      return `<div class="motion-row">
        <label class="motion-label">视频号（av/BV）<input class="motion-input" name="oid" value="${escape(payload.oid || '')}" placeholder="av2 或 BV1xx" required></label>
        <label class="motion-label">存储方式
          <select class="motion-select" name="storage_mode">
            <option value="both" ${(payload.storage_mode || 'both') === 'both' ? 'selected' : ''}>JSONL + SQLite</option>
            <option value="jsonl" ${payload.storage_mode === 'jsonl' ? 'selected' : ''}>仅 JSONL</option>
            <option value="sqlite" ${payload.storage_mode === 'sqlite' ? 'selected' : ''}>仅 SQLite</option>
          </select>
        </label>
      </div>`;
    }
    if (type === 'watch_forward_roots') {
      return `<div class="motion-row">
        <label class="motion-label">源视频号（av/BV）<input class="motion-input" name="source_oid" value="${escape(sourceOID(payload) || '')}" placeholder="av2 或 BV1xx" required></label>
        <label class="motion-label">目标视频号（av/BV）<input class="motion-input" name="target_oid" value="${escape(targetOID(payload) || '')}" placeholder="av123456 或 BV1xx" required></label>
      </div>
      <label class="motion-label">监控用户名列表
        <textarea class="motion-input" name="monitor_unames" rows="4" placeholder="每行一个用户名，或用逗号分隔">${escape((payload.monitor_unames || []).join('\n'))}</textarea>
        <span class="bilibili-help">只转发这些用户名发出的源视频根评论。</span>
      </label>`;
    }
    if (type === 'sync_root_mentions') {
      return `<div class="motion-row">
        <label class="motion-label">源视频号（av/BV）<input class="motion-input" name="source_oid" value="${escape(sourceOID(payload) || '')}" placeholder="av2 或 BV1xx" required></label>
        <label class="motion-label">目标视频号（av/BV）<input class="motion-input" name="target_oid" value="${escape(targetOID(payload) || '')}" placeholder="av123456 或 BV1xx" required></label>
      </div>
      <div class="motion-row">
        <label class="motion-label">源评论内容触发词
          <input class="motion-input" name="mention_trigger_text" value="${escape(payload.mention_trigger_text || '')}" placeholder="例如 666">
          <span class="bilibili-help">留空时，所有源视频根评论用户都会参与 @ 分析；填写后，只有评论内容包含该文本的用户才参与。</span>
        </label>
        <label class="motion-label">@后缀文案
          <input class="motion-input" name="mention_suffix" value="${escape(payload.mention_suffix || '')}" placeholder="例如 请查收">
          <span class="bilibili-help">会生成 @user1 @user2 xxxxx 的评论格式，后缀只追加一次。</span>
        </label>
        <label class="motion-label">每条评论 @ 人数
          <input class="motion-input" type="number" name="mention_users_per_message" min="1" max="10" value="${Number(payload.mention_users_per_message || 10)}">
          <span class="bilibili-help">默认 10，范围 1-10；完整评论超过 500 字时会自动拆到下一条。</span>
        </label>
      </div>
      <label class="motion-check">
        <input type="checkbox" name="mention_random_chars" ${payload.mention_random_chars ? 'checked' : ''}>
        在 @ 后缀文案后追加随机字符
        <span class="bilibili-help">会在后缀后再追加一段随机字符和空格</span>
      </label>`;
    }
    return `<div class="motion-row">
      <label class="motion-label">源快照<select class="motion-select" name="snapshot_id" required>${snapshotOptions(selectedSnapshotID)}</select></label>
      <label class="motion-label">目标视频号（av/BV）<input class="motion-input" name="snapshot_target_oid" value="${escape(targetOID(payload) || '')}" placeholder="av123456 或 BV1xx" required></label>
    </div>`;
  }

  function advancedFetchOptions(payload, withCron) {
    return `<div class="motion-row">
      <label class="motion-label">源视频每页评论数<input class="motion-input" name="page_size" type="number" min="1" max="20" value="${escape(payload.page_size || 20)}"><span class="bilibili-help">只限制源视频；目标视频固定每页 20 条，范围 1-20</span></label>
      <label class="motion-label">源视频最大页数<input class="motion-input" name="max_pages" type="number" min="0" value="${escape(payload.max_pages || 0)}"><span class="bilibili-help">只限制源视频；0 表示源视频不限制，目标视频始终不限制</span></label>
      ${withCron ? '' : '<label class="motion-label">请求间隔秒数<input class="motion-input" name="request_delay_seconds" type="number" min="0" step="0.1" value="' + escape(payload.request_delay_seconds || 0) + '"><span class="bilibili-help">外部请求之间的最小等待时间</span></label>'}
    </div>`;
  }

  function delayedPublishOptions(type, payload) {
    if (!isPublishTaskType(type)) return '';
    const enabled = Boolean(payload.delayed_publish_enabled);
    const batchSize = clampDelayedPublishBatchSize(payload.delayed_publish_batch_size || 30);
    return `<div class="motion-row">
      <label class="motion-check">
        <input type="checkbox" name="delayed_publish_enabled" ${enabled ? 'checked' : ''}>
        启用延迟处理
        <span class="bilibili-help">开启后首次运行完成拉取和分析，只发送本阶段条目；后续计划时间继续发送，不重新拉取差异。</span>
      </label>
      <label class="motion-label">每阶段发送条目数
        <input class="motion-input" name="delayed_publish_batch_size" type="number" min="1" max="100" value="${escape(batchSize)}">
        <span class="bilibili-help">默认 30，范围 1-100；成功、跳过、失败、阻塞、重对账都会消耗条目，冷却待重试不消耗。</span>
      </label>
      <label class="motion-label">延迟处理执行频率
        <input class="motion-input" name="delayed_publish_cron_spec" value="${escape(payload.delayed_publish_cron_spec || '0 * * * *')}">
        <span class="bilibili-help">每阶段未发完时，按这个 Cron 继续发送；默认每 1 小时。全部发完后，下一次任务 Cron 才会重新拉取和分析。</span>
      </label>
    </div>`;
  }

  function bindTaskForm() {
    const form = document.getElementById('bilibili-task-form');
    if (!form) return;
    const preset = form.querySelector('[data-cron-preset]');
    const customWrap = form.querySelector('[data-custom-cron-wrap]');
    const customInput = form.querySelector('[name="cron_spec"]');
    preset?.addEventListener('change', () => {
      const isCustom = preset.value === 'custom';
      if (customWrap) customWrap.hidden = !isCustom;
      if (customInput) customInput.disabled = !isCustom;
      if (!isCustom && customInput) customInput.value = preset.value;
    });
  }

  function parseMonitorUNames(raw) {
    return String(raw || '')
      .split(/[\n,，]+/)
      .map(item => item.trim())
      .filter(Boolean);
  }

  function clampMentionUsersPerMessage(raw) {
    const value = Number(raw || 10);
    if (!Number.isFinite(value) || value <= 0) return 10;
    return Math.min(10, Math.max(1, Math.floor(value)));
  }

  function clampDelayedPublishBatchSize(raw) {
    const value = Number(raw || 30);
    if (!Number.isFinite(value) || value <= 0) return 30;
    return Math.min(100, Math.max(1, Math.floor(value)));
  }

  function openTaskModal(type = 'align_backfill', options = {}) {
    if (!accounts.length) {
      Mimotion.showMessage('请先扫码登录 Bilibili 账号', 'error');
      return;
    }
    if (type === 'snapshot_backfill' && !snapshots.length) {
      Mimotion.showMessage('请先拉取评论快照', 'error');
      return;
    }
    const task = options.task || {};
    Mimotion.showModal(task.id ? '编辑任务' : taskTypeText(type), taskFormBody(type, options), submitTask, {confirmText: task.id ? '保存修改' : '保存任务'});
    bindTaskForm();
  }

  function openTaskPicker() {
    if (!accounts.length) {
      Mimotion.showMessage('请先扫码登录 Bilibili 账号', 'error');
      return;
    }
    const body = `<div class="bilibili-operation-picker">
      <button class="bilibili-quick-action" type="button" data-action="open-task-modal" data-task-type="align_backfill">
        <span>新建任务</span>
        <strong>把源视频缺失评论补到目标视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-task-modal" data-task-type="fetch_snapshot">
        <span>新建任务</span>
        <strong>保存一个视频的完整评论树</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-task-modal" data-task-type="snapshot_backfill">
        <span>新建任务</span>
        <strong>用已保存快照补到新视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-task-modal" data-task-type="watch_forward_roots">
        <span>新建任务</span>
        <strong>监控指定用户名的源视频根评论，并转发到目标视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-task-modal" data-task-type="sync_root_mentions">
        <span>新建任务</span>
        <strong>按源视频根评论用户名，补齐目标视频缺失的 @ 根评论</strong>
      </button>
    </div>`;
    Mimotion.showModal('选择任务类型', body, null, {hideConfirm: true, cancelText: '关闭'});
  }

  function operationFormBody(type = 'align_backfill', options = {}) {
    const payload = options.payload || {};
    const selectedSnapshotID = options.snapshotID || payload.snapshot_id || 0;
    return `
      <form class="motion-form bilibili-task-form" id="bilibili-operation-form" data-task-type="${escape(type)}">
        <label class="motion-label">运行名称<input class="motion-input" name="display_name" value="${escape(taskTypeText(type))}" required></label>
        ${taskSpecificFields(type, payload, selectedSnapshotID)}
        <details class="motion-details">
          <summary>高级参数</summary>
          ${advancedFetchOptions(payload, false)}
          ${delayedPublishOptions(type, payload)}
          <label class="motion-check"><input type="checkbox" name="dry_run" ${payload.dry_run ? 'checked' : ''}>只分析并生成补全计划</label>
          <span class="bilibili-help">不会发表评论，结果会显示在运行详情和事件列表中。</span>
        </details>
        <div class="motion-label">使用账号<div class="motion-picker">${accountPicker([])}</div></div>
      </form>`;
  }

  function openOperationModal(type = 'align_backfill', options = {}) {
    if (!accounts.length) {
      Mimotion.showMessage('请先扫码登录 Bilibili 账号', 'error');
      return;
    }
    if (type === 'snapshot_backfill' && !snapshots.length) {
      Mimotion.showMessage('请先拉取评论快照', 'error');
      return;
    }
    Mimotion.showModal(`手动${taskTypeText(type)}`, operationFormBody(type, options), submitOperation, {confirmText: '开始运行'});
  }

  function openOperationPicker() {
    if (!accounts.length) {
      Mimotion.showMessage('请先扫码登录 Bilibili 账号', 'error');
      return;
    }
    const body = `<div class="bilibili-operation-picker">
      <button class="bilibili-quick-action" type="button" data-action="open-operation-modal" data-task-type="align_backfill">
        <span>手动评论对齐补全</span>
        <strong>把源视频缺失评论补到目标视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-operation-modal" data-task-type="fetch_snapshot">
        <span>手动拉取评论</span>
        <strong>拉取一个视频的评论并保存快照</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-operation-modal" data-task-type="snapshot_backfill">
        <span>手动用快照补全</span>
        <strong>用已保存快照补到目标视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-operation-modal" data-task-type="watch_forward_roots">
        <span>手动指定用户根评论转发</span>
        <strong>监控指定用户名的源视频根评论，并转发到目标视频</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="open-operation-modal" data-task-type="sync_root_mentions">
        <span>手动根评论用户 @ 补全</span>
        <strong>按源视频根评论用户名，补齐目标视频缺失的 @ 根评论</strong>
      </button>
    </div>`;
    Mimotion.showModal('选择手动操作', body, null, {hideConfirm: true, cancelText: '关闭'});
  }

  function bindCurrentRunHeader() {
    const head = currentRunCard?.querySelector('.bilibili-current-run-head');
    if (!head) return;
    head.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      setCurrentRunCollapsed(!isCurrentRunCollapsed());
    });
  }

  async function submitTask(button) {
    const form = document.getElementById('bilibili-task-form');
    if (!form) return;
    const type = form.dataset.taskType;
    const data = new FormData(form);
    const payload = {
      page_size: Number(data.get('page_size') || 20),
      max_pages: Number(data.get('max_pages') || 0),
      dry_run: data.has('dry_run'),
      request_delay_seconds: Number(data.get('request_delay_seconds') || 0)
    };
    if (type === 'align_backfill') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      if (!payload.source_oid || !payload.target_oid) return Mimotion.showMessage('请填写源视频号和目标视频号', 'error');
    } else if (type === 'fetch_snapshot') {
      payload.oid = String(data.get('oid') || '').trim();
      payload.storage_mode = String(data.get('storage_mode') || 'both').trim();
      if (!payload.oid) return Mimotion.showMessage('请填写视频号', 'error');
    } else if (type === 'snapshot_backfill') {
      payload.snapshot_id = Number(data.get('snapshot_id') || 0);
      payload.target_oid = String(data.get('snapshot_target_oid') || '').trim();
      if (!payload.snapshot_id || !payload.target_oid) return Mimotion.showMessage('请选择源快照并填写目标视频号', 'error');
    } else if (type === 'watch_forward_roots') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      payload.monitor_unames = parseMonitorUNames(data.get('monitor_unames'));
      if (!payload.source_oid || !payload.target_oid || !payload.monitor_unames.length) return Mimotion.showMessage('请填写源/目标视频号，并至少提供一个用户名', 'error');
    } else if (type === 'sync_root_mentions') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      payload.mention_trigger_text = String(data.get('mention_trigger_text') || '').trim();
      payload.mention_suffix = String(data.get('mention_suffix') || '').trim();
      payload.mention_random_chars = data.has('mention_random_chars');
      payload.mention_users_per_message = clampMentionUsersPerMessage(data.get('mention_users_per_message'));
      if (!payload.source_oid || !payload.target_oid) return Mimotion.showMessage('请填写源视频号和目标视频号', 'error');
    }
    if (isPublishTaskType(type)) {
      payload.delayed_publish_enabled = data.has('delayed_publish_enabled');
      if (payload.delayed_publish_enabled) {
        payload.delayed_publish_batch_size = clampDelayedPublishBatchSize(data.get('delayed_publish_batch_size'));
        payload.delayed_publish_cron_spec = String(data.get('delayed_publish_cron_spec') || '0 * * * *').trim();
      }
    }
    const accountIDs = data.getAll('account_ids').map(Number).filter(Boolean);
    if (!accountIDs.length) return Mimotion.showMessage('请选择至少一个账号', 'error');
    const cronPreset = data.get('cron_preset');
    const cronSpec = cronPreset && cronPreset !== 'custom' ? cronPreset : String(data.get('cron_spec') || '').trim();
    if (!cronSpec) return Mimotion.showMessage('请填写 Cron 表达式', 'error');

    const req = {
      name: String(data.get('name') || taskTypeText(type)).trim(),
      task_type: type,
      cron_spec: cronSpec,
      payload,
      is_active: data.has('is_active'),
      account_ids: accountIDs
    };
    const taskID = form.dataset.taskId;
    const method = taskID ? 'PUT' : 'POST';
    const url = taskID ? `/web/api/bilibili/tasks/${taskID}` : '/web/api/bilibili/tasks';
    Mimotion.setButtonLoading(button, true, '保存中...');
    try {
      await Mimotion.api(url, {method, body: JSON.stringify(req)});
      Mimotion.closeModal();
      Mimotion.showMessage('任务已保存', 'success');
      switchTab('tasks');
      await loadAll();
    } catch (error) {
      Mimotion.showMessage(error.message || '保存失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  async function submitOperation(button) {
    const form = document.getElementById('bilibili-operation-form');
    if (!form) return;
    const type = form.dataset.taskType;
    const data = new FormData(form);
    const payload = {
      page_size: Number(data.get('page_size') || 20),
      max_pages: Number(data.get('max_pages') || 0),
      dry_run: data.has('dry_run'),
      request_delay_seconds: Number(data.get('request_delay_seconds') || 0)
    };
    if (type === 'align_backfill') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      if (!payload.source_oid || !payload.target_oid) return Mimotion.showMessage('请填写源视频号和目标视频号', 'error');
    } else if (type === 'fetch_snapshot') {
      payload.oid = String(data.get('oid') || '').trim();
      payload.storage_mode = String(data.get('storage_mode') || 'both').trim();
      if (!payload.oid) return Mimotion.showMessage('请填写视频号', 'error');
    } else if (type === 'snapshot_backfill') {
      payload.snapshot_id = Number(data.get('snapshot_id') || 0);
      payload.target_oid = String(data.get('snapshot_target_oid') || '').trim();
      if (!payload.snapshot_id || !payload.target_oid) return Mimotion.showMessage('请选择源快照并填写目标视频号', 'error');
    } else if (type === 'watch_forward_roots') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      payload.monitor_unames = parseMonitorUNames(data.get('monitor_unames'));
      if (!payload.source_oid || !payload.target_oid || !payload.monitor_unames.length) return Mimotion.showMessage('请填写源/目标视频号，并至少提供一个用户名', 'error');
    } else if (type === 'sync_root_mentions') {
      payload.source_oid = String(data.get('source_oid') || '').trim();
      payload.target_oid = String(data.get('target_oid') || '').trim();
      payload.mention_trigger_text = String(data.get('mention_trigger_text') || '').trim();
      payload.mention_suffix = String(data.get('mention_suffix') || '').trim();
      payload.mention_random_chars = data.has('mention_random_chars');
      payload.mention_users_per_message = clampMentionUsersPerMessage(data.get('mention_users_per_message'));
      if (!payload.source_oid || !payload.target_oid) return Mimotion.showMessage('请填写源视频号和目标视频号', 'error');
    }
    if (isPublishTaskType(type)) {
      payload.delayed_publish_enabled = data.has('delayed_publish_enabled');
      if (payload.delayed_publish_enabled) {
        payload.delayed_publish_batch_size = clampDelayedPublishBatchSize(data.get('delayed_publish_batch_size'));
        payload.delayed_publish_cron_spec = String(data.get('delayed_publish_cron_spec') || '0 * * * *').trim();
      }
    }
    const accountIDs = data.getAll('account_ids').map(Number).filter(Boolean);
    if (!accountIDs.length) return Mimotion.showMessage('请选择至少一个账号', 'error');
    const req = {
      display_name: String(data.get('display_name') || taskTypeText(type)).trim(),
      operation_type: type,
      payload,
      account_ids: accountIDs
    };
    Mimotion.setButtonLoading(button, true, '提交中...');
    try {
      await Mimotion.api('/web/api/bilibili/operations/run', {method: 'POST', body: JSON.stringify(req)});
      Mimotion.closeModal();
      Mimotion.showMessage('运行已加入队列', 'success');
      switchTab('runs');
      await loadAll();
    } catch (error) {
      Mimotion.showMessage(error.message || '提交失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  function stopLoginTimers() {
    clearInterval(loginPollTimer);
    clearInterval(loginCountdownTimer);
    loginPollTimer = null;
    loginCountdownTimer = null;
  }

  async function openScanLogin() {
    stopLoginTimers();
    const body = `<div class="bilibili-qr-box">
      <div id="bilibili-qr"></div>
      <strong data-login-status>正在生成二维码...</strong>
      <span data-login-countdown></span>
      <button class="motion-btn small secondary" type="button" data-action="restart-scan-login" hidden>重新生成二维码</button>
    </div>`;
    Mimotion.showModal('扫码登录 Bilibili', body, null, {hideConfirm: true, cancelText: '关闭'});
    await startQRCodeSession();
  }

  async function startQRCodeSession() {
    stopLoginTimers();
    const status = document.querySelector('[data-login-status]');
    const countdown = document.querySelector('[data-login-countdown]');
    const restart = document.querySelector('[data-action="restart-scan-login"]');
    const target = document.getElementById('bilibili-qr');
    if (restart) restart.hidden = true;
    try {
      const session = await Mimotion.api('/web/api/bilibili/login-sessions', {method: 'POST'});
      if (target) {
        target.innerHTML = '';
        if (window.QRCode) {
          new QRCode(target, {text: session.url, width: 220, height: 220});
        } else {
          target.innerHTML = `<a href="${escape(session.url)}" target="_blank">打开登录二维码</a>`;
        }
      }
      if (status) status.textContent = '等待扫码';
      const expiresAt = new Date(session.expires_at || Date.now() + 180000).getTime();
      loginCountdownTimer = setInterval(() => {
        const left = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
        if (countdown) countdown.textContent = left ? `剩余 ${left} 秒` : '二维码已过期';
        if (left <= 0) {
          clearInterval(loginCountdownTimer);
          if (restart) restart.hidden = false;
        }
      }, 1000);
      loginPollTimer = setInterval(async () => {
        try {
          const result = await Mimotion.api(`/web/api/bilibili/login-sessions/${session.id}`);
          if (status) status.textContent = result.message || result.status;
          if (result.status === 'success') {
            stopLoginTimers();
            if (status) status.textContent = result.account ? `登录成功：${result.account.uname || result.account.mid}` : '登录成功';
            Mimotion.showMessage('Bilibili 账号已登录', 'success');
            setTimeout(() => Mimotion.closeModal(), 700);
            await loadAll();
          }
          if (result.status === 'expired') {
            stopLoginTimers();
            if (restart) restart.hidden = false;
          }
        } catch (error) {
          if (status) status.textContent = error.message || '轮询失败';
        }
      }, 2500);
    } catch (error) {
      if (status) status.textContent = error.message || '生成二维码失败';
      if (restart) restart.hidden = false;
      Mimotion.showMessage(error.message || '生成二维码失败', 'error');
    }
  }

  function editAccount(id) {
    const account = accounts.find(item => Number(item.id) === Number(id));
    if (!account) return;
    const body = `<form class="motion-form" id="bilibili-account-form">
      <label class="motion-label">显示名称<input class="motion-input" name="display_name" value="${escape(account.display_name || '')}"></label>
      <div class="motion-row">
        <label class="motion-label">抓取最小毫秒<input class="motion-input" name="fetch_min_delay_ms" type="number" min="2000" value="${escape(account.fetch_min_delay_ms || 2000)}"></label>
        <label class="motion-label">抓取最大毫秒<input class="motion-input" name="fetch_max_delay_ms" type="number" min="2000" value="${escape(account.fetch_max_delay_ms || 8000)}"></label>
      </div>
      <div class="motion-row">
        <label class="motion-label">发送最小毫秒<input class="motion-input" name="send_min_delay_ms" type="number" min="2000" value="${escape(account.send_min_delay_ms || 2000)}"></label>
        <label class="motion-label">发送最大毫秒<input class="motion-input" name="send_max_delay_ms" type="number" min="2000" value="${escape(account.send_max_delay_ms || 8000)}"></label>
      </div>
    </form>`;
    Mimotion.showModal('账号设置', body, async button => {
      const form = document.getElementById('bilibili-account-form');
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.fetch_min_delay_ms = Number(payload.fetch_min_delay_ms || 2000);
      payload.fetch_max_delay_ms = Number(payload.fetch_max_delay_ms || 8000);
      payload.send_min_delay_ms = Number(payload.send_min_delay_ms || 2000);
      payload.send_max_delay_ms = Number(payload.send_max_delay_ms || 8000);
      Mimotion.setButtonLoading(button, true, '保存中...');
      await Mimotion.api(`/web/api/bilibili/accounts/${id}`, {method: 'PATCH', body: JSON.stringify(payload)});
      Mimotion.closeModal();
      await loadAll();
    }, {confirmText: '保存'});
  }

  function taskByID(id) {
    return tasks.find(item => Number(item.id) === Number(id));
  }

  async function toggleTask(button, id) {
    const task = taskByID(id);
    if (!task) return;
    const req = {
      name: task.name,
      task_type: task.task_type,
      cron_spec: task.cron_spec,
      payload: parsePayload(task.payload),
      is_active: !task.is_active,
      account_ids: task.account_ids || []
    };
    Mimotion.setButtonLoading(button, true);
    try {
      await Mimotion.api(`/web/api/bilibili/tasks/${id}`, {method: 'PUT', body: JSON.stringify(req)});
      await loadAll();
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  async function duplicateTask(id) {
    const task = taskByID(id);
    if (!task) return;
    const req = {
      name: `${task.name || taskTypeText(task.task_type)} 副本`,
      task_type: task.task_type,
      cron_spec: task.cron_spec,
      payload: parsePayload(task.payload),
      is_active: false,
      account_ids: task.account_ids || []
    };
    await Mimotion.api('/web/api/bilibili/tasks', {method: 'POST', body: JSON.stringify(req)});
    Mimotion.showMessage('任务已复制，默认停用', 'success');
    await loadAll();
  }

  function openTaskActions(id) {
    const task = taskByID(id);
    if (!task) return;
    const body = `<div class="bilibili-operation-picker">
      <button class="bilibili-quick-action" type="button" data-action="run-task" data-id="${escape(task.id)}">
        <span>立即运行</span>
        <strong>立刻把这条任务加入队列</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="edit-task" data-id="${escape(task.id)}">
        <span>编辑任务</span>
        <strong>修改任务名称、调度和参数</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="duplicate-task" data-id="${escape(task.id)}">
        <span>复制任务</span>
        <strong>复制当前任务并默认停用</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="toggle-task" data-id="${escape(task.id)}">
        <span>${task.is_active ? '停用任务' : '启用任务'}</span>
        <strong>${task.is_active ? '停止后续定时调度' : '恢复定时调度'}</strong>
      </button>
      <button class="bilibili-quick-action" type="button" data-action="stop-task" data-id="${escape(task.id)}">
        <span>强制停止任务</span>
        <strong>停止当前和排队中的运行</strong>
      </button>
      <button class="bilibili-quick-action bilibili-quick-action-danger" type="button" data-action="delete-task" data-id="${escape(task.id)}">
        <span>删除任务</span>
        <strong>删除任务配置和任务状态文件</strong>
      </button>
    </div>`;
    Mimotion.showModal(task.name || taskTypeText(task.task_type), body, null, {hideConfirm: true, cancelText: '关闭'});
  }

  function selectRun(runID) {
    selectedRunID = String(runID || '');
    switchTab('events');
    return loadEvents(true);
  }

  function confirmStop(title, message, url) {
    Mimotion.confirmModal(title, message, async button => {
      Mimotion.setButtonLoading(button, true, '停止中...');
      try {
        await Mimotion.api(url, {method: 'POST'});
        Mimotion.closeModal();
        await loadAll();
      } catch (error) {
        Mimotion.showMessage(error.message || '停止失败', 'error');
      } finally {
        Mimotion.setButtonLoading(button, false);
      }
    }, {confirmText: '强制停止'});
  }

  async function loadMoreAccounts() {
    return loadAccounts(false);
  }

  async function loadMoreTasks() {
    return loadTasks(false);
  }

  async function loadMoreSnapshots() {
    return loadSnapshots(false);
  }

  async function loadMoreRuns() {
    return loadRuns(false);
  }

  document.addEventListener('click', async (event) => {
    if (event.target.closest('[data-motion-modal-close]')) {
      stopLoginTimers();
    }
    const button = event.target.closest('[data-action], [data-tab-target]');
    if (!button) return;
    const tab = button.dataset.tabTarget;
    if (tab) return switchTab(tab);
    const action = button.dataset.action;
    const id = button.dataset.id;
    try {
      if (action === 'refresh') return loadAll();
      if (action === 'refresh-events') return loadEvents(true);
      if (action === 'clear-run-filter') {
        selectedRunID = '';
        return loadEvents(true);
      }
      if (action === 'load-more-accounts') return loadMoreAccounts();
      if (action === 'load-more-tasks') return loadMoreTasks();
      if (action === 'load-more-snapshots') return loadMoreSnapshots();
      if (action === 'load-more-runs') return loadMoreRuns();
      if (action === 'load-more-events') return loadMoreEvents();
      if (action === 'scan-login') return openScanLogin();
      if (action === 'open-operation-picker') return openOperationPicker();
      if (action === 'open-task-picker') return openTaskPicker();
      if (action === 'restart-scan-login') return startQRCodeSession();
      if (action === 'open-task-modal') return openTaskModal(button.dataset.taskType || 'align_backfill');
      if (action === 'open-operation-modal') return openOperationModal(button.dataset.taskType || 'align_backfill');
      if (action === 'use-snapshot') return openOperationModal('snapshot_backfill', {snapshotID: Number(id)});
      if (action === 'validate-account') return actionRequest(button, `/web/api/bilibili/accounts/${id}/validate`, '校验完成');
      if (action === 'edit-account') return editAccount(id);
      if (action === 'delete-account') return confirmDelete('删除账号', '确认删除该 Bilibili 账号？会永久删除账号记录和凭证，但会保留历史运行、事件和快照。', `/web/api/bilibili/accounts/${id}`);
      if (action === 'run-task') return actionRequest(button, `/web/api/bilibili/tasks/${id}/run`, '任务已加入队列');
      if (action === 'task-actions') return openTaskActions(id);
      if (action === 'edit-task') return openTaskModal(taskByID(id)?.task_type || 'align_backfill', {task: taskByID(id)});
      if (action === 'duplicate-task') return duplicateTask(id);
      if (action === 'toggle-task') return toggleTask(button, id);
      if (action === 'stop-task') return confirmStop('强制停止任务', '会停止当前和排队中的运行，并停用后续 Cron。确认继续？', `/web/api/bilibili/tasks/${id}/stop`);
      if (action === 'delete-task') return confirmDelete('删除任务', '确认删除该任务？', `/web/api/bilibili/tasks/${id}`);
      if (action === 'select-run') return selectRun(button.dataset.runId);
      if (action === 'toggle-current-run') return setCurrentRunCollapsed(!isCurrentRunCollapsed());
      if (action === 'view-current-run-events') return selectRun(currentRunID);
      if (action === 'pause-current-run' && currentRunID) return actionRequest(button, `/web/api/bilibili/runs/${currentRunID}/pause`, '运行已暂停');
      if (action === 'resume-current-run' && currentRunID) return actionRequest(button, `/web/api/bilibili/runs/${currentRunID}/resume`, '运行已恢复');
      if (action === 'pause-run') return actionRequest(button, `/web/api/bilibili/runs/${id}/pause`, '运行已暂停');
      if (action === 'resume-run') return actionRequest(button, `/web/api/bilibili/runs/${id}/resume`, '运行已恢复');
      if (action === 'stop-run') return confirmStop('停止运行', '将在当前步骤结束后停止该运行。确认继续？', `/web/api/bilibili/runs/${id}/stop`);
      if (action === 'stop-current-run' && currentRunID) return confirmStop('停止运行', '将在当前步骤结束后停止该运行。确认继续？', `/web/api/bilibili/runs/${currentRunID}/stop`);
      if (action === 'logout') {
        await Mimotion.api('/web/api/logout', {method: 'POST'});
        window.location.href = '/web/bilibili/login';
      }
    } catch (error) {
      Mimotion.showMessage(error.message || '操作失败', 'error');
    }
  });

  async function actionRequest(button, url, message) {
    Mimotion.setButtonLoading(button, true);
    try {
      const data = await Mimotion.api(url, {method: 'POST'});
      Mimotion.showMessage(data.message || message, 'success');
      await loadAll();
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  function confirmDelete(title, message, url) {
    Mimotion.confirmModal(title, message, async button => {
      Mimotion.setButtonLoading(button, true, '删除中...');
      try {
        await Mimotion.api(url, {method: 'DELETE'});
        Mimotion.closeModal();
        await loadAll();
      } catch (error) {
        Mimotion.showMessage(error.message || '删除失败', 'error');
      } finally {
        Mimotion.setButtonLoading(button, false);
      }
    }, {confirmText: '删除'});
  }

  bindCurrentRunHeader();
  loadAll();
  eventTimer = setInterval(() => {
    loadEvents().catch(() => {});
    loadRuns().catch(() => {});
    loadSnapshots().catch(() => {});
  }, 3000);
  window.addEventListener('beforeunload', () => {
    clearInterval(eventTimer);
    stopLoginTimers();
  });
}());
