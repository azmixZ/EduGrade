// frontend/js/access.js — вкладка администратора «Доступ преподавателей»:
// закрепление групп / дисциплин / практик за преподавателем, передача ведомостей, назначение кураторов.
// Подключается ПОСЛЕ app.js: использует его глобальные $, esc, api, switchView.

const acc = { mode: 'teacher', ov: null, teacher: null, group: null, detail: null };
const PRACTICE_KINDS = [
    { label: 'Учебная практика (ПО)', suffix: '(ПО)' },
    { label: 'Производственная практика (ПП)', suffix: '(ПП)' },
    { label: 'Профессиональная практика', suffix: 'Профессиональная практика' },
    { label: 'Преддипломная практика', suffix: '' },
];

function accToast(text, bad) {
    const t = document.createElement('div');
    t.className = 'acc-toast' + (bad ? ' bad' : '');
    t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.classList.add('show'), 10);
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, bad ? 6000 : 2600);
}

function kindBadge(practice) {
    return practice ? '<span class="risk-badge" style="background:#EDE9FE;color:#6D28D9;">практика</span>'
                    : '<span class="risk-badge" style="background:#F1F5F9;color:#475569;">дисциплина</span>';
}

function teacherOptions(selected, withEmpty) {
    const list = (acc.ov ? acc.ov.teachers : []).filter(t => t.is_active || t.id === selected);
    return (withEmpty ? '<option value="">— выберите преподавателя —</option>' : '') +
        list.map(t => `<option value="${t.id}" ${t.id === selected ? 'selected' : ''}>${esc(t.full_name)}${t.is_active ? '' : ' (отключён)'}</option>`).join('');
}

function groupOptions(selected, withEmpty, showCurator) {
    return (withEmpty ? '<option value="">— выберите группу —</option>' : '') +
        (acc.ov ? acc.ov.groups : []).map(g => `<option value="${g.id}" ${g.id === selected ? 'selected' : ''}>${esc(g.name)}` +
            `${showCurator && g.curator ? ' · куратор: ' + esc(g.curator) : ''}${!g.workloads ? ' · нет нагрузки' : ''}</option>`).join('');
}

function subjectDatalist() {
    return `<datalist id="acc-subjects">${(acc.ov ? acc.ov.subjects : []).map(s => `<option value="${esc(s.name)}"></option>`).join('')}</datalist>`;
}

// ---------- загрузка и список слева
async function loadAccessView() {
    try { acc.ov = await api('/api/admin/access/overview'); } catch (e) { return alert(e.message); }
    $('ac-empty').textContent = acc.ov.groups.filter(g => !g.workloads).length;
    renderAccessList();
    if (acc.mode === 'teacher' && acc.teacher) openTeacher(acc.teacher);
    else if (acc.mode === 'group' && acc.group) openGroup(acc.group);
}

