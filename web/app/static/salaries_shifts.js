(function(){
  const INIT = (function(){
    try {
      if (window.SALARY_SHIFTS_INIT) return window.SALARY_SHIFTS_INIT || {};
    } catch(_){ }
    try {
      const root = document.querySelector('.salary-shifts-page');
      if(!root) return {};
      return {
        user_id: Number(root.getAttribute('data-user-id') || 0) || 0,
        user_name: String(root.getAttribute('data-user-name') || ''),
        user_color: String(root.getAttribute('data-user-color') || ''),
        month: String(root.getAttribute('data-month') || ''),
      };
    } catch(_){
      return {};
    }
  })();

  const userId = Number(INIT.user_id || 0);
  const userName = String(INIT.user_name || ('#' + String(userId)));
  const userColorRaw = String(INIT.user_color || '').trim();
  const safeUserColor = (/^#[0-9a-fA-F]{6}$/.test(userColorRaw)) ? userColorRaw : '';

  const monthInput = document.getElementById('salary-shifts-month');
  const applyBtn = document.getElementById('salary-shifts-month-apply');
  const prevBtn = document.getElementById('salary-shifts-prev');
  const nextBtn = document.getElementById('salary-shifts-next');
  const titleEl = document.getElementById('salary-shifts-title');
  const gridEl = document.getElementById('salary-shifts-grid');

  const selectedEl = document.getElementById('salary-shifts-selected');
  const editorEl = document.getElementById('salary-shifts-editor');
  const editorCardEl = document.getElementById('salary-shifts-editor-card');
  const editorEmptyEl = document.getElementById('salary-shifts-editor-empty');
  const hintEl = document.getElementById('salary-shifts-hint');

  const stateEl = document.getElementById('salary-shifts-state');
  const mhEl = document.getElementById('salary-shifts-manual-hours');
  const maEl = document.getElementById('salary-shifts-manual-amount');
  const cEl = document.getElementById('salary-shifts-comment');
  const errEl = document.getElementById('salary-shifts-error');
  const saveBtn = document.getElementById('salary-shifts-save');

  const confirmWrapEl = document.getElementById('salary-shifts-confirm-wrap');
  const confirmBadgeEl = document.getElementById('salary-shifts-confirm-badge');
  const confirmBtn = document.getElementById('salary-shifts-confirm');

  const commentFieldErrEl = document.getElementById('salary-shifts-comment-error');
  const labelStateEl = document.getElementById('salary-shifts-label-state');
  const labelCommentEl = document.getElementById('salary-shifts-label-comment');
  const labelAdjDeltaEl = document.getElementById('salary-shifts-label-adj-delta');
  const labelAdjCommentEl = document.getElementById('salary-shifts-label-adj-comment');

  const adjListEl = document.getElementById('salary-shifts-adjustments');
  const adjDeltaEl = document.getElementById('salary-shifts-adj-delta');
  const adjCommentEl = document.getElementById('salary-shifts-adj-comment');
  const adjErrEl = document.getElementById('salary-shifts-adj-error');
  const adjAddBtn = document.getElementById('salary-shifts-adj-add');

  const exportBtn = document.getElementById('salary-shifts-export');
  const onlyDevEl = document.getElementById('salary-shifts-only-deviations');
  const devPrevBtn = document.getElementById('salary-shifts-dev-prev');
  const devNextBtn = document.getElementById('salary-shifts-dev-next');

  const historyToggleBtn = document.getElementById('salary-shifts-history-toggle');
  const historyEl = document.getElementById('salary-shifts-history');

  const summaryDotEl = document.getElementById('salary-shifts-summary-dot');
  const summaryNameEl = document.getElementById('salary-shifts-summary-name');
  const summaryMonthEl = document.getElementById('salary-shifts-summary-month');
  const summaryPositionEl = document.getElementById('salary-shifts-summary-position');
  const summaryRateEl = document.getElementById('salary-shifts-summary-rate');
  const summaryShiftsEl = document.getElementById('salary-shifts-summary-shifts');
  const summaryReviewEl = document.getElementById('salary-shifts-summary-review');
  const summaryAccruedEl = document.getElementById('salary-shifts-summary-accrued');
  const summaryPaidEl = document.getElementById('salary-shifts-summary-paid');
  const summaryBalanceEl = document.getElementById('salary-shifts-summary-balance');

  if (!userId) return;

  function apiBase(){
    const m = document.querySelector('meta[name="crm-api-base"]');
    return (m && m.getAttribute('content')) ? m.getAttribute('content') : '/crm/api';
  }

  function apiFetch(url, options){
    try {
      if (window.crmApiFetch) return window.crmApiFetch(url, options);
    } catch(_){ }
    const o = Object.assign({}, (options || {}));
    if (o.credentials === undefined) o.credentials = 'include';
    return fetch(url, o);
  }

  function pageBase(){
    try {
      const m = document.querySelector('meta[name="crm-page-base"]');
      const v = (m && m.getAttribute('content')) ? String(m.getAttribute('content')) : '';
      const s = v.trim();
      return s ? s.replace(/\/$/, '') : '/crm';
    } catch(_){
      return '/crm';
    }
  }

  function escapeHtml(s){
    return String(s || '')
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;')
      .replaceAll('"','&quot;')
      .replaceAll("'",'&#039;');
  }

  function pad2(n){ return String(n).padStart(2,'0'); }

  const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];

  function ymToDate(ym){
    const m = String(ym||'').match(/^(\d{4})-(\d{2})$/);
    if(!m) return null;
    const y = Number(m[1]);
    const mo = Number(m[2]);
    if(!Number.isFinite(y) || !Number.isFinite(mo) || mo < 1 || mo > 12) return null;
    const d = new Date(y, mo - 1, 1);
    d.setHours(0,0,0,0);
    return d;
  }

  function dateToYm(d){
    return String(d.getFullYear()).padStart(4,'0') + '-' + pad2(d.getMonth()+1);
  }

  function isoDate(d){
    return String(d.getFullYear()).padStart(4,'0') + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate());
  }

  function setErr(target, msg){
    if(!target) return;
    if(!msg){
      target.style.display = 'none';
      target.textContent = '';
      return;
    }
    target.style.display = 'block';
    target.textContent = String(msg);
  }

  function setFieldError(labelEl, msgEl, msg){
    try {
      if(labelEl){
        if(msg) labelEl.classList.add('is-error');
        else labelEl.classList.remove('is-error');
      }
    } catch(_){ }
    try {
      if(!msgEl) return;
      if(!msg){
        msgEl.style.display = 'none';
        msgEl.textContent = '';
        return;
      }
      msgEl.style.display = 'block';
      msgEl.textContent = String(msg);
    } catch(_){ }
  }

  function fmtRub(v){
    try {
      const raw = (v === null || v === undefined) ? '' : String(v);
      const num = Number(raw.replace(/\s+/g,'').replace(',', '.'));
      if (!Number.isFinite(num)) return (String(v || '0') + ' ₽');
      const rounded = Math.round(num);
      const parts = String(rounded).split('');
      let out = '';
      for (let i=0;i<parts.length;i++){
        const idxFromEnd = parts.length - i;
        out += parts[i];
        if (idxFromEnd > 1 && idxFromEnd % 3 === 1) out += ' ';
      }
      return out.trim() + ' ₽';
    } catch(_){
      const s = String(v || '0');
      return s + ' ₽';
    }
  }

  function fmtHoursHuman(v){
    try {
      if (v === null || v === undefined) return '';
      const raw = String(v).trim();
      if (!raw) return '';
      const num = Number(raw.replace(',', '.'));
      if (!Number.isFinite(num)) return '';
      const sign = num < 0 ? '-' : '';
      const abs = Math.abs(num);
      const h = Math.floor(abs + 1e-9);
      const mins = Math.round((abs - h) * 60);
      if (mins <= 0) return sign + String(h) + ' ч';
      if (h <= 0) return sign + String(mins) + ' мин';
      return sign + String(h) + ' ч ' + String(mins) + ' мин';
    } catch(_){
      return '';
    }
  }

  function pickHoursForChip(it){
    if(!it) return '';
    const actual = fmtHoursHuman(it.actual_hours);
    if(actual) return actual;
    const planned = fmtHoursHuman(it.planned_hours);
    return planned;
  }

  function isDeviation(it){
    return !!(it && it.needs_review);
  }

  function isPaid(it){
    return !!(it && it.is_paid);
  }

  function deviationDaysIso(){
    const out = [];
    for(let i=0;i<shifts.length;i++){
      const it = shifts[i];
      if(it && isDeviation(it)) out.push(String(it.day || ''));
    }
    out.sort();
    return out;
  }

  function gotoDeviation(delta){
    const list = deviationDaysIso();
    if(!list.length) return;
    const cur = String(selectedDayIso || '');
    let idx = list.indexOf(cur);
    if(idx < 0){
      idx = (delta >= 0) ? -1 : 0;
    }
    let next = idx + (delta >= 0 ? 1 : -1);
    if(next < 0) next = list.length - 1;
    if(next >= list.length) next = 0;
    const dayIso = list[next];
    if(dayIso){
      selectDay(dayIso);
      try {
        const el = document.querySelector('[data-salary-shifts-day="' + CSS.escape(dayIso) + '"]');
        if(el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      } catch(_){ }
    }
  }

  function downloadCsv(filename, csvText){
    try {
      const blob = new Blob(["\ufeff" + String(csvText || '')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      try { URL.revokeObjectURL(url); } catch(_){ }
    } catch(_){ }
  }

  function shiftsToCsv(){
    const ym = dateToYm(view);
    const rows = [];
    rows.push(['Дата','Статус','План, ч','Факт, ч','Начислено, ₽','Выплачено','Отклонение','Комментарий'].join(';'));
    const list = Array.isArray(shifts) ? shifts.slice() : [];
    list.sort((a,b)=>String((a&&a.day)||'').localeCompare(String((b&&b.day)||'')));
    for(let i=0;i<list.length;i++){
      const it = list[i] || {};
      const day = String(it.day || '');
      if(day.slice(0,7) !== ym) continue;
      const status = stateLabelRu(it.state);
      const planned = String(it.planned_hours || '').replace('.', ',');
      const actual = String(it.actual_hours || '').replace('.', ',');
      const accrued = String(it.total_amount || '').replace('.', ',');
      const paid = isPaid(it) ? 'да' : 'нет';
      const dev = isDeviation(it) ? 'да' : 'нет';
      const cmt = String(it.comment || '').replaceAll('\n',' ').replaceAll(';',',');
      rows.push([day,status,planned,actual,accrued,paid,dev,cmt].map((x)=>{
        const s = String(x||'');
        return '"' + s.replaceAll('"','""') + '"';
      }).join(';'));
    }
    return rows.join('\n');
  }

  function renderHistoryForSelected(){
    if(!historyEl) return;
    const it = shiftByDayIso(selectedDayIso);
    if(!it){
      historyEl.innerHTML = '<div class="muted">Выберите смену</div>';
      return;
    }

    const sid = Number(it.shift_id || 0);
    if(!sid){
      historyEl.innerHTML = '<div class="muted">Нет данных</div>';
      return;
    }

    historyEl.innerHTML = '<div class="muted">Загрузка…</div>';

    function fmtDt(s){
      const raw = String(s || '').trim();
      if(!raw) return '—';
      return raw.replace('T',' ').replace('Z','');
    }

    function diffLines(before, after){
      const out = [];
      const b = (before && typeof before === 'object') ? before : {};
      const a = (after && typeof after === 'object') ? after : {};

      const bs = String(b.state || '');
      const as = String(a.state || '');
      if(bs && as && bs !== as){
        out.push('Статус: ' + stateLabelRu(bs) + ' → ' + stateLabelRu(as));
      }
      const bmh = (b.manual_hours === null || b.manual_hours === undefined) ? '' : String(b.manual_hours);
      const amh = (a.manual_hours === null || a.manual_hours === undefined) ? '' : String(a.manual_hours);
      if(bmh !== amh){
        out.push('Часы (ручные): ' + prettyManualHours(bmh) + ' → ' + prettyManualHours(amh));
      }
      const bma = (b.manual_amount_override === null || b.manual_amount_override === undefined) ? '' : String(b.manual_amount_override);
      const ama = (a.manual_amount_override === null || a.manual_amount_override === undefined) ? '' : String(a.manual_amount_override);
      if(bma !== ama){
        out.push('Сумма (ручная): ' + prettyMoneyOrDash(bma) + ' → ' + prettyMoneyOrDash(ama));
      }
      const bc = String(b.comment || '');
      const ac = String(a.comment || '');
      if(bc !== ac && (bc || ac)){
        out.push('Комментарий: обновлён');
      }
      return out;
    }

    (async ()=>{
      try {
        const url = apiBase() + '/salaries/shifts/' + encodeURIComponent(String(sid)) + '/audit';
        const r = await apiFetch(url);
        if(r.status === 403){
          window.location.href = pageBase() + '/salaries';
          return;
        }
        const data = await r.json().catch(()=> ({}));
        if(!data || !data.ok){
          historyEl.innerHTML = '<div class="muted">Не удалось загрузить историю</div>';
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        if(!items.length){
          historyEl.innerHTML = '<div class="muted">Пока нет записей</div>';
          return;
        }

        const blocks = [];
        for(let i=0;i<items.length;i++){
          const ev = items[i] || {};
          const who = String(ev.actor_name || ev.actor_user_id || '—');
          const when = fmtDt(ev.created_at);
          const t = String(ev.event_type || '');

          if(t === 'adjustment_create'){
            const a = (ev.after && typeof ev.after === 'object') ? ev.after : {};
            const delta = String(a.delta_amount || '');
            const cmt = String(a.comment || '');
            blocks.push(
              '<div class="salary-shifts-history-line"><b>' + escapeHtml(when) + '</b> — ' + escapeHtml(who)
              + '<br/>Корректировка: <b>' + escapeHtml(delta) + ' ₽</b>' + (cmt ? (' — ' + escapeHtml(cmt)) : '')
              + '</div>'
            );
            continue;
          }

          const lines = diffLines(ev.before, ev.after);
          const body = lines.length ? ('<br/>' + lines.map((x)=>escapeHtml(x)).join('<br/>')) : '';
          blocks.push(
            '<div class="salary-shifts-history-line"><b>' + escapeHtml(when) + '</b> — ' + escapeHtml(who)
            + (t ? ('<br/>' + escapeHtml(t)) : '')
            + body
            + '</div>'
          );
        }
        historyEl.innerHTML = blocks.join('');
      } catch(_e){
        historyEl.innerHTML = '<div class="muted">Ошибка загрузки</div>';
      }
    })();
  }

  function stateBadge(state){
    const s = String(state || 'worked');
    const label = (s === 'worked') ? '✅ Отработано'
      : (s === 'day_off') ? '🟡 Выходной'
      : (s === 'overtime') ? '⚡ Переработка'
      : (s === 'skip') ? '❌ Пропуск'
      : (s === 'needs_review') ? '⚠️ Требует подтверждения'
      : '⚠️ Требует подтверждения';
    const cls = (s === 'worked' || s === 'day_off' || s === 'overtime' || s === 'skip' || s === 'needs_review') ? s : 'needs_review';
    return '<span class="salary-shifts-state-badge ' + escapeHtml(cls) + '">' + escapeHtml(label) + '</span>';
  }

  function stateLabelRu(state){
    const s = String(state || 'worked');
    return (s === 'worked') ? 'Отработано'
      : (s === 'day_off') ? 'Выходной'
      : (s === 'overtime') ? 'Переработка'
      : (s === 'skip') ? 'Пропуск'
      : (s === 'needs_review') ? 'Требует подтверждения'
      : s;
  }

  function prettyManualHours(v){
    const h = fmtHoursHuman(v);
    return h ? h : '—';
  }

  function prettyMoneyOrDash(v){
    const raw = String(v === null || v === undefined ? '' : v).trim();
    if(!raw) return '—';
    return fmtRub(raw);
  }

  function changeLines(beforeSnap, payload){
    const out = [];
    const b = beforeSnap || { state: '', manual_hours: '', manual_amount_override: '', comment: '' };
    const p = payload || { state: '', manual_hours: '', manual_amount_override: '', comment: '' };

    const norm = (v)=>String(v || '').trim();

    if(norm(p.state) !== norm(b.state)){
      out.push('Статус: ' + stateLabelRu(b.state) + ' → ' + stateLabelRu(p.state));
    }
    if(norm(p.manual_hours) !== norm(b.manual_hours)){
      out.push('Часы (ручные): ' + prettyManualHours(b.manual_hours) + ' → ' + prettyManualHours(p.manual_hours));
    }
    if(norm(p.manual_amount_override) !== norm(b.manual_amount_override)){
      out.push('Сумма (ручная): ' + prettyMoneyOrDash(b.manual_amount_override) + ' → ' + prettyMoneyOrDash(p.manual_amount_override));
    }
    if(norm(p.comment) !== norm(b.comment)){
      out.push('Комментарий: обновлён');
    }
    return out;
  }

  let view = (function(){
    const d = ymToDate(String(INIT.month || ''));
    return d || (function(){ const x = new Date(); x.setDate(1); x.setHours(0,0,0,0); return x; })();
  })();

  let shifts = []; // from API
  let selectedShiftId = 0;
  let selectedDayIso = '';
  let initialSnapshot = null;

  function isConfirmed(it){
    try {
      return !!(it && it.confirmed_at);
    } catch(_){
      return false;
    }
  }

  function syncConfirmUi(it){
    try {
      if(!confirmWrapEl) return;
      if(!it || !String(selectedDayIso || '').trim()){
        confirmWrapEl.style.display = 'none';
        return;
      }
      const need = isDeviation(it);
      const ok = isConfirmed(it);
      if(ok){
        confirmWrapEl.style.display = '';
        if(confirmBadgeEl) confirmBadgeEl.textContent = '✅ Подтверждено';
        if(confirmBtn){
          confirmBtn.disabled = true;
          confirmBtn.style.display = 'none';
        }
        return;
      }
      if(need){
        confirmWrapEl.style.display = '';
        if(confirmBadgeEl) confirmBadgeEl.textContent = '⚠ Требует подтверждения';
        if(confirmBtn){
          confirmBtn.disabled = false;
          confirmBtn.style.display = '';
        }
        return;
      }
      confirmWrapEl.style.display = '';
      if(confirmBadgeEl) confirmBadgeEl.textContent = '✅ Подтверждено';
      if(confirmBtn){
        confirmBtn.disabled = true;
        confirmBtn.style.display = 'none';
      }
    } catch(_){ }
  }

  function shiftByDayIso(dayIso){
    for (let i=0;i<shifts.length;i++){
      const s = shifts[i];
      if (String(s.day || '') === String(dayIso)) return s;
    }
    return null;
  }

  function shiftById(id){
    const sid = Number(id || 0);
    for (let i=0;i<shifts.length;i++){
      if (Number(shifts[i].shift_id || 0) === sid) return shifts[i];
    }
    return null;
  }

  function selectShiftByDay(dayIso){
    selectedDayIso = String(dayIso || '').trim();
    const it = shiftByDayIso(selectedDayIso);
    if(!it){
      selectedShiftId = 0;
      if(selectedEl) selectedEl.textContent = selectedDayIso ? selectedDayIso : 'Выберите день';
      if(editorEl) editorEl.style.display = 'none';
      if(editorEmptyEl) editorEmptyEl.style.display = '';
      if(hintEl) hintEl.style.display = '';
      initialSnapshot = null;
      try { syncConfirmUi(null); } catch(_){ }
      return;
    }

    selectedShiftId = Number(it.shift_id || 0);
    if(selectedEl) selectedEl.textContent = String(it.day || '');
    if(editorEl) editorEl.style.display = '';
    if(editorEmptyEl) editorEmptyEl.style.display = 'none';
    if(hintEl) hintEl.style.display = 'none';

    try { if(stateEl) stateEl.value = String(it.state || 'worked'); } catch(_) {}
    try { if(mhEl) mhEl.value = String(it.manual_hours || ''); } catch(_) {}
    try { if(maEl) maEl.value = String(it.manual_amount_override || ''); } catch(_) {}
    try { if(cEl) cEl.value = String(it.comment || ''); } catch(_) {}

    const plannedOnly = (Number(it.shift_id || 0) <= 0);
    try {
      if(adjDeltaEl) adjDeltaEl.disabled = plannedOnly;
      if(adjCommentEl) adjCommentEl.disabled = plannedOnly;
      if(adjAddBtn) adjAddBtn.disabled = plannedOnly;
    } catch(_){ }

    setErr(errEl, null);
    setErr(adjErrEl, null);
    setFieldError(labelCommentEl, commentFieldErrEl, null);
    setFieldError(labelStateEl, null, null);
    setFieldError(labelAdjDeltaEl, null, null);
    setFieldError(labelAdjCommentEl, null, null);

    renderAdjustments(it);

    initialSnapshot = {
      state: String(it.state || 'worked'),
      manual_hours: String(it.manual_hours || ''),
      manual_amount_override: String(it.manual_amount_override || ''),
      comment: String(it.comment || ''),
    };

    syncConfirmUi(it);

    try{
      document.querySelectorAll('.schedule-day').forEach((el)=>el.classList.remove('salary-shifts-day-selected'));
      const d = document.querySelector('[data-salary-shifts-day="' + CSS.escape(String(it.day || '')) + '"]');
      if (d) d.classList.add('salary-shifts-day-selected');
    }catch(_){ }

    try {
      if(editorCardEl && editorCardEl.scrollIntoView){
        editorCardEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    } catch(_){ }

    try {
      if(historyEl && historyEl.style.display !== 'none'){
        renderHistoryForSelected();
      }
    } catch(_){ }
  }

  function selectDay(dayIso){
    selectShiftByDay(String(dayIso || '').trim());
  }

  function renderAdjustments(it){
    if(!adjListEl) return;
    const list = Array.isArray(it && it.adjustments) ? it.adjustments : [];
    if(!list.length){
      adjListEl.innerHTML = '<div class="salary-shifts-adj-empty">Пока нет корректировок</div>';
      return;
    }
    adjListEl.innerHTML = list.map((a)=>{
      const d = String(a.delta_amount || '0');
      const c = String(a.comment || '').trim();
      return '<div class="salary-shifts-adj-item">'
        + '<div><b>' + escapeHtml(d) + ' ₽</b>' + (c ? (' — ' + escapeHtml(c)) : '') + '</div>'
      + '</div>';
    }).join('');
  }

  function render(){
    if(!gridEl) return;

    const y = view.getFullYear();
    const mo = view.getMonth();
    if(titleEl) titleEl.textContent = MONTHS_RU[mo] + ' ' + String(y);

    const first = new Date(y, mo, 1);
    const start = new Date(first);
    // Monday=0..Sunday=6
    const dow = (first.getDay() + 6) % 7;
    start.setDate(first.getDate() - dow);

    const days = [];
    for (let i=0;i<42;i++){
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      days.push(d);
    }

    const ym = dateToYm(view);

    const onlyDev = !!(onlyDevEl && onlyDevEl.checked);

    gridEl.innerHTML = days.map((d)=>{
      const other = d.getMonth() !== mo;
      const dayIso = isoDate(d);
      const it = shiftByDayIso(dayIso);
      const kind = it ? String(it.state || '') : '';
      const needsReview = isDeviation(it);
      const confirmed = isConfirmed(it);
      const paid = isPaid(it);
      const showMeta = !!it && (!onlyDev || needsReview);

      const plannedOnly = !!it && (Number(it.shift_id || 0) <= 0);

      const dayClass = 'schedule-day'
        + (other ? ' other' : '')
        + ((kind === 'worked' || kind === 'overtime') ? ' work' : '')
        + ((kind === 'day_off' || kind === 'skip' || kind === 'needs_review') ? ' off' : '');

      const meta = showMeta ? (
        '<div class="salary-shifts-day-meta">'
          + '<div class="salary-shifts-day-badges">'
            + (plannedOnly ? '<span class="salary-shifts-state-badge needs_review">⚠️ Не отмечено</span>' : stateBadge(kind))
            + (needsReview ? '<span class="salary-shifts-state-badge needs_review">⚠️ Требует подтверждения</span>' : '')
            + (confirmed ? '<span class="salary-shifts-state-badge confirmed">✅ Подтверждено</span>' : '')
            + (paid ? '<span class="salary-shifts-state-badge paid">💰 Выплачено</span>' : '')
          + '</div>'
          + '<div class="salary-shifts-chips">'
            + (pickHoursForChip(it) ? ('<span class="salary-shifts-chip hours">' + escapeHtml(pickHoursForChip(it)) + '</span>') : '')
            + '<span class="salary-shifts-chip money">' + escapeHtml(fmtRub(it.total_amount)) + '</span>'
          + '</div>'
        + '</div>'
      ) : '';

      return '<div class="' + dayClass + '" data-salary-shifts-day="' + escapeHtml(dayIso) + '">'
        + '<div class="schedule-day-top">'
          + '<div class="schedule-day-num">' + String(d.getDate()) + '</div>'
          + '<div class="schedule-day-dot">' + (needsReview ? '<span title="Отклонение">⚠️</span>' : (paid ? '<span title="Выплачено">💰</span>' : '')) + '</div>'
        + '</div>'
        + meta
      + '</div>';
    }).join('');

    try{
      gridEl.querySelectorAll('[data-salary-shifts-day]').forEach((el)=>{
        if (el.dataset.bound === '1') return;
        el.dataset.bound = '1';
        el.addEventListener('click', ()=>{
          const dayIso = String(el.dataset.salaryShiftsDay || '').trim();
          if(!dayIso) return;
          selectDay(dayIso);
        });
      });
    }catch(_){ }

    // preserve selection highlight
    if (selectedDayIso) selectDay(selectedDayIso);

    // sync month input
    try{ if(monthInput) monthInput.value = ym; }catch(_){ }
  }

  async function loadMonth(){
    const ym = dateToYm(view);
    try {
      if(gridEl) gridEl.innerHTML = '<div class="muted">Загрузка…</div>';
      const url = apiBase() + '/salaries/shifts/list?user_id=' + encodeURIComponent(String(userId)) + '&month=' + encodeURIComponent(ym);
      const r = await apiFetch(url);
      if(r.status === 403){
        window.location.href = pageBase() + '/salaries';
        return;
      }
      const data = await r.json().catch(()=> ({}));
      if(!data || !data.ok){
        shifts = [];
        render();
        return;
      }
      shifts = Array.isArray(data.items) ? data.items : [];
      if(!selectedDayIso){
        selectedDayIso = isoDate(new Date(view.getFullYear(), view.getMonth(), 1));
      }
      render();
      await loadSummary();
      try {
        if (selectedDayIso) syncConfirmUi(shiftByDayIso(selectedDayIso));
      } catch(_){ }
    } catch (e){
      shifts = [];
      render();
    }
  }

  async function confirmSelectedShift(){
    if(!selectedDayIso) return;
    const it = shiftByDayIso(selectedDayIso);
    if(!it) return;
    if(isConfirmed(it)) return;
    if(!isDeviation(it)) return;

    try {
      const day = String(it.day || selectedDayIso || '').trim();
      const ok = (window.crmConfirm)
        ? await window.crmConfirm('Подтвердить смену за ' + day + '?', { title: 'Подтвердите действие', okText: 'Подтвердить' })
        : window.confirm('Подтвердить смену за ' + day + '?');
      if(!ok) return;
    } catch(_){ }

    try {
      if(confirmBtn) confirmBtn.disabled = true;
      const sid = Number(it.shift_id || 0);
      const url = (sid > 0)
        ? (apiBase() + '/salaries/shifts/' + String(sid) + '/confirm')
        : (apiBase() + '/salaries/shifts/planned/confirm');
      const payload = (sid > 0)
        ? '{}'
        : JSON.stringify({
            user_id: userId,
            day: String(it.day || selectedDayIso || '').trim(),
            month: dateToYm(view),
            state: String(it.state || 'worked'),
            manual_hours: String(it.manual_hours || ''),
            manual_amount_override: String(it.manual_amount_override || ''),
            comment: String(it.comment || '').trim(),
          });
      const r = await apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload
      });
      if(r.status === 403){
        window.location.href = pageBase() + '/salaries';
        return;
      }
      if(r.status === 404){
        try { window.crmAlert && window.crmAlert('Смена не найдена'); } catch(_){ }
        return;
      }
      const d = await r.json().catch(()=> ({}));
      if(!d || !d.ok){
        try { window.crmAlert && window.crmAlert('Не удалось подтвердить смену'); } catch(_){ }
        return;
      }
      await loadMonth();
      try { window.crmAlert && window.crmAlert('Смена подтверждена', { title: 'Готово' }); } catch(_){ }
    } catch(_e){
      try { window.crmAlert && window.crmAlert('Ошибка подтверждения'); } catch(_){ }
    } finally {
      try { if(confirmBtn) confirmBtn.disabled = false; } catch(_){ }
    }
  }

  async function loadSummary(){
    const ym = dateToYm(view);
    try {
      const url = apiBase() + '/salaries/' + encodeURIComponent(String(userId)) + '/summary?month=' + encodeURIComponent(ym);
      const r = await apiFetch(url);
      if(r.status === 403){
        window.location.href = pageBase() + '/salaries';
        return;
      }
      const data = await r.json().catch(()=> ({}));
      if(!data || !data.ok) return;

      const pos = String(data.position_ru || data.position || '').trim() || '—';
      const hrRaw = (data.hour_rate !== undefined && data.hour_rate !== null) ? String(data.hour_rate).trim() : '';
      const hrNum = hrRaw ? Number(hrRaw.replace(',', '.')) : NaN;
      const hr = Number.isFinite(hrNum) ? (hrNum.toFixed(2) + ' ₽/ч') : (hrRaw ? (hrRaw + ' ₽/ч') : '—');

      if(summaryNameEl) summaryNameEl.textContent = userName;
      if(summaryMonthEl) summaryMonthEl.textContent = ym;
      if(summaryDotEl) summaryDotEl.style.background = safeUserColor || '#94a3b8';
      if(summaryPositionEl) summaryPositionEl.textContent = pos;
      if(summaryRateEl) summaryRateEl.textContent = hr;
      if(summaryShiftsEl) summaryShiftsEl.textContent = String(Number(data.shifts_total || 0));
      if(summaryReviewEl) summaryReviewEl.textContent = String(Number(data.needs_review_total || 0));
      if(summaryAccruedEl) summaryAccruedEl.textContent = fmtRub(data.accrued || '0');
      if(summaryPaidEl) summaryPaidEl.textContent = fmtRub(data.paid || '0');
      if(summaryBalanceEl) summaryBalanceEl.textContent = fmtRub(data.balance || '0');
    } catch(_){ }
  }

  function isChangedPayload(payload){
    if(!initialSnapshot) return true;
    const norm = (v)=>String(v || '').trim();
    return (
      norm(payload.state) !== norm(initialSnapshot.state) ||
      norm(payload.manual_hours) !== norm(initialSnapshot.manual_hours) ||
      norm(payload.manual_amount_override) !== norm(initialSnapshot.manual_amount_override)
    );
  }

  async function save(){
    if(!selectedDayIso) return;
    setErr(errEl, null);
    setFieldError(labelCommentEl, commentFieldErrEl, null);
    try {
      if(saveBtn) saveBtn.disabled = true;
      const before = initialSnapshot ? Object.assign({}, initialSnapshot) : null;
      const payload = {
        month: dateToYm(view),
        state: String(stateEl && stateEl.value ? stateEl.value : 'worked').trim(),
        manual_hours: String(mhEl && mhEl.value ? mhEl.value : '').trim(),
        manual_amount_override: String(maEl && maEl.value ? maEl.value : '').trim(),
        comment: String(cEl && cEl.value ? cEl.value : '').trim(),
      };

      const changed = isChangedPayload(payload);
      if(changed && !payload.comment){
        setFieldError(labelCommentEl, commentFieldErrEl, 'Комментарий обязателен при изменениях');
        return;
      }
      const it = shiftByDayIso(selectedDayIso);
      const sid = Number(it && it.shift_id ? it.shift_id : 0);
      const url = (sid > 0)
        ? (apiBase() + '/salaries/shifts/' + String(sid) + '/update')
        : (apiBase() + '/salaries/shifts/planned/confirm');
      const bodyPayload = (sid > 0)
        ? JSON.stringify(payload)
        : JSON.stringify(Object.assign({ user_id: userId, day: String(selectedDayIso) }, payload));
      const r = await apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: bodyPayload,
      });
      if(r.status === 403){
        window.location.href = pageBase() + '/salaries';
        return;
      }
      const d = await r.json().catch(()=> ({}));
      if(!d || !d.ok){
        const code = String(d && d.error ? d.error : 'bad');
        if(code === 'comment_required'){ setErr(errEl, 'Комментарий обязателен'); return; }
        if(code === 'bad_manual_hours'){ setErr(errEl, 'Неверные ручные часы'); return; }
        if(code === 'bad_manual_amount'){ setErr(errEl, 'Неверная ручная сумма'); return; }
        setErr(errEl, 'Не удалось сохранить');
        return;
      }
      await loadMonth();
      try { await loadSummary(); } catch(_){ }

      const lines = changeLines(before, payload);
      const msg = lines.length ? lines.map((x)=>('- ' + x)).join('\n') : 'Изменений нет';
      try {
        if(window.crmAlert){
          await window.crmAlert(msg, { title: 'Готово' });
        }
      } catch(_){ }
    } catch (e){
      setErr(errEl, 'Ошибка сохранения');
    } finally {
      if(saveBtn) saveBtn.disabled = false;
    }
  }

  async function addAdj(){
    if(!selectedDayIso) return;
    const it = shiftByDayIso(selectedDayIso);
    const sid = Number(it && it.shift_id ? it.shift_id : 0);
    if(sid <= 0) return;
    setErr(adjErrEl, null);
    try { if(labelAdjDeltaEl) labelAdjDeltaEl.classList.remove('is-error'); } catch(_) {}
    try { if(labelAdjCommentEl) labelAdjCommentEl.classList.remove('is-error'); } catch(_) {}
    const delta = String(adjDeltaEl && adjDeltaEl.value ? adjDeltaEl.value : '').trim();
    const comment = String(adjCommentEl && adjCommentEl.value ? adjCommentEl.value : '').trim();
    if(!delta){
      try { if(labelAdjDeltaEl) labelAdjDeltaEl.classList.add('is-error'); } catch(_) {}
      setErr(adjErrEl, 'Введите сумму');
      return;
    }
    if(!comment){
      try { if(labelAdjCommentEl) labelAdjCommentEl.classList.add('is-error'); } catch(_) {}
      setErr(adjErrEl, 'Комментарий обязателен');
      return;
    }
    try {
      if(adjAddBtn) adjAddBtn.disabled = true;
      const r = await apiFetch(apiBase() + '/salaries/shifts/' + String(sid) + '/adjustments/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month: dateToYm(view), delta_amount: delta, comment: comment }),
      });
      if(r.status === 403){
        window.location.href = pageBase() + '/salaries';
        return;
      }
      const d = await r.json().catch(()=> ({}));
      if(!d || !d.ok){
        const code = String(d && d.error ? d.error : 'bad');
        if(code === 'comment_required'){ setErr(adjErrEl, 'Комментарий обязателен'); return; }
        if(code === 'bad_delta_amount'){ setErr(adjErrEl, 'Неверная сумма'); return; }
        setErr(adjErrEl, 'Не удалось добавить корректировку');
        return;
      }
      try { if(adjDeltaEl) adjDeltaEl.value = ''; } catch(_) {}
      try { if(adjCommentEl) adjCommentEl.value = ''; } catch(_) {}
      await loadMonth();
    } catch (e){
      setErr(adjErrEl, 'Ошибка');
    } finally {
      if(adjAddBtn) adjAddBtn.disabled = false;
    }
  }

  function shiftMonth(delta){
    view.setMonth(view.getMonth() + delta);
    view.setDate(1);
    const ym = dateToYm(view);
    const url = pageBase() + '/salaries/' + encodeURIComponent(String(userId)) + '/shifts?month=' + encodeURIComponent(ym);
    try { window.location.href = url; } catch(_){ }
  }

  try {
    if(prevBtn) prevBtn.addEventListener('click', ()=>shiftMonth(-1));
    if(nextBtn) nextBtn.addEventListener('click', ()=>shiftMonth(1));
    if(applyBtn) applyBtn.addEventListener('click', ()=>{
      const v = String(monthInput && monthInput.value ? monthInput.value : '').trim();
      const d = ymToDate(v);
      if(!d) return;
      const url = pageBase() + '/salaries/' + encodeURIComponent(String(userId)) + '/shifts?month=' + encodeURIComponent(dateToYm(d));
      window.location.href = url;
    });
    if(saveBtn) saveBtn.addEventListener('click', save);
    if(confirmBtn) confirmBtn.addEventListener('click', confirmSelectedShift);
    if(adjAddBtn) adjAddBtn.addEventListener('click', addAdj);

    if(onlyDevEl) onlyDevEl.addEventListener('change', render);
    if(devPrevBtn) devPrevBtn.addEventListener('click', ()=>gotoDeviation(-1));
    if(devNextBtn) devNextBtn.addEventListener('click', ()=>gotoDeviation(1));
    if(exportBtn) exportBtn.addEventListener('click', ()=>{
      const ym = dateToYm(view);
      const csv = shiftsToCsv();
      downloadCsv('salaries_shifts_' + String(userId) + '_' + ym + '.csv', csv);
    });
    if(historyToggleBtn) historyToggleBtn.addEventListener('click', ()=>{
      if(!historyEl) return;
      const isOpen = historyEl.style.display !== 'none';
      historyEl.style.display = isOpen ? 'none' : '';
      if(!isOpen) renderHistoryForSelected();
    });
  } catch(_){ }

  loadMonth();
})();
