// frontend/js/extras.js — смена пароля, журнал действий с поиском, пользователи (выбор, Excel, выдача паролей).
// Подключается ПОСЛЕ app.js: использует его глобальные $, esc, api, journalData, renderLogs.

// ==========================================
// 1. СМЕНА ПАРОЛЯ (обязательная при первом входе / после сброса администратором)
// ==========================================
function setupAccount() {
    const bar = document.querySelector('.header-right');
    if (bar && !$('change-pw-btn')) {
        const b = document.createElement('button');
        b.id = 'change-pw-btn';
        b.className = 'logout-btn';
        b.style.color = '#3B82F6';
        b.textContent = 'Сменить пароль';
        b.onclick = () => showPasswordModal(false);
        bar.insertBefore(b, bar.querySelector('.logout-btn'));
    }
    if (localStorage.getItem('kkci_must_pw') === '1') showPasswordModal(true);
}

function showPasswordModal(forced) {
    if ($('pw-modal')) return;
    const ov = document.createElement('div');
    ov.id = 'pw-modal';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.7);z-index:1000;display:flex;align-items:center;justify-content:center;';
    ov.innerHTML = `
        <div class="auth-card" style="max-width:420px;">
            <h3 style="margin-bottom:6px;color:var(--primary-color);">${forced ? 'Смените пароль' : 'Сменить пароль'}</h3>
            <p style="font-size:13px;color:var(--text-muted);margin-bottom:18px;">${forced
                ? 'Текущий пароль был задан администратором. Для продолжения работы задайте свой.'
                : 'Не короче 8 символов, буквы и цифры.'}</p>
            <div class="egov-input-group"><label>Текущий пароль</label><input type="password" id="pw-old" autocomplete="current-password"></div>
            <div class="egov-input-group"><label>Новый пароль</label><input type="password" id="pw-new" autocomplete="new-password"></div>
            <div class="egov-input-group"><label>Повторите новый пароль</label><input type="password" id="pw-new2" autocomplete="new-password"></div>
            <div id="pw-err" style="color:#DC2626;font-size:13px;min-height:18px;margin-bottom:10px;"></div>
            <div style="display:flex;gap:10px;">
                <button class="egov-btn-primary" style="flex:1;" id="pw-save">Сохранить</button>
                ${forced ? '<button class="egov-btn-secondary" id="pw-logout">Выйти</button>' : '<button class="egov-btn-secondary" id="pw-cancel">Отмена</button>'}
            </div>
        </div>`;
    document.body.appendChild(ov);
    $('pw-old').focus();
    const close = () => ov.remove();
    if ($('pw-cancel')) $('pw-cancel').onclick = close;
    if ($('pw-logout')) $('pw-logout').onclick = logout;
    $('pw-save').onclick = async () => {
        const o = $('pw-old').value, n = $('pw-new').value, n2 = $('pw-new2').value;
        const err = (t) => { $('pw-err').textContent = t; };
        if (!o || !n) return err('Заполните все поля');
        if (n !== n2) return err('Новые пароли не совпадают');
        $('pw-save').disabled = true;
        try {
            const r = await api('/api/me/password', 'POST', { old_password: o, new_password: n });
            if (r.access_token) localStorage.setItem('kkci_token', r.access_token);   // старые токены после смены недействительны
            localStorage.setItem('kkci_must_pw', '');
            close();
            alert('Пароль изменён.');
        } catch (e) {
            err(e.message);
            $('pw-save').disabled = false;
        }
    };
}

// ==========================================
// 3. ЖУРНАЛ ДЕЙСТВИЙ: поиск и страницы
// ==========================================
const logsState = { page: 1, size: 50, q: '', total: 0, timer: null };

async function loadLogsView(resetPage = true) {
    if (resetPage) logsState.page = 1;
    try {
        const qs = new URLSearchParams({ q: logsState.q, page: logsState.page, size: logsState.size });
        const d = await api('/api/admin/logs?' + qs);
        logsState.total = d.total;
        renderLogs(d.items);
        const pages = Math.max(1, Math.ceil(d.total / d.size));
        $('logs-page').textContent = `Стр. ${d.page} из ${pages} · записей: ${d.total}`;
        $('logs-prev').disabled = d.page <= 1;
        $('logs-next').disabled = d.page >= pages;
    } catch (e) { alert(e.message); }
}

