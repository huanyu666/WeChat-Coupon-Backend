(function () {
  const form = document.querySelector('[data-mimotion-auth-form]');
  if (!form) return;

  const mode = form.dataset.mode || 'login';
  const alertEl = document.querySelector('[data-auth-alert]');
  const captchaImg = document.getElementById('captcha-img');
  const captchaInput = document.getElementById('captcha-id');
  const captchaTextInput = form.querySelector('[name="captcha"]');
  const submitBtn = form.querySelector('[type="submit"]');

  async function refreshInlineCaptcha() {
    await Mimotion.refreshCaptcha({img: captchaImg, input: captchaInput, alertEl});
  }

  async function buildBasePayload() {
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.username = String(payload.username || '').trim();
    payload.password = await Mimotion.sha256(payload.password || '');
    if (mode === 'register') {
      payload.captcha = String(payload.captcha || '').trim().toUpperCase();
    } else {
      delete payload.captcha;
      delete payload.captcha_id;
    }
    return payload;
  }

  async function submitLoginPayload(payload) {
    return Mimotion.api('/web/api/mimotion/login', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }

  async function completeLogin(data) {
    Mimotion.toast(data.message || '登录成功', 'success');
    window.location.href = '/web/mimotion/dashboard';
  }

  async function openAdminCaptcha(payload) {
    const body = `
      <div class="motion-alert" data-admin-captcha-alert></div>
      <div class="motion-captcha-row">
        <label class="motion-label">验证码
          <input class="motion-input" name="admin_captcha" autocomplete="off" inputmode="text" required>
        </label>
        <img class="motion-captcha" id="admin-captcha-img" alt="验证码">
      </div>
      <input type="hidden" id="admin-captcha-id">
    `;
    const overlay = Mimotion.showModal('管理员验证', body, async button => {
      const modalAlert = overlay.querySelector('[data-admin-captcha-alert]');
      const modalInput = overlay.querySelector('[name="admin_captcha"]');
      const modalCaptchaID = overlay.querySelector('#admin-captcha-id');
      const captcha = String(modalInput?.value || '').trim().toUpperCase();
      const captchaID = String(modalCaptchaID?.value || '').trim();
      if (!captcha || !captchaID) {
        Mimotion.setAlert(modalAlert, '请输入验证码', 'error');
        modalInput?.focus();
        return;
      }
      Mimotion.setButtonLoading(button, true, '验证中...');
      try {
        const data = await submitLoginPayload(Object.assign({}, payload, {
          captcha,
          captcha_id: captchaID
        }));
        Mimotion.closeModal();
        await completeLogin(data);
      } catch (error) {
        Mimotion.setAlert(modalAlert, error.message || '验证码错误', 'error');
        if (modalInput) {
          modalInput.value = '';
          modalInput.focus();
        }
        await Mimotion.refreshCaptcha({
          img: overlay.querySelector('#admin-captcha-img'),
          input: modalCaptchaID,
          alertEl: modalAlert
        });
      } finally {
        Mimotion.setButtonLoading(button, false);
      }
    }, {confirmText: '验证登录'});

    await Mimotion.refreshCaptcha({
      img: overlay.querySelector('#admin-captcha-img'),
      input: overlay.querySelector('#admin-captcha-id'),
      alertEl: overlay.querySelector('[data-admin-captcha-alert]')
    });
    overlay.querySelector('#admin-captcha-img')?.addEventListener('click', () => {
      Mimotion.refreshCaptcha({
        img: overlay.querySelector('#admin-captcha-img'),
        input: overlay.querySelector('#admin-captcha-id'),
        alertEl: overlay.querySelector('[data-admin-captcha-alert]')
      });
    });
  }

  captchaImg?.addEventListener('click', refreshInlineCaptcha);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    Mimotion.setAlert(alertEl, '', '');
    Mimotion.setButtonLoading(submitBtn, true, mode === 'login' ? '正在登录...' : '正在提交...');
    try {
      const payload = await buildBasePayload();
      const endpoint = mode === 'login' ? '/web/api/mimotion/login' : '/web/api/mimotion/register';
      const data = await Mimotion.api(endpoint, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (mode === 'register') {
        Mimotion.setAlert(alertEl, data.message || '注册成功', 'success');
        setTimeout(() => { window.location.href = '/web/mimotion/login'; }, 850);
        return;
      }
      await completeLogin(data);
    } catch (error) {
      if (mode === 'login' && error.captcha_required) {
        Mimotion.setAlert(alertEl, '', '');
        await openAdminCaptcha(await buildBasePayload());
        return;
      }
      const message = error.message || '操作失败';
      Mimotion.setAlert(alertEl, message, 'error');
      Mimotion.showMessage(message, 'error');
      if (mode === 'register') {
        if (captchaTextInput) {
          captchaTextInput.value = '';
          captchaTextInput.focus();
        }
        await refreshInlineCaptcha();
      }
    } finally {
      Mimotion.setButtonLoading(submitBtn, false);
    }
  });

  if (mode === 'register') refreshInlineCaptcha();
}());
