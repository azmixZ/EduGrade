// frontend/js/app.js
// Локально (start.bat: сайт на :5500 или файл index.html) API на 127.0.0.1:8000;
// на сервере сайт и API за одним nginx — запросы идут на тот же адрес.
const API_URL = (location.protocol === 'file:' || location.port === '5500') ? "http://127.0.0.1:8000" : "";

let workloads = [];        // нагрузка преподавателя: [{workload_id, group_id, group_name, subject_id, subject_name}]
let journalData = null;    // текущая открытая ведомость
let activeGroupId = "";    // для отката выбора, если сохранение не удалось
let activeSubjectId = "";
let analyticsData = null;
let usersCache = [];       // список пользователей для таблицы и выгрузок
let charts = {};

const GRADE_LABELS = ['Н', '<50', '3', '4', '5'];   // корзины статистики (как на сервере)
const $ = (id) => document.getElementById(id);

// ==========================================
// 0. УТИЛИТЫ
// ==========================================
function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Единая обёртка над fetch: подставляет токен, разбирает ошибки, при 401 выходит из системы
// Период аттестации, выбранный во вкладках (админ и куратор). Пусто = текущий по календарю.
// Преподавателю сервер всегда отдаёт текущий период, что бы ни пришло в запросе.
let attPeriod = '';
let periodsInfo = null;
function withPeriod(path) {
    if (!attPeriod || !path.startsWith('/api/') || /[?&]period=/.test(path)) return path;
    return path + (path.includes('?') ? '&' : '?') + 'period=' + encodeURIComponent(attPeriod);
}

async function api(path, method = 'GET', body = null) {
    if (method === 'GET') path = withPeriod(path);
    const headers = {};
    const token = localStorage.getItem('kkci_token');
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const opts = { method, headers };
    if (body !== null) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(API_URL + path, opts);
    if (res.status === 401) {
        logout();
        throw new Error('Сессия истекла. Войдите заново.');
    }
    if (!res.ok) {
        let msg = `Ошибка сервера (${res.status})`;
        try {
            const d = await res.json();
            if (typeof d.detail === 'string') msg = d.detail;
            else if (Array.isArray(d.detail)) msg = d.detail.map(x => x.msg).join('; ');
        } catch (_) { /* тело не JSON */ }
        throw new Error(msg);
    }
    return res.json();
}

function destroyChart(key) {
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

// 100-балльная система: 90–100 = «5», 70–89 = «4», 50–69 = «3», меньше 50 = неаттестация, «Н» = 0 баллов.
// Та же нормализация, что и на сервере. null = недопустимое значение
function normalizeGrade(raw) {
    const v = String(raw ?? '').trim().toUpperCase().replace(/\s+/g, '');
    if (v === '') return '';
    if (['Н', 'H', 'N', 'НБ', 'Н/Б'].includes(v)) return 'Н';
    if (/^[0-9]{1,3}$/.test(v) && Number(v) <= 100) return String(Number(v));
    return null;
}

function gradePoints(v) { return v === 'Н' ? 0 : Number(v); }

function finalGrade(points) {
    return points >= 90 ? '5' : points >= 70 ? '4' : points >= 50 ? '3' : 'неатт.';
}

// CSS-класс для цвета ячейки по уровню оценки
function levelClass(raw) {
    const v = normalizeGrade(raw);
    if (v === null || v === '') return '';
    if (v === 'Н') return 'lvl-n';
    const p = gradePoints(v);
    return p < 50 ? 'lvl-0' : p < 70 ? 'lvl-3' : p < 90 ? 'lvl-4' : 'lvl-5';
}

function setLevel(inp) {
    inp.classList.remove('lvl-5', 'lvl-4', 'lvl-3', 'lvl-0', 'lvl-n');
    const c = levelClass(inp.value);
    if (c) inp.classList.add(c);
}

// ==========================================
// 1. АВТОРИЗАЦИЯ И ИНИЦИАЛИЗАЦИЯ
// ==========================================
async function handleLogin(event) {
    event.preventDefault();
    const formData = new URLSearchParams();
    formData.append("username", $('username').value.trim());
    formData.append("password", $('password').value);
    try {
        const response = await fetch(`${API_URL}/login`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: formData
        });
        if (!response.ok) {
            let msg = "Неверный логин или пароль!";
            try {
                const d = await response.json();
                if (response.status === 429 && typeof d.detail === 'string') msg = d.detail;   // временная блокировка входа
            } catch (_) { /* тело не JSON */ }
            return alert(msg);
        }
        const data = await response.json();
        localStorage.setItem("kkci_token", data.access_token);
        localStorage.setItem("kkci_role", data.role);
        localStorage.setItem("kkci_head", data.head || "");
        localStorage.setItem("kkci_name", data.full_name);
        localStorage.setItem("kkci_must_pw", data.must_change_password ? "1" : "");
        initApp();
    } catch (e) {
        alert("Ошибка сервера. Убедитесь, что бэкенд запущен.");
    }
}

