// frontend/js/pullup.js — вкладка «Можно подтянуть» (1–2 итоговые тройки, остальные 4–5)
// Подключается ПОСЛЕ app.js: использует его глобальные $, esc, api, downloadFile.

let pullupData = null;
let pullupTabName = 'students';
let pullupParams = {};
let pullupReq = 0;
const PULLUP_LIMIT = 500;

const PULLUP_FIELDS = [
    { key: 'max_threes', label: 'Допустимо итоговых троек, не больше', min: 1, max: 6 },
    { key: 'min_subjects', label: 'Итоговые выставлены минимум по предметам', min: 1, max: 30 },
    { key: 'easy_gap', label: '«Лёгкий случай»: до четвёрки не хватает не более, баллов', min: 1, max: 19 },
];

function pullupQuery() {
    const q = new URLSearchParams();
    Object.entries(pullupParams).forEach(([k, v]) => q.set(k, v));
    return q;
}

async function loadPullupView() {
    const my = ++pullupReq;
    $('pullup-hint').textContent = 'Считаю…';
    try {
        const qs = pullupQuery().toString();
        const d = await api('/api/admin/pullup' + (qs ? '?' + qs : ''));
        if (my !== pullupReq) return;
        pullupData = d;
        renderPullupSettings();
        renderPullupKpis();
        renderPullup();
    } catch (e) {
        if (my === pullupReq) $('pullup-hint').textContent = '';
        alert(e.message);
    }
}

function renderPullupSettings() {
    const box = $('pullup-settings');
    if (box.dataset.ready) return;
    box.dataset.ready = '1';
    const p = pullupData.params;
    box.innerHTML = PULLUP_FIELDS.map(f => `<label class="risk-field"><span>${esc(f.label)}</span>
        <input type="number" id="pp-${f.key}" min="${f.min}" max="${f.max}" step="1" value="${esc(p[f.key])}"></label>`).join('') +
        `<div class="risk-settings-actions">
            <button class="egov-btn-primary" onclick="pullupApply()">Пересчитать</button>
            <button class="egov-btn-secondary" onclick="pullupReset()">Сбросить</button>
            <span class="risk-hint-inline">Параметры действуют и на выгрузку в Excel.</span></div>`;
}

