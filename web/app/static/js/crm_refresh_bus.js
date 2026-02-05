(function(){
  const _handlers = new Map();

  function _setFor(scope){
    const s = String(scope || 'global');
    if (!_handlers.has(s)) _handlers.set(s, new Set());
    return _handlers.get(s);
  }

  const bus = {
    on: function(scope, handler){
      if (!handler) return;
      _setFor(scope).add(handler);
    },
    off: function(scope, handler){
      try {
        const set = _handlers.get(String(scope || 'global'));
        if (set) set.delete(handler);
      } catch (_){ }
    },
    emit: function(scope, payload){
      const s = String(scope || 'global');
      const set = _handlers.get(s);
      if (!set || set.size === 0) return;
      set.forEach((h) => {
        try { h(payload); } catch (_){ }
      });
    },
  };

  try { window.crmRefreshBus = bus; } catch (_){ }
})();