function isHead() { return localStorage.getItem("kkci_role") !== "admin" && !!localStorage.getItem("kkci_head"); }

function initApp() {
    if (!localStorage.getItem("kkci_token")) return;
    $("login-screen").style.display = "none";
    $("app-screen").style.display = "block";
    const role = localStorage.getItem("kkci_role");
    const userName = localStorage.getItem("kkci_name") || "Пользователь";

    $("current-user-name").textContent = userName;
    $("avatar-circle").textContent = userName.charAt(0).toUpperCase();

    if (role === "admin") {
        $("current-user-role").textContent = "Администратор системы";
        $("current-user-role").style.color = "#3B82F6";
        $("admin-menu").style.display = "block";
        $("teacher-menu").style.display = "none";
        switchView('dashboard-view', document.querySelector('#admin-menu .nav-item'));
    } else if (isHead()) {
        // заведующий отделением: своя ведомость + аналитика своего отделения, без журнала и управления учётками
        $("current-user-role").textContent = "Зав. отделением · " + localStorage.getItem("kkci_head");
        $("current-user-role").style.color = "#3B82F6";
        $("admin-menu").style.display = "block";
        $("teacher-menu").style.display = "block";
        document.querySelectorAll('#admin-menu .admin-strict').forEach(el => el.style.display = 'none');
        $("analytics-title").textContent = "Аналитика отделения";
        $("dept-chart-title").textContent = "Успеваемость отделения: " + localStorage.getItem("kkci_head");
        switchView('dashboard-view', document.querySelector('#admin-menu .nav-item'));
        loadWorkloads();
    } else {
        $("current-user-role").textContent = "Преподаватель";
        $("admin-menu").style.display = "none";
        $("teacher-menu").style.display = "block";
        switchView('grid-view', document.querySelector('#teacher-menu .nav-item'));
        loadWorkloads();
    }
    // права могли поменяться с прошлого входа (назначили / сняли заведующего) — сверяемся с сервером
    if (role !== "admin") api('/api/me').then(m => {
        if ((m.head || "") !== (localStorage.getItem("kkci_head") || "")) {
            localStorage.setItem("kkci_head", m.head || "");
            location.reload();
        }
    }).catch(() => {});
    if (typeof setupCurator === 'function') setupCurator();   // вкладки куратора и сверки
    if (typeof setupAccount === 'function') setupAccount();   // кнопка «Сменить пароль» и принудительная смена
}

function logout() {
    localStorage.clear();
    location.reload();
}

