// frontend/js/curator.js — куратор группы (контроль заполнения итоговых, выгрузки родителям) и сверка контингента (админ).
// Подключается ПОСЛЕ app.js: использует его глобальные $, esc, api, downloadFile, switchView.

let curatorGroups = [];
let curatorData = null;
let curatorTab = 'summary';
let curatorSummary = null;
let reconcileData = null;
const STATUS_LABEL = { all: 'Заполнено', partial: 'Частично', none: 'Не заполнено' };
const STATUS_CLASS = { all: 'background:#D1FAE5;color:#047857;', partial: 'background:#FEF3C7;color:#B45309;', none: 'background:#FEE2E2;color:#B91C1C;' };

function statusBadge(st) { return `<span class="risk-badge" style="${STATUS_CLASS[st] || ''}">${esc(STATUS_LABEL[st] || st)}</span>`; }

function addNav(menuId, view, icon, label, tab) {
    const id = 'nav-' + view + (tab ? '-' + tab : '');
    if ($(id)) return;
    const menu = $(menuId);
    const b = document.createElement('button');
    b.id = id;
    b.className = 'nav-item';
    b.innerHTML = `<span class="icon">${icon}</span> ${esc(label)}`;
    b.onclick = () => {
        if (view === 'curator-view') curatorSetTab(tab || 'summary');   // «Состав группы» открывает сразу вкладку со студентами
        switchView(view, b);
    };
    const br = menu.querySelector('br');
    br ? menu.insertBefore(b, br) : menu.appendChild(b);
}

async function setupCurator() {
    const role = localStorage.getItem('kkci_role');
    try { curatorGroups = await api('/api/curator/groups'); } catch (e) { return; }
    if (role === 'admin') {
        addNav('admin-menu', 'curator-view', '🧭', 'Контроль заполнения');
        addNav('admin-menu', 'curator-view', '🎓', 'Студенты групп', 'roster');
        addNav('admin-menu', 'reconcile-view', '🔎', 'Сверка контингента');
        const all = $('curator-all-btn');
        if (all) all.style.display = '';
    } else {
        if (isHead()) {
            addNav('admin-menu', 'curator-view', '🧭', 'Группы отделения');
            const all = $('curator-all-btn');
            if (all) { all.style.display = ''; all.textContent = '⬇ По всему отделению'; }
        }
        if (curatorGroups.some(g => g.own)) {
            addNav('teacher-menu', 'curator-view', '🧭', 'Моя группа (куратор)');
            addNav('teacher-menu', 'curator-view', '🎓', 'Студенты моей группы', 'roster');
        }
    }
    if (typeof setupPeriods === 'function') await setupPeriods();   // вкладки периода аттестации (periods.js)
    if (typeof setupNotify === 'function') setupNotify();           // колокольчик и «Что нового» (notify.js)
}

// ---------- контроль заполнения
async function loadCuratorView() {
    const sel = $('curator-group');
    const prev = sel.value;
    try { curatorGroups = await api('/api/curator/groups'); } catch (e) { return alert(e.message); }
    sel.innerHTML = curatorGroups.map(g => `<option value="${g.id}">${esc(g.name)}${g.curator ? ' · ' + esc(g.curator) : ''}</option>`).join('');
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
    if (!curatorGroups.length) { $('curator-info').textContent = 'За вами не закреплено ни одной группы.'; return; }
    loadCuratorGroup();
}

