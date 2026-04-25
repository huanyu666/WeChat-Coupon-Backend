(function () {
  const form = document.querySelector('[data-bilibili-auth-form]');
  if (!form) return;
  const alertEl = document.querySelector('[data-auth-alert]');
  const captchaImg = document.getElementById('captcha-img');
  const captchaInput = document.getElementById('captcha-id');
  const captchaTextInput = form.querySelector('[name="captcha"]');
  const submitBtn = form.querySelector('[type="submit"]');

  async function refresh() {
    await Mimotion.refreshCaptcha({img: captchaImg, input: captchaInput, alertEl});
  }

  captchaImg?.addEventListener('click', refresh);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    Mimotion.setAlert(alertEl, '', '');
    Mimotion.setButtonLoading(submitBtn, true, '正在登录...');
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.username = String(payload.username || '').trim();
      payload.captcha = String(payload.captcha || '').trim().toUpperCase();
      payload.password = await Mimotion.sha256(payload.password || '');
      const data = await Mimotion.api('/web/api/login', {method: 'POST', body: JSON.stringify(payload)});
      if (!data.user || !data.user.is_admin) {
        throw new Error('仅主站管理员可使用');
      }
      window.location.href = '/web/bilibili/dashboard';
    } catch (error) {
      const message = error.message || '登录失败';
      Mimotion.setAlert(alertEl, message, 'error');
      Mimotion.showMessage(message, 'error');
      if (captchaTextInput) {
        captchaTextInput.value = '';
        captchaTextInput.focus();
      }
      await refresh();
    } finally {
      Mimotion.setButtonLoading(submitBtn, false);
    }
  });

  refresh();
}());