let currentView = null, currentViewBtn = null;
function switchView(viewId, btnElement) {
    currentView = viewId; currentViewBtn = btnElement || null;
    if (typeof periodTabsFor === 'function') periodTabsFor(viewId);
    document.querySelectorAll('.view-panel').forEach(el => { el.style.display = 'none'; el.classList.remove('animate-fade-up'); });
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));

    const targetView = $(viewId);
    targetView.style.display = 'flex';
    void targetView.offsetWidth;
    targetView.classList.add('animate-fade-up');
    if (btnElement) btnElement.classList.add('active');

    if (viewId === 'users-view') loadUsersView();
    if (viewId === 'risk-view' && typeof loadRiskView === 'function') loadRiskView();
    if (['dashboard-view', 'performance-view', 'analytics-view'].includes(viewId)) loadAdminView(viewId);
    if (viewId === 'logs-view' && typeof loadLogsView === 'function') loadLogsView();
    if (viewId === 'pullup-view' && typeof loadPullupView === 'function') loadPullupView();
    if (viewId === 'curator-view' && typeof loadCuratorView === 'function') loadCuratorView();
    if (viewId === 'reconcile-view' && typeof loadReconcileView === 'function') loadReconcileView();
    if (viewId === 'access-view' && typeof loadAccessView === 'function') loadAccessView();
}

// ==========================================
// 2. ЖУРНАЛ (ПРЕПОДАВАТЕЛЬ)
// ==========================================
async function loadWorkloads() {
    try {
        workloads = await api('/api/workloads');
    } catch (e) {
        return alert(e.message);
    }

    const groups = new Map();
    workloads.forEach(w => groups.set(w.group_id, w.group_name));

    const select = $('group-selector');
    select.innerHTML = '<option value="">Выберите группу...</option>' +
        [...groups].map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join('');
    select.onchange = onGroupChange;
    $('subject-selector').onchange = onSubjectChange;

    if (typeof renderJournalHints === 'function') renderJournalHints();   // notify.js: названия групп и предметов в подсказке
    if (!workloads.length) showSheetMessage('За вами не закреплена учебная нагрузка. Обратитесь к администратору.');
    else showSheetMessage('Выберите группу и предмет, чтобы открыть ведомость.');
}

function showSheetMessage(text) {
    document.querySelector('#grade-sheet thead').innerHTML = '';
    document.querySelector('#grade-sheet tbody').innerHTML =
        `<tr><td class="empty-note" colspan="99">${esc(text)}</td></tr>`;
    journalData = null;
}

async function onGroupChange(e) {
    const newGroup = e.target.value;
    if (!(await saveAllGrades(false))) { e.target.value = activeGroupId; return; }

    const subjSel = $('subject-selector');
    activeGroupId = newGroup;
    if (!newGroup) {
        subjSel.innerHTML = '<option value="">Предмет...</option>';
        activeSubjectId = "";
        return showSheetMessage('Выберите группу и предмет, чтобы открыть ведомость.');
    }
    const subjects = workloads.filter(w => String(w.group_id) === newGroup);
    subjSel.innerHTML = subjects.map(w => `<option value="${w.subject_id}">${esc(w.subject_name)}</option>`).join('');
    activeSubjectId = subjSel.value;
    loadJournal();
}

async function onSubjectChange(e) {
    if (!(await saveAllGrades(false))) { e.target.value = activeSubjectId; return; }
    activeSubjectId = e.target.value;
    loadJournal();
}

async function loadJournal() {
    if (!activeGroupId || !activeSubjectId) return;
    try {
        journalData = await api(`/api/journal/${activeGroupId}?subject_id=${activeSubjectId}`);
        renderTable();
        if (typeof loadHints === 'function') { await loadHints(); markSingleThrees(); }   // notify.js
    } catch (e) {
        showSheetMessage(e.message);
    }
}

