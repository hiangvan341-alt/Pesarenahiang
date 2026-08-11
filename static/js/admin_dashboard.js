(function () {
    const buttons = Array.from(document.querySelectorAll('[data-admin-tab]'));
    const panels = Array.from(document.querySelectorAll('[data-admin-panel]'));
    const allowedTabs = new Set(buttons.map(function (button) { return button.dataset.adminTab; }));

    function activateAdminTab(tabName) {
        const selected = allowedTabs.has(tabName) ? tabName : 'overview';
        buttons.forEach(function (button) {
            const active = button.dataset.adminTab === selected;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        panels.forEach(function (panel) {
            panel.hidden = panel.dataset.adminPanel !== selected;
        });
    }

    buttons.forEach(function (button) {
        button.addEventListener('click', function () {
            const tabName = button.dataset.adminTab;
            window.location.hash = tabName;
            activateAdminTab(tabName);
        });
    });

    window.addEventListener('hashchange', function () {
        activateAdminTab(window.location.hash.replace('#', ''));
    });
    activateAdminTab(window.location.hash.replace('#', ''));

    const searchInput = document.getElementById('adminUserSearch');
    const duplicateOnly = document.getElementById('adminDuplicateOnly');
    const userRows = Array.from(document.querySelectorAll('[data-user-summary]'));
    const emptyState = document.getElementById('adminUserEmpty');

    function applyUserFilters() {
        const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
        const onlyDuplicate = duplicateOnly ? duplicateOnly.checked : false;
        let visible = 0;

        userRows.forEach(function (row) {
            const matchesQuery = !query || (row.dataset.userSearch || '').includes(query);
            const matchesDuplicate = !onlyDuplicate || row.dataset.duplicateIp === '1';
            const shouldShow = matchesQuery && matchesDuplicate;
            const button = row.querySelector('[data-user-toggle]');
            const detail = button ? document.getElementById(button.dataset.userToggle) : null;

            row.hidden = !shouldShow;
            if (!shouldShow && detail) {
                detail.hidden = true;
                button.setAttribute('aria-expanded', 'false');
                button.textContent = 'Quản lý';
            }
            if (shouldShow) visible += 1;
        });

        if (emptyState) emptyState.hidden = visible !== 0;
    }

    if (searchInput) searchInput.addEventListener('input', applyUserFilters);
    if (duplicateOnly) duplicateOnly.addEventListener('change', applyUserFilters);

    document.querySelectorAll('[data-user-toggle]').forEach(function (button) {
        button.addEventListener('click', function () {
            const detail = document.getElementById(button.dataset.userToggle);
            if (!detail) return;
            const opening = detail.hidden;
            detail.hidden = !opening;
            button.setAttribute('aria-expanded', opening ? 'true' : 'false');
            button.textContent = opening ? 'Đóng' : 'Quản lý';
        });
    });

    const showPasswords = document.getElementById('showAdminPasswords');
    if (showPasswords) {
        showPasswords.addEventListener('change', function () {
            document.querySelectorAll('.admin-new-password').forEach(function (input) {
                input.type = showPasswords.checked ? 'text' : 'password';
            });
        });
    }

    document.querySelectorAll('.temporary-password-input').forEach(function (input) {
        input.addEventListener('focus', function () { input.type = 'text'; });
        input.addEventListener('blur', function () { input.type = 'password'; });
    });

    document.querySelectorAll('.admin-permission-form').forEach(function (form) {
        form.addEventListener('submit', function () {
            const button = form.querySelector('.admin-save-permissions');
            if (!button || button.disabled) return;
            button.disabled = true;
            button.classList.add('is-saving');
            const label = button.querySelector('.admin-save-label');
            if (label) label.textContent = 'Đang lưu...';
        });
    });
})();

(function () {
    function loadLazyModule(tabName) {
        const panel = document.querySelector('[data-admin-panel="' + tabName + '"]');
        if (!panel) return;
        panel.querySelectorAll('iframe[data-admin-lazy-src]').forEach(function (frame) {
            if (frame.src) return;
            frame.src = frame.dataset.adminLazySrc;
        });
    }
    document.querySelectorAll('[data-admin-tab]').forEach(function (button) {
        button.addEventListener('click', function () { loadLazyModule(button.dataset.adminTab); });
    });
    loadLazyModule(window.location.hash.replace('#', ''));
})();
