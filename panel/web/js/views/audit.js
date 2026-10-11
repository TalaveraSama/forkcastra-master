/**
 * views/audit.js — auditoría del panel.
 *
 * Consume:
 *   GET /api/logs/audit?limit=N
 *
 * Cada entrada la escribe el backend en `audit.jsonl` con los campos
 * `t, iso, user, ip, action, target, result, detail`.
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

const REFRESH_MS = 10000;

export function render(container, ctx) {
  const { api } = ctx;
  const filters = { user: '', action: '', result: '', limit: 200 };
  let entries = [];

  ctx.setTitle(t('audit.title'), t('settings.section_security'));

  const tableHolder = ui.div('card__body card__body--flush');

  const userInput = ui.input({ type: 'search', placeholder: t('audit.filter_user'), 'aria-label': t('audit.filter_user') });
  const actionInput = ui.input({ type: 'search', placeholder: t('audit.filter_action'), 'aria-label': t('audit.filter_action') });
  const resultSelect = ui.select([
    { value: '', label: t('common.all') },
    { value: 'ok', label: t('audit.result_ok') },
    { value: 'fail', label: t('audit.result_fail') },
    { value: 'blocked', label: t('audit.result_blocked') },
    { value: 'degraded', label: t('audit.result_degraded') }
  ], { 'aria-label': t('audit.filter_result') });
  const limitSelect = ui.select([100, 200, 500, 1000, 2000].map((value) => ({
    value: String(value), label: String(value), selected: value === filters.limit
  })), { 'aria-label': t('audit.limit') });

  [userInput, actionInput].forEach((control) => control.addEventListener('input', () => {
    filters.user = userInput.value.trim().toLowerCase();
    filters.action = actionInput.value.trim().toLowerCase();
    draw();
  }));
  resultSelect.addEventListener('change', () => {
    filters.result = resultSelect.value;
    draw();
  });
  limitSelect.addEventListener('change', () => {
    filters.limit = fmt.toInt(limitSelect.value, 200);
    load();
  });

  const toolbar = ui.div('toolbar', [
    ui.labeled(t('audit.filter_user'), userInput),
    ui.labeled(t('audit.filter_action'), actionInput),
    ui.labeled(t('audit.filter_result'), resultSelect),
    ui.labeled(t('audit.limit'), limitSelect),
    ui.div('grow')
  ]);

  container.appendChild(ui.card({
    title: t('audit.title'),
    flush: true,
    content: [toolbar, tableHolder],
    tools: [ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() })]
  }));

  function resultBadge(result) {
    const value = String(result || '').toLowerCase();
    const variants = { ok: 'ok', fail: 'danger', blocked: 'warn', degraded: 'info' };
    const labels = {
      ok: t('audit.result_ok'),
      fail: t('audit.result_fail'),
      blocked: t('audit.result_blocked'),
      degraded: t('audit.result_degraded')
    };
    return ui.badge(labels[value] || value || '—', variants[value] || '');
  }

  function draw() {
    const rows = entries.filter((entry) => {
      if (filters.user && !String(entry.user || '').toLowerCase().includes(filters.user)) return false;
      if (filters.action && !String(entry.action || '').toLowerCase().includes(filters.action)) return false;
      if (filters.result && String(entry.result || '') !== filters.result) return false;
      return true;
    });

    ui.clear(tableHolder);
    if (!entries.length) {
      tableHolder.appendChild(ui.emptyState(t('audit.empty')));
      return;
    }
    if (!rows.length) {
      tableHolder.appendChild(ui.emptyState(t('channels.no_match')));
      return;
    }

    tableHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row, index) => `${row.t}-${index}`,
      rows,
      columns: [
        {
          label: t('audit.col_time'),
          render: (row) => ui.el('span', { class: 'nowrap', text: row.iso || fmt.formatDate(row.t) })
        },
        { label: t('audit.col_user'), render: (row) => row.user || '—' },
        { label: t('audit.col_ip'), render: (row) => ui.el('span', { class: 'mono small', text: row.ip || '—' }) },
        { label: t('audit.col_action'), render: (row) => ui.el('span', { class: 'mono small', text: row.action || '—' }) },
        { label: t('audit.col_target'), render: (row) => ui.el('span', { class: 'truncate', text: row.target || '—' }) },
        { label: t('audit.col_result'), render: (row) => resultBadge(row.result) },
        {
          label: t('audit.col_detail'),
          render: (row) => ui.el('span', { class: 'small muted break', text: row.detail || '' })
        }
      ]
    }));
  }

  async function load() {
    try {
      const data = await api.get(`/api/logs/audit?limit=${filters.limit}`);
      entries = data.entries || [];
      draw();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  const poll = setInterval(() => {
    if (!document.hidden) load();
  }, REFRESH_MS);

  load();

  return {
    destroy() {
      clearInterval(poll);
    }
  };
}