async function loadCuratorGroup() {
    const gid = $('curator-group').value;
    if (!gid) return;
    $('curator-hint').textContent = 'Считаю…';
    try {
        [curatorData, curatorSummary] = await Promise.all([api('/api/curator/group/' + gid), api(`/api/curator/group/${gid}/summary`)]);
    } catch (e) { $('curator-hint').textContent = ''; return alert(e.message); }
    const d = curatorData, s = d.summary, g = d.group;
    const contingent = g.contingent == null ? '' : ` · по списку колледжа: ${g.contingent}` + (g.contingent !== s.students ? ` (в системе ${s.students})` : '');
    const isAdmin = localStorage.getItem('kkci_role') === 'admin';
    // состав группы правят только куратор и админ; заведующий в чужих группах его только смотрит в сводной
    const own = isAdmin || (curatorGroups.find(x => String(x.id) === String(gid)) || {}).own;
    const rosterBtn = document.querySelector('#curator-view .risk-tab[data-tab="roster"]');
    if (rosterBtn) rosterBtn.style.display = own ? '' : 'none';
    if (!own && curatorTab === 'roster') curatorSetTab('summary');
    $('curator-info').innerHTML = `<b>${esc(d.period_title || '')}</b> · Куратор: ${esc(g.curator || '—')} · студентов в системе: ${esc(s.students)}${esc(contingent)}` +
        (isAdmin ? ` <label class="grad-check" title="У выпускной группы итоги семестра называются «итоговая аттестация»">` +
            `<input type="checkbox" ${g.graduating ? 'checked' : ''} onchange="setGraduating(${g.id}, this)"> выпускная группа</label>` : '');
    const cards = [
        { label: 'Итоговые выставлены', value: s.percent + '%', sub: `${s.cells_done} из ${s.cells} оценок`, color: '#10B981' },
        { label: 'Предметов заполнено полностью', value: `${s.subjects_complete} / ${s.subjects}`, sub: `совсем без итоговых: ${s.subjects_none}`, color: '#3B82F6' },
        { label: 'Преподавателей с незаполненным', value: s.teachers_incomplete, sub: 'им нужно проставить итоговые', color: '#F59E0B' },
        { label: 'Студентов в группе', value: s.students, sub: g.contingent == null ? 'контингент не задан' : `контингент: ${g.contingent}`, color: '#64748B' },
    ];
    if (typeof renderCuratorHints === 'function') renderCuratorHints(gid);   // notify.js
    $('curator-kpis').innerHTML = cards.map(c => `<div class="dash-card risk-kpi" style="border-left:4px solid ${c.color};">
        <span class="stat-label">${esc(c.label)}</span><div class="risk-kpi-value">${esc(c.value)}</div><div class="risk-kpi-sub">${esc(c.sub)}</div></div>`).join('');
    renderCurator();
}

async function setGraduating(gid, box) {
    try {
        await api(`/api/admin/groups/${gid}/graduating`, 'POST', { graduating: box.checked });
        alert(box.checked ? 'Группа отмечена выпускной: итоги семестра — итоговая аттестация.' : 'Отметка «выпускная» снята.');
        loadCuratorGroup();
    } catch (e) { box.checked = !box.checked; alert(e.message); }
}

