(function(){
  function nowMs(){ return Date.now(); }

  function debounce(fn, waitMs){
    let t = null;
    return function(){
      const args = arguments;
      if (t) clearTimeout(t);
      t = setTimeout(() => {
        t = null;
        try { fn.apply(null, args); } catch (_){ }
      }, Math.max(0, Number(waitMs) || 0));
    };
  }

  function isModalOpen(){
    try {
      const modal = document.getElementById('modal');
      if (!modal) return false;
      // Alpine x-show toggles display; in addition, presence of 'open' class is not guaranteed.
      const style = window.getComputedStyle ? window.getComputedStyle(modal) : null;
      if (style && style.display === 'none') return false;
      // If modal-body has content, consider modal open.
      const body = document.getElementById('modal-body');
      if (body && String(body.innerHTML || '').trim()) return true;
      // Fallback: visible modal element
      return !!(modal.offsetParent);
    } catch (_){
      return false;
    }
  }

  function createPoller(){
    const state = {
      running: false,
      timer: null,
      intervalMs: 15000,
      onTick: null,
      scope: 'global',
      backoffStep: 0,
      lastTickAt: 0,
      pausedForModal: false,
      pendingAfterModal: false,
    };

    function _clear(){
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
    }

    function _effectiveInterval(){
      const base = Math.max(1000, Number(state.intervalMs) || 15000);
      if (state.backoffStep <= 0) return base;
      if (state.backoffStep === 1) return Math.max(base, base * 2);
      return Math.max(base, base * 4);
    }

    function _schedule(nextInMs){
      _clear();
      if (!state.running) return;
      state.timer = setTimeout(_loop, Math.max(0, Number(nextInMs) || 0));
    }

    async function _loop(){
      if (!state.running) return;

      // pause on hidden tab
      try {
        if (document.hidden) {
          _schedule(_effectiveInterval());
          return;
        }
      } catch (_){ }

      // UI safety: do not refresh while modal is open (avoids wiping input state in pages that rerender).
      try {
        if (isModalOpen()) {
          state.pausedForModal = true;
          state.pendingAfterModal = true;
          _schedule(1000);
          return;
        }
        state.pausedForModal = false;
      } catch (_){ }

      const fn = state.onTick;
      if (!fn) {
        _schedule(_effectiveInterval());
        return;
      }

      state.lastTickAt = nowMs();
      try {
        await fn();
        state.backoffStep = 0;
        _schedule(_effectiveInterval());
      } catch (_e){
        state.backoffStep = Math.min(2, state.backoffStep + 1);
        _schedule(_effectiveInterval());
      }
    }

    function start(opts){
      const o = opts || {};
      state.intervalMs = Number(o.intervalMs) || state.intervalMs;
      state.onTick = o.onTick || state.onTick;
      state.scope = String(o.scope || state.scope);
      state.running = true;
      _schedule(0);

      // resume fast when tab becomes visible
      try {
        document.addEventListener('visibilitychange', () => {
          if (!state.running) return;
          if (!document.hidden) {
            _schedule(0);
          }
        });
      } catch (_){ }

      try {
        window.addEventListener('close-modal', () => {
          if (!state.running) return;
          if (!state.pendingAfterModal) return;
          state.pendingAfterModal = false;
          _schedule(0);
        });
      } catch (_){ }
    }

    function stop(){
      state.running = false;
      _clear();
    }

    return { start, stop, debounce, isModalOpen };
  }

  try {
    window.crmCreatePoller = createPoller;
    window.crmDebounce = debounce;
    window.crmIsModalOpen = isModalOpen;
  } catch (_){ }
})();
