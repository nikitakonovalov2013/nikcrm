(function(){
  const originalFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!originalFetch) return;

  function _methodFromInit(init){
    try {
      const m = (init && init.method) ? String(init.method) : 'GET';
      return m.toUpperCase();
    } catch (_){
      return 'GET';
    }
  }

  function _asUrl(input){
    try {
      if (typeof input === 'string') return new URL(input, window.location.origin);
      if (input && typeof input === 'object' && input.url) return new URL(String(input.url), window.location.origin);
    } catch (_){ }
    try { return new URL(String(input || ''), window.location.origin); } catch (_){ return null; }
  }

  function _needsCrmPrefix(){
    try {
      return String(window.location && window.location.pathname ? window.location.pathname : '').startsWith('/crm');
    } catch (_){
      return false;
    }
  }

  function _rewriteApiUrl(input){
    const urlObj = _asUrl(input);
    if (!urlObj) return { input: input, urlObj: urlObj };
    try {
      if (urlObj.origin !== window.location.origin) return { input: input, urlObj: urlObj };
      const p = String(urlObj.pathname || '');
      if (_needsCrmPrefix() && p.startsWith('/api/')) {
        const next = new URL(String(urlObj), window.location.origin);
        next.pathname = '/crm' + p;
        return { input: String(next), urlObj: next };
      }
    } catch (_){ }
    return { input: input, urlObj: urlObj };
  }

  function scopeFromUrl(urlObj){
    try {
      const p = String(urlObj && urlObj.pathname ? urlObj.pathname : '');
      if (p.startsWith('/api/tasks') || p.startsWith('/crm/api/tasks')) return 'tasks';
      if (p.startsWith('/api/purchases') || p.startsWith('/crm/api/purchases')) return 'purchases';
      if (p.startsWith('/api/schedule') || p.startsWith('/api/shifts') || p.startsWith('/crm/api/schedule') || p.startsWith('/crm/api/shifts')) return 'shifts';
      if (p.startsWith('/api/mailings') || p.startsWith('/crm/api/mailings')) return 'mailings';
      if (p.startsWith('/api/materials') || p.startsWith('/api/expenses') || p.startsWith('/crm/api/materials') || p.startsWith('/crm/api/expenses')) return 'materials';
      if (p.startsWith('/api/users') || p.startsWith('/api/employees') || p.startsWith('/crm/api/users') || p.startsWith('/crm/api/employees')) return 'employees';
      return 'global';
    } catch (_){
      return 'global';
    }
  }

  function _isMutation(method){
    return method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
  }

  function _isSameOriginApi(urlObj){
    try {
      if (!urlObj) return false;
      if (urlObj.origin !== window.location.origin) return false;
      const p = String(urlObj.pathname || '');
      return p.startsWith('/api/') || p.startsWith('/crm/api/');
    } catch (_){
      return false;
    }
  }

  async function crmFetch(input, init){
    const method = _methodFromInit(init);
    const rewritten = _rewriteApiUrl(input);
    const urlObj = rewritten.urlObj;
    const isMutation = _isMutation(method);
    const isWatched = isMutation && _isSameOriginApi(urlObj);

    const resp = await originalFetch(rewritten.input, init);

    try {
      if (isWatched && resp && resp.ok) {
        const scope = scopeFromUrl(urlObj);
        if (window.crmRefreshBus && typeof window.crmRefreshBus.emit === 'function') {
          window.crmRefreshBus.emit(scope, { url: urlObj ? String(urlObj) : '', method: method });
          window.crmRefreshBus.emit('global', { url: urlObj ? String(urlObj) : '', method: method, scope: scope });
        }
      }
    } catch (_){ }

    return resp;
  }

  function _appendTs(urlObj){
    try {
      const u = new URL(String(urlObj), window.location.origin);
      u.searchParams.set('_ts', String(Date.now()));
      return u;
    } catch (_){
      return urlObj;
    }
  }

  async function crmFetchNoCache(input, init){
    const rewritten = _rewriteApiUrl(input);
    const urlObj = rewritten.urlObj;
    if (!urlObj) return originalFetch(input, init);

    const method = _methodFromInit(init);
    const isGetLike = method === 'GET' || method === 'HEAD';
    const u = isGetLike ? _appendTs(urlObj) : urlObj;
    const nextInit = Object.assign({}, (init || {}), {
      cache: 'no-store',
      credentials: (init && init.credentials) ? init.credentials : 'include',
    });

    try {
      if (!nextInit.headers) nextInit.headers = {};
      if (nextInit.headers instanceof Headers) {
        nextInit.headers.set('Cache-Control', 'no-cache');
        nextInit.headers.set('Pragma', 'no-cache');
      } else {
        nextInit.headers['Cache-Control'] = 'no-cache';
        nextInit.headers['Pragma'] = 'no-cache';
      }
    } catch (_){ }

    const finalUrl = String(u);
    try {
      if (isGetLike && window.CRM_DEBUG_POLLING) {
        console.log('[poll] fetch', method, finalUrl);
      }
    } catch (_){ }

    const resp = await originalFetch(finalUrl, nextInit);

    try {
      if (isGetLike && window.CRM_DEBUG_POLLING) {
        console.log('[poll] fetch', method, finalUrl, 'status=' + String(resp && resp.status));
      }
    } catch (_){ }

    return resp;
  }

  try {
    window.crmFetch = crmFetch;
    window.crmOriginalFetch = originalFetch;
    window.crmFetchNoCache = crmFetchNoCache;
    window.crmScopeFromUrl = scopeFromUrl;
  } catch (_){ }

  // Minimal-invasion global hook: keep semantics identical, only emits refresh after successful API mutations.
  try {
    window.fetch = crmFetch;
  } catch (_){ }
})();
