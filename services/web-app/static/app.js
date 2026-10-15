/* Progressive enhancements: native dialogs, accessible navigation and safe form feedback. */
(() => {
  const sidebar = document.querySelector('#sidebar');
  const toggle = document.querySelector('[data-nav-toggle]');
  const scrim = document.querySelector('[data-nav-close]');
  const mobile = window.matchMedia('(max-width: 960px)');
  function setNavigation(open) {
    if (!sidebar || !toggle || !scrim) return;
    sidebar.classList.toggle('is-open', open);
    sidebar.inert = mobile.matches && !open;
    const shell = document.querySelector('.app-shell');
    if (shell) shell.inert = mobile.matches && open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    scrim.hidden = !open;
    document.body.style.overflow = open ? 'hidden' : '';
    if (open) sidebar.querySelector('a')?.focus();
  }
  toggle?.addEventListener('click', () => setNavigation(!sidebar.classList.contains('is-open')));
  scrim?.addEventListener('click', () => { setNavigation(false); toggle?.focus(); });
  document.querySelector('[data-sidebar-close]')?.addEventListener('click', () => { setNavigation(false); toggle?.focus(); });
  mobile.addEventListener('change', () => setNavigation(false));
  setNavigation(false);
  sidebar?.addEventListener('click', event => {
    if (mobile.matches && event.target.closest('a')) setNavigation(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar?.classList.contains('is-open')) {
      setNavigation(false); toggle?.focus();
    }
    if (event.key === 'Tab' && mobile.matches && sidebar?.classList.contains('is-open')) {
      const items = [...sidebar.querySelectorAll('a, button')];
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.querySelectorAll('[data-open-dialog]').forEach(button => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && !dialog.open) dialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog')?.close()));
  document.querySelectorAll('dialog[data-auto-open]').forEach(dialog => dialog.showModal());
  document.querySelectorAll('[data-dismiss-message]').forEach(button => button.addEventListener('click', () => button.closest('.message').remove()));
  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.passwordToggle);
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.textContent = visible ? 'Hide' : 'Show';
      button.setAttribute('aria-pressed', String(visible));
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
    });
  });
  const confirmDialog = document.querySelector('#confirm-dialog');
  let pendingForm = null;
  confirmDialog?.querySelector('[data-confirm-cancel]').addEventListener('click', () => confirmDialog.close());
  confirmDialog?.querySelector('[data-confirm-accept]').addEventListener('click', () => {
    const form = pendingForm;
    confirmDialog.close();
    if (form) { form.dataset.confirmed = 'true'; form.requestSubmit(); }
  });
  confirmDialog?.addEventListener('close', () => { pendingForm = null; });
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', event => {
      if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
      if (form.dataset.confirm && form.dataset.confirmed !== 'true' && confirmDialog) {
        event.preventDefault();
        pendingForm = form;
        confirmDialog.querySelector('[data-confirm-description]').textContent = form.dataset.confirm;
        confirmDialog.showModal();
        confirmDialog.querySelector('[data-confirm-cancel]').focus();
        return;
      }
      if (form.method.toLowerCase() === 'post') {
        form.dataset.submitting = 'true';
        form.setAttribute('aria-busy', 'true');
        window.requestAnimationFrame(() => form.querySelectorAll('button[type="submit"], button:not([type])').forEach(button => {
          button.disabled = true; button.dataset.originalText = button.textContent; button.textContent = 'Saving…';
        }));
      }
    });
  });
  window.addEventListener('pageshow', () => document.querySelectorAll('form[data-submitting]').forEach(form => {
    delete form.dataset.submitting; delete form.dataset.confirmed; form.removeAttribute('aria-busy');
    form.querySelectorAll('button[data-original-text]').forEach(button => { button.disabled = false; button.textContent = button.dataset.originalText; });
  }));
})();