function accessMode(mode) {
    acc.mode = mode;
    document.querySelectorAll('#access-view [data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    $('access-search').placeholder = mode === 'teacher' ? 'Поиск преподавателя…' : 'Поиск группы…';
    renderAccessList();
    const id = mode === 'teacher' ? acc.teacher : acc.group;
    if (id) (mode === 'teacher' ? openTeacher : openGroup)(id);
    else $('access-detail').innerHTML = `<div class="dash-card empty-note">Выберите ${mode === 'teacher' ? 'преподавателя' : 'группу'} слева.</div>`;
}

function renderAccessList() {
    if (!acc.ov) return;
    const q = $('access-search').value.trim().toLowerCase();
    let html;
    if (acc.mode === 'teacher') {
        html = acc.ov.teachers.filter(t => !q || t.full_name.toLowerCase().includes(q) || t.username.toLowerCase().includes(q)).map(t => `
            <div class="acc-item ${t.id === acc.teacher ? 'active' : ''} ${t.is_active ? '' : 'off'}" onclick="openTeacher(${t.id})">
                <div class="acc-item-name">${esc(t.full_name)}</div>
                <div class="acc-item-sub">${t.workloads ? `ведомостей: ${t.workloads}` : '<span style="color:#DC2626;">нет нагрузки</span>'}` +
                `${t.curated.length ? ` · куратор: ${esc(t.curated.join(', '))}` : ''}${t.is_active ? '' : ' · отключён'}</div>
            </div>`);
    } else {
        const groups = [...acc.ov.groups].sort((a, b) => (a.workloads ? 1 : 0) - (b.workloads ? 1 : 0) || a.name.localeCompare(b.name));
        html = groups.filter(g => !q || g.name.toLowerCase().includes(q) || (g.curator || '').toLowerCase().includes(q)).map(g => `
            <div class="acc-item ${g.id === acc.group ? 'active' : ''}" onclick="openGroup(${g.id})">
                <div class="acc-item-name">${esc(g.name)} ${g.workloads ? '' : '<span class="risk-badge risk-critical" style="margin-left:6px;">нет нагрузки</span>'}</div>
                <div class="acc-item-sub">ведомостей: ${g.workloads} · студентов: ${g.students} · куратор: ${esc(g.curator || '—')}</div>
            </div>`);
    }
    $('access-list-items').innerHTML = html.length ? html.join('') : '<div class="empty-note">Ничего не найдено.</div>';
}

// ---------- карточка преподавателя
async function openTeacher(id) {
    acc.teacher = id;
    renderAccessList();
    let d;
    try { d = await api('/api/admin/access/teacher/' + id); } catch (e) { return alert(e.message); }
    acc.detail = d;
    const t = d.teacher;
    const chips = d.curated.map(g => `<span class="acc-chip">${esc(g.name)}<button title="Снять кураторство" onclick="accSetCurator(${g.id}, null)">✕</button></span>`).join('')
        || '<span style="color:var(--text-muted);font-size:13px;">Не куратор ни одной группы.</span>';
    const rows = d.workloads.map(w => `<tr>
        <td><b>${esc(w.group_name)}</b></td><td>${esc(w.subject_name)}</td><td>${kindBadge(w.practice)}</td>
        <td class="num">${w.grades}</td>
        <td class="num"><button class="acc-link" onclick="accMove(${w.workload_id})">Передать…</button>
            <button class="acc-link danger" onclick="accDelete(${w.workload_id}, ${w.grades})">Открепить</button></td></tr>`).join('');
    $('access-detail').innerHTML = `
        <div class="dash-card acc-card">
            <div class="acc-head"><div><h3 style="margin:0;">${esc(t.full_name)}</h3>
                <div class="acc-item-sub">логин: ${esc(t.username)} · ${t.is_active ? 'активна' : 'отключена'} · последний вход: ${esc(t.last_login || 'не входил(а)')}</div></div></div>
        </div>
        <div class="dash-card acc-card">
            <h4>Кураторство</h4>
            <div class="acc-chips">${chips}</div>
            <div class="acc-row">
                <select class="egov-select" id="acc-cur-group" style="min-width:300px;">${groupOptions(null, true, true)}</select>
                <button class="egov-btn-primary" onclick="accSetCurator(Number($('acc-cur-group').value), ${t.id})">Назначить куратором</button>
            </div>
        </div>
        <div class="dash-card acc-card">
            <h4>Закреплённые группы, дисциплины и практики <span class="risk-count">${d.workloads.length}</span></h4>
            <div style="overflow-x:auto;"><table class="google-sheet data-table risk-table" style="width:100%;">
                <thead><tr><th style="width:130px;">Группа</th><th>Дисциплина / практика</th><th style="width:120px;">Тип</th><th style="width:80px;">Оценок</th><th style="width:190px;"></th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" class="empty-note">Ничего не закреплено — преподаватель не видит ни одной ведомости.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="acc-two">
            <div class="dash-card acc-card">
                <h4>Добавить дисциплину</h4>
                <label class="acc-label">Группа</label>
                <select class="egov-select acc-full" id="acc-add-group">${groupOptions(null, true)}</select>
                <label class="acc-label">Дисциплина (выберите из списка или впишите новую)</label>
                <input class="egov-select acc-full" id="acc-add-subject" list="acc-subjects" placeholder="Начните вводить название…">
                ${subjectDatalist()}
                <label class="acc-check"><input type="checkbox" id="acc-add-practice"> это практика</label>
                <button class="egov-btn-primary" onclick="accAddSubject(${t.id})">Закрепить</button>
            </div>
            ${practiceFormHtml({ teacherId: t.id })}
        </div>`;
}

// ---------- карточка группы
async function openGroup(id) {
    acc.group = id;
    renderAccessList();
    let d;
    try { d = await api('/api/admin/access/group/' + id); } catch (e) { return alert(e.message); }
    acc.detail = d;
    const g = d.group;
    const rows = d.workloads.map(w => `<tr>
        <td>${esc(w.subject_name)}</td><td>${kindBadge(w.practice)}</td><td><b>${esc(w.teacher)}</b></td>
        <td class="num">${w.grades}</td>
        <td class="num"><button class="acc-link" onclick="accMove(${w.workload_id})">Передать…</button>
            <button class="acc-link danger" onclick="accDelete(${w.workload_id}, ${w.grades})">Открепить</button></td></tr>`).join('');
    $('access-detail').innerHTML = `
        <div class="dash-card acc-card">
            <div class="acc-head"><div><h3 style="margin:0;">Группа ${esc(g.name)}</h3>
                <div class="acc-item-sub">студентов в системе: ${g.students}${g.contingent != null ? ` · по списку колледжа: ${g.contingent}` : ''}</div></div></div>
            <div class="acc-row" style="margin-top:14px;">
                <span class="acc-label" style="margin:0;">Куратор:</span>
                <select class="egov-select" id="acc-g-curator" style="min-width:280px;">${teacherOptions(g.curator_id, true)}</select>
                <button class="egov-btn-primary" onclick="accSetCurator(${g.id}, Number($('acc-g-curator').value) || null)">Сохранить</button>
                ${g.curator_id ? `<button class="egov-btn-secondary" onclick="accSetCurator(${g.id}, null)">Снять куратора</button>` : ''}
            </div>
        </div>
        <div class="dash-card acc-card">
            <h4>Дисциплины и практики группы <span class="risk-count">${d.workloads.length}</span></h4>
            <div style="overflow-x:auto;"><table class="google-sheet data-table risk-table" style="width:100%;">
                <thead><tr><th>Дисциплина / практика</th><th style="width:120px;">Тип</th><th style="width:200px;">Преподаватель</th><th style="width:80px;">Оценок</th><th style="width:190px;"></th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" class="empty-note">У группы нет ни одной ведомости — добавьте дисциплины ниже.</td></tr>'}</tbody>
            </table></div>
        </div>
        <div class="acc-two">
            <div class="dash-card acc-card">
                <h4>Добавить дисциплину</h4>
                <label class="acc-label">Дисциплина (выберите из списка или впишите новую)</label>
                <input class="egov-select acc-full" id="acc-add-subject" list="acc-subjects" placeholder="Начните вводить название…">
                ${subjectDatalist()}
                <label class="acc-label">Преподаватель</label>
                <select class="egov-select acc-full" id="acc-add-teacher">${teacherOptions(null, true)}</select>
                <label class="acc-check"><input type="checkbox" id="acc-add-practice"> это практика</label>
                <button class="egov-btn-primary" onclick="accAddSubject(null, ${g.id})">Закрепить</button>
            </div>
            ${practiceFormHtml({ groupId: g.id })}
        </div>`;
    accFillModules(g.id, d.workloads);
}

// ---------- форма «Создать практику» (в карточке преподавателя выбирается группа, в карточке группы — преподаватель)
function practiceFormHtml({ teacherId, groupId }) {
    return `<div class="dash-card acc-card">
        <h4>Создать практику</h4>
        ${groupId ? '' : `<label class="acc-label">Группа</label>
            <select class="egov-select acc-full" id="acc-pr-group" onchange="accLoadModules()">${groupOptions(null, true)}</select>`}
        ${teacherId ? '' : `<label class="acc-label">Преподаватель</label>
            <select class="egov-select acc-full" id="acc-pr-teacher">${teacherOptions(null, true)}</select>`}
        <label class="acc-label">Модуль (необязательно — подставит название)</label>
        <select class="egov-select acc-full" id="acc-pr-module" onchange="accPracticeName()"><option value="">— без модуля —</option></select>
        <label class="acc-label">Вид практики</label>
        <select class="egov-select acc-full" id="acc-pr-kind" onchange="accPracticeName()">
            ${PRACTICE_KINDS.map((k, i) => `<option value="${i}">${esc(k.label)}</option>`).join('')}</select>
        <label class="acc-label">Название практики</label>
        <input class="egov-select acc-full" id="acc-pr-name" placeholder="Например: ПМ 3. … (ПП)">
        <button class="egov-btn-primary" onclick="accAddPractice(${teacherId || 'null'}, ${groupId || 'null'})">Создать и закрепить</button>
    </div>`;
}

// модули (ПМ / КМ) группы — из её дисциплин, без пометок о практике
function accFillModules(gid, workloads) {
    const sel = $('acc-pr-module');
    if (!sel) return;
    const mods = new Map();
    workloads.forEach(w => {
        if (!/^\s*(ПМ|КМ|БМ)\s*\.?\s*\d+/i.test(w.subject_name)) return;
        const base = w.subject_name.replace(/\s*\((ПП|ПО)\)\s*$/i, '').replace(/\s+(ПП|ПО)\s*$/i, '')
            .replace(/\s*(Производственное обучение \(учебная практика\)|Профессиональная практика|Кәсіптік практика)\s*$/i, '').trim();
        mods.set(base, true);
    });
    sel.innerHTML = '<option value="">— без модуля —</option>' + [...mods.keys()].sort().map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
}

async function accLoadModules() {
    const gid = $('acc-pr-group').value;
    if (!gid) return accFillModules(0, []);
    try { const d = await api('/api/admin/access/group/' + gid); accFillModules(gid, d.workloads); } catch (e) { accFillModules(0, []); }
}

function accPracticeName() {
    const mod = $('acc-pr-module').value;
    const kind = PRACTICE_KINDS[Number($('acc-pr-kind').value)];
    if (!mod) { if (!kind.suffix) $('acc-pr-name').value = 'Преддипломная практика'; return; }
    $('acc-pr-name').value = kind.suffix ? `${mod} ${kind.suffix}` : `${mod} — преддипломная практика`;
}

// ---------- действия
async function accPostWorkload(body) {
    try {
        const r = await api('/api/admin/access/workloads', 'POST', body);
        if (r.warning) accToast(r.warning, true); else accToast('Закреплено.');
        await loadAccessView();
        return true;
    } catch (e) { alert(e.message); return false; }
}

function accSubjectBody(name) {
    const s = acc.ov.subjects.find(x => x.name === name);
    return s ? { subject_id: s.id } : { subject_name: name };
}

async function accAddSubject(teacherId, groupId) {
    const name = $('acc-add-subject').value.trim();
    teacherId = teacherId || Number(($('acc-add-teacher') || {}).value);
    groupId = groupId || Number(($('acc-add-group') || {}).value);
    if (!groupId) return alert('Выберите группу.');
    if (!teacherId) return alert('Выберите преподавателя.');
    if (!name) return alert('Укажите дисциплину.');
    const body = { teacher_id: teacherId, group_id: groupId, practice: $('acc-add-practice').checked, ...accSubjectBody(name) };
    if (!body.subject_id && !confirm(`Дисциплины «${name}» ещё нет в справочнике. Создать новую?`)) return;
    await accPostWorkload(body);
}

async function accAddPractice(teacherId, groupId) {
    teacherId = teacherId || Number(($('acc-pr-teacher') || {}).value);
    groupId = groupId || Number(($('acc-pr-group') || {}).value);
    const name = $('acc-pr-name').value.trim();
    if (!groupId) return alert('Выберите группу.');
    if (!teacherId) return alert('Выберите преподавателя.');
    if (!name) return alert('Укажите название практики.');
    await accPostWorkload({ teacher_id: teacherId, group_id: groupId, practice: true, ...accSubjectBody(name) });
}

async function accSetCurator(groupId, teacherId) {
    if (!groupId) return alert('Выберите группу.');
    const g = acc.ov.groups.find(x => x.id === groupId);
    if (teacherId === null) {
        if (!confirm(`Снять куратора с группы ${(g && g.name) || ''}?`)) return;
    } else if (g && g.curator_id && g.curator_id !== teacherId) {
        if (!confirm(`У группы ${g.name} уже есть куратор: ${g.curator}. Заменить?`)) return;
    }
    try {
        await api('/api/admin/access/curator', 'POST', { group_id: groupId, teacher_id: teacherId });
        accToast(teacherId === null ? 'Кураторство снято.' : 'Куратор назначен.');
        await loadAccessView();
    } catch (e) { alert(e.message); }
}

async function accDelete(wid, grades) {
    const w = acc.detail.workloads.find(x => x.workload_id === wid);
    const what = w ? `«${w.subject_name}» (${w.group_name}, ${w.teacher})` : 'ведомость';
    if (!confirm(`Открепить ${what}?`)) return;
    if (grades && !confirm(`В этой ведомости уже ${grades} оценок. Они будут УДАЛЕНЫ вместе с ней.\n\nЕсли нужно сменить преподавателя — нажмите «Отмена» и используйте «Передать…» (оценки сохранятся).\n\nУдалить?`)) return;
    try {
        await api(`/api/admin/access/workloads/${wid}${grades ? '?force=true' : ''}`, 'DELETE');
        accToast('Откреплено.');
        await loadAccessView();
    } catch (e) { alert(e.message); }
}

function accMove(wid) {
    const w = acc.detail.workloads.find(x => x.workload_id === wid);
    if (!w || $('acc-modal')) return;
    const ov = document.createElement('div');
    ov.id = 'acc-modal';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.6);z-index:1000;display:flex;align-items:center;justify-content:center;';
    ov.innerHTML = `<div class="auth-card" style="max-width:520px;">
        <h3 style="margin-bottom:6px;color:var(--primary-color);">Передать ведомость</h3>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:16px;">${esc(w.group_name)} · ${esc(w.subject_name)}<br>Сейчас: <b>${esc(w.teacher)}</b>. Оценки сохранятся.</p>
        <select class="egov-select acc-full" id="acc-move-teacher">${teacherOptions(w.teacher_id, false)}</select>
        <div style="display:flex;gap:10px;margin-top:16px;">
            <button class="egov-btn-primary" style="flex:1;" id="acc-move-ok">Передать</button>
            <button class="egov-btn-secondary" id="acc-move-cancel">Отмена</button>
        </div></div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    $('acc-move-cancel').onclick = close;
    ov.onclick = (e) => { if (e.target === ov) close(); };
    $('acc-move-ok').onclick = async () => {
        const tid = Number($('acc-move-teacher').value);
        try {
            await api(`/api/admin/access/workloads/${wid}/teacher`, 'POST', { teacher_id: tid });
            close();
            accToast('Ведомость передана.');
            await loadAccessView();
        } catch (e) { alert(e.message); }
    };
}
