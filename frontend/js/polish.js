// frontend/js/polish.js — анимация чисел в карточках и всплывающие уведомления. Логику приложения не меняет.
(function () {
    const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---- 1. Числа в карточках «набегают» от нуля
    function countUp(el) {
        if (reduce || el.dataset.cu) return;
        const m = /^(\D*)(\d+(?:[.,]\d+)?)(.*)$/.exec((el.textContent || '').trim());
        if (!m) return;
        el.dataset.cu = '1';
        const end = parseFloat(m[2].replace(',', '.'));
        const dec = (m[2].split(/[.,]/)[1] || '').length;
        const t0 = performance.now(), dur = 750;
        (function tick(now) {
            const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);          // easeOutCubic
            el.textContent = m[1] + (end * e).toFixed(dec) + m[3];
            if (k < 1) requestAnimationFrame(tick);
        })(t0);
    }
    const sel = '.risk-kpi-value';
    new MutationObserver(list => {
        list.forEach(r => r.addedNodes.forEach(n => {
            if (n.nodeType !== 1) return;
            if (n.matches && n.matches(sel)) countUp(n);
            n.querySelectorAll && n.querySelectorAll(sel).forEach(countUp);
        }));
    }).observe(document.body, { childList: true, subtree: true });

    // ---- 2. Уведомления вместо alert() (confirm/prompt остаются обычными окнами)
    let box;
    function toast(text) {
        if (!box) { box = document.createElement('div'); box.id = 'toast-box'; document.body.appendChild(box); }
        const t = document.createElement('div');
        const bad = /ошиб|не удалось|недопуст|неверн|нет прав|слишком/i.test(text);
        t.className = 'toast ' + (bad ? 'error' : (/сохранен|изменён|готово|успешно/i.test(text) ? 'ok' : ''));
        t.textContent = text;
        box.appendChild(t);
        const ms = Math.min(9000, 3000 + text.length * 45);
        setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, ms);
        t.onclick = () => t.remove();
    }
    window.alert = msg => toast(String(msg ?? ''));
})();
