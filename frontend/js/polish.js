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

    // ---- 3. «Глазик» у полей пароля: показать / скрыть набранное (и в окнах, которые появляются позже)
    const EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
    const EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
    function addEye(input) {
        if (input.dataset.eye) return;
        input.dataset.eye = '1';
        const wrap = document.createElement('span');
        wrap.className = 'pw-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'pw-eye';
        b.tabIndex = -1;
        b.title = 'Показать пароль';
        b.innerHTML = EYE;
        b.onclick = () => {
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            b.innerHTML = show ? EYE_OFF : EYE;
            b.title = show ? 'Скрыть пароль' : 'Показать пароль';
            input.focus();
        };
        wrap.appendChild(b);
    }
    const PW = 'input[type="password"]';
    document.querySelectorAll(PW).forEach(addEye);
    new MutationObserver(list => {
        list.forEach(r => r.addedNodes.forEach(n => {
            if (n.nodeType !== 1) return;
            if (n.matches && n.matches(PW)) addEye(n);
            n.querySelectorAll && n.querySelectorAll(PW).forEach(addEye);
        }));
    }).observe(document.body, { childList: true, subtree: true });
    // после входа/выхода поле снова скрывает пароль
    document.addEventListener('submit', () => document.querySelectorAll('.pw-wrap input').forEach(i => {
        i.type = 'password';
        const b = i.parentNode.querySelector('.pw-eye');
        if (b) { b.innerHTML = EYE; b.title = 'Показать пароль'; }
    }), true);
})();
