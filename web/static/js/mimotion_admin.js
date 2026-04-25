(function () {
  const overviewGrid = document.getElementById('overview-grid');
  const usersBody = document.querySelector('#users-table tbody');
  const accountsBody = document.querySelector('#accounts-table tbody');
  const jobsBody = document.querySelector('#jobs-table tbody');
  const logsBody = document.querySelector('#logs-table tbody');
  const usersEmpty = document.getElementById('users-empty');
  const accountsEmpty = document.getElementById('accounts-empty');
  const jobsEmpty = document.getElementById('jobs-empty');
  const logsEmpty = document.getElementById('logs-empty');
  const userCount = document.getElementById('home-user-count');
  const accountCount = document.getElementById('home-account-count');
  const jobCount = document.getElementById('home-job-count');
  const pageAlert = document.querySelector('[data-page-alert]');

  function setTableVisibility(tableBody, emptyEl, hasRows, emptyMessage) {
    const table = tableBody.closest('.motion-table-wrap');
    table.style.display = hasRows ? '' : 'none';
    emptyEl.style.display = hasRows ? 'none' : '';
    if (!hasRows) Mimotion.renderEmpty(emptyEl, emptyMessage);
  }

  function setRefreshLoading(loading) {
    document.querySelectorAll('[data-refresh-btn]').forEach(btn => Mimotion.setButtonLoading(btn, loading, '刷新中...'));
  }

  async function loadOverview() {
    const data = await Mimotion.api('/web/admin/mimotion/api/overview');
    const cards = [
      ['用户', data.total_users || 0],
      ['账号数量', data.total_accounts || 0],
      ['任务数量', data.total_jobs || 0],
      ['同步记录', data.total_sync_logs || 0]
    ];
    overviewGrid.innerHTML = cards.map(([label, value]) => `<div class="motion-stat"><strong>${Mimotion.escapeHTML(value)}</strong><span>${Mimotion.escapeHTML(label)}</span></div>`).join('');
    userCount.textContent = data.total_users || 0;
    accountCount.textContent = data.total_accounts || 0;
    jobCount.textContent = data.total_jobs || 0;
  }

  async function loadUsers() {
    const data = await Mimotion.api('/web/admin/mimotion/api/users');
    const users = data.users || [];
    usersBody.innerHTML = users.map(item => `<tr>
      <td data-label="ID">#${Mimotion.escapeHTML(item.id)}</td>
      <td data-label="用户名"><span class="motion-cell-title">${Mimotion.escapeHTML(item.username)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML(item.created_at || '')}</span></td>
      <td data-label="状态">${Mimotion.statusBadge(item.status || 'approved')}</td>
    </tr>`).join('');
    setTableVisibility(usersBody, usersEmpty, users.length > 0, '无用户');
  }

  async function loadAccounts() {
    const data = await Mimotion.api('/web/api/mimotion/accounts?scope=all');
    const accounts = data.accounts || [];
    accountsBody.innerHTML = accounts.map(item => `<tr>
      <td data-label="ID">#${Mimotion.escapeHTML(item.id)}</td>
      <td data-label="用户">#${Mimotion.escapeHTML(item.user_id || '-')}</td>
      <td data-label="账号"><span class="motion-cell-title">${Mimotion.escapeHTML(item.login_name)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML(item.remark || '')}</span></td>
      <td data-label="状态">${Mimotion.statusBadge(item.last_run_status || item.last_validation_status || '')}</td>
      <td data-label="步数">${Mimotion.escapeHTML(item.min_step)} - ${Mimotion.escapeHTML(item.max_step)}</td>
    </tr>`).join('');
    setTableVisibility(accountsBody, accountsEmpty, accounts.length > 0, '无账号');
  }

  async function loadJobs() {
    const data = await Mimotion.api('/web/api/mimotion/jobs?scope=all');
    const jobs = data.jobs || [];
    jobsBody.innerHTML = jobs.map(item => `<tr>
      <td data-label="ID">#${Mimotion.escapeHTML(item.id)}</td>
      <td data-label="用户">#${Mimotion.escapeHTML(item.user_id || '-')}</td>
      <td data-label="任务"><span class="motion-cell-title">${Mimotion.escapeHTML(item.name)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML((item.account_ids || []).join(', ') || '-')}</span></td>
      <td data-label="调度">${Mimotion.escapeHTML(item.schedule_spec || '手动')}</td>
      <td data-label="状态">${Mimotion.statusBadge(item.last_run_status || '')}</td>
    </tr>`).join('');
    setTableVisibility(jobsBody, jobsEmpty, jobs.length > 0, '无任务');
  }

  async function loadLogs() {
    const data = await Mimotion.api('/web/admin/mimotion/api/repo-sync-logs');
    const logs = data.logs || [];
    logsBody.innerHTML = logs.map(item => `<tr>
      <td data-label="ID">#${Mimotion.escapeHTML(item.id)}</td>
      <td data-label="状态">${Mimotion.statusBadge(item.status)}</td>
      <td data-label="Commit"><span class="motion-cell-title">${Mimotion.escapeHTML((item.from_commit || '').slice(0, 8) || '-')} -> ${Mimotion.escapeHTML((item.to_commit || '').slice(0, 8) || '-')}</span></td>
      <td data-label="消息">${Mimotion.escapeHTML(item.message || '-')}</td>
      <td data-label="时间">${Mimotion.escapeHTML(item.created_at || '-')}</td>
    </tr>`).join('');
    setTableVisibility(logsBody, logsEmpty, logs.length > 0, '无记录');
  }

  async function loadAll() {
    setRefreshLoading(true);
    Mimotion.setAlert(pageAlert, '', '');
    try {
      await Promise.all([loadOverview(), loadUsers(), loadAccounts(), loadJobs(), loadLogs()]);
    } catch (error) {
      Mimotion.setAlert(pageAlert, error.message || '加载失败', 'error');
      Mimotion.showMessage(error.message || '加载失败', 'error');
    } finally {
      setRefreshLoading(false);
    }
  }

  async function syncRepo(button) {
    Mimotion.setButtonLoading(button, true, '同步中...');
    try {
      const data = await Mimotion.api('/web/admin/mimotion/api/repo-sync', {method: 'POST', body: JSON.stringify({})});
      Mimotion.closeModal();
      Mimotion.showMessage(data.message || data.summary || '同步完成', data.success === false ? 'error' : 'success');
      await loadAll();
      Mimotion.switchView('logs');
    } catch (error) {
      Mimotion.showMessage(error.message || '同步失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    if (action === 'back') {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = '/web/mimotion/dashboard';
      }
      return;
    }
    if (action === 'dashboard') {
      window.location.href = '/web/mimotion/dashboard';
      return;
    }
    if (action === 'show-view') return Mimotion.switchView(button.dataset.viewTarget || 'home');
    if (action === 'refresh') return loadAll();
    if (action === 'sync') {
      return Mimotion.confirmModal('同步上游', '确认执行仓库同步？', confirmBtn => syncRepo(confirmBtn), {confirmText: '开始同步'});
    }
  });

  loadAll();
}());
