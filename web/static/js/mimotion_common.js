(function () {
  const textEncoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;

  function utf8Bytes(text) {
    if (textEncoder) return textEncoder.encode(text);
    const encoded = unescape(encodeURIComponent(text));
    const out = new Uint8Array(encoded.length);
    for (let i = 0; i < encoded.length; i += 1) out[i] = encoded.charCodeAt(i);
    return out;
  }

  function rightRotate(value, amount) {
    return (value >>> amount) | (value << (32 - amount));
  }

  function sha256Fallback(text) {
    const bytes = utf8Bytes(text);
    const k = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ];
    const h = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    ];
    const bitLength = bytes.length * 8;
    const paddedLength = (((bytes.length + 9 + 63) >> 6) << 6);
    const padded = new Uint8Array(paddedLength);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(paddedLength - 4, bitLength >>> 0);
    view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000));

    const w = new Uint32Array(64);
    for (let offset = 0; offset < paddedLength; offset += 64) {
      for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i += 1) {
        const s0 = rightRotate(w[i - 15], 7) ^ rightRotate(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rightRotate(w[i - 2], 17) ^ rightRotate(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (let i = 0; i < 64; i += 1) {
        const s1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
        const ch = (e & f) ^ (~e & g);
        const temp1 = (hh + s1 + ch + k[i] + w[i]) >>> 0;
        const s0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (s0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
      }
      h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
      h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
    }
    return Array.from(h).map(v => v.toString(16).padStart(8, '0')).join('');
  }

  async function sha256(text) {
    if (window.crypto && window.crypto.subtle && textEncoder) {
      const hash = await window.crypto.subtle.digest('SHA-256', textEncoder.encode(text));
      return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
    }
    return sha256Fallback(text);
  }

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  async function api(url, options = {}) {
    const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
    const resp = await fetch(url, Object.assign({credentials: 'same-origin', headers}, options));
    const text = await resp.text();
    let data = {};
    if (text) {
      try { data = JSON.parse(text); } catch (_) { data = {message: text}; }
    }
    if (!resp.ok) {
      const error = new Error(data.error || data.message || '请求失败');
      Object.assign(error, data);
      error.status = resp.status;
      throw error;
    }
    return data;
  }

  function showMessage(message, type = '') {
    toast(message, type);
  }

  function setAlert(el, message, type) {
    if (!el) return;
    el.textContent = message || '';
    el.className = `motion-alert ${type || ''} ${message ? 'show' : ''}`.trim();
  }

  let toastTimer = null;
  function toast(message, type = '') {
    let el = document.querySelector('.motion-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'motion-toast';
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = `motion-toast ${type} show`.trim();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  let modalConfirmHandler = null;

  function ensureModal() {
    let overlay = document.querySelector('[data-motion-modal]');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'motion-modal-overlay';
    overlay.dataset.motionModal = 'true';
    overlay.innerHTML = `
      <div class="motion-modal" role="dialog" aria-modal="true">
        <div class="motion-modal-head">
          <h2 data-motion-modal-title></h2>
          <button class="motion-icon-btn" type="button" data-motion-modal-close aria-label="关闭">×</button>
        </div>
        <div class="motion-modal-body" data-motion-modal-body></div>
        <div class="motion-modal-actions">
          <button class="motion-btn secondary" type="button" data-motion-modal-close>取消</button>
          <button class="motion-btn" type="button" data-motion-modal-confirm>确认</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', event => {
      if (event.target === overlay || event.target.closest('[data-motion-modal-close]')) closeModal();
      const confirmBtn = event.target.closest('[data-motion-modal-confirm]');
      if (confirmBtn && modalConfirmHandler) modalConfirmHandler(confirmBtn);
    });
    return overlay;
  }

  function showModal(title, body, onConfirm, options = {}) {
    const overlay = ensureModal();
    overlay.querySelector('[data-motion-modal-title]').textContent = title || '';
    overlay.querySelector('[data-motion-modal-body]').innerHTML = body || '';
    const confirmBtn = overlay.querySelector('[data-motion-modal-confirm]');
    const cancelBtn = overlay.querySelector('.motion-modal-actions [data-motion-modal-close]');
    confirmBtn.textContent = options.confirmText || '确认';
    cancelBtn.textContent = options.cancelText || '取消';
    confirmBtn.style.display = options.hideConfirm ? 'none' : '';
    cancelBtn.style.display = options.hideCancel ? 'none' : '';
    modalConfirmHandler = typeof onConfirm === 'function' ? onConfirm : null;
    overlay.classList.add('show');
    document.body.classList.add('motion-modal-open');
    const firstInput = overlay.querySelector('input, textarea, select, button');
    if (firstInput) setTimeout(() => firstInput.focus(), 30);
    return overlay;
  }

  function closeModal() {
    const overlay = document.querySelector('[data-motion-modal]');
    if (!overlay) return;
    overlay.classList.remove('show');
    document.body.classList.remove('motion-modal-open');
    modalConfirmHandler = null;
  }

  function confirmModal(title, body, onConfirm, options = {}) {
    return showModal(title, body, async button => {
      if (typeof onConfirm === 'function') await onConfirm(button);
    }, Object.assign({confirmText: '确认'}, options));
  }

  function switchView(viewName, root = document) {
    root.querySelectorAll('[data-view]').forEach(view => {
      view.classList.toggle('active', view.dataset.view === viewName);
    });
    root.querySelectorAll('[data-view-link]').forEach(link => {
      link.classList.toggle('active', link.dataset.viewLink === viewName);
    });
  }

  function collectForm(form) {
    return new FormData(form);
  }


  function setButtonLoading(button, loading, text) {
    if (!button) return;
    if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
    button.disabled = Boolean(loading);
    button.classList.toggle('is-loading', Boolean(loading));
    button.textContent = loading ? (text || '处理中...') : button.dataset.originalText;
  }

  async function refreshCaptcha({img, input, alertEl} = {}) {
    if (!img || !input) return;
    try {
      const resp = await fetch(`/web/api/captcha?t=${Date.now()}`, {
        credentials: 'same-origin',
        cache: 'no-store'
      });
      if (!resp.ok) throw new Error('验证码加载失败');
      const captchaId = (resp.headers.get('X-Captcha-ID') || resp.headers.get('x-captcha-id') || '').trim();
      const blob = await resp.blob();
      input.value = captchaId;
      if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
      const objectUrl = URL.createObjectURL(blob);
      img.dataset.objectUrl = objectUrl;
      img.src = objectUrl;
      setAlert(alertEl, '', '');
    } catch (error) {
      setAlert(alertEl, error.message || '验证码加载失败', 'error');
    }
  }

  function statusBadge(status) {
    const normalized = String(status || 'idle').toLowerCase();
    const textMap = {
      success: '成功',
      failed: '失败',
      running: '运行中',
      pending: '待启用',
      approved: '已启用',
      rejected: '已停用',
      valid: '有效',
      invalid: '失效',
      skipped: '已跳过',
      idle: '未运行'
    };
    return `<span class="motion-badge ${escapeHTML(normalized)}">${escapeHTML(textMap[normalized] || status || '未运行')}</span>`;
  }

  function renderEmpty(target, message) {
    target.innerHTML = `<div class="motion-empty">${escapeHTML(message)}</div>`;
  }

  window.Mimotion = {
    api,
    closeModal,
    collectForm,
    confirmModal,
    escapeHTML,
    refreshCaptcha,
    renderEmpty,
    setAlert,
    setButtonLoading,
    sha256,
    showMessage,
    showModal,
    statusBadge,
    switchView,
    toast
  };
}());
