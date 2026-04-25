(function () {
  const accountsBody = document.querySelector('#accounts-table tbody');
  const jobsBody = document.querySelector('#jobs-table tbody');
  const runsBody = document.querySelector('#runs-table tbody');
  const accountsEmpty = document.getElementById('accounts-empty');
  const jobsEmpty = document.getElementById('jobs-empty');
  const runsEmpty = document.getElementById('runs-empty');
  const accountCount = document.getElementById('account-count');
  const jobCount = document.getElementById('job-count');
  const runCount = document.getElementById('run-count');
  const successCount = document.getElementById('success-count');
  const adminLink = document.querySelector('[data-admin-link]');
  const pageAlert = document.querySelector('[data-page-alert]');
  let accountsCache = [];
  let jobsCache = [];

  const optionHelp = {
    active: '加入任务执行：开启后，这个账号会参与手动执行和定时任务。',
    persist: '缓存登录状态：保存 Zepp 登录 token，减少重复登录。加密密钥由系统自动管理。',
    concurrent: '多账号同时执行：多个账号一起跑，速度更快；账号多时建议谨慎开启。',
    device: '固定 device_id：高级兼容项，不确定时留空。',
    sleep: '间隔秒数：单账号执行过程中的等待间隔。',
    push: 'PushPlus 小时：限制推送通知出现的小时段，不需要可留空。'
  };

  function setRefreshLoading(loading) {
    document.querySelectorAll('[data-refresh-btn]').forEach(btn => Mimotion.setButtonLoading(btn, loading, '刷新中...'));
  }

  function setTableVisibility(tableBody, emptyEl, hasRows, emptyMessage) {
    const table = tableBody.closest('.motion-table-wrap');
    table.style.display = hasRows ? '' : 'none';
    emptyEl.style.display = hasRows ? 'none' : '';
    if (!hasRows) Mimotion.renderEmpty(emptyEl, emptyMessage);
  }

  function renderEmptyAction(emptyEl, message, action, label) {
    emptyEl.innerHTML = `<div class="motion-empty">${Mimotion.escapeHTML(message)}<div class="motion-empty-actions"><button class="motion-btn small" type="button" data-action="${Mimotion.escapeHTML(action)}">${Mimotion.escapeHTML(label)}</button></div></div>`;
  }

  function syncCounts() {
    const successRuns = Number(successCount.textContent || 0);
    accountCount.textContent = accountsCache.length;
    jobCount.textContent = jobsCache.length;
    successCount.textContent = successRuns;
  }

  function isGenericFailureMessage(message) {
    const text = String(message || '').trim();
    return !text || ['登陆失败！', '登录失败！', '账号校验失败', '账号校验异常', '执行异常', 'runner 执行异常'].includes(text);
  }

  function logsDetail(logs) {
    const lines = String(logs || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const line = lines[index];
      if (line.startsWith('Traceback')) continue;
      if (line.startsWith('File ') || line.startsWith('line ')) continue;
      return line;
    }
    return '';
  }

  function effectiveRunMessage(item) {
    const message = String(item.message || '').trim();
    const detail = logsDetail(item.logs);
    if (isGenericFailureMessage(message) && detail) return detail;
    return message || detail || '-';
  }

  async function loadCurrentUser() {
    try {
      const user = await Mimotion.api('/web/api/mimotion/me');
      if (adminLink && user.is_admin) adminLink.hidden = false;
    } catch (_) {
      if (adminLink) adminLink.hidden = true;
    }
  }

  async function loadAccounts() {
    const data = await Mimotion.api('/web/api/mimotion/accounts');
    accountsCache = data.accounts || [];
    accountsBody.innerHTML = accountsCache.map(item => {
      const status = item.last_run_status || item.last_validation_status || '';
      const message = item.last_run_message || item.last_validation_message || item.remark || '';
      return `<tr>
        <td data-label="ID"><span class="motion-cell-title">#${Mimotion.escapeHTML(item.id)}</span><span class="motion-cell-sub">${item.is_active ? '已加入' : '未加入'}</span></td>
        <td data-label="账号"><span class="motion-cell-title">${Mimotion.escapeHTML(item.login_name)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML(message || '-')}</span></td>
        <td data-label="步数">${Mimotion.escapeHTML(item.min_step)} - ${Mimotion.escapeHTML(item.max_step)}</td>
        <td data-label="状态">${Mimotion.statusBadge(status)}</td>
        <td data-label="操作"><div class="motion-inline-actions">
          <button class="motion-btn small" data-action="run-account" data-id="${item.id}">执行</button>
          <button class="motion-btn small secondary" data-action="validate-account" data-id="${item.id}">校验</button>
          <button class="motion-btn small danger" data-action="delete-account" data-id="${item.id}">删除</button>
        </div></td>
      </tr>`;
    }).join('');
    setTableVisibility(accountsBody, accountsEmpty, accountsCache.length > 0, '无账号');
    if (!accountsCache.length) renderEmptyAction(accountsEmpty, '无账号', 'open-account-modal', '新增账号');
  }

  async function loadJobs() {
    const data = await Mimotion.api('/web/api/mimotion/jobs');
    jobsCache = data.jobs || [];
    jobsBody.innerHTML = jobsCache.map(item => `<tr>
      <td data-label="ID"><span class="motion-cell-title">#${Mimotion.escapeHTML(item.id)}</span><span class="motion-cell-sub">${item.is_active ? '启用' : '停用'}</span></td>
      <td data-label="任务"><span class="motion-cell-title">${Mimotion.escapeHTML(item.name)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML((item.account_ids || []).join(', ') || '-')}</span></td>
      <td data-label="调度">${Mimotion.escapeHTML(item.schedule_spec || '手动')}</td>
      <td data-label="状态">${Mimotion.statusBadge(item.last_run_status || '')}</td>
      <td data-label="操作"><div class="motion-inline-actions">
        <button class="motion-btn small" data-action="run-job" data-id="${item.id}">执行</button>
        <button class="motion-btn small danger" data-action="delete-job" data-id="${item.id}">删除</button>
      </div></td>
    </tr>`).join('');
    setTableVisibility(jobsBody, jobsEmpty, jobsCache.length > 0, '无任务');
    if (!jobsCache.length) renderEmptyAction(jobsEmpty, '无任务', 'open-job-modal', '新增任务');
  }

  async function loadRuns() {
    const data = await Mimotion.api('/web/api/mimotion/runs');
    const runs = data.runs || [];
    runCount.textContent = runs.length;
    successCount.textContent = runs.filter(item => item.status === 'success').length;
    runsBody.innerHTML = runs.map(item => `<tr>
      <td data-label="ID">#${Mimotion.escapeHTML(item.id)}</td>
      <td data-label="账号"><span class="motion-cell-title">${Mimotion.escapeHTML(item.login_name)}</span><span class="motion-cell-sub">${Mimotion.escapeHTML(item.summary || '')}</span></td>
      <td data-label="状态">${Mimotion.statusBadge(item.status)}</td>
      <td data-label="消息">${Mimotion.escapeHTML(effectiveRunMessage(item))}</td>
      <td data-label="时间">${Mimotion.escapeHTML(item.started_at || '-')}</td>
    </tr>`).join('');
    setTableVisibility(runsBody, runsEmpty, runs.length > 0, '无记录');
  }

  async function loadAll() {
    setRefreshLoading(true);
    Mimotion.setAlert(pageAlert, '', '');
    try {
      await loadAccounts();
      await Promise.all([loadJobs(), loadRuns()]);
      syncCounts();
    } catch (error) {
      Mimotion.setAlert(pageAlert, error.message || '加载失败', 'error');
      Mimotion.showMessage(error.message || '加载失败', 'error');
    } finally {
      setRefreshLoading(false);
    }
  }

  function helpButton(key) {
    return `<button class="motion-help-btn" type="button" data-action="show-help" data-help-key="${key}">?</button>`;
  }

  function accountFormBody() {
    return `
      <form class="motion-form" id="motion-account-modal-form">
        <label class="motion-label">小米 / Zepp 账号
          <input class="motion-input" name="login_name" autocomplete="off" required>
        </label>
        <label class="motion-label">密码
          <input class="motion-input" name="password" type="password" autocomplete="off" required>
        </label>
        <div class="motion-row">
          <label class="motion-label">最小步数
            <input class="motion-input" name="min_step" type="number" min="1" value="18000">
          </label>
          <label class="motion-label">最大步数
            <input class="motion-input" name="max_step" type="number" min="1" value="25000">
          </label>
        </div>
        <label class="motion-label">备注
          <input class="motion-input" name="remark">
        </label>
        <label class="motion-check"><input type="checkbox" name="is_active" checked>加入任务执行 ${helpButton('active')}</label>
        <details class="motion-details">
          <summary>高级设置</summary>
          <div class="motion-form">
            <div class="motion-row">
              <label class="motion-label">间隔秒数 ${helpButton('sleep')}
                <input class="motion-input" name="sleep_seconds" type="number" min="0" step="0.1" value="5">
              </label>
              <label class="motion-label">PushPlus 小时 ${helpButton('push')}
                <input class="motion-input" name="push_plus_hour">
              </label>
            </div>
            <label class="motion-label">固定 device_id ${helpButton('device')}
              <input class="motion-input" name="device_id">
            </label>
            <div class="motion-row">
              <label class="motion-check"><input type="checkbox" name="persist_tokens">缓存登录状态 ${helpButton('persist')}</label>
              <label class="motion-check"><input type="checkbox" name="use_concurrent">多账号同时执行 ${helpButton('concurrent')}</label>
            </div>
          </div>
        </details>
      </form>
    `;
  }

  function jobAccountPicker() {
    if (!accountsCache.length) return '<div class="motion-empty compact">无账号</div>';
    return accountsCache.map(item => `<label class="motion-check">
      <input type="checkbox" name="account_ids" value="${item.id}">
      <span>#${Mimotion.escapeHTML(item.id)} ${Mimotion.escapeHTML(item.login_name)}</span>
    </label>`).join('');
  }

  function jobFormBody() {
    return `
      <form class="motion-form" id="motion-job-modal-form">
        <label class="motion-label">任务名
          <input class="motion-input" name="name" required>
        </label>
        <div class="motion-row">
          <label class="motion-label">调度表达式
            <input class="motion-input" name="schedule_spec" placeholder="@every 6h 或 daily:09:30">
          </label>
          <label class="motion-label">超时秒数
            <input class="motion-input" name="timeout_seconds" type="number" min="1" value="180">
          </label>
        </div>
        <div class="motion-label">绑定账号
          <div class="motion-picker">${jobAccountPicker()}</div>
        </div>
        <label class="motion-check"><input type="checkbox" name="is_active" checked>启用任务</label>
      </form>
    `;
  }

  async function submitAccount(button) {
    const formEl = document.getElementById('motion-account-modal-form');
    if (!formEl) return;
    Mimotion.setButtonLoading(button, true, '保存中...');
    try {
      const form = new FormData(formEl);
      const payload = Object.fromEntries(form.entries());
      payload.min_step = Number(payload.min_step || 18000);
      payload.max_step = Number(payload.max_step || 25000);
      payload.sleep_seconds = Number(payload.sleep_seconds || 5);
      payload.push_plus_max = Number(payload.push_plus_max || 30);
      payload.is_active = form.has('is_active');
      payload.use_concurrent = form.has('use_concurrent');
      payload.persist_tokens = form.has('persist_tokens');
      await Mimotion.api('/web/api/mimotion/accounts', {method: 'POST', body: JSON.stringify(payload)});
      Mimotion.closeModal();
      Mimotion.showMessage('账号已保存', 'success');
      await loadAll();
      Mimotion.switchView('accounts');
    } catch (error) {
      Mimotion.showMessage(error.message || '保存失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  async function submitJob(button) {
    const formEl = document.getElementById('motion-job-modal-form');
    if (!formEl) return;
    Mimotion.setButtonLoading(button, true, '保存中...');
    try {
      const form = new FormData(formEl);
      const payload = Object.fromEntries(form.entries());
      payload.timeout_seconds = Number(payload.timeout_seconds || 180);
      payload.is_active = form.has('is_active');
      payload.account_ids = form.getAll('account_ids').map(Number).filter(Boolean);
      await Mimotion.api('/web/api/mimotion/jobs', {method: 'POST', body: JSON.stringify(payload)});
      Mimotion.closeModal();
      Mimotion.showMessage('任务已保存', 'success');
      await loadAll();
      Mimotion.switchView('jobs');
    } catch (error) {
      Mimotion.showMessage(error.message || '保存失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  async function actionRequest(button, url, options = {}, successMessage = '操作完成') {
    Mimotion.setButtonLoading(button, true);
    try {
      const data = await Mimotion.api(url, options);
      Mimotion.showMessage(data.message || data.summary || successMessage, data.success === false ? 'error' : 'success');
      await loadAll();
    } catch (error) {
      Mimotion.showMessage(error.message || '操作失败', 'error');
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  async function runAll(button) {
    const activeAccounts = accountsCache.filter(item => item.is_active);
    if (!activeAccounts.length) {
      Mimotion.showMessage('没有可执行账号', 'error');
      return;
    }
    Mimotion.setButtonLoading(button, true, '执行中...');
    try {
      for (const account of activeAccounts) {
        await Mimotion.api(`/web/api/mimotion/accounts/${account.id}/run`, {method: 'POST'});
      }
      Mimotion.showMessage('执行完成', 'success');
      await loadAll();
    } catch (error) {
      Mimotion.showMessage(error.message || '执行失败', 'error');
      await loadAll();
    } finally {
      Mimotion.setButtonLoading(button, false);
    }
  }

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const id = button.dataset.id;

    if (action === 'back') {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = '/web/mimotion/dashboard';
      }
      return;
    }
    if (action === 'admin') {
      window.location.href = '/web/admin/mimotion/';
      return;
    }
    if (action === 'refresh') return loadAll();
    if (action === 'open-account-modal') return Mimotion.showModal('新增账号', accountFormBody(), submitAccount, {confirmText: '保存账号'});
    if (action === 'open-job-modal') return Mimotion.showModal('新增任务', jobFormBody(), submitJob, {confirmText: '保存任务'});
    if (action === 'show-help') {
      const text = optionHelp[button.dataset.helpKey] || '';
      return Mimotion.showMessage(text, 'info');
    }
    if (action === 'run-all') return runAll(button);
    if (action === 'logout') {
      Mimotion.confirmModal('退出登录', '确认退出登录？', async confirmBtn => {
        Mimotion.setButtonLoading(confirmBtn, true, '退出中...');
        await Mimotion.api('/web/api/mimotion/logout', {method: 'POST'});
        window.location.href = '/web/mimotion/login';
      }, {confirmText: '退出登录'});
      return;
    }
    if (action === 'delete-account') {
      return Mimotion.confirmModal('确认删除', '确定删除这个账号？', async confirmBtn => {
        await actionRequest(confirmBtn, `/web/api/mimotion/accounts/${id}`, {method: 'DELETE'}, '账号已删除');
        Mimotion.closeModal();
      }, {confirmText: '删除'});
    }
    if (action === 'delete-job') {
      return Mimotion.confirmModal('确认删除', '确定删除这个任务？', async confirmBtn => {
        await actionRequest(confirmBtn, `/web/api/mimotion/jobs/${id}`, {method: 'DELETE'}, '任务已删除');
        Mimotion.closeModal();
      }, {confirmText: '删除'});
    }

    const routes = {
      'run-account': [`/web/api/mimotion/accounts/${id}/run`, {method: 'POST'}, '执行完成'],
      'validate-account': [`/web/api/mimotion/accounts/${id}/validate`, {method: 'POST'}, '校验完成'],
      'run-job': [`/web/api/mimotion/jobs/${id}/run`, {method: 'POST'}, '任务执行完成']
    };
    if (routes[action]) await actionRequest(button, ...routes[action]);
  });

  loadCurrentUser();
  loadAll();
}());
