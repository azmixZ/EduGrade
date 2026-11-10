// frontend/js/risk.js — вкладка «Группа риска» (слабые группы, слабые студенты, мёртвые души)
// Подключается ПОСЛЕ app.js: использует его глобальные $, esc, api, downloadFile.

let riskData = null;
let riskTab = 'groups';
let riskParams = {};          // пороги, заданные пользователем (пусто = значения по умолчанию с сервера)
let riskReq = 0;              // защита от «гонки» ответов при быстрой смене порогов
const RISK_ROW_LIMIT = 500;

// Поля настроек: key совпадает с query-параметром API; pct = на сервере доля (0..1), в форме — проценты
const RISK_FIELDS = [
    { group: 'Слабый студент', items: [
        { key: 'weak_avg', label: 'Средний балл ниже', min: 0, max: 100, step: 1 },
        { key: 'fail_subjects', label: 'Не аттестован по предметам, от', min: 1, max: 30, step: 1 },
        { key: 'abs_warn', label: 'Доля «Н» от, %', min: 0, max: 100, step: 1, pct: true },
    ]},
    { group: 'Мёртвая душа', items: [
        { key: 'dead_abs', label: 'Доля «Н» от, %', min: 0, max: 100, step: 1, pct: true },
        { key: 'dead_cov', label: 'Итоговые выставлены менее чем по, % предметов', min: 0, max: 100, step: 1, pct: true },
    ]},
    { group: 'Слабая группа', items: [
        { key: 'group_avg', label: 'Средний балл ниже', min: 0, max: 100, step: 1 },
        { key: 'group_unatt', label: 'Неаттестованных от, %', min: 0, max: 100, step: 1, pct: true },
        { key: 'group_weak', label: 'Слабых студентов от, %', min: 0, max: 100, step: 1, pct: true },
    ]},
];

const RISK_LEVELS = {
    groups:   [['', 'Все уровни'], ['critical', 'Критические'], ['weak', 'Слабые']],
    students: [['', 'Все уровни'], ['critical', 'Критический'], ['high', 'Высокий'], ['watch', 'Наблюдение']],
    dead:     [['', 'Все типы'], ['high', 'Явные'], ['watch', 'Подозрение']],
};
const RISK_LEVEL_LABEL = { critical: 'Критический', high: 'Высокий', watch: 'Наблюдение', weak: 'Слабая', ok: 'Норма' };
const RISK_DEAD_LABEL = { high: 'Явная', watch: 'Подозрение' };

function riskQuery() {
    const q = new URLSearchParams();
    Object.entries(riskParams).forEach(([k, v]) => q.set(k, v));
    return q;
}

async function loadRiskView() {
    const my = ++riskReq;
    $('risk-hint').textContent = 'Считаю…';
    try {
        const qs = riskQuery().toString();
        const data = await api('/api/admin/risk' + (qs ? '?' + qs : ''));
        if (my !== riskReq) return;
        riskData = data;
        renderRiskSettings();
        renderRiskKpis();
        renderRiskTab();
    } catch (e) {
        if (my === riskReq) $('risk-hint').textContent = '';
        alert(e.message);
    }
}

// ---------- настройки порогов
function renderRiskSettings() {
    const box = $('risk-settings');
    if (box.dataset.ready) return;                       // форма строится один раз, чтобы не терять ввод
    const p = riskData.params;
    box.dataset.ready = '1';
    box.innerHTML = '<div class="risk-settings-grid">' + RISK_FIELDS.map(g => `
        <div>
            <h4>${esc(g.group)}</h4>
            ${g.items.map(f => {
                const v = f.pct ? Math.round(p[f.key] * 100) : p[f.key];
                return `<label class="risk-field"><span>${esc(f.label)}</span>
                    <input type="number" id="rp-${f.key}" min="${f.min}" max="${f.max}" step="${f.step}" value="${v}"></label>`;
            }).join('')}
        </div>`).join('') + `</div>
        <div class="risk-settings-actions">
            <button class="egov-btn-primary" onclick="riskApplySettings()">Пересчитать</button>
            <button class="egov-btn-secondary" onclick="riskResetSettings()">Сбросить</button>
            <span class="risk-hint-inline">Пороги действуют и на выгрузку в Excel.</span>
        </div>`;
}

