(function(){
  function _debugEnabled(){
    try { return !!window.CRM_DEBUG_POLLING; } catch (_){ return false; }
  }

  function _log(){
    if (!_debugEnabled()) return;
    try { console.log.apply(console, arguments); } catch (_){ }
  }

  function _pad2(n){
    const x = Number(n) || 0;
    return (x < 10 ? '0' : '') + String(x);
  }

  function _hhmmss(d){
    try {
      return _pad2(d.getHours()) + ':' + _pad2(d.getMinutes()) + ':' + _pad2(d.getSeconds());
    } catch (_){
      return '';
    }
  }

  function _ensureCRM(){
    try {
      if (!window.CRM) window.CRM = {};
      return window.CRM;
    } catch (_){
      return {};
    }
  }

  function pollFetch(input, init){
    const doFetch = (window.crmFetchNoCache && typeof window.crmFetchNoCache === 'function')
      ? window.crmFetchNoCache
      : fetch;

    const nextInit = Object.assign({}, (init || {}));
    if (!nextInit.headers) nextInit.headers = {};

    try {
      if (nextInit.headers instanceof Headers) {
        nextInit.headers.set('Cache-Control', 'no-cache');
        nextInit.headers.set('Pragma', 'no-cache');
      } else {
        nextInit.headers['Cache-Control'] = 'no-cache';
        nextInit.headers['Pragma'] = 'no-cache';
      }
    } catch (_){ }

    try {
      if (!nextInit.credentials) nextInit.credentials = 'include';
    } catch (_){ }

    return doFetch(input, Object.assign({}, nextInit, { cache: 'no-store' }));
  }

  function _indicator(){
    const id = 'crm-live-indicator';
    try {
      let el = document.getElementById(id);
      if (el) return el;
      el = document.createElement('div');
      el.id = id;
      el.style.position = 'fixed';
      el.style.right = '10px';
      el.style.bottom = '10px';
      el.style.zIndex = '99999';
      el.style.padding = '8px 10px';
      el.style.borderRadius = '10px';
      el.style.fontSize = '12px';
      el.style.fontFamily = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';
      el.style.border = '1px solid rgba(148,163,184,0.65)';
      el.style.background = 'rgba(15,23,42,0.9)';
      el.style.color = '#e2e8f0';
      el.style.boxShadow = '0 8px 20px rgba(0,0,0,0.25)';
      el.style.pointerEvents = 'none';
      el.textContent = 'Live: OFF';
      document.body.appendChild(el);
      return el;
    } catch (_){
      return null;
    }
  }

  function _setIndicator(text){
    if (!_debugEnabled()) return;
    const el = _indicator();
    if (!el) return;
    try { el.textContent = String(text || ''); } catch (_){ }
  }

  function _getPageId(){
    try {
      const b = document.body;
      const v = b && b.dataset ? (b.dataset.crmPage || '') : '';
      if (v) return String(v);
    } catch (_){ }

    let p = '';
    try { p = String((window.location && window.location.pathname) || ''); } catch (_){ p = ''; }

    if (p.startsWith('/crm/tasks')) return 'tasks';
    if (p.startsWith('/crm/purchases')) return 'purchases';
    if (p.startsWith('/crm/stocks')) return 'stocks_dashboard';
    if (p.startsWith('/crm/materials/consumptions')) return 'materials_consumptions';
    if (p === '/crm/materials') return 'materials';
    if (p.startsWith('/crm/schedule')) return 'schedule';
    if (p.startsWith('/crm/broadcast')) return 'mailings';
    if (p === '/crm/' || p === '/crm') return 'employees';
    return 'global';
  }

  function _debounce(fn, waitMs){
    try {
      if (window.crmDebounce) return window.crmDebounce(fn, waitMs);
    } catch (_){ }
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

  function createLiveUpdates(){
    const state = {
      running: false,
      pageId: 'global',
      timer: null,
      intervalMs: 10000,
      backoffStep: 0,
      lastTickAt: 0,
      tickInFlight: false,
      tickFn: null,
    };

    function _clear(){
      if (state.timer) {
        clearTimeout(state.timer);
        state.timer = null;
      }
    }

    function _effectiveInterval(){
      const base = Math.max(1000, Number(state.intervalMs) || 10000);
      if (state.backoffStep <= 0) return base;
      if (state.backoffStep === 1) return Math.max(base, 16000);
      return Math.max(base, 30000);
    }

    function _schedule(inMs){
      _clear();
      if (!state.running) return;
      state.timer = setTimeout(_loop, Math.max(0, Number(inMs) || 0));
    }

    async function _loop(){
      if (!state.running) return;

      try {
        if (document.hidden) {
          _schedule(_effectiveInterval());
          return;
        }
      } catch (_){ }

      if (state.tickInFlight) {
        _schedule(600);
        return;
      }

      const fn = state.tickFn;
      if (!fn) {
        _schedule(_effectiveInterval());
        return;
      }

      state.tickInFlight = true;
      state.lastTickAt = Date.now();
      const startedAt = Date.now();

      _log('[poll] tick page=' + state.pageId);
      _setIndicator('Live: ON | page: ' + state.pageId + ' | tick: ' + _hhmmss(new Date()));

      try {
        await fn();
        state.backoffStep = 0;
        const dur = Date.now() - startedAt;
        _log('[poll] apply page=' + state.pageId + ' ms=' + dur);
      } catch (e) {
        state.backoffStep = Math.min(2, state.backoffStep + 1);
        _log('[poll] error page=' + state.pageId + ' backoff=' + state.backoffStep + ' err=' + ((e && e.message) ? e.message : String(e)));
      } finally {
        state.tickInFlight = false;
        _schedule(_effectiveInterval());
      }
    }

    function start(opts){
      const o = opts || {};
      state.pageId = String(o.pageId || state.pageId);
      state.intervalMs = Number(o.intervalMs) || state.intervalMs;
      state.tickFn = o.tickFn || state.tickFn;
      state.running = true;
      state.backoffStep = 0;

      _log('[poll] start page=' + state.pageId + ' interval=' + state.intervalMs);
      _setIndicator('Live: ON | page: ' + state.pageId + ' | start: ' + _hhmmss(new Date()));

      _schedule(0);

      try {
        document.addEventListener('visibilitychange', () => {
          if (!state.running) return;
          if (!document.hidden) {
            _log('[poll] resume page=' + state.pageId + ' reason=visibility');
            _schedule(0);
          }
        });
      } catch (_){ }

      try {
        window.addEventListener('focus', () => {
          if (!state.running) return;
          _log('[poll] resume page=' + state.pageId + ' reason=focus');
          _schedule(0);
        });
      } catch (_){ }
    }

    function stop(){
      state.running = false;
      _clear();
      _setIndicator('Live: OFF');
    }

    function requestTick(reason){
      if (!state.running) return;
      _log('[poll] request page=' + state.pageId + ' reason=' + String(reason || 'manual'));
      _schedule(0);
    }

    return { start, stop, requestTick };
  }

  function _resolveTickFn(pageId){
    const CRM = _ensureCRM();
    if (pageId === 'tasks' && CRM.reloadTasksBoard) return CRM.reloadTasksBoard;
    if (pageId === 'purchases' && CRM.reloadPurchasesBoard) return CRM.reloadPurchasesBoard;
    if (pageId === 'stocks_dashboard' && CRM.reloadStocksDashboard) return CRM.reloadStocksDashboard;

    // Keep crm_live_init.js doing its own polling for other pages for now.
    return null;
  }

  function _defaultIntervalMs(pageId){
    if (pageId === 'tasks') return 8000;
    if (pageId === 'purchases') return 10000;
    if (pageId === 'stocks_dashboard') return 10000;
    return 15000;
  }

  function init(){
    const CRM = _ensureCRM();
    CRM.pollFetch = pollFetch;

    const pageId = _getPageId();
    const tickFn = _resolveTickFn(pageId);
    if (!tickFn) {
      _log('[poll] skip page=' + pageId + ' reason=no_tickFn');
      return;
    }

    const live = createLiveUpdates();
    CRM.__live = live;

    const debouncedTick = _debounce(async function(){
      try {
        // wrap to allow future instrumentation
        await tickFn();
      } catch (e) {
        throw e;
      }
    }, 500);

    live.start({
      pageId: pageId,
      intervalMs: _defaultIntervalMs(pageId),
      tickFn: debouncedTick,
    });

    try {
      if (window.crmRefreshBus && window.crmRefreshBus.on) {
        window.crmRefreshBus.on(pageId, () => live.requestTick('bus:' + pageId));
        window.crmRefreshBus.on('global', () => live.requestTick('bus:global'));
      }
    } catch (_){ }

    if (_debugEnabled()) {
      try { window.addEventListener('error', (e) => _log('[poll] window.error', e && e.message ? e.message : e)); } catch (_){ }
    }
  }

  try {
    window.CRM = window.CRM || {};
    window.CRM.pollFetch = pollFetch;
  } catch (_){ }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
