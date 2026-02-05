(function(){
  function _path(){
    try { return String((window.location && window.location.pathname) || ''); } catch (_){ return ''; }
  }

  function _has(elId){
    try { return !!document.getElementById(String(elId)); } catch (_){ return false; }
  }

  async function _fetchHtmlNoCache(url){
    const doFetch = (window.crmFetchNoCache && typeof window.crmFetchNoCache === 'function')
      ? window.crmFetchNoCache
      : fetch;
    const r = await doFetch(url, { method: 'GET', credentials: 'include' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.text();
  }

  async function _partialReloadBySelector(selector, opts){
    const o = opts || {};
    try {
      if (window.crmIsModalOpen && window.crmIsModalOpen()) {
        // poller will retry after close-modal
        return;
      }
    } catch (_){ }

    const url = o.url || window.location.href;
    const html = await _fetchHtmlNoCache(url);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const next = doc.querySelector(selector);
    const cur = document.querySelector(selector);
    if (!next || !cur) return;

    const nextHtml = String(next.outerHTML || '');
    const curHtml = String(cur.outerHTML || '');
    if (nextHtml === curHtml) return;

    cur.outerHTML = nextHtml;

    try {
      if (window.applyTableDataLabels) {
        window.applyTableDataLabels(document);
      }
    } catch (_){ }
  }

  function _startPolling(scope, intervalMs, reloadFn){
    try {
      if (!window.crmCreatePoller) return;
      const poller = window.crmCreatePoller();
      poller.start({ intervalMs: intervalMs, onTick: reloadFn, scope: scope });
    } catch (_){ }
  }

  function _subscribe(scope, reloadFn){
    try {
      if (!window.crmRefreshBus || typeof window.crmRefreshBus.on !== 'function') return;
      const deb = (window.crmDebounce ? window.crmDebounce(reloadFn, 700) : reloadFn);
      window.crmRefreshBus.on(scope, deb);
      window.crmRefreshBus.on('global', deb);
    } catch (_){ }
  }

  async function reloadMaterialsTable(){
    // Materials page is SSR+HTMX; refresh only the table wrapper.
    await _partialReloadBySelector('#materials-table-wrap');
  }

  async function reloadConsumptionsTable(){
    await _partialReloadBySelector('#consumptions-table-wrap');
  }

  async function reloadUsersTbody(){
    // Users page uses filters bound to DOM; update tbody only.
    await _partialReloadBySelector('#users-tbody');
    try {
      // re-apply filter UI logic (defined inside index.html closure)
      // Can't call directly; but htmx:afterSwap listeners handle some; we trigger label fill at least.
      if (window.htmx) {
        // no-op
      }
    } catch (_){ }
  }

  function init(){
    const p = _path();

    // Materials
    if (p === '/crm/materials' || _has('materials-table-wrap')) {
      _subscribe('materials', reloadMaterialsTable);
      _startPolling('materials', 12000, reloadMaterialsTable);
    }

    // Consumptions
    if (p === '/crm/materials/consumptions' || _has('consumptions-table-wrap')) {
      _subscribe('materials', reloadConsumptionsTable);
      _startPolling('materials', 12000, reloadConsumptionsTable);
    }

    // Users/employees
    if (p === '/crm/' || _has('users-tbody')) {
      _subscribe('employees', reloadUsersTbody);
      _startPolling('employees', 20000, reloadUsersTbody);
    }

    // Broadcast page already has its own polling; still subscribe for global refresh.
    if (p.startsWith('/crm/broadcast')) {
      _subscribe('mailings', function(){});
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
