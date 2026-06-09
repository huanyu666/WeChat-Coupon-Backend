(function(global) {
    'use strict';

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function(char) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[char];
        });
    }

    function requestJson(url, options) {
        return fetch(url, Object.assign({ credentials: 'same-origin' }, options || {})).then(function(response) {
            return response.text().then(function(text) {
                var payload = {};
                try {
                    payload = text ? JSON.parse(text) : {};
                } catch (error) {
                    payload = {};
                }
                if (!response.ok) {
                    var message = (payload && (payload.error || payload.detail)) || ('HTTP ' + response.status);
                    var err = new Error(message);
                    err.status = response.status;
                    err.payload = payload;
                    throw err;
                }
                return payload && typeof payload === 'object' ? payload : {};
            });
        });
    }

    function normalizeGuestReadKey(id) {
        return 'web_announcement_guest_read_' + String(id || '').trim();
    }

    function markGuestRead(id) {
        try {
            localStorage.setItem(normalizeGuestReadKey(id), '1');
        } catch (error) {}
    }

    function isGuestRead(id) {
        try {
            return localStorage.getItem(normalizeGuestReadKey(id)) === '1';
        } catch (error) {
            return false;
        }
    }

    function renderAnnouncementItem(item) {
        var body = String(item && item.body || '').trim();
        var title = String(item && item.title || '').trim();
        var startAtText = String(item && item.start_at_text || '').trim();
        var linkUrl = String(item && item.link_url || '').trim();
        var linkText = String(item && item.link_text || '').trim() || '查看详情';
        var metaHtml = startAtText ? '<span class="web-announcement-tag">' + escapeHtml(startAtText) + '</span>' : '';
        var actionHtml = linkUrl
            ? '<div class="web-announcement-actions"><a class="web-announcement-link" href="' + escapeHtml(linkUrl) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(linkText) + '</a></div>'
            : '';
        return '' +
            '<article class="web-announcement-item">' +
                '<div class="web-announcement-head">' +
                    '<h3 class="web-announcement-title">' + escapeHtml(title) + '</h3>' +
                    '<div class="web-announcement-meta">' + metaHtml + '</div>' +
                '</div>' +
                '<div class="web-announcement-body">' + escapeHtml(body) + '</div>' +
                actionHtml +
            '</article>';
    }

    function ensureModalRoot() {
        var existing = document.getElementById('webAnnouncementModal');
        if (existing) {
            return existing;
        }
        var root = document.createElement('div');
        root.id = 'webAnnouncementModal';
        root.className = 'web-announcement-modal';
        root.innerHTML = '' +
            '<div class="web-announcement-modal-card" role="dialog" aria-modal="true">' +
                '<h3 class="web-announcement-modal-title" id="webAnnouncementModalTitle"></h3>' +
                '<div class="web-announcement-modal-body" id="webAnnouncementModalBody"></div>' +
                '<div class="web-announcement-modal-actions" id="webAnnouncementModalActions"></div>' +
            '</div>';
        root.addEventListener('click', function(event) {
            if (event.target === root && typeof root._closeHandler === 'function') {
                root._closeHandler();
            }
        });
        document.body.appendChild(root);
        return root;
    }

    function showPopup(item, options) {
        var modal = ensureModalRoot();
        var titleEl = document.getElementById('webAnnouncementModalTitle');
        var bodyEl = document.getElementById('webAnnouncementModalBody');
        var actionsEl = document.getElementById('webAnnouncementModalActions');
        var linkUrl = String(item && item.link_url || '').trim();
        var linkText = String(item && item.link_text || '').trim() || '查看详情';
        var closeDelaySeconds = Math.max(0, parseInt(item && item.popup_close_delay_seconds, 10) || 0);
        var closeAllowed = closeDelaySeconds <= 0;
        var countdownTimer = null;

        function closeAndMark() {
            if (!closeAllowed) {
                return;
            }
            if (countdownTimer) {
                clearInterval(countdownTimer);
                countdownTimer = null;
            }
            modal.classList.remove('show');
            if (typeof options.onRead === 'function') {
                options.onRead(item);
            }
        }

        titleEl.textContent = String(item && item.title || '').trim();
        bodyEl.textContent = String(item && item.body || '').trim();
        actionsEl.innerHTML = '';

        if (linkUrl) {
            var link = document.createElement('a');
            link.className = 'web-announcement-modal-btn primary';
            link.href = linkUrl;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = linkText;
            link.addEventListener('click', function() {
                closeAndMark();
            });
            actionsEl.appendChild(link);
        }

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'web-announcement-modal-btn';
        closeBtn.textContent = linkUrl ? '我知道了' : '关闭';
        closeBtn.addEventListener('click', function() {
            closeAndMark();
        });
        if (!closeAllowed) {
            closeBtn.disabled = true;
            closeBtn.classList.add('disabled');
            closeBtn.textContent = closeDelaySeconds + ' 秒后可关闭';
            countdownTimer = setInterval(function() {
                closeDelaySeconds -= 1;
                if (closeDelaySeconds <= 0) {
                    closeAllowed = true;
                    closeBtn.disabled = false;
                    closeBtn.classList.remove('disabled');
                    closeBtn.textContent = linkUrl ? '我知道了' : '关闭';
                    clearInterval(countdownTimer);
                    countdownTimer = null;
                    return;
                }
                closeBtn.textContent = closeDelaySeconds + ' 秒后可关闭';
            }, 1000);
        }
        actionsEl.appendChild(closeBtn);

        modal._closeHandler = function() {
            if (!closeAllowed) {
                return;
            }
            closeAndMark();
        };
        modal.classList.add('show');
    }

    function init(config) {
        var options = config || {};
        var surface = String(options.surface || '').trim();
        var bannerSelector = String(options.bannerSelector || '').trim();
        var bannerEl = bannerSelector ? document.querySelector(bannerSelector) : null;
        if (!surface || !bannerEl) {
            return;
        }

        requestJson('/web/api/announcements/current?surface=' + encodeURIComponent(surface)).then(function(payload) {
            var bannerItems = Array.isArray(payload.banner_items) ? payload.banner_items : [];
            var popupItem = payload.popup_item && typeof payload.popup_item === 'object' ? payload.popup_item : null;

            if (bannerItems.length > 0) {
                bannerEl.innerHTML = bannerItems.map(renderAnnouncementItem).join('');
                bannerEl.classList.add('show');
            } else {
                bannerEl.innerHTML = '';
                bannerEl.classList.remove('show');
            }

            if (!popupItem) {
                return;
            }
            if (surface === 'login' && !popupItem.popup_repeat_always && isGuestRead(popupItem.id)) {
                return;
            }
            showPopup(popupItem, {
                onRead: function(item) {
                    if (surface === 'login') {
                        if (!item.popup_repeat_always) {
                            markGuestRead(item.id);
                        }
                        return;
                    }
                    if (surface === 'query' && item.popup_repeat_always) {
                        requestJson('/web/api/announcements/' + encodeURIComponent(item.id) + '/read', {
                            method: 'POST'
                        }).catch(function() {});
                        return;
                    }
                    requestJson('/web/api/announcements/' + encodeURIComponent(item.id) + '/read', {
                        method: 'POST'
                    }).catch(function() {});
                }
            });
        }).catch(function() {
            bannerEl.innerHTML = '';
            bannerEl.classList.remove('show');
        });
    }

    global.WebAnnouncements = {
        init: init,
        requestJson: requestJson,
        escapeHtml: escapeHtml
    };
})(window);
