(function(){
  function qs(id){ return document.getElementById(id); }

  const state = {
    open: false,
    resolver: null,
    mode: 'alert',
    lastActive: null,
    promptCleanup: null,
  };

  function getEls(){
    const root = qs('crm-ui-dialog');
    if (!root) return null;
    return {
      root,
      overlay: qs('crm-ui-dialog-overlay'),
      panel: qs('crm-ui-dialog-panel'),
      title: qs('crm-ui-dialog-title'),
      message: qs('crm-ui-dialog-message'),
      inputWrap: qs('crm-ui-dialog-input-wrap'),
      input: qs('crm-ui-dialog-input'),
      textarea: qs('crm-ui-dialog-textarea'),
      error: qs('crm-ui-dialog-error'),
      ok: qs('crm-ui-dialog-ok'),
      cancel: qs('crm-ui-dialog-cancel'),
      close: qs('crm-ui-dialog-close'),
    };
  }

  function setOpen(open){
    const els = getEls();
    if (!els) return;
    state.open = open;
    els.root.classList.toggle('open', open);
    document.body.classList.toggle('no-scroll', open);
  }

  function cleanup(){
    const els = getEls();
    if (!els) return;
    try {
      if (typeof state.promptCleanup === 'function') state.promptCleanup();
    } catch (_) {}
    state.promptCleanup = null;
    els.ok.classList.remove('btn-danger');
    els.ok.classList.add('btn');
    els.cancel.style.display = '';
    try { els.ok.disabled = false; } catch (_) {}
    if (els.inputWrap) els.inputWrap.style.display = 'none';
    if (els.input) {
      els.input.value = '';
      els.input.placeholder = '';
      els.input.maxLength = 524288;
      els.input.style.display = '';
    }
    if (els.textarea) {
      els.textarea.value = '';
      els.textarea.placeholder = '';
      els.textarea.maxLength = 524288;
      els.textarea.style.display = 'none';
    }
    if (els.error) els.error.style.display = 'none';
  }

  function resolve(val){
    const r = state.resolver;
    state.resolver = null;
    setOpen(false);
    cleanup();
    try {
      if (state.lastActive && typeof state.lastActive.focus === 'function') state.lastActive.focus();
    } catch (_) {}
    if (typeof r === 'function') r(val);
  }

  function onKeyDown(e){
    if (!state.open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      resolve(state.mode === 'confirm' ? false : (state.mode === 'prompt' ? null : undefined));
      return;
    }

    if (state.mode === 'prompt' && e.key === 'Enter') {
      const els = getEls();
      if (!els) return;
      const isMultiline = els.textarea && els.textarea.offsetParent !== null;
      if (!isMultiline) {
        e.preventDefault();
        if (!els.ok.disabled) els.ok.click();
        return;
      }
    }

    // minimal focus trap: keep focus inside dialog
    if (e.key === 'Tab') {
      const els = getEls();
      if (!els) return;
      const focusables = [
        els.close,
        els.input && els.input.offsetParent !== null ? els.input : null,
        els.textarea && els.textarea.offsetParent !== null ? els.textarea : null,
        els.cancel,
        els.ok,
      ].filter(x => x && x.offsetParent !== null);
      if (!focusables.length) return;
      const idx = focusables.indexOf(document.activeElement);
      let next = idx;
      if (e.shiftKey) next = idx <= 0 ? focusables.length - 1 : idx - 1;
      else next = idx === focusables.length - 1 ? 0 : idx + 1;
      e.preventDefault();
      focusables[next].focus();
    }
  }

  function openDialog(mode, message, opts){
    const els = getEls();
    if (!els) {
      return Promise.resolve(mode === 'confirm' ? false : (mode === 'prompt' ? null : undefined));
    }

    state.lastActive = document.activeElement;
    state.mode = mode;

    const o = opts || {};
    const title = o.title || (mode === 'confirm' ? 'Подтвердите действие' : 'Сообщение');
    const okText = o.okText || 'ОК';
    const cancelText = o.cancelText || 'Отмена';
    const danger = !!o.danger;

    els.title.textContent = String(title);
    els.message.textContent = String(message || '');
    els.ok.textContent = String(okText);
    els.cancel.textContent = String(cancelText);

    if (mode === 'alert') {
      els.cancel.style.display = 'none';
    } else {
      els.cancel.style.display = '';
    }

    if (danger) {
      els.ok.classList.remove('btn');
      els.ok.classList.add('btn-danger');
    } else {
      els.ok.classList.remove('btn-danger');
      els.ok.classList.add('btn');
    }

    setOpen(true);

    // focus primary
    setTimeout(() => {
      try { els.ok.focus(); } catch (_) {}
    }, 0);

    return new Promise((res) => {
      state.resolver = res;
    });
  }

  function openPrompt(message, opts){
    const els = getEls();
    if (!els) return Promise.resolve(null);

    state.lastActive = document.activeElement;
    state.mode = 'prompt';

    const o = opts || {};
    const title = o.title || 'Подтвердите действие';
    const placeholder = o.placeholder || '';
    const okText = o.okText || 'OK';
    const cancelText = o.cancelText || 'Отмена';
    const required = !!o.required;
    const multiline = !!o.multiline;
    const initialValue = typeof o.initialValue === 'string' ? o.initialValue : '';
    const maxLength = typeof o.maxLength === 'number' ? o.maxLength : 1000;

    els.title.textContent = String(title);
    els.message.textContent = String(message || '');
    els.ok.textContent = String(okText);
    els.cancel.textContent = String(cancelText);
    els.cancel.style.display = '';

    if (els.inputWrap) els.inputWrap.style.display = '';
    if (els.error) els.error.style.display = 'none';

    const field = multiline ? els.textarea : els.input;
    const other = multiline ? els.input : els.textarea;
    if (field) {
      field.style.display = '';
      field.placeholder = String(placeholder);
      field.value = String(initialValue);
      field.maxLength = maxLength;
    }
    if (other) other.style.display = 'none';

    function getValue(){
      const raw = field ? String(field.value || '') : '';
      return raw.trim();
    }

    function syncValidity(){
      if (!required) {
        els.ok.disabled = false;
        if (els.error) els.error.style.display = 'none';
        return;
      }
      const v = getValue();
      const ok = v.length > 0;
      els.ok.disabled = !ok;
      if (els.error) els.error.style.display = ok ? 'none' : '';
    }

    function onInput(){ syncValidity(); }
    try {
      if (field) field.addEventListener('input', onInput);
    } catch (_) {}
    state.promptCleanup = function(){
      try { if (field) field.removeEventListener('input', onInput); } catch (_) {}
    };

    syncValidity();
    setOpen(true);

    setTimeout(() => {
      try { if (field) field.focus(); } catch (_) {}
      try {
        if (field && typeof field.setSelectionRange === 'function') {
          field.setSelectionRange(field.value.length, field.value.length);
        }
      } catch (_) {}
    }, 0);

    return new Promise((res) => {
      state.resolver = res;
    });
  }

  function bindOnce(){
    const els = getEls();
    if (!els || els.root.dataset.bound === '1') return;
    els.root.dataset.bound = '1';

    document.addEventListener('keydown', onKeyDown);

    els.ok.addEventListener('click', () => {
      if (state.mode === 'confirm') return resolve(true);
      if (state.mode === 'prompt') {
        const field = (els.textarea && els.textarea.offsetParent !== null) ? els.textarea : els.input;
        const v = String(field && field.value ? field.value : '').trim();
        if (els.ok.disabled) return;
        return resolve(v);
      }
      return resolve(undefined);
    });
    els.cancel.addEventListener('click', () => resolve(state.mode === 'prompt' ? null : false));
    els.close.addEventListener('click', () => resolve(state.mode === 'confirm' ? false : (state.mode === 'prompt' ? null : undefined)));
    els.overlay.addEventListener('click', () => resolve(state.mode === 'confirm' ? false : (state.mode === 'prompt' ? null : undefined)));
  }

  window.crmAlert = function(message, options){
    bindOnce();
    return openDialog('alert', message, options);
  };

  window.crmConfirm = function(message, options){
    bindOnce();
    return openDialog('confirm', message, options);
  };

  window.crmPrompt = function(message, options){
    bindOnce();
    return openPrompt(message, options);
  };
})();
