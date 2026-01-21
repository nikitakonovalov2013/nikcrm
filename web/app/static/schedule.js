(function(){
  const modalBody = () => document.getElementById('modal-body');

  const IS_ADMIN = (function(){
    try { return !!window.SCHEDULE_IS_ADMIN; } catch (_) { return false; }
  })();
  const IS_MANAGER = (function(){
    try { return !!window.SCHEDULE_IS_MANAGER; } catch (_) { return false; }
  })();

  function openModal(html){
    const b = modalBody();
    if (!b) return;
    b.innerHTML = html;
    window.dispatchEvent(new CustomEvent('open-modal'));
  }

  function closeModal(){
    try { const b = modalBody(); if (b) b.innerHTML = ''; } catch(_){ }
    window.dispatchEvent(new CustomEvent('close-modal'));
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
  function iso(d){ return d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate()); }

  function normalizeTimeValue(v, fallback){
    const s = String(v || '').trim();
    if (!s) return fallback;
    // expect HH:MM
    return s;
  }

  function timeToMinutes(hhmm){
    const s = String(hhmm || '').trim();
    const m = s.match(/^([0-9]{1,2}):([0-9]{2})$/);
    if (!m) return null;
    const hh = Number(m[1]);
    const mm = Number(m[2]);
    if (Number.isNaN(hh) || Number.isNaN(mm)) return null;
    if (hh < 0 || hh > 23) return null;
    if (mm < 0 || mm > 59) return null;
    return hh * 60 + mm;
  }

  function calcHoursInt(startHHMM, endHHMM){
    const s = timeToMinutes(startHHMM);
    const e = timeToMinutes(endHHMM);
    if (s === null || e === null) return null;
    const diff = e - s;
    if (diff <= 0) return null;
    if (diff % 60 !== 0) return null;
    return diff / 60;
  }

  function calcHoursFloat(startHHMM, endHHMM){
    const s = timeToMinutes(startHHMM);
    const e = timeToMinutes(endHHMM);
    if (s === null || e === null) return null;
    const diff = e - s;
    if (diff <= 0) return null;
    return diff / 60;
  }

  function formatHours(h){
    if (h === null || h === undefined) return '';
    const v = Math.round(Number(h) * 10) / 10;
    if (!Number.isFinite(v)) return '';
    // drop trailing .0
    const s = (Math.abs(v - Math.round(v)) < 1e-9) ? String(Math.round(v)) : String(v);
    return s + ' ч';
  }

  function formatShiftInterval(info){
    try {
      if (!info || String(info.kind || '') !== 'work') return '';
      const st = normalizeTimeValue(info.start_time, '10:00');
      const et = normalizeTimeValue(info.end_time, '18:00');
      if (!st || !et) return '';
      const h = calcHoursInt(st, et);
      const hs = (h !== null) ? formatHours(h) : '';
      return st + '–' + et + (hs ? (' (' + hs + ')') : '');
    } catch (_){
      return '';
    }
  }

  function quickPresetTimes(mode){
    const m = String(mode || '');
    if (m === 'preset_10_18') return { start: '10:00', end: '18:00' };
    if (m === 'preset_10_20') return { start: '10:00', end: '20:00' };
    if (m === 'preset_10_22') return { start: '10:00', end: '22:00' };
    return null;
  }

  function shiftStatusLabel(shiftStatus){
    const st = String(shiftStatus || '');
    return (st === 'planned') ? 'Запланировано' :
      (st === 'started') ? 'Открыта' :
      (st === 'closed') ? 'Закрыта' :
      (st === 'pending_approval') ? 'На подтверждении' :
      (st === 'approved') ? 'Подтверждена' :
      (st === 'needs_rework') ? 'На доработку' :
      (st === 'rejected') ? 'Отклонена' :
      (st ? st : '');
  }

  function swapStatusLabel(st){
    const s = String(st || '');
    return (s === 'open') ? 'Открыт' : (s === 'accepted') ? 'Принят' : (s ? s : '—');
  }

  function shiftStatusBadgeHtml(s){
    const st = String((s && s.shift_status) || '');
    const approval = !!(s && s.shift_approval_required);
    if (!st) return '';
    if (st === 'started') return '<span class="schedule-shift-badge started" title="Смена открыта">Открыта</span>';
    if (st === 'pending_approval' || approval) return '<span class="schedule-shift-badge pending" title="Ожидает подтверждения">На подтверждении</span>';
    if (st === 'approved') return '<span class="schedule-shift-badge approved" title="Подтверждено">Подтверждена</span>';
    if (st === 'closed') return '<span class="schedule-shift-badge closed" title="Смена закрыта">Закрыта</span>';
    if (st === 'needs_rework') return '<span class="schedule-shift-badge rework" title="Нужна доработка">Доработка</span>';
    if (st === 'rejected') return '<span class="schedule-shift-badge rejected" title="Отклонено">Отклонена</span>';
    if (st === 'planned') return '';
    return '<span class="schedule-shift-badge" title="' + escapeHtml(st) + '">' + escapeHtml(st) + '</span>';
  }

  function shortShiftStatusText(shiftStatus, approvalRequired){
    const st = String(shiftStatus || '');
    const approval = !!approvalRequired;
    if (!st && !approval) return '';
    if (st === 'approved') return 'Подтверждена';
    if (st === 'pending_approval' || approval) return 'На подтверждении';
    if (st === 'started') return 'Открыта';
    if (st === 'closed') return 'Закрыта';
    if (st === 'needs_rework') return 'Доработка';
    if (st === 'rejected') return 'Отклонена';
    return st;
  }

  function renderStaffNames(info){
    const preview = (info && Array.isArray(info.staff_preview)) ? info.staff_preview : [];
    if (!preview.length) return '';
    const total = (info && (info.staff_total !== null && info.staff_total !== undefined)) ? Number(info.staff_total) : preview.length;

    // All-mode preview: ONLY names (2 rows max) + +N
    if (info && info.all_mode) {
      const rows = preview
        .filter(s => s && String((s && s.name) || '').trim())
        .slice(0, 2)
        .map(s => {
          const c = String((s && s.color) || '#94a3b8');
          const n = String((s && s.name) || '').trim();
          return '<div class="schedule-staff-name-row">'
            + '<div style="display:flex;gap:6px;align-items:flex-start;min-width:0">'
              + '<span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>'
              + '<span class="schedule-staff-name-text">' + escapeHtml(n) + '</span>'
            + '</div>'
          + '</div>';
        })
        .join('');
      if (!rows) return '';
      const extra = (total > 2) ? ('<div class="schedule-staff-more-row">+' + String(total - 2) + '</div>') : '';
      return '<div class="schedule-day-staff-names">' + rows + extra + '</div>';
    }

    // Single-user preview: dot + selected user name
    const selectedName = getSelectedUserName();
    if (selectedName) {
      const c = getSelectedUserColor();
      const kind = (info && info.kind) ? String(info.kind) : '';
      const st = normalizeTimeValue(info && info.start_time ? info.start_time : '', '10:00');
      const et = normalizeTimeValue(info && info.end_time ? info.end_time : '', '18:00');
      const h = (kind === 'work') ? calcHoursInt(st, et) : null;
      const interval = (kind === 'work') ? (st + '–' + et + (h !== null ? (' (' + formatHours(h) + ')') : '')) : (kind === 'off' ? 'Выходной' : '');
      const stShort = shortShiftStatusText(info && info.shift_status ? info.shift_status : '', !!(info && info.shift_approval_required));
      return '<div class="schedule-day-staff-names">'
        + '<div class="schedule-staff-name-row line">'
          + '<div class="schedule-staff-name-left">'
            + '<span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>'
            + '<span class="schedule-staff-name-text">' + escapeHtml(selectedName) + '</span>'
          + '</div>'
        + '</div>'
        + (interval ? ('<div class="schedule-day-label muted">' + escapeHtml(interval) + '</div>') : '')
        + (stShort ? ('<div class="schedule-day-label">' + escapeHtml(stShort) + '</div>') : '')
        + '</div>';
    }

    const names = preview.slice(0, 2).map(s => {
      const c = String((s && s.color) || '#94a3b8');
      const n = String((s && s.name) || '').trim();
      if (!n) return '';
      const badge = shiftStatusBadgeHtml(s);
      const st = normalizeTimeValue(s && s.start_time ? s.start_time : '', '10:00');
      const et = normalizeTimeValue(s && s.end_time ? s.end_time : '', '18:00');
      const h = calcHoursInt(st, et);
      const interval = (st && et) ? (st + '–' + et + (h !== null ? (' (' + formatHours(h) + ')') : '')) : '';
      const em = s && s.is_emergency ? ' <span class="schedule-emergency-mark" title="Экстренная смена">⚡</span>' : '';
      return '<div class="schedule-staff-name-row">'
        + '<div style="display:flex;gap:6px;align-items:flex-start;min-width:0">'
          + '<span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>'
          + '<span class="schedule-staff-name-text">' + escapeHtml(n) + '</span>'
          + em
        + '</div>'
        + (interval ? ('<div class="schedule-staff-hours">' + escapeHtml(interval) + '</div>') : '')
        + (badge ? ('<div class="schedule-staff-status">' + badge + '</div>') : '')
        + '</div>';
    }).filter(Boolean).join('');
    if (!names) return '';
    const more = (total > preview.length) ? ('<div class="schedule-staff-more-row">+' + String(total - preview.length) + '</div>') : '';
    return '<div class="schedule-day-staff-names">' + names + more + '</div>';
  }

  const MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];

  let view = new Date();
  view.setDate(1);

  const USERS = (function(){
    try { return (window.SCHEDULE_USERS && Array.isArray(window.SCHEDULE_USERS)) ? window.SCHEDULE_USERS : []; } catch (_) { return []; }
  })();

  let selectedUserId = null;
  let quickMode = '';
  let selectedAll = false;

  function isAllViewMode(){
    return isAllFilterMode();
  }

  function canEditSchedule(){
    // Editing is only for admin/manager AND only in single-user mode.
    return (IS_ADMIN || IS_MANAGER) && isSingleUserMode();
  }

  function isAllFilterMode(){
    return !!selectedAll;
  }

  function isSingleUserMode(){
    try {
      if (isAllFilterMode()) return false;
      return !!selectedUserId;
    } catch (_){
      return false;
    }
  }

  function requireSingleUserModeOrWarn(){
    if (isSingleUserMode()) return true;
    try { window.crmAlert && window.crmAlert('Выберите сотрудника, чтобы назначать смены'); } catch (_){ }
    return false;
  }

  function getSelectedUserName(){
    try {
      if (!selectedUserId) return '';
      const u = (USERS || []).find(x => String(x.id) === String(selectedUserId));
      return (u && u.name) ? String(u.name) : '';
    } catch (_){
      return '';
    }
  }

  function getSelectedUserColor(){
    try {
      if (!selectedUserId) return '#94a3b8';
      const u = (USERS || []).find(x => String(x.id) === String(selectedUserId));
      const c = (u && u.color) ? String(u.color) : '';
      return c || '#94a3b8';
    } catch (_){
      return '#94a3b8';
    }
  }

  function updateUiForMode(){
    try {
      const allMode = isAllFilterMode();
      const quickWrap = document.querySelector('.schedule-quick');
      if (quickWrap) quickWrap.style.display = allMode ? 'none' : '';
      const emergencyBtn = document.getElementById('schedule-emergency');
      if (emergencyBtn) emergencyBtn.style.display = allMode ? 'none' : '';
      if (allMode) {
        quickMode = '';
        try {
          document.querySelectorAll('.schedule-quick-btn').forEach(b => b.classList.remove('active'));
          const tw = document.getElementById('schedule-quick-time-wrap');
          if (tw) tw.style.display = 'none';
        } catch (_){ }
      }
    } catch (_){ }
  }

  async function apiJson(url, opts){
    const o = Object.assign({ credentials: 'include' }, (opts || {}));
    const r = await fetch(url, o);
    if (r.status === 401) {
      try { window.location.href = '/crm/auth/tg'; } catch (_){ }
      throw new Error('Сессия истекла. Откройте страницу ещё раз.');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = (data && (data.detail || data.error)) ? (data.detail || data.error) : ('HTTP ' + r.status);
      throw new Error(msg);
    }
    return data;
  }

  async function loadMonth(){
    const y = view.getFullYear();
    const m = view.getMonth() + 1;
    let url = '/crm/api/schedule/month?year=' + String(y) + '&month=' + String(m);
    if (selectedAll) {
      url += '&all=1';
    } else if (selectedUserId) {
      url += '&user_id=' + String(selectedUserId);
    }
    const data = await apiJson(url);
    renderMonth(data);
  }

  function dayClass(info){
    if (!info || !info.kind) return 'none';
    if (info.kind === 'work') return 'work';
    if (info.kind === 'off') return 'off';
    return 'none';
  }

  function bindUserSelector(){
    const wrap = document.getElementById('schedule-user-wrap');
    const sel = document.getElementById('schedule-user');
    if (!wrap || !sel) return;
    if (!Array.isArray(USERS) || !USERS.length) return;

    wrap.style.display = '';
    const baseOpts = USERS.map(u => '<option value="' + String(u.id) + '">' + escapeHtml(u.name) + '</option>').join('');
    // "Все" доступно всем ролям, но режим всегда view-only.
    sel.innerHTML = '<option value="">Все</option>' + baseOpts;
    if (IS_ADMIN || IS_MANAGER) {
      selectedAll = true;
      selectedUserId = null;
      try { sel.value = ''; } catch (_){ }
    } else {
      // Обычный пользователь по умолчанию видит только себя (первый в списке)
      try {
        const first = USERS[0];
        selectedUserId = first ? Number(first.id) : null;
        selectedAll = false;
        try { sel.value = selectedUserId ? String(selectedUserId) : ''; } catch (_){ }
      } catch (_){ selectedUserId = null; selectedAll = true; }
    }
    updateUiForMode();
    sel.addEventListener('change', async () => {
      if (!sel.value) {
        selectedAll = true;
        selectedUserId = null;
      } else {
        selectedAll = false;
        selectedUserId = Number(sel.value);
      }
      updateUiForMode();
      await loadMonth();
    });
  }

  function bindQuick(){
    document.querySelectorAll('.schedule-quick-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const q = String(btn.dataset.quick || '');
        quickMode = q;
        document.querySelectorAll('.schedule-quick-btn').forEach(b => b.classList.remove('active'));
        if (q) btn.classList.add('active');

        const wrap = document.getElementById('schedule-quick-time-wrap');
        if (wrap) wrap.style.display = (q === 'custom') ? '' : 'none';
      });
    });
  }

  function bindEmergency(){
    const em = document.getElementById('schedule-emergency');
    if (!em) return;
    em.addEventListener('click', () => {
      openModal(renderEmergencyModal());
      const b = modalBody();
      if (!b) return;
      const createBtn = document.getElementById('em-create');
      const cancelBtn = document.getElementById('em-cancel');
      const startEl = document.getElementById('em-start');
      const endEl = document.getElementById('em-end');
      const hoursEl = document.getElementById('em-hours');
      if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

      const syncEmergencyHours = () => {
        try {
          const st = startEl ? String(startEl.value || '').trim() : '';
          const et = endEl ? String(endEl.value || '').trim() : '';
          const h = calcHoursInt(st, et);
          if (!hoursEl) return;
          hoursEl.textContent = (h !== null) ? (String(h) + ' ч') : '—';
        } catch (_){ }
      };
      if (startEl) startEl.addEventListener('change', syncEmergencyHours);
      if (endEl) endEl.addEventListener('change', syncEmergencyHours);
      syncEmergencyHours();

      if (createBtn) createBtn.addEventListener('click', async () => {
        const d = document.getElementById('em-day');
        const st = document.getElementById('em-start');
        const et = document.getElementById('em-end');
        const c = document.getElementById('em-comment');
        await createEmergencyShift({
          day: d ? d.value : '',
          start_time: st ? st.value : '',
          end_time: et ? et.value : '',
          comment: c ? c.value : '',
        });
      });
    });
  }

  function renderMonth(data){
    const title = document.getElementById('schedule-title');
    const grid = document.getElementById('schedule-grid');
    if (!title || !grid) return;

    const y = Number(data.year);
    const m = Number(data.month);
    title.textContent = (MONTHS_RU[(m-1) >= 0 ? (m-1) : 0] || '') + ' ' + String(y);

    const first = new Date(y, m-1, 1);
    const firstDow = (first.getDay() + 6) % 7; // Monday=0
    const start = new Date(first);
    start.setDate(first.getDate() - firstDow);

    const totalCells = 42;
    const days = (data.days || {});
    const allMode = isAllFilterMode();
    const singleUserMode = isSingleUserMode();

    const today = new Date();
    const todayIso = iso(today);

    const cells = [];
    for (let i=0;i<totalCells;i++){
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const inMonth = (d.getMonth() === (m-1));
      const key = iso(d);
      const info = days[key] || null;
      const cls = dayClass(info);
      const isEmergency = !!(info && info.is_emergency);
      const isToday = (key === todayIso);
      const shiftStatus = info && info.shift_status ? String(info.shift_status) : '';
      const label = (allMode || singleUserMode) ? '' : ((info && info.kind === 'work') ? (function(){
        const st = normalizeTimeValue(info.start_time, '10:00');
        const et = normalizeTimeValue(info.end_time, '18:00');
        const h = calcHoursInt(st, et);
        const hs = (h !== null) ? formatHours(h) : '';
        return hs ? hs : '';
      })() : (info && info.kind === 'off' ? 'Выходной' : (shiftStatus ? 'Факт' : '')));
      const interval = (allMode || singleUserMode) ? '' : formatShiftInterval(info);
      const shiftAmount = (info && (info.shift_amount !== null && info.shift_amount !== undefined)) ? Number(info.shift_amount) : null;
      const factLabel = (allMode || singleUserMode) ? '' : (shiftStatus ? shiftStatusLabel(shiftStatus) : '');
      const staffNamesHtml = renderStaffNames(info);
      cells.push(
        '<button type="button" class="schedule-day ' + (inMonth ? '' : 'other') + ' ' + cls + ' ' + (isEmergency ? 'emergency' : '') + ' ' + (isToday ? 'today' : '') + '" data-day="' + key + '">' +
          '<div class="schedule-day-top">' +
            '<div class="schedule-day-num">' + String(d.getDate()) + '</div>' +
            '<div class="schedule-day-dot">' +
              (isEmergency ? '<span class="schedule-emergency-mark" title="Экстренная смена">⚡</span>' : '') +
              '<span class="schedule-dot ' + cls + '"></span>' +
            '</div>' +
          '</div>' +
          staffNamesHtml +
          (!allMode && label ? ('<div class="schedule-day-label">' + escapeHtml(label) + '</div>') : '') +
          (!allMode && interval ? ('<div class="schedule-day-label muted">' + escapeHtml(interval) + '</div>') : '') +
          (!allMode && factLabel ? ('<div class="schedule-day-label">' + escapeHtml(factLabel) + (shiftAmount !== null ? (' · ' + escapeHtml(String(shiftAmount)) + ' ₽') : '') + '</div>') : '') +
        '</button>'
      );
    }

    grid.innerHTML = cells.join('');

    grid.querySelectorAll('.schedule-day').forEach(btn => {
      btn.addEventListener('click', async () => {
        const day = String(btn.dataset.day || '');
        const info = (days && days[day]) ? days[day] : null;
        if (quickMode) {
          try {
            await applyQuick(day);
            await loadMonth();
          } catch (e) {
            try { window.crmAlert && window.crmAlert((e && e.message) || 'Не удалось сохранить изменения'); } catch (_){ }
          }
          return;
        }
        openDayModal(day, info);
      });
    });
  }

  function renderDayModal(day, info){
    const kind = (info && info.kind) ? String(info.kind) : '';
    const isEmergency = !!(info && info.is_emergency);

    const st = normalizeTimeValue(info && info.start_time ? info.start_time : '', '10:00');
    const et = normalizeTimeValue(info && info.end_time ? info.end_time : '', '18:00');

    const shiftStatus = info && info.shift_status ? String(info.shift_status) : '';
    const shiftAmount = (info && (info.shift_amount !== null && info.shift_amount !== undefined)) ? Number(info.shift_amount) : null;
    const factStr = shiftStatus ? shiftStatusLabel(shiftStatus) : '';
    const hoursF = (kind === 'work') ? calcHoursInt(st, et) : null;
    const statusStr = kind === 'work'
      ? ('Рабочий день' + (isEmergency ? ' ⚡' : ''))
      : (kind === 'off' ? 'Выходной' : (shiftStatus ? 'План очищен (факт сохранён)' : 'Не задано'));
    const intervalStr = (kind === 'work') ? (st + '–' + et + (hoursF !== null ? (' (' + formatHours(hoursF) + ')') : '')) : '';

    const isAllMode = isAllViewMode() || !!(info && info.all_mode);
    const canManage = canEditSchedule();
    const headerStatus = isAllMode ? 'Просмотр' : statusStr;
    return (
      '<div class="modal-header">' + escapeHtml(day) + '</div>' +
      '<div class="modal-body tasks-modal">' +
        '<div class="schedule-modal-head">' +
          '<div class="schedule-modal-date">' + escapeHtml(day) + '</div>' +
          '<div class="schedule-modal-status muted">' + escapeHtml(headerStatus) + (!isAllMode && intervalStr ? (' · ' + escapeHtml(intervalStr)) : '') + (!isAllMode && factStr ? (' · ' + escapeHtml(factStr) + (shiftAmount !== null ? (' · ' + escapeHtml(String(shiftAmount)) + ' ₽') : '')) : '') + '</div>' +
        '</div>' +
        ((kind === 'work' && canManage && !isAllMode) ? (
          '<div class="task-field" style="margin-top:10px">' +
            '<div class="task-field-label">Время смены</div>' +
            '<div style="display:flex;gap:8px;align-items:center">' +
              '<input class="input" type="time" id="day-start" value="' + escapeHtml(st) + '" style="width:140px" />' +
              '<div class="muted">—</div>' +
              '<input class="input" type="time" id="day-end" value="' + escapeHtml(et) + '" style="width:140px" />' +
              '<button class="btn" type="button" data-action="save_time">Сохранить</button>' +
            '</div>' +
          '</div>'
        ) : '') +
        ((canManage && !isAllMode) ? (
          '<div class="divider"></div>' +
          '<div class="schedule-modal-actions">' +
            '<button class="btn" type="button" data-action="preset_10_18">10:00–18:00 (8 часов)</button>' +
            '<button class="btn" type="button" data-action="preset_10_20">10:00–20:00 (10 часов)</button>' +
            '<button class="btn" type="button" data-action="preset_10_22">10:00–22:00 (12 часов)</button>' +
            '<button class="btn-outline" type="button" data-action="off">Выходной</button>' +
            '<button class="btn-outline" type="button" data-action="clear">Очистить</button>' +
            ((IS_ADMIN || IS_MANAGER) ? '<button class="btn-outline" type="button" data-action="delete_shift">Удалить смену</button>' : '') +
          '</div>'
        ) : '') +
        '<div class="divider"></div>' +
        '<div class="schedule-staff-block">' +
          '<div class="schedule-staff-title">Сотрудники в этот день</div>' +
          '<div class="schedule-staff-list" id="schedule-staff-list">Загрузка…</div>' +
          '<div id="schedule-swap-info"></div>' +
        '</div>' +
        '<div class="schedule-modal-footer">' +
          '<button type="button" class="btn-outline" data-action="close">Закрыть</button>' +
        '</div>' +
      '</div>'
    );
  }

  async function applyQuick(day){
    if (!requireSingleUserModeOrWarn()) return;
    const act = String(quickMode || '');
    let payload = { day: day };
    if (selectedUserId) payload.user_id = Number(selectedUserId);

    const preset = quickPresetTimes(act);
    if (preset) {
      payload = Object.assign(payload, { kind: 'work', start_time: preset.start, end_time: preset.end });
    } else if (act === 'custom') {
      const qs = document.getElementById('schedule-quick-start');
      const qe = document.getElementById('schedule-quick-end');
      const quickStart = normalizeTimeValue(qs ? qs.value : '', '10:00');
      const quickEnd = normalizeTimeValue(qe ? qe.value : '', '18:00');
      const h = calcHoursInt(quickStart, quickEnd);
      if (h === null) throw new Error('Часы должны быть целыми (например 10:00–18:00)');
      payload = Object.assign(payload, { kind: 'work', start_time: quickStart, end_time: quickEnd });
    }
    if (act === 'off') payload = Object.assign(payload, { kind: 'off' });
    if (act === 'clear') payload = Object.assign(payload, { kind: '' });
    await apiJson('/crm/api/schedule/day', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }

  async function loadDayStaff(day){
    const wrap = document.getElementById('schedule-staff-list');
    const swapWrap = document.getElementById('schedule-swap-info');
    if (!wrap) return;
    try {
      const data = await apiJson('/crm/api/schedule/day/staff?day=' + encodeURIComponent(String(day)));
      const staff = (data && data.staff) ? data.staff : [];
      const swap = (data && data.swap_request) ? data.swap_request : null;

      const singleMode = isSingleUserMode();
      const canManage = canEditSchedule();
      const viewOnly = !canManage;

      let list = staff;
      if (singleMode && selectedUserId) {
        list = staff.filter(x => Number(x.user_id) === Number(selectedUserId));
      }

      if (swapWrap) {
        if (viewOnly || !swap) {
          swapWrap.innerHTML = '';
        } else {
          const st = String(swap.status || '');
          const stLabel = swapStatusLabel(st);
          const bonus = (swap.bonus_amount !== null && swap.bonus_amount !== undefined) ? (String(swap.bonus_amount) + ' ₽') : '—';
          swapWrap.innerHTML = '<div class="schedule-staff-title">Запрос замены</div>' +
            '<div class="schedule-staff-item"><span class="schedule-staff-name">Статус</span><span class="schedule-staff-hours">' + escapeHtml(stLabel) + '</span></div>' +
            '<div class="schedule-staff-item"><span class="schedule-staff-name">Доплата</span><span class="schedule-staff-hours">' + escapeHtml(bonus) + '</span></div>';
        }
      }

      if (!list.length) {
        wrap.innerHTML = '<div class="muted">Никто не назначен</div>';
        return;
      }

      const html = list.map(s => {
        const c = String((s && s.color) || '#94a3b8');
        const badge = shiftStatusBadgeHtml(s);
        const st = normalizeTimeValue(s && s.start_time ? s.start_time : '', '10:00');
        const et = normalizeTimeValue(s && s.end_time ? s.end_time : '', '18:00');
        const h = calcHoursInt(st, et);
        const interval = (s && s.kind === 'work')
          ? (st + '–' + et + (h !== null ? (' (' + formatHours(h) + ')') : ''))
          : ((s && s.kind === 'off') ? 'Выходной' : '');
        const em = s.is_emergency ? ' <span class="schedule-emergency-mark" title="Экстренная смена">⚡</span>' : '';

        if (viewOnly) {
          return '<div class="schedule-staff-item" data-user-id="' + String(s.user_id) + '">' +
            '<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">' +
              '<span class="schedule-staff-name" style="min-width:0;display:flex;align-items:center;gap:8px">'
                + '<span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>'
                + '<span>' + escapeHtml(s.name) + '</span>'
              + '</span>' +
              '<span class="schedule-staff-hours">' + escapeHtml(interval) + '</span>' +
            '</div>' +
            '<div style="display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap">' +
              (badge ? badge : '') +
            '</div>' +
          '</div>';
        }

        return '<div class="schedule-staff-item" data-user-id="' + String(s.user_id) + '">' +
          '<div style="display:flex;justify-content:space-between;gap:12px;align-items:center">' +
            '<span class="schedule-staff-name"><span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>' + escapeHtml(s.name) + (badge ? (' ' + badge) : '') + '</span>' +
            '<span class="schedule-staff-hours">' + escapeHtml(interval) + '</span>' +
          '</div>' +
          '<div style="display:flex;gap:8px;align-items:center;margin-top:6px">' +
            '<input class="input" type="time" data-role="st" value="' + escapeHtml(st) + '" style="width:130px" />' +
            '<div class="muted">—</div>' +
            '<input class="input" type="time" data-role="et" value="' + escapeHtml(et) + '" style="width:130px" />' +
            '<button class="btn" type="button" data-action="save_staff_time" data-user-id="' + String(s.user_id) + '">Сохранить</button>' +
          '</div>' +
        '</div>';
      }).join('');
      wrap.innerHTML = html;

      if (viewOnly) return;

      wrap.querySelectorAll('button[data-action="save_staff_time"]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const uid = Number(btn.dataset.userId || 0);
          if (!uid) return;
          const row = btn.closest('.schedule-staff-item');
          const stEl = row ? row.querySelector('input[data-role="st"]') : null;
          const etEl = row ? row.querySelector('input[data-role="et"]') : null;
          const st = normalizeTimeValue(stEl ? stEl.value : '', '');
          const et = normalizeTimeValue(etEl ? etEl.value : '', '');
          const h = calcHoursInt(st, et);
          if (h === null) {
            try { window.crmAlert && window.crmAlert('Часы должны быть целыми (например 10:00–18:00)'); } catch (_){ }
            return;
          }
          try {
            await apiJson('/crm/api/schedule/day', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ day: day, user_id: uid, kind: 'work', start_time: st, end_time: et })
            });
            await loadMonth();
            await loadDayStaff(day);
          } catch (e2) {
            try { window.crmAlert && window.crmAlert((e2 && e2.message) || 'Не удалось сохранить время'); } catch (_){ }
          }
        });
      });
    } catch (e) {
      wrap.innerHTML = '<div class="muted">Не удалось загрузить список</div>';
      if (swapWrap) swapWrap.innerHTML = '';
    }
  }

  function openDayModal(day, info){
    openModal(renderDayModal(day, info));
    const b = modalBody();
    if (!b) return;
    loadDayStaff(day);
    b.querySelectorAll('button[data-action]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const act = String(btn.dataset.action || '');

        // View-only mode: no management actions.
        if (!canEditSchedule()) {
          if (act === 'close') {
            closeModal();
          }
          return;
        }

        if (act === 'close') {
          closeModal();
          return;
        }
        if (act === 'save_time') {
          const s = document.getElementById('day-start');
          const e = document.getElementById('day-end');
          const start = normalizeTimeValue(s ? s.value : '', '');
          const end = normalizeTimeValue(e ? e.value : '', '');
          if (!start || !end) {
            try { window.crmAlert && window.crmAlert('Заполните время начала и конца смены'); } catch (_){ }
            return;
          }
          if (start === end) {
            try { window.crmAlert && window.crmAlert('Начало и конец смены не должны совпадать'); } catch (_){ }
            return;
          }
          if (end < start) {
            try { window.crmAlert && window.crmAlert('Конец смены должен быть позже начала'); } catch (_){ }
            return;
          }
          const h2 = calcHoursInt(start, end);
          if (h2 === null) {
            try { window.crmAlert && window.crmAlert('Часы должны быть целыми (например 10:00–18:00)'); } catch (_){ }
            return;
          }
          let payload = { day: day, kind: 'work', start_time: start, end_time: end };
          if (selectedUserId) payload.user_id = Number(selectedUserId);
          try {
            await apiJson('/crm/api/schedule/day', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            closeModal();
            await loadMonth();
          } catch (e2) {
            try { window.crmAlert && window.crmAlert((e2 && e2.message) || 'Не удалось сохранить время'); } catch (_){ }
          }
          return;
        }

        if (act === 'delete_shift') {
          try {
            if (!(IS_ADMIN || IS_MANAGER)) return;
            let ok2 = false;
            if (!window.crmConfirm) {
              ok2 = window.confirm('Удалить смену? Это уберет ее из календаря');
            } else {
              ok2 = await window.crmConfirm('Удалить смену? Это уберет ее из календаря', { title: 'Подтвердите действие', okText: 'Удалить' });
            }
            if (!ok2) return;
            let payload = { day: day };
            if (selectedUserId) payload.user_id = Number(selectedUserId);
            await apiJson('/crm/api/schedule/delete', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            closeModal();
            await loadMonth();
          } catch (e2) {
            try { window.crmAlert && window.crmAlert((e2 && e2.message) || 'Не удалось удалить смену'); } catch (_){ }
          }
          return;
        }

        let payload = { day: day };
        const preset = quickPresetTimes(act);
        if (preset) payload = Object.assign({ day: day, kind: 'work', start_time: preset.start, end_time: preset.end });
        if (act === 'off') payload = { day: day, kind: 'off' };
        if (act === 'clear') payload = { day: day, kind: '' };
        if (selectedUserId) payload.user_id = Number(selectedUserId);
        try {
          await apiJson('/crm/api/schedule/day', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          closeModal();
          await loadMonth();
        } catch (e) {
          try { window.crmAlert && window.crmAlert((e && e.message) || 'Не удалось сохранить изменения'); } catch (_){ }
        }
      });
    });
  }

  function renderEmergencyModal(){
    const today = iso(new Date());
    return (
      '<div class="modal-header">Экстренная смена</div>' +
      '<div class="modal-body tasks-modal">' +
        '<div class="task-field">' +
          '<div class="task-field-label">Дата</div>' +
          '<input class="input" type="date" id="em-day" value="' + escapeHtml(today) + '" />' +
        '</div>' +
        '<div class="task-field">' +
          '<div class="task-field-label">Время</div>' +
          '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
            '<div class="muted">С</div>' +
            '<input class="input" type="time" id="em-start" value="10:00" style="width:120px" />' +
            '<div class="muted">До</div>' +
            '<input class="input" type="time" id="em-end" value="18:00" style="width:120px" />' +
          '</div>' +
        '</div>' +
        '<div class="task-field">' +
          '<div class="task-field-label">Часы (справочно)</div>' +
          '<div class="muted" id="em-hours">—</div>' +
        '</div>' +
        '<div class="task-field">' +
          '<div class="task-field-label">Комментарий (необязательно)</div>' +
          '<textarea class="textarea" id="em-comment" rows="3" placeholder="Причина / договорённость"></textarea>' +
        '</div>' +
        '<div class="task-actions">' +
          '<button class="btn" type="button" id="em-create">Создать</button>' +
          '<button class="btn-outline" type="button" id="em-cancel">Отмена</button>' +
        '</div>' +
      '</div>'
    );
  }

  async function createEmergencyShift(opts){
    if (!requireSingleUserModeOrWarn()) return;
    const day = String((opts && opts.day) || '').trim();
    const startTime = String((opts && opts.start_time) || '').trim();
    const endTime = String((opts && opts.end_time) || '').trim();
    const comment = String((opts && opts.comment) || '').trim();
    if (!day) {
      try { window.crmAlert && window.crmAlert('Не задан день'); } catch (_){ }
      return;
    }
    const h = calcHoursInt(startTime, endTime);
    if (h === null) {
      const s = timeToMinutes(startTime);
      const e = timeToMinutes(endTime);
      if (s === null || e === null) {
        try { window.crmAlert && window.crmAlert('Неверный формат времени'); } catch (_){ }
        return;
      }
      if (e <= s) {
        try { window.crmAlert && window.crmAlert('Конец должен быть позже начала'); } catch (_){ }
        return;
      }
      try { window.crmAlert && window.crmAlert('Можно только целые часы. Выберите другое время.'); } catch (_){ }
      return;
    }
    let payload = { day: day, start_time: startTime, end_time: endTime, comment: comment };
    if (selectedUserId) payload.user_id = Number(selectedUserId);
    try {
      await apiJson('/crm/api/schedule/emergency', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      closeModal();
      await loadMonth();
    } catch (e) {
      const msg = (e && e.message) ? String(e.message) : '';
      if (msg.includes('Заменить') && window.crmConfirm) {
        const ok = await window.crmConfirm('На этот день уже стоит плановая смена. Заменить на экстренную?', { title: 'Подтвердите действие', okText: 'Заменить' });
        if (!ok) return;
        payload.replace = true;
        await apiJson('/crm/api/schedule/emergency', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        closeModal();
        await loadMonth();
        return;
      }
      try { window.crmAlert && window.crmAlert(msg || 'Не удалось создать экстренную смену'); } catch (_){ }
    }
  }

  function bindNav(){
    const prev = document.getElementById('schedule-prev');
    const next = document.getElementById('schedule-next');
    const todayBtn = document.getElementById('schedule-today');
    if (prev) prev.addEventListener('click', async () => { view.setMonth(view.getMonth()-1); await loadMonth(); });
    if (next) next.addEventListener('click', async () => { view.setMonth(view.getMonth()+1); await loadMonth(); });
    if (todayBtn) todayBtn.addEventListener('click', async () => { const d = new Date(); d.setDate(1); view = d; await loadMonth(); });
  }

  bindNav();
  bindUserSelector();
  bindQuick();
  bindEmergency();
  loadMonth().catch(() => {});
})();