function riskToggleSettings() {
    const box = $('risk-settings');
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function riskApplySettings() {
    const next = {};
    RISK_FIELDS.forEach(g => g.items.forEach(f => {
        const raw = $('rp-' + f.key).value;
        if (raw === '') return;
        const n = Number(raw);
        if (!Number.isFinite(n)) return;
        next[f.key] = f.pct ? n / 100 : n;
    }));
    riskParams = next;
    loadRiskView();
}

function riskResetSettings() {
    riskParams = {};
    const box = $('risk-settings');
    delete box.dataset.ready;                            // перестроить форму значениями по умолчанию
    loadRiskView();
}

// ---------- KPI и отделения
function renderRiskKpis() {
    const s = riskData.summary;
    const cards = [
        { label: 'Слабые группы', value: s.weak_groups, sub: `критических: ${s.critical_groups} · из ${s.groups_analyzed}`, color: '#F59E0B' },
        { label: 'Слабые студенты', value: s.weak_students, sub: `критических: ${s.critical_students} · из ${s.students_analyzed}`, color: '#EF4444' },
        { label: 'Неаттестованы (средний < 50)', value: s.unattested_students, sub: `${pctOf(s.unattested_students, s.students_analyzed)}% от проанализированных`, color: '#DC2626' },
        { label: 'Мёртвые души', value: s.dead_souls_high, sub: `явных · ещё под подозрением: ${s.dead_souls_watch}`, color: '#7C3AED' },
        { label: 'Группы без данных', value: s.groups_no_data, sub: `студентов: ${s.students_no_data} · итоговых нет`, color: '#64748B' },
    ];
    $('risk-kpis').innerHTML = cards.map(c => `
        <div class="dash-card risk-kpi" style="border-left: 4px solid ${c.color};">
            <span class="stat-label">${esc(c.label)}</span>
            <div class="risk-kpi-value">${esc(c.value)}</div>
            <div class="risk-kpi-sub">${esc(c.sub)}</div>
        </div>`).join('');

    $('rc-groups').textContent = riskData.weak_groups.length;
    $('rc-students').textContent = riskData.weak_students.length;
    $('rc-dead').textContent = riskData.dead_souls.length;
    $('rc-nodata').textContent = riskData.no_data_groups.length;

    const dept = $('risk-dept');
    const prev = dept.value;
    dept.innerHTML = '<option value="">Все отделения</option>' +
        riskData.departments.map(d => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join('');
    if ([...dept.options].some(o => o.value === prev)) dept.value = prev;
}

function pctOf(a, b) { return b ? (a * 100 / b).toFixed(1) : '0.0'; }

// ---------- вкладки и таблицы
function riskSwitchTab(tab) {
    riskTab = tab;
    document.querySelectorAll('.risk-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const lvl = $('risk-level');
    lvl.innerHTML = (RISK_LEVELS[tab] || []).map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join('');
    lvl.style.display = RISK_LEVELS[tab] ? '' : 'none';
    $('risk-search').value = '';
    renderRiskTab();
}

function riskMatch(row, query, dept, level, levelKey = 'level') {
    if (dept && row.department !== dept) return false;
    if (level && row[levelKey] !== level) return false;
    if (query) {
        const hay = `${row.name || ''} ${row.group || ''}`.toLowerCase();
        if (!hay.includes(query)) return false;
    }
    return true;
}

function badge(level, labels = RISK_LEVEL_LABEL) {
    return `<span class="risk-badge risk-${esc(level)}">${esc(labels[level] || level)}</span>`;
}

function scoreCell(v) {
    const w = Math.max(0, Math.min(100, v));
    const cls = v < 50 ? 'bad' : v < 60 ? 'warn' : 'ok';
    return `<div class="risk-score"><div class="risk-score-bar ${cls}" style="width:${w}%"></div><span>${esc(v)}</span></div>`;
}

function flagsCell(list) {
    return `<ul class="risk-flags">${(list || []).map(f => `<li>${esc(f)}</li>`).join('')}</ul>`;
}

function renderRiskTab() {
    if (!riskData) return;
    const thead = document.querySelector('#risk-table thead');
    const tbody = document.querySelector('#risk-table tbody');
    const dept = $('risk-dept').value;
    const level = $('risk-level').value;
    const query = $('risk-search').value.trim().toLowerCase();
    $('risk-filters').style.display = '';
    $('risk-dept').style.display = riskTab === 'nodata' ? 'none' : '';
    $('risk-search').style.display = riskTab === 'groups' || riskTab === 'nodata' ? 'none' : '';
    $('risk-level').style.display = RISK_LEVELS[riskTab] ? '' : 'none';
    if (!$('risk-level').options.length && RISK_LEVELS[riskTab]) riskSwitchTab(riskTab);

    let head, rows, empty, hint = '';

    if (riskTab === 'groups') {
        const list = riskData.weak_groups.filter(r => riskMatch(r, '', dept, level));
        head = ['№', 'Группа', 'Отделение', 'Студентов', 'Средний балл', 'Аттестовано студентов, %', 'Неаттест.', 'Слабых', 'Пропуски «Н», %', 'Уровень', 'Причины'];
        rows = list.map((g, i) => `<tr>
            <td class="num">${i + 1}</td><td><b>${esc(g.name)}</b></td><td>${esc(g.department)}</td>
            <td class="num">${esc(g.students)}</td><td>${scoreCell(g.avg)}</td><td class="num">${esc(g.attested)}</td>
            <td class="num">${esc(g.unattested)}</td><td class="num">${esc(g.weak_students)}</td>
            <td class="num">${esc(g.abs_share)}</td><td>${badge(g.level)}</td><td>${flagsCell(g.flags)}</td></tr>`);
        empty = 'Слабых групп по текущим порогам нет.';
        const d = riskData.departments.map(x => `${x.name}: слабых групп ${x.weak_groups} из ${x.groups}, средний балл ${x.avg}`).join(' · ');
        hint = `Средний балл по колледжу (по группам): ${riskData.summary.college_avg}. ${d}`;

    } else if (riskTab === 'students') {
        const list = riskData.weak_students.filter(r => riskMatch(r, query, dept, level));
        head = ['№', 'ФИО', 'Группа', 'Итоговый балл', 'Пропуски «Н», %', 'Не аттест. предметов', 'Самые слабые предметы', 'Уровень', 'Причины'];
        rows = list.slice(0, RISK_ROW_LIMIT).map((s, i) => `<tr>
            <td class="num">${i + 1}</td><td><b>${esc(s.name)}</b></td><td>${esc(s.group)}</td>
            <td>${scoreCell(s.avg)}</td><td class="num">${esc(s.abs_share)}</td>
            <td class="num">${esc(s.failed)} из ${esc(s.subjects)}</td>
            <td>${flagsCell(s.worst)}</td><td>${badge(s.level)}</td><td>${flagsCell(s.flags)}</td></tr>`);
        empty = 'Слабых студентов по текущим порогам нет.';
        if (list.length > RISK_ROW_LIMIT) hint = `Показаны первые ${RISK_ROW_LIMIT} из ${list.length}. Полный список — в выгрузке Excel.`;
        else hint = `Найдено: ${list.length}. Сортировка: от самых критичных.`;

    } else if (riskTab === 'dead') {
        const list = riskData.dead_souls.filter(r => riskMatch(r, query, dept, level));
        head = ['№', 'ФИО', 'Группа', 'Итоговых / предметов', 'Заполнено, %', 'Пропуски «Н», %', 'Последняя оценка (не «Н»)', 'Тип', 'Причины'];
        rows = list.slice(0, RISK_ROW_LIMIT).map((s, i) => `<tr>
            <td class="num">${i + 1}</td><td><b>${esc(s.name)}</b></td><td>${esc(s.group)}</td>
            <td class="num">${esc(s.grades)} / ${esc(s.cells)}</td><td class="num">${esc(s.coverage)}</td>
            <td class="num">${esc(s.abs_share)}</td><td class="num">${esc(s.last_grade)}</td><td>${badge(s.level, RISK_DEAD_LABEL)}</td><td>${flagsCell(s.reasons)}</td></tr>`);
        empty = 'Мёртвых душ по текущим порогам не выявлено. Чтобы проверить строже, поднимите порог заполненности или снизьте порог «Н» в настройках.';
        hint = 'Признаки: нет ни одной итоговой при заполненных ведомостях группы; «Н» по большинству предметов; итоговые выставлены лишь по части предметов; почти все итоговые одинаковые. Это повод для проверки по приказам и посещаемости, а не окончательный вывод.';

    } else {
        const list = riskData.no_data_groups;
        head = ['Группа', 'Отделение', 'Студентов', 'Причина'];
        rows = list.map(g => `<tr><td><b>${esc(g.name)}</b></td><td>${esc(g.department)}</td><td class="num">${esc(g.students)}</td><td>${esc(g.reason)}</td></tr>`);
        empty = 'Во всех группах есть оценки.';
        hint = 'В этих группах нет ни одной оценки, поэтому анализ невозможен. Их студенты не считаются мёртвыми душами — сначала загрузите учебный план или проверьте, ведутся ли ведомости.';
    }

    thead.innerHTML = '<tr>' + head.map(h => `<th>${esc(h)}</th>`).join('') + '</tr>';
    tbody.innerHTML = rows.length ? rows.join('') : `<tr><td class="empty-note" colspan="${head.length}">${esc(empty)}</td></tr>`;
    $('risk-hint').textContent = hint;
}

// ---------- выгрузка (те же пороги, что на экране)
function exportRisk(kind, btn) {
    const q = riskQuery();
    q.set('kind', kind);
    const names = { all: 'Группа_риска.xlsx', groups: 'Слабые_группы.xlsx', students: 'Слабые_студенты.xlsx', dead: 'Мертвые_души.xlsx' };
    return downloadFile('/api/export/risk?' + q.toString(), names[kind] || 'Группа_риска.xlsx', btn);
}
