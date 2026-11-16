// frontend/js/notify.js — подсказка «одна тройка до хорошиста» прямо в ведомости и у куратора, окно «Что нового».
// Подключается ПОСЛЕ app.js, curator.js и periods.js: использует их глобальные $, esc, api, workloads, journalData.

let hintsData = null;          // /api/notifications: студенты, у которых единственная тройка

async function setupNotify() {
    await loadHints();
    renderJournalHints();
    showNews();
}

async function loadHints() {
    try { hintsData = await api('/api/notifications'); } catch (_) { hintsData = null; }
    return hintsData;
}

function hintWord(n) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return 'студент';
    if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return 'студента';
    return 'студентов';
}

// ---------- преподаватель: над ведомостью — где есть такие студенты (по клику открывается нужная ведомость)
function renderJournalHints() {
    const box = $('sheet-hints');
    if (!box) return;
    const mine = hintsData ? hintsData.items.filter(x => x.mine) : [];
    if (!mine.length || localStorage.getItem('kkci_role') === 'admin') { box.innerHTML = ''; return; }
    const by = new Map();
    mine.forEach(x => by.set(x.workload_id, [...(by.get(x.workload_id) || []), x]));
    const chips = [...by].map(([wid, list]) => {
        const w = (typeof workloads !== 'undefined' ? workloads : []).find(v => v.workload_id === wid);
        const label = w ? `${w.group_name} · ${w.subject_name}` : `${list[0].group} · ${list[0].subject}`;
        const active = journalData && journalData.workload_id === wid;
        return `<button class="hint-chip${active ? ' active' : ''}" ${w ? `onclick="openJournalFor(${w.group_id}, ${w.subject_id})"` : ''}>` +
            `${esc(label)} <b>${list.length}</b></button>`;
    }).join('');
    box.innerHTML = `<div class="hint-box"><div class="hint-text">🎯 <b>Одна тройка до хорошиста.</b> У этих студентов по вашему предмету ` +
        `единственная тройка, остальные предметы на 4 и 5. Если подтянуть — студент станет хорошистом. Нажмите, чтобы открыть ведомость:</div>` +
        `<div class="hint-chips">${chips}</div></div>`;
}

// ---------- преподаватель: в открытой ведомости подсветить таких студентов
function markSingleThrees() {
    renderJournalHints();
    if (!hintsData || !journalData) return;
    const here = hintsData.items.filter(x => x.workload_id === journalData.workload_id);
    here.forEach(x => {
        const input = document.querySelector(`#grade-sheet .grade-input[data-student="${x.student_id}"]`);
        if (!input) return;
        const tr = input.closest('tr');
        tr.classList.add('row-one-three');
        const name = tr.querySelector('.frozen-col');
        if (name && !name.querySelector('.one-three-tag'))
            name.insertAdjacentHTML('beforeend', `<span class="one-three-tag" title="По остальным предметам у студента 4 и 5">` +
                `единственная тройка · до «4» не хватает ${esc(x.gap)}</span>`);
    });
}

async function openJournalFor(groupId, subjectId) {
    const g = $('group-selector'), s = $('subject-selector');
    if (String(activeGroupId) !== String(groupId)) {
        g.value = String(groupId);
        await onGroupChange({ target: g });
    }
    if (String(activeSubjectId) !== String(subjectId)) {
        s.value = String(subjectId);
        await onSubjectChange({ target: s });
    }
}

// ---------- куратор и администратор: список по выбранной группе в «Контроле заполнения»
async function renderCuratorHints(groupId) {
    const box = $('curator-hints');
    if (!box) return;
    await loadHints();
    const list = hintsData ? hintsData.items.filter(x => x.group_id === Number(groupId)) : [];
    if (!list.length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="hint-box"><div class="hint-text">🎯 <b>Одна тройка до хорошиста: ${list.length} ${hintWord(list.length)}.</b> ` +
        `Остальные предметы у них на 4 и 5 — стоит поговорить с преподавателем.</div>` +
        `<table class="hint-table"><tr><th>Студент</th><th>Предмет с тройкой</th><th>Преподаватель</th><th>Балл</th><th>До «4»</th></tr>` +
        list.map(x => `<tr><td><b>${esc(x.student)}</b></td><td>${esc(x.subject)}</td><td>${esc(x.teacher)}</td>` +
            `<td class="num">${esc(x.points)}</td><td class="num ${x.gap <= 5 ? 'gap-easy' : ''}">+${esc(x.gap)}</td></tr>`).join('') +
        `</table></div>`;
}

// ---------- окно «Что нового»: один раз для каждого пользователя после обновления
async function showNews() {
    if (localStorage.getItem('kkci_must_pw') === '1' || $('news-modal')) return;   // сначала — обязательная смена пароля
    let n;
    try { n = await api('/api/news'); } catch (_) { return; }
    if (!n.show || !n.items.length) return;
    const ov = document.createElement('div');
    ov.id = 'news-modal';
    ov.className = 'news-overlay';
    ov.innerHTML = `<div class="auth-card news-card">
        <div class="news-badge">Новое</div>
        <h3>${esc(n.title)}</h3>
        <ul>${n.items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>
        <button class="egov-btn-primary block" id="news-ok">Понятно</button></div>`;
    document.body.appendChild(ov);
    $('news-ok').onclick = async () => {
        ov.remove();
        try { await api('/api/news/seen', 'POST', {}); } catch (_) { /* покажем ещё раз при следующем входе */ }
    };
}
