'use strict';
(function (global) {
  function initPinGate(cfg) {
    var pfx = String(cfg.pfx || '');
    var apiVerify = String(cfg.apiVerify || '');
    var apiSetPin = String(cfg.apiSetPin || '');
    var mainId = cfg.mainId || null;
    var headerActionsId = cfg.headerActionsId || null;
    var pinBtnId = cfg.pinBtnId || null;
    var pinOk = !!cfg.pinOk;
    var isAdmin = !!cfg.isAdmin;
    var onSuccess = (typeof cfg.onSuccess === 'function') ? cfg.onSuccess : function () {};

    function eid(suffix) { return document.getElementById(pfx + '-' + suffix); }
    function showEl(id) { var e = id ? document.getElementById(id) : null; if (e) e.style.display = ''; }

    function humanErr(code) {
      var m = { wrong_pin: 'Неверный пин-код', invalid_pin: 'PIN должен состоять из 6 цифр' };
      var c = String(code || '').trim();
      return m[c] || 'Произошла ошибка. Попробуйте ещё раз.';
    }

    function getInputs() {
      var cont = eid('pin-inputs');
      return cont ? Array.prototype.slice.call(cont.querySelectorAll('input')) : [];
    }

    if (!pinOk) {
      var inputs = getInputs();
      inputs.forEach(function (inp, idx) {
        inp.addEventListener('input', function () {
          inp.value = inp.value.replace(/\D/g, '').slice(0, 1);
          if (inp.value && idx < inputs.length - 1) inputs[idx + 1].focus();
          if (inp.value && idx === inputs.length - 1) doVerify();
        });
        inp.addEventListener('keydown', function (e) {
          if (e.key === 'Backspace' && !inp.value && idx > 0) inputs[idx - 1].focus();
        });
        inp.addEventListener('paste', function (e) {
          e.preventDefault();
          var text = ((e.clipboardData || window.clipboardData).getData('text') || '')
            .replace(/\D/g, '').slice(0, 6);
          for (var j = 0; j < text.length && j < inputs.length; j++) inputs[j].value = text[j];
          var last = Math.min(text.length, inputs.length - 1);
          if (inputs[last]) inputs[last].focus();
          if (text.length >= inputs.length) doVerify();
        });
      });
      var submitBtn = eid('pin-submit');
      if (submitBtn) submitBtn.addEventListener('click', doVerify);
    }

    async function doVerify() {
      var inputs = getInputs();
      var pin = inputs.map(function (i) { return i.value; }).join('');
      var errEl = eid('pin-err');
      if (errEl) errEl.textContent = '';
      if (pin.length !== 6) {
        if (errEl) errEl.textContent = 'Введите 6-значный пин-код';
        return;
      }
      try {
        var r = await fetch(apiVerify, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pin: pin })
        });
        var j = await r.json().catch(function () { return {}; });
        if (j && j.ok) {
          var wall = eid('pin-wall');
          if (wall) wall.style.display = 'none';
          showEl(mainId);
          showEl(headerActionsId);
          onSuccess();
          return;
        }
        var errMsg = (j && j.error_message) ? j.error_message : humanErr(j && j.error);
        if (errEl) errEl.textContent = errMsg;
        inputs.forEach(function (i) { i.value = ''; });
        if (inputs[0]) inputs[0].focus();
      } catch (e) {
        if (errEl) errEl.textContent = 'Произошла ошибка. Попробуйте ещё раз.';
      }
    }

    if (isAdmin) {
      var pinBtn = pinBtnId ? document.getElementById(pinBtnId) : null;
      var modal = eid('pin-modal');

      function openModal() {
        var newEl = eid('pin-new');
        var mErr = eid('pin-modal-err');
        if (newEl) newEl.value = '';
        if (mErr) mErr.textContent = '';
        if (modal) modal.classList.add('open');
        if (newEl) setTimeout(function () { newEl.focus(); }, 50);
      }
      function closeModal() { if (modal) modal.classList.remove('open'); }

      if (pinBtn) pinBtn.addEventListener('click', openModal);
      var closeBtn = eid('pin-modal-close');
      var cancelBtn = eid('pin-cancel');
      if (closeBtn) closeBtn.addEventListener('click', closeModal);
      if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

      var saveBtn = eid('pin-save');
      if (saveBtn) saveBtn.addEventListener('click', async function () {
        var newEl = eid('pin-new');
        var mErr = eid('pin-modal-err');
        var loadEl = eid('pin-loading');
        var p = ((newEl && newEl.value) || '').trim();
        if (mErr) mErr.textContent = '';
        if (!/^\d{6}$/.test(p)) {
          if (mErr) mErr.textContent = 'PIN должен состоять из 6 цифр';
          return;
        }
        if (loadEl) loadEl.style.display = '';
        try {
          var r = await fetch(apiSetPin, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin: p })
          });
          var j = await r.json().catch(function () { return {}; });
          if (loadEl) loadEl.style.display = 'none';
          if (j && j.ok) {
            closeModal();
            if (window.crmAlert) window.crmAlert('PIN обновлён');
            else alert('PIN обновлён');
          } else {
            if (mErr) mErr.textContent = humanErr(j && j.error);
          }
        } catch (e) {
          if (loadEl) loadEl.style.display = 'none';
          if (mErr) mErr.textContent = 'Произошла ошибка. Попробуйте ещё раз.';
        }
      });
    }

    if (pinOk) {
      showEl(headerActionsId);
      onSuccess();
    }
  }

  global.initPinGate = initPinGate;
})(window);