function logsSearchInput(v) {
    clearTimeout(logsState.timer);
    logsState.timer = setTimeout(() => { logsState.q = v.trim(); loadLogsView(); }, 350);
}

function logsGo(delta) {
    logsState.page = Math.max(1, logsState.page + delta);
    loadLogsView(false);
}


// ==========================================
// 4. ПОЛЬЗОВАТЕЛИ: таблица с выбором, список в Excel, выдача новых паролей
// ==========================================
const usersSelected = new Set();

function renderUsers() {
    const me = localStorage.getItem('kkci_name');
    document.querySelector('#users-table tbody').innerHTML = usersCache.map(u => {
        const role = u.role === 'admin' ? '<span style="color:#3B82F6; font-weight:bold;">Админ</span>' : 'Преподаватель';
        const status = u.is_active
            ? (u.must_change_password ? '<span class="risk-badge risk-watch" title="Должен сменить пароль при входе">Новый пароль</span>' : '<span style="color:#059669;font-weight:600;">Активна</span>')
            : '<span class="risk-badge risk-critical">Отключена</span>';
        return `<tr>
            <td style="text-align:center;"><input type="checkbox" ${usersSelected.has(u.id) ? 'checked' : ''} onchange="usersToggle(${u.id}, this.checked)"></td>
            <td style="text-align:center;">${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.full_name)}</td><td>${role}</td>
            <td>${status}</td><td style="color:#64748B;">${esc(u.last_login || 'не входил(а)')}</td></tr>`;
    }).join('');
    usersSyncAll();
}

function usersSyncAll() {
    const all = $('users-all');
    if (all) all.checked = usersCache.length > 0 && usersCache.every(u => usersSelected.has(u.id));
}
function usersToggle(id, on) { on ? usersSelected.add(id) : usersSelected.delete(id); usersSyncAll(); }
function usersToggleAll(on) { usersCache.forEach(u => on ? usersSelected.add(u.id) : usersSelected.delete(u.id)); renderUsers(); }
function usersSelectNever() {
    usersSelected.clear();
    usersCache.filter(u => !u.last_login && u.role !== 'admin').forEach(u => usersSelected.add(u.id));
    renderUsers();
    if (!usersSelected.size) alert('Все преподаватели уже входили в систему.');
}

function exportUsers(btn) {
    return downloadFile('/api/export/users', 'Пользователи.xlsx', btn);
}

// POST с файлом в ответе (в отличие от downloadFile из app.js, который делает GET)
async function downloadPost(path, body, fallbackName, btn) {
    const label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Формирую…'; }
    try {
        const res = await fetch(API_URL + path, {
            method: 'POST',
            headers: { Authorization: `Bearer ${localStorage.getItem('kkci_token')}`, 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (res.status === 401) { logout(); return false; }
        if (!res.ok) {
            let msg = `Ошибка сервера (${res.status})`;
            try { const d = await res.json(); if (typeof d.detail === 'string') msg = d.detail; } catch (_) { /* не JSON */ }
            throw new Error(msg);
        }
        const blob = await res.blob();
        let name = fallbackName;
        const m = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('Content-Disposition') || '');
        if (m) { try { name = decodeURIComponent(m[1]); } catch (_) { /* запасное имя */ } }
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        return true;
    } catch (e) {
        alert('Не удалось выполнить: ' + e.message);
        return false;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = label; }
    }
}

async function issuePasswords(btn) {
    const ids = [...usersSelected];
    if (!ids.length) return alert('Отметьте пользователей в таблице галочками (или нажмите «Выбрать не входивших»).');
    if (!confirm(`Выдать НОВЫЕ временные пароли выбранным пользователям: ${ids.length}?\n\n` +
        'Их текущие пароли перестанут работать, открытые сессии завершатся. Пароли будут только в скачанном файле — ' +
        'при первом входе система потребует задать свой. Выдача большого числа паролей занимает до минуты.')) return;
    const ok = await downloadPost('/api/admin/users/issue-passwords', { ids }, 'Новые_пароли.xlsx', btn);
    if (ok) { usersSelected.clear(); loadUsersView(); }
}