function renderTable() {
    const thead = document.querySelector('#grade-sheet thead');
    const tbody = document.querySelector('#grade-sheet tbody');
    if (!journalData) return;

    const lesson = journalData.lessons[0];              // единственная колонка — итоговая за период
    const pt = $('sheet-period');
    if (pt) pt.textContent = journalData.period_title ? `Сейчас идёт: ${journalData.period_title}` : '';
    const gradeMap = new Map(journalData.grades.map(g => [String(g.student_id), g.value]));

    thead.innerHTML = '<tr><th class="row-num-col">№</th><th class="frozen-col">ФИО студента</th>' +
        '<th style="min-width:150px;">Итоговый балл<br><small>0–100 или Н</small></th>' +
        '<th class="final-col" style="min-width:110px;">Оценка</th></tr>';

    if (!journalData.students.length || !lesson) {
        tbody.innerHTML = '<tr><td class="empty-note" colspan="99">В группе нет студентов.</td></tr>';
        return updateSheetProgress();
    }

    tbody.innerHTML = journalData.students.map((s, i) => {
        const v = gradeMap.get(String(s.id)) || '';
        return `<tr><td class="row-num">${i + 1}</td><td class="frozen-col">${esc(s.full_name)}</td>` +
            `<td><input type="text" class="grade-input ${levelClass(v)}" maxlength="3" autocomplete="off" ` +
            `data-student="${s.id}" data-lesson="${lesson.id}" data-orig="${esc(v)}" value="${esc(v)}"></td>` +
            `<td class="final-td">${esc(markOf(v))}</td></tr>`;
    }).join('');
    updateSheetProgress();
}

// Оценка (5/4/3/неатт.) по итоговому баллу; «Н» = не аттестован
function markOf(raw) {
    const v = normalizeGrade(raw);
    if (v === null || v === '') return '';
    return v === 'Н' ? 'Н' : finalGrade(gradePoints(v));
}

function updateFinalCell(inp) {
    const cell = inp.closest('tr').querySelector('.final-td');
    if (cell) cell.textContent = markOf(inp.value);
    updateSheetProgress();
}

// «Заполнено X из Y» и распределение оценок по ведомости
function updateSheetProgress() {
    const box = $('sheet-progress');
    if (!box) return;
    const inputs = [...document.querySelectorAll('#grade-sheet .grade-input')];
    if (!inputs.length) { box.textContent = ''; return; }
    const cnt = { '5': 0, '4': 0, '3': 0, 'неатт.': 0, 'Н': 0 };
    let filled = 0;
    inputs.forEach(i => {
        const m = markOf(i.value);
        if (m) { filled++; cnt[m]++; }
    });
    box.textContent = `Заполнено ${filled} из ${inputs.length} · «5»: ${cnt['5']} · «4»: ${cnt['4']} · «3»: ${cnt['3']} · ` +
        `неаттестованы: ${cnt['неатт.'] + cnt['Н']}`;
}

function getChangedInputs() {
    return [...document.querySelectorAll('#grade-sheet .grade-input')]
        .filter(i => i.value.trim() !== i.dataset.orig);
}

// Делегирование событий: один обработчик на всю таблицу
document.addEventListener('input', (e) => {
    const inp = e.target;
    if (!inp.classList || !inp.classList.contains('grade-input')) return;
    inp.classList.toggle('changed', inp.value.trim() !== inp.dataset.orig);
    inp.classList.remove('invalid');
    setLevel(inp);
    updateFinalCell(inp);
});