function pullupToggleSettings() {
    const box = $('pullup-settings');
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function pullupApply() {
    const next = {};
    PULLUP_FIELDS.forEach(f => { const v = $('pp-' + f.key).value; if (v !== '') next[f.key] = v; });
    pullupParams = next;
    loadPullupView();
}

function pullupReset() {
    pullupParams = {};
    delete $('pullup-settings').dataset.ready;
    loadPullupView();
}

function renderPullupKpis() {
    const s = pullupData.summary;
    const cards = [
        { label: 'Кандидатов на подтяжку', value: s.candidates, sub: `из ${s.students_analyzed} студентов с итоговыми`, color: '#3B82F6' },
        { label: 'Лёгкие случаи', value: s.easy, sub: `до «4» не хватает не более ${pullupData.params.easy_gap} б.`, color: '#10B981' },
        { label: 'Одна тройка', value: s.one_three, sub: `две тройки: ${s.two_threes}`, color: '#F59E0B' },
        { label: 'Качество знаний сейчас', value: s.quality_now + '%', sub: `студентов без троек: ${s.good_now}`, color: '#64748B' },
        { label: 'Потенциал', value: s.quality_potential + '%', sub: 'если подтянуть всех кандидатов', color: '#7C3AED' },
    ];
    $('pullup-kpis').innerHTML = cards.map(c => `
        <div class="dash-card risk-kpi" style="border-left: 4px solid ${c.color};">
            <span class="stat-label">${esc(c.label)}</span>
            <div class="risk-kpi-value">${esc(c.value)}</div>
            <div class="risk-kpi-sub">${esc(c.sub)}</div>
        </div>`).join('');
    $('pc-students').textContent = pullupData.candidates.length;
    $('pc-groups').textContent = pullupData.groups.length;

    const dept = $('pullup-dept');
    const prev = dept.value;
    const names = [...new Set(pullupData.groups.map(g => g.department))].sort();
    dept.innerHTML = '<option value="">Все отделения</option>' + names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
    if (names.includes(prev)) dept.value = prev;
}

function pullupTab(tab) {
    pullupTabName = tab;
    document.querySelectorAll('#pullup-view .risk-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    $('pullup-easy').style.display = tab === 'students' ? '' : 'none';
    $('pullup-search').style.display = tab === 'students' ? '' : 'none';
    renderPullup();
}

function renderPullup() {
    if (!pullupData) return;
    const thead = document.querySelector('#pullup-table thead');
    const tbody = document.querySelector('#pullup-table tbody');
    const dept = $('pullup-dept').value;
    let head, rows, empty, hint;

    if (pullupTabName === 'students') {
        const easy = $('pullup-easy').value === '1';
        const query = $('pullup-search').value.trim().toLowerCase();
        const list = pullupData.candidates.filter(c =>
            (!dept || c.department === dept) && (!easy || c.easy) &&
            (!query || `${c.name} ${c.group} ${c.targets.map(t => t.subject + ' ' + t.teacher).join(' ')}`.toLowerCase().includes(query)));
        head = ['№', 'ФИО', 'Группа', 'Предметов', 'Средний итог', 'Что подтянуть', 'Преподаватель', 'Случай'];
        rows = list.slice(0, PULLUP_LIMIT).map((c, i) => `<tr>
            <td class="num">${i + 1}</td><td><b>${esc(c.name)}</b></td><td>${esc(c.group)}</td>
            <td class="num">${esc(c.subjects)}</td><td class="num">${esc(c.avg)}</td>
            <td><ul class="risk-flags">${c.targets.map(t => `<li>${esc(t.subject)} — <b>${esc(t.points)}</b> б. (до «4» не хватает ${esc(t.gap)})</li>`).join('')}</ul></td>
            <td><ul class="risk-flags">${c.targets.map(t => `<li>${esc(t.teacher)}</li>`).join('')}</ul></td>
            <td>${c.easy ? '<span class="risk-badge" style="background:#D1FAE5;color:#047857;">Лёгкий</span>' : '<span class="risk-badge risk-watch">Средний</span>'}</td></tr>`);
        empty = 'Подходящих студентов нет. Попробуйте увеличить допустимое число троек в параметрах.';
        hint = list.length > PULLUP_LIMIT ? `Показаны первые ${PULLUP_LIMIT} из ${list.length}. Полный список — в выгрузке Excel.`
            : `Найдено: ${list.length}. Сначала самые лёгкие случаи — когда до четвёрки не хватает нескольких баллов.`;
    } else {
        const list = pullupData.groups.filter(g => !dept || g.department === dept);
        head = ['Группа', 'Отделение', 'Студентов', 'Без троек', 'Кандидатов', 'Качество знаний, %', 'Потенциал, %'];
        rows = list.map(g => `<tr><td><b>${esc(g.group)}</b></td><td>${esc(g.department)}</td>
            <td class="num">${esc(g.students)}</td><td class="num">${esc(g.good)}</td><td class="num"><b>${esc(g.candidates)}</b></td>
            <td class="num">${esc(g.quality_now)}</td><td class="num">${esc(g.quality_potential)}</td></tr>`);
        empty = 'Нет данных.';
        hint = 'Качество знаний — доля студентов без итоговых троек. Потенциал — какой она станет, если подтянуть всех кандидатов группы.';
    }
    thead.innerHTML = '<tr>' + head.map(h => `<th>${esc(h)}</th>`).join('') + '</tr>';
    tbody.innerHTML = rows.length ? rows.join('') : `<tr><td class="empty-note" colspan="${head.length}">${esc(empty)}</td></tr>`;
    $('pullup-hint').textContent = hint;
}

function exportPullup(btn) {
    const qs = pullupQuery().toString();
    return downloadFile('/api/export/pullup' + (qs ? '?' + qs : ''), 'Можно_подтянуть.xlsx', btn);
}