function curatorSetTab(tab) {
    curatorTab = tab;
    document.querySelectorAll('#curator-view .risk-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
}

function curatorSwitchTab(tab) {
    curatorSetTab(tab);
    renderCurator();
}

function renderCurator() {
    if (!curatorData) return;
    const d = curatorData, s = d.summary;
    const isSummary = curatorTab === 'summary';
    $('curator-summary-filters').style.display = isSummary ? '' : 'none';
    $('curator-roster-bar').style.display = curatorTab === 'roster' ? '' : 'none';
    $('curator-table').classList.toggle('cs-matrix', isSummary);
    if (isSummary) return renderCuratorSummary();
    if (curatorTab === 'roster') return loadRoster();
    let head, rows, hint;
    if (curatorTab === 'subjects') {
        head = ['№', 'Предмет', 'Преподаватель', 'Итоговые выставлены', 'Статус'];
        const order = { none: 0, partial: 1, all: 2 };
        rows = [...d.subjects].sort((a, b) => order[a.status] - order[b.status] || a.teacher.localeCompare(b.teacher)).map((x, i) => `<tr>
            <td class="num">${i + 1}</td><td>${esc(x.subject)}</td><td>${esc(x.teacher)}</td>
            <td class="num">${esc(x.entered)} из ${esc(x.total)}</td><td>${statusBadge(x.status)}</td></tr>`);
        hint = d.teachers_incomplete.length ? `Не заполнили: ${d.teachers_incomplete.join(', ')}.` : 'Все итоговые выставлены.';
    } else {
        head = ['№', 'Студент', 'Итоговых', 'Нет итоговых по предметам'];
        rows = d.students.filter(x => x.missing.length).map((x, i) => `<tr>
            <td class="num">${i + 1}</td><td><b>${esc(x.name)}</b></td><td class="num">${esc(x.entered)} из ${esc(s.subjects)}</td>
            <td><ul class="risk-flags">${x.missing.slice(0, 6).map(m => `<li>${esc(m)}</li>`).join('')}${x.missing.length > 6 ? `<li>… ещё ${x.missing.length - 6}</li>` : ''}</ul></td></tr>`);
        hint = 'Показаны студенты, у которых есть предметы без итоговой оценки.';
    }
    document.querySelector('#curator-table thead').innerHTML = '<tr>' + head.map(h => `<th>${esc(h)}</th>`).join('') + '</tr>';
    document.querySelector('#curator-table tbody').innerHTML = rows.length ? rows.join('') : `<tr><td class="empty-note" colspan="${head.length}">Всё заполнено.</td></tr>`;
    $('curator-hint').textContent = hint;
}

function exportCurator(kind, btn) {
    const gid = $('curator-group').value;
    if (!gid) return alert('Выберите группу.');
    const name = kind === 'parents' ? 'Итоги_для_родителей.zip' : kind === 'summary' ? 'Сводная.xlsx' : 'Контроль_заполнения.xlsx';
    return downloadFile(`/api/export/curator/${gid}?kind=${kind}`, name, btn);
}

function exportCuratorAll(btn) { return downloadFile('/api/export/completeness', 'Контроль_заполнения_колледж.xlsx', btn); }

// ---------- сверка контингента (админ)
async function loadReconcileView() {
    $('reconcile-hint').textContent = 'Считаю…';
    try { reconcileData = await api('/api/admin/reconcile'); } catch (e) { $('reconcile-hint').textContent = ''; return alert(e.message); }
    const s = reconcileData.summary;
    const cards = [
        { label: 'Контингент по списку колледжа', value: s.contingent, sub: 'сумма по группам', color: '#3B82F6' },
        { label: 'Студентов в системе', value: s.system, sub: 'загружено поимённо', color: '#10B981' },
        { label: 'Разница', value: (s.diff > 0 ? '+' : '') + s.diff, sub: 'система − список', color: s.diff === 0 ? '#10B981' : '#EF4444' },
        { label: 'Групп совпадает', value: `${s.groups_ok} / ${reconcileData.groups.length}`, sub: `требуют внимания: ${s.groups_bad}`, color: '#F59E0B' },
    ];
    $('reconcile-kpis').innerHTML = cards.map(c => `<div class="dash-card risk-kpi" style="border-left:4px solid ${c.color};">
        <span class="stat-label">${esc(c.label)}</span><div class="risk-kpi-value">${esc(c.value)}</div><div class="risk-kpi-sub">${esc(c.sub)}</div></div>`).join('');
    const head = ['Группа', 'Отделение', 'Куратор', 'По списку', 'В системе', 'Разница', 'Статус'];
    const bad = reconcileData.groups.filter(g => g.status !== 'совпадает');
    const rows = [...bad, ...reconcileData.groups.filter(g => g.status === 'совпадает')].map(g => `<tr>
        <td><b>${esc(g.name)}</b></td><td>${esc(g.department)}</td><td>${esc(g.curator || '—')}</td>
        <td class="num">${g.contingent == null ? '—' : esc(g.contingent)}</td><td class="num">${esc(g.system)}</td>
        <td class="num">${g.diff == null ? '—' : esc((g.diff > 0 ? '+' : '') + g.diff)}</td>
        <td>${g.status === 'совпадает' ? '<span class="risk-badge" style="background:#D1FAE5;color:#047857;">совпадает</span>' : `<span class="risk-badge risk-high">${esc(g.status)}</span>`}</td></tr>`);
    document.querySelector('#reconcile-table thead').innerHTML = '<tr>' + head.map(h => `<th>${esc(h)}</th>`).join('') + '</tr>';
    document.querySelector('#reconcile-table tbody').innerHTML = rows.join('');
    $('reconcile-hint').textContent = 'Сначала группы с расхождениями. Для групп «нужен список» загрузите поимённый список студентов.';
}

function exportReconcile(btn) { return downloadFile('/api/export/reconcile', 'Сверка_контингента.xlsx', btn); }

// ---------- сводная таблица группы: студенты × предметы
const CS_CLASS = { '5': 'm5', '4': 'm4', '3': 'm3', 'неатт.': 'm0', 'Н': 'mn' };

function renderCuratorSummary() {
    const sm = curatorSummary;
    if (!sm) return;
    const t = sm.totals, cols = sm.subjects;
    const q = $('cs-search').value.trim().toLowerCase();
    const flt = $('cs-filter').value, sort = $('cs-sort').value;
    let list = sm.students.map((st, i) => ({ ...st, n: i + 1 }));
    if (q) list = list.filter(st => st.name.toLowerCase().includes(q));
    if (flt === 'fail') list = list.filter(st => st.fail);
    if (flt === 'c3') list = list.filter(st => st.c3);
    if (flt === 'good') list = list.filter(st => st.avg != null && !st.c3 && !st.fail);
    if (flt === 'missing') list = list.filter(st => st.missing);
    const av = (st) => st.avg == null ? -1 : st.avg;
    if (sort === 'avg-desc') list.sort((a, b) => av(b) - av(a));
    if (sort === 'avg-asc') list.sort((a, b) => (a.avg == null ? 999 : a.avg) - (b.avg == null ? 999 : b.avg));
    if (sort === 'fail') list.sort((a, b) => b.fail - a.fail || av(a) - av(b));

    const head = '<tr><th style="width:40px;">№</th><th class="cs-name">ФИО студента</th>' +
        cols.map((c, i) => `<th class="cs-vert" title="${esc(c.subject)}\n${esc(c.teacher)}"><div>${i + 1}. ${esc(c.short)} <small>· ${esc(c.teacher)}</small></div></th>`).join('') +
        '<th class="cs-sep">Ср. балл</th><th>Оценка</th><th>«5»</th><th>«4»</th><th>«3»</th><th>Неатт.</th><th title="Предметов без итоговой">Нет</th></tr>';
    const cell = (c) => c.p == null ? '<td class="c empty">·</td>'
        : `<td class="c ${CS_CLASS[c.m] || ''}">${c.m === 'Н' ? 'Н' : esc(c.p)}</td>`;
    const rows = list.map(st => `<tr><td class="num">${st.n}</td><td class="cs-name"><b>${esc(st.name)}</b></td>` +
        st.cells.map(cell).join('') +
        `<td class="c cs-sep ${CS_CLASS[st.mark] || ''}">${st.avg == null ? '—' : esc(st.avg)}</td><td class="c">${esc(st.mark || '—')}</td>` +
        `<td class="num">${st.c5 || ''}</td><td class="num">${st.c4 || ''}</td><td class="num">${st.c3 || ''}</td>` +
        `<td class="num" style="${st.fail ? 'color:#B91C1C;font-weight:700;' : ''}">${st.fail || ''}</td>` +
        `<td class="num" style="${st.missing ? 'color:#B45309;' : ''}">${st.missing || ''}</td></tr>`);
    const foot = (label, key, suffix = '') => `<tr class="cs-foot"><td></td><td class="cs-name">${label}</td>` +
        cols.map(c => `<td class="c">${c[key] == null ? '—' : esc(c[key]) + suffix}</td>`).join('') + '<td class="cs-sep" colspan="7"></td></tr>';
    document.querySelector('#curator-table thead').innerHTML = head;
    document.querySelector('#curator-table tbody').innerHTML = !cols.length
        ? `<tr><td class="empty-note" colspan="9">У группы нет ни одной дисциплины в системе.</td></tr>`
        : (rows.join('') || `<tr><td class="empty-note" colspan="${cols.length + 9}">Нет студентов по выбранному фильтру.</td></tr>`) +
          foot('Средний балл по предмету', 'avg') + foot('Успеваемость', 'uspev', '%') + foot('Выставлено итоговых', 'filled');
    $('curator-hint').textContent = `Средний балл группы: ${t.avg == null ? '—' : t.avg} · отличников: ${t.excellent} · хорошистов: ${t.good} · ` +
        `с тройками: ${t.with3} · с неаттестацией: ${t.failing} · без оценок: ${t.no_grades}. ` +
        'Наведите на заголовок столбца, чтобы увидеть полное название предмета. Цвет: зелёный — 5, синий — 4, жёлтый — 3, красный — неаттестация, серый — Н.';
}

// ---------- состав группы: добавить / исправить ФИО / перевести (админ) / удалить
let rosterData = null;

async function loadRoster() {
    const gid = $('curator-group').value;
    if (!gid) return;
    try { rosterData = await api(`/api/curator/group/${gid}/students`); } catch (e) { return alert(e.message); }
    const isAdmin = localStorage.getItem('kkci_role') === 'admin';
    const head = ['№', 'ФИО студента', 'Оценок', ''];
    const rows = rosterData.students.map((st, i) => `<tr>
        <td class="num">${i + 1}</td><td><b>${esc(st.full_name)}</b></td><td class="num">${st.grades}</td>
        <td class="num" style="white-space:nowrap;">
            <button class="acc-link" onclick="rosterRename(${st.id})">Исправить ФИО</button>
            ${isAdmin ? `<button class="acc-link" onclick="rosterMove(${st.id})">Перевести…</button>` : ''}
            <button class="acc-link danger" onclick="rosterDelete(${st.id})">Удалить</button></td></tr>`);
    document.querySelector('#curator-table thead').innerHTML = '<tr>' + head.map(h => `<th>${esc(h)}</th>`).join('') + '</tr>';
    document.querySelector('#curator-table tbody').innerHTML = rows.length ? rows.join('')
        : '<tr><td class="empty-note" colspan="4">В группе пока нет студентов — добавьте их выше.</td></tr>';
    const g = rosterData.group, n = rosterData.students.length;
    $('curator-hint').textContent = `Студентов в системе: ${n}` +
        (g.contingent != null ? ` · по списку колледжа: ${g.contingent}` + (g.contingent !== n ? ' — не совпадает!' : ' — совпадает') : '') +
        (isAdmin ? '. Перевод в другую группу сохраняет оценки студента.' : '. Перевести студента в другую группу может администратор.');
}

async function rosterChanged(msg) {
    if (typeof accToast === 'function') accToast(msg);
    await loadCuratorGroup();            // пересчитать сводную и контроль заполнения
}

async function rosterAdd() {
    const name = $('roster-new').value.trim();
    if (!name) return alert('Впишите ФИО студента.');
    try {
        await api(`/api/curator/group/${$('curator-group').value}/students`, 'POST', { full_name: name });
        $('roster-new').value = '';
        await rosterChanged('Студент добавлен.');
    } catch (e) { alert(e.message); }
}

async function rosterRename(id) {
    const st = rosterData.students.find(x => x.id === id);
    const name = prompt('Исправленное ФИО:', st.full_name);
    if (name === null || name.trim() === st.full_name) return;
    try { await api(`/api/curator/students/${id}`, 'POST', { full_name: name }); await rosterChanged('ФИО исправлено.'); }
    catch (e) { alert(e.message); }
}

function rosterMove(id) {
    const st = rosterData.students.find(x => x.id === id);
    if (!st || $('acc-modal')) return;
    const cur = Number($('curator-group').value);
    const ov = document.createElement('div');
    ov.id = 'acc-modal';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.6);z-index:1000;display:flex;align-items:center;justify-content:center;';
    ov.innerHTML = `<div class="auth-card" style="max-width:480px;">
        <h3 style="margin-bottom:6px;color:var(--primary-color);">Перевести студента</h3>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;"><b>${esc(st.full_name)}</b><br>Из группы ${esc(rosterData.group.name)}. Оценки сохранятся.</p>
        <select class="egov-select" id="roster-move-group" style="width:100%;">
            ${curatorGroups.filter(g => g.id !== cur).map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select>
        <div style="display:flex;gap:10px;margin-top:16px;">
            <button class="egov-btn-primary" style="flex:1;" id="roster-move-ok">Перевести</button>
            <button class="egov-btn-secondary" id="roster-move-cancel">Отмена</button>
        </div></div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    $('roster-move-cancel').onclick = close;
    ov.onclick = (e) => { if (e.target === ov) close(); };
    $('roster-move-ok').onclick = async () => {
        try {
            await api(`/api/curator/students/${id}`, 'POST', { group_id: Number($('roster-move-group').value) });
            close();
            await rosterChanged('Студент переведён.');
        } catch (e) { alert(e.message); }
    };
}

async function rosterDelete(id) {
    const st = rosterData.students.find(x => x.id === id);
    if (!confirm(`Удалить студента «${st.full_name}» из группы?`)) return;
    if (st.grades && !confirm(`У студента уже ${st.grades} оценок — они будут УДАЛЕНЫ.\n\nЕсли студент перешёл в другую группу, лучше попросите администратора перевести его (оценки сохранятся).\n\nУдалить?`)) return;
    try {
        await api(`/api/curator/students/${id}${st.grades ? '?force=true' : ''}`, 'DELETE');
        await rosterChanged('Студент удалён.');
    } catch (e) { alert(e.message); }
}
