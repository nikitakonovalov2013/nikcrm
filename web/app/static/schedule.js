(function(){
  const modalBody = () => document.getElementById('modal-body');

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

  function renderStaffNames(info){
    const preview = (info && Array.isArray(info.staff_preview)) ? info.staff_preview : [];
    if (!preview.length) return '';
    const total = (info && (info.staff_total !== null && info.staff_total !== undefined)) ? Number(info.staff_total) : preview.length;
    const names = preview.slice(0, 2).map(s => {
      const c = String((s && s.color) || '#94a3b8');
      const n = String((s && s.name) || '').trim();
      if (!n) return '';
      const badge = shiftStatusBadgeHtml(s);
      return '<div class="schedule-staff-name-row">'
        + '<span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>'
        + '<span class="schedule-staff-name-text">' + escapeHtml(n) + '</span>'
        + (badge ? ('<span class="schedule-staff-status">' + badge + '</span>') : '')
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
    if (selectedUserId) url += '&user_id=' + String(selectedUserId);
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
    sel.innerHTML = USERS.map(u => '<option value="' + String(u.id) + '">' + escapeHtml(u.name) + '</option>').join('');
    try {
      const first = USERS[0];
      selectedUserId = first ? Number(first.id) : null;
    } catch (_){ selectedUserId = null; }
    sel.addEventListener('change', async () => {
      selectedUserId = sel.value ? Number(sel.value) : null;
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
      if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
      if (createBtn) createBtn.addEventListener('click', async () => {
        const d = document.getElementById('em-day');
        const h = document.getElementById('em-hours');
        const c = document.getElementById('em-comment');
        await createEmergencyShift({
          day: d ? d.value : '',
          hours: h ? h.value : '',
          comment: c ? c.value : ''
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
      const label = info && info.kind === 'work' && info.hours ? (String(info.hours) + 'ч') : (info && info.kind === 'off' ? 'Выходной' : '');
      const shiftStatus = info && info.shift_status ? String(info.shift_status) : '';
      const shiftAmount = (info && (info.shift_amount !== null && info.shift_amount !== undefined)) ? Number(info.shift_amount) : null;
      const factLabel = shiftStatus ? shiftStatusLabel(shiftStatus) : '';
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
          (label ? ('<div class="schedule-day-label">' + escapeHtml(label) + '</div>') : '') +
          (factLabel ? ('<div class="schedule-day-label">' + escapeHtml(factLabel) + (shiftAmount !== null ? (' · ' + escapeHtml(String(shiftAmount)) + ' ₽') : '') + '</div>') : '') +
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
    const hours = (info && info.hours) ? Number(info.hours) : 8;
    const isEmergency = !!(info && info.is_emergency);

    const shiftStatus = info && info.shift_status ? String(info.shift_status) : '';
    const shiftAmount = (info && (info.shift_amount !== null && info.shift_amount !== undefined)) ? Number(info.shift_amount) : null;
    const factStr = shiftStatus ? shiftStatusLabel(shiftStatus) : '';
    const statusStr = kind === 'work' ? ('Рабочий день (' + String(hours) + 'ч)' + (isEmergency ? ' ⚡' : '')) : (kind === 'off' ? 'Выходной' : 'Не задано');

    return (
      '<div class="modal-header">' + escapeHtml(day) + '</div>' +
      '<div class="modal-body tasks-modal">' +
        '<div class="schedule-modal-head">' +
          '<div class="schedule-modal-date">' + escapeHtml(day) + '</div>' +
          '<div class="schedule-modal-status muted">' + escapeHtml(statusStr) + (factStr ? (' · ' + escapeHtml(factStr) + (shiftAmount !== null ? (' · ' + escapeHtml(String(shiftAmount)) + ' ₽') : '')) : '') + '</div>' +
        '</div>' +
        '<div class="divider"></div>' +
        '<div class="schedule-modal-actions">' +
          '<button class="btn" type="button" data-action="work8">Рабочий день (8 часов)</button>' +
          '<button class="btn" type="button" data-action="work10">Рабочий день (10 часов)</button>' +
          '<button class="btn" type="button" data-action="work12">Рабочий день (12 часов)</button>' +
          '<button class="btn-outline" type="button" data-action="off">Выходной</button>' +
          '<button class="btn-outline" type="button" data-action="clear">Очистить</button>' +
        '</div>' +
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
    const act = String(quickMode || '');
    let payload = { day: day };
    if (selectedUserId) payload.user_id = Number(selectedUserId);
    if (act === 'work8') payload = Object.assign(payload, { kind: 'work', hours: 8 });
    if (act === 'work10') payload = Object.assign(payload, { kind: 'work', hours: 10 });
    if (act === 'work12') payload = Object.assign(payload, { kind: 'work', hours: 12 });
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

      if (swapWrap) {
        if (!swap) {
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

      if (!staff.length) {
        wrap.innerHTML = '<div class="muted">Никто не назначен</div>';
        return;
      }
      const html = staff.map(s => {
        const label = (s.kind === 'work' && s.hours) ? (String(s.hours) + 'ч') : (s.kind === 'off' ? 'Выходной' : '');
        const em = s.is_emergency ? ' <span class="schedule-emergency-mark" title="Экстренная смена">⚡</span>' : '';
        const c = String((s && s.color) || '#94a3b8');
        const badge = shiftStatusBadgeHtml(s);
        return '<div class="schedule-staff-item">' +
          '<span class="schedule-staff-name"><span class="schedule-staff-dot" style="background:' + escapeHtml(c) + '"></span>' + escapeHtml(s.name) + (badge ? (' ' + badge) : '') + '</span>' +
          '<span class="schedule-staff-hours">' + escapeHtml(label) + '</span>' + em +
        '</div>';
      }).join('');
      wrap.innerHTML = html;
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
        if (act === 'close') {
          closeModal();
          return;
        }
        let payload = { day: day };
        if (act === 'work8') payload = { day: day, kind: 'work', hours: 8 };
        if (act === 'work10') payload = { day: day, kind: 'work', hours: 10 };
        if (act === 'work12') payload = { day: day, kind: 'work', hours: 12 };
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
          '<div class="task-field-label">Длительность</div>' +
          '<select class="input" id="em-hours">' +
            '<option value="8">8 часов</option>' +
            '<option value="10">10 часов</option>' +
            '<option value="12">12 часов</option>' +
          '</select>' +
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
    const day = String((opts && opts.day) || '').trim();
    const hours = Number((opts && opts.hours) || 0);
    const comment = String((opts && opts.comment) || '').trim();
    let payload = { day: day, hours: hours, comment: comment };
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
