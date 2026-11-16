// frontend/js/periods.js — вкладки периода аттестации в шапке (администратор и кураторы).
// Преподаватель ничего не выбирает: сервер сам открывает ему период, который идёт по календарю.
// Подключается ПОСЛЕ app.js и curator.js: использует их глобальные $, esc, api, attPeriod, switchView.

const PERIOD_SHORT = { oct: 'Октябрь', s1: 'Итоги 1 сем.', mar: 'Март', s2: 'Итоги 2 сем.' };

async function setupPeriods() {
    const role = localStorage.getItem('kkci_role');
    try { periodsInfo = await api('/api/periods'); } catch (e) { return; }
    const isCurator = typeof curatorGroups !== 'undefined' && curatorGroups.length > 0;
    if (role !== 'admin' && !isCurator && !isHead()) return;             // обычному преподавателю вкладки не нужны
    attPeriod = attPeriod || periodsInfo.current;
    renderPeriodTabs();
}

function renderPeriodTabs() {
    const box = $('period-tabs');
    if (!box || !periodsInfo) return;
    box.style.display = currentView === 'grid-view' && localStorage.getItem('kkci_role') !== 'admin' ? 'none' : '';
    box.innerHTML = '<span class="period-label">Аттестация:</span>' + periodsInfo.periods.map(p =>
        `<button class="period-tab${p.code === attPeriod ? ' active' : ''}" title="${esc(p.title)} · преподавателям открыта ${esc(p.window)}"` +
        ` onclick="choosePeriod('${p.code}')">${esc(PERIOD_SHORT[p.code] || p.title)}${p.current ? ' <i>сейчас</i>' : ''}</button>`).join('');
}

// над своей ведомостью преподаватель-куратор вкладки не видит: там всегда текущий период
function periodTabsFor(viewId) {
    const box = document.getElementById('period-tabs');
    if (!box || !periodsInfo || !attPeriod) return;
    box.style.display = viewId === 'grid-view' && localStorage.getItem('kkci_role') !== 'admin' ? 'none' : '';
}

function choosePeriod(code) {
    if (code === attPeriod) return;
    attPeriod = code;
    renderPeriodTabs();
    // перезагружаем открытый раздел уже за выбранный период (кроме ведомости преподавателя — там всегда текущий)
    if (currentView && currentView !== 'grid-view') switchView(currentView, currentViewBtn);
}