// Перемещение по ячейкам: Enter / ↓ вниз, ↑ вверх
document.addEventListener('keydown', (e) => {
    const inp = e.target;
    if (!inp.classList || !inp.classList.contains('grade-input')) return;
    if (!['Enter', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
    e.preventDefault();
    const td = inp.closest('td');
    const tr = td.parentElement;
    const target = e.key === 'ArrowUp' ? tr.previousElementSibling : tr.nextElementSibling;
    const next = target && target.cells[td.cellIndex] && target.cells[td.cellIndex].querySelector('input');
    if (next) { next.focus(); next.select(); }
});

// Нормализация при выходе из ячейки (h -> Н, 085 -> 85)
document.addEventListener('focusout', (e) => {
    const inp = e.target;
    if (!inp.classList || !inp.classList.contains('grade-input')) return;
    const v = normalizeGrade(inp.value);
    if (v === null) inp.classList.add('invalid');
    else { inp.value = v; inp.classList.toggle('changed', v !== inp.dataset.orig); setLevel(inp); updateFinalCell(inp); }
});

window.addEventListener('beforeunload', (e) => {
    if (getChangedInputs().length) { e.preventDefault(); e.returnValue = ''; }
});

// Возвращает true, если всё сохранено (или сохранять нечего)
async function saveAllGrades(showAlert = true) {
    const changed = getChangedInputs();
    if (!changed.length) {
        if (showAlert) alert("Нет новых итоговых оценок для сохранения!");
        return true;
    }

    const payload = [];
    for (const inp of changed) {
        const v = normalizeGrade(inp.value);
        if (v === null) {
            inp.classList.add('invalid');
            inp.focus();
            alert(`Недопустимый итоговый балл «${inp.value}». Допустимо: число от 0 до 100 или Н (или пусто).`);
            return false;
        }
        payload.push({ student_id: Number(inp.dataset.student), lesson_id: Number(inp.dataset.lesson), value: v });
    }

    try {
        const r = await api('/api/journal/grades', 'POST', { grades: payload });
        changed.forEach(inp => {
            const v = normalizeGrade(inp.value);
            inp.value = v;
            inp.dataset.orig = v;
            inp.classList.remove('changed', 'invalid');
        });
        if (showAlert) alert(`Сохранено изменений: ${r.saved}`);
        return true;
    } catch (e) {
        alert('Не удалось сохранить: ' + e.message);
        return false;
    }
}

async function loadAdminView(viewId) {
    try {
        const needStats = ['dashboard-view', 'logs-view', 'performance-view'].includes(viewId);
        const needAnalytics = ['dashboard-view', 'analytics-view'].includes(viewId);
        const [stats, analytics] = await Promise.all([
            needStats ? api('/api/admin/stats') : null,
            needAnalytics ? api('/api/admin/analytics') : null
        ]);
        if (analytics) analyticsData = analytics;

        if (viewId === 'dashboard-view') {
            $('stat-students').textContent = stats.counts.students;
            $('stat-grades').textContent = stats.counts.grades;
            $('stat-groups').textContent = stats.counts.groups;
            renderSparklines(stats, analytics);
        }
        if (viewId === 'logs-view') renderLogs(stats.logs);
        if (viewId === 'performance-view') renderCollegeCharts(stats.chart);
        if (viewId === 'analytics-view') initAnalyticsTab();
    } catch (e) {
        console.error("Ошибка аналитики", e);
        alert(e.message);
    }
}

function renderLogs(logs) {
    const tbody = document.querySelector('#audit-table tbody');
    if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-note">Действий пока нет.</td></tr>';
        return;
    }
    tbody.innerHTML = logs.map(l => `<tr>
        <td style="color:#64748B;">${esc(l.time)}</td>
        <td style="font-weight:500;">${esc(l.month)}</td>
        <td style="color:#3B82F6; font-weight:600;">${esc(l.group)}</td>
        <td>${esc(l.subject)}</td>
        <td>${esc(l.student)}</td>
        <td><span style="background:#F1F5F9; padding:4px 8px; border-radius:4px;">${esc(l.action)}</span></td>
        <td style="font-weight:600;">${esc(l.teacher)}</td>
    </tr>`).join('');
}

function renderCollegeCharts(dist) {
    const order = ['5', '4', '3', '<50', 'Н'];
    const labels = ['Отлично (90–100)', 'Хорошо (70–89)', 'Удовл. (50–69)', 'Неаттестация (<50)', 'Пропуски (Н = 0)'];
    const colors = ['#10B981', '#3B82F6', '#F59E0B', '#EF4444', '#94A3B8'];
    const counts = order.map(k => dist[k] || 0);
    const total = counts.reduce((a, b) => a + b, 0);

    destroyChart('bar');
    charts.bar = new Chart($('barChart'), {
        type: 'bar',
        data: { labels, datasets: [{ data: counts, backgroundColor: colors, borderRadius: 6 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, animation: { duration: 1000, easing: 'easeOutQuart' } }
    });

    destroyChart('doughnut');
    charts.doughnut = new Chart($('doughnutChart'), {
        type: 'doughnut',
        data: { labels, datasets: [{ data: counts, backgroundColor: colors, borderWidth: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '70%',
            animation: { animateScale: true, duration: 1000 },
            plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                    callbacks: {
                        label: (c) => ` ${c.label}: ${c.raw} (${total ? (c.raw * 100 / total).toFixed(1) : 0}%)`
                    }
                }
            }
        }
    });
}

function renderSparklines(stats, an) {
    const opts = {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
        elements: { point: { radius: 0, hoverRadius: 4 } }
    };
    const names = an.groups.map(g => g.name);

    destroyChart('sp1');
    charts.sp1 = new Chart($('spark-students'), {
        type: 'line',
        data: { labels: names, datasets: [{ label: 'Студентов', data: an.groups.map(g => g.students), borderColor: '#3B82F6', tension: 0.4, borderWidth: 3 }] },
        options: opts
    });

    destroyChart('sp2');
    charts.sp2 = new Chart($('spark-grades'), {
        type: 'bar',
        data: { labels: GRADE_LABELS, datasets: [{ label: 'Оценок', data: GRADE_LABELS.map(k => stats.chart[k] || 0), backgroundColor: '#10B981', borderRadius: 4 }] },
        options: opts
    });

    destroyChart('sp3');
    charts.sp3 = new Chart($('spark-groups'), {
        type: 'line',
        data: { labels: names, datasets: [{ label: 'Успеваемость, %', data: an.groups.map(g => g.uspev), borderColor: '#F59E0B', tension: 0.4, borderWidth: 3, fill: true, backgroundColor: 'rgba(245, 158, 11, 0.1)' }] },
        options: opts
    });
}

function initAnalyticsTab() {
    if (!analyticsData) return;
    const depts = analyticsData.departments;

    destroyChart('dept');
    charts.dept = new Chart($('deptCompareChart'), {
        type: 'bar',
        data: {
            labels: depts.map(d => d.name),
            datasets: [{ label: 'Успеваемость (%)', data: depts.map(d => d.uspev), backgroundColor: ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6'], borderRadius: 6 }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (c) => [` Успеваемость: ${c.raw}%`, ` Средний балл: ${depts[c.dataIndex].avg} из 100`]
                    }
                }
            },
            scales: { y: { beginAtZero: true, max: 100 } },
            animation: { duration: 1000 }
        }
    });

    const sA = $('group-a-select');
    const sB = $('group-b-select');
    const prevA = sA.value, prevB = sB.value;
    const optionsHtml = analyticsData.groups.map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join('');
    sA.innerHTML = optionsHtml;
    sB.innerHTML = optionsHtml;
    if (prevA && sA.querySelector(`option[value="${prevA}"]`)) sA.value = prevA;
    if (prevB && sB.querySelector(`option[value="${prevB}"]`)) sB.value = prevB;
    else if (sB.options.length > 1) sB.selectedIndex = 1;

    renderGroupCharts();
}

function renderGroupCharts() {
    if (!analyticsData) return;
    const find = (id) => analyticsData.groups.find(g => String(g.id) === String(id));
    const a = find($('group-a-select').value);
    const b = find($('group-b-select').value);
    if (!a || !b) return;
    const pair = [a, b];

    destroyChart('gvsg');
    charts.gvsg = new Chart($('groupVsGroupChart'), {
        type: 'bar',
        data: { labels: [a.name, b.name], datasets: [{ label: 'Успеваемость', data: [a.uspev, b.uspev], backgroundColor: ['#3B82F6', '#EF4444'], borderRadius: 4 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (c) => [` Успеваемость: ${pair[c.dataIndex].uspev}%`, ` Средний балл: ${pair[c.dataIndex].avg} из 100`]
                    }
                }
            },
            scales: { y: { beginAtZero: true, max: 100 } }
        }
    });

    destroyChart('gWave');
    charts.gWave = new Chart($('groupWaveChart'), {
        type: 'line',
        data: {
            labels: ['Н (0 баллов)', 'Неаттестация (<50)', '50–69 (3)', '70–89 (4)', '90–100 (5)'],
            datasets: [
                { label: a.name, data: a.dist, borderColor: '#3B82F6', backgroundColor: 'rgba(59, 130, 246, 0.4)', fill: true, tension: 0.5 },
                { label: b.name, data: b.dist, borderColor: '#EF4444', backgroundColor: 'rgba(239, 68, 68, 0.4)', fill: true, tension: 0.5 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top' },
                tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.raw} оц.` } }
            },
            scales: { y: { beginAtZero: true, display: false } },
            animation: { duration: 1200, easing: 'easeOutQuart' }
        }
    });
}

// ==========================================
// 4. УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ
// ==========================================
async function loadUsersView() {
    try {
        [usersCache, departmentsCache] = await Promise.all([api('/api/admin/users'), api('/api/admin/departments')]);
        renderUsers();                 // таблица с выбором и статусами — в extras.js
    } catch (e) {
        alert(e.message);
    }
}

async function handleCreateUser(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
        username: $('new-username').value.trim(),
        full_name: $('new-fullname').value.trim(),
        password: $('new-password').value,
        role: $('new-role').value
    };
    try {
        await api('/api/admin/users', 'POST', payload);
        alert("Аккаунт создан!");
        form.reset();
        loadUsersView();
    } catch (err) {
        alert(err.message);
    }
}

// ==========================================
// 5. ВЫГРУЗКИ В EXCEL
// ==========================================
async function downloadFile(path, fallbackName, btn) {
    const label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Формирую…'; }
    try {
        const token = localStorage.getItem('kkci_token');
        const res = await fetch(API_URL + withPeriod(path), { headers: { Authorization: `Bearer ${token}` } });
        if (res.status === 401) { logout(); return; }
        if (!res.ok) {
            let msg = `Ошибка сервера (${res.status})`;
            try { const d = await res.json(); if (typeof d.detail === 'string') msg = d.detail; } catch (_) { /* не JSON */ }
            throw new Error(msg);
        }
        const blob = await res.blob();
        let name = fallbackName;
        const m = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('Content-Disposition') || '');
        if (m) { try { name = decodeURIComponent(m[1]); } catch (_) { /* оставляем запасное имя */ } }
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
        alert('Не удалось сформировать файл: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = label; }
    }
}

// Преподаватель: текущая ведомость
async function exportJournal(btn) {
    if (!journalData || !journalData.workload_id) return alert('Выберите группу и предмет!');
    if (!(await saveAllGrades(false))) return;          // сначала сохраняем введённое, чтобы в файле было всё
    await downloadFile(`/api/export/journal/${journalData.workload_id}`, 'Ведомость.xlsx', btn);
}

// Админ: полный отчёт по группе A, сравнение A и B, сравнение всех групп
function exportGroupReport(btn) {
    const id = $('group-a-select').value;
    if (!id) return alert('Выберите группу');
    return downloadFile(`/api/export/group/${encodeURIComponent(id)}`, 'Отчет_по_группе.xlsx', btn);
}

function exportCompare(btn) {
    const a = $('group-a-select').value, b = $('group-b-select').value;
    if (!a || !b) return alert('Выберите две группы');
    if (a === b) return alert('Выберите две разные группы');
    return downloadFile(`/api/export/compare?groups=${encodeURIComponent(a)},${encodeURIComponent(b)}`, 'Сравнение_групп.xlsx', btn);
}

function exportAllGroups(btn) {
    return downloadFile('/api/export/compare', 'Сравнение_всех_групп.xlsx', btn);
}

document.addEventListener("DOMContentLoaded", () => {
    if (localStorage.getItem("kkci_token")) initApp();
});
