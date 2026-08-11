(function () {
  'use strict';

  const modal = document.getElementById('gameNoticeModal');
  const title = document.getElementById('gameNoticeTitle');
  const message = document.getElementById('gameNoticeMessage');
  const icon = document.getElementById('gameNoticeIcon');
  const storageKey = 'pesarena_quick_match_chain';
  let lastFocus = null;
  let statusTimer = null;
  let requestInFlight = false;

  window.showGameNotice = function (text, tone, heading) {
    if (!modal) return;
    lastFocus = document.activeElement;
    modal.className = 'game-notice-modal tone-' + (tone || 'warning');
    title.textContent = heading || (tone === 'success' ? 'Đã gửi lời mời' : 'Tìm Nhanh');
    message.textContent = text || '';
    icon.textContent = tone === 'success' ? '✓' : (tone === 'danger' ? '!' : '⚡');
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('game-notice-open');
    const close = modal.querySelector('.game-notice-button');
    if (close) close.focus();
  };

  function closeNotice() {
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('game-notice-open');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function readState() {
    try { return JSON.parse(sessionStorage.getItem(storageKey) || 'null'); }
    catch (_error) { return null; }
  }

  function saveState(state) {
    if (state) sessionStorage.setItem(storageKey, JSON.stringify(state));
    else sessionStorage.removeItem(storageKey);
  }

  function findButton() {
    return document.querySelector('[data-quick-match-url]');
  }

  function setButton(mode) {
    const button = findButton();
    if (!button) return;
    const label = button.querySelector('.quick-match-label');
    const buttonIcon = button.querySelector('.quick-match-icon');
    button.classList.toggle('is-searching', mode === 'searching');
    button.classList.toggle('is-sent', mode === 'sent');
    button.disabled = mode === 'searching' || mode === 'sent';
    if (buttonIcon) buttonIcon.textContent = mode === 'searching' ? '⏳' : (mode === 'sent' ? '✓' : '⚡');
    if (label) label.textContent = mode === 'searching' ? 'ĐANG TÌM ĐỐI THỦ...' : (mode === 'sent' ? 'ĐANG CHỜ PHẢN HỒI' : 'TÌM NHANH');
  }

  async function sendNext(button, excluded) {
    if (requestInFlight) return;
    requestInFlight = true;
    setButton('searching');
    try {
      const response = await fetch(button.dataset.quickMatchUrl, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ excluded_user_ids: excluded || [] })
      });
      const data = await response.json().catch(function () { return {}; });
      if (!response.ok || !data.ok) throw new Error(data.message || 'Không thể tìm đối thủ lúc này.');
      const state = {
        inviteId: String(data.invite_id || ''),
        opponentId: String(data.opponent_id || ''),
        excluded: excluded || [],
        quickMatchUrl: button.dataset.quickMatchUrl
      };
      saveState(state);
      document.dispatchEvent(new CustomEvent('pes:invite-changed'));
      setButton('sent');
      if (window.showGameNotice) window.showGameNotice(data.message || 'Đã tìm thấy đối thủ. Đang chờ phản hồi...', 'success', 'Tìm Nhanh');
      watchState(state);
    } finally {
      requestInFlight = false;
    }
  }

  async function checkState(state) {
    if (!state || !state.inviteId || requestInFlight) return;
    const response = await fetch('/api/invites/quick-match/' + encodeURIComponent(state.inviteId) + '/status', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    });
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok || !data.ok) {
      // A missing/stale server state must never leave the button spinning forever.
      clearInterval(statusTimer);
      saveState(null);
      setButton('idle');
      return;
    }
    if (data.status === 'accepted' || data.status === 'room_filled') {
      saveState(null);
      clearInterval(statusTimer);
      setButton('idle');
      document.dispatchEvent(new CustomEvent('pes:room-changed'));
      window.location.reload();
      return;
    }
    if (data.status !== 'pending') {
      clearInterval(statusTimer);
      if (!data.continue_search) {
        saveState(null);
        document.dispatchEvent(new CustomEvent('pes:invite-changed'));
        setButton('idle');
        return;
      }
      const excluded = Array.from(new Set([
        ...(state.excluded || []),
        String(state.opponentId || data.opponent_id || '')
      ].filter(Boolean)));
      const button = findButton();
      if (!button) { saveState(null); return; }
      try {
        await sendNext(button, excluded);
      } catch (error) {
        saveState(null);
        setButton('idle');
        if (window.showGameNotice) window.showGameNotice(error.message, 'warning', 'Tìm Nhanh');
      }
    } else {
      // Room live polling may replace the button node. Reapply its visual state.
      setButton('sent');
    }
  }

  function watchState(state) {
    clearInterval(statusTimer);
    statusTimer = setInterval(function () { checkState(readState() || state); }, 5000);
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-game-notice-close]')) closeNotice();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal && !modal.hidden) closeNotice();
  });
  document.addEventListener('click', async function (event) {
    const button = event.target.closest('[data-quick-match-url]');
    if (!button || button.disabled || requestInFlight) return;
    saveState(null);
    try {
      await sendNext(button, []);
    } catch (error) {
      saveState(null);
      setButton('idle');
      if (window.showGameNotice) window.showGameNotice(error.message, 'warning', 'Tìm Nhanh');
    }
  });

  const existing = readState();
  if (existing && existing.inviteId) {
    setButton('sent');
    watchState(existing);
    checkState(existing);
  }
})();
