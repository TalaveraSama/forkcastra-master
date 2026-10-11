/**
 * views/users.js — gestión de usuarios del panel y de sus tokens de streaming.
 *
 * Consume:
 *   GET    /api/users                     (lista + sesiones activas)
 *   GET    /api/users/{id}
 *   POST   /api/users                     ({…, password})
 *   PUT    /api/users/{id}
 *   DELETE /api/users/{id}
 *   POST   /api/users/{id}/token/rotate
 *   POST   /api/users/{id}/password       ({password})
 *   GET    /api/channels                  (para la lista de canales permitidos)
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

export function render(container, ctx) {
  const { api, state } = ctx;

  ctx.setTitle(t('users.title'), t('settings.section_security'));

  const tableHolder = ui.div('card__body card__body--flush');
  const sessionsBadge = ui.badge('—', '');
  const filters = { q: '' };

  const searchInput = ui.input({
    type: 'search',
    placeholder: t('channels.search_ph'),
    'aria-label': t('common.search')
  });
  searchInput.addEventListener('input', () => {
    filters.q = searchInput.value.trim().toLowerCase();
    draw();
  });

  const toolbar = ui.div('toolbar', [
    ui.labeled(t('common.search'), searchInput, 'grow'),
    ui.div('grow'),
    ui.button(t('users.new'), { variant: 'primary', icon: 'plus', onClick: () => openEditor(ctx, null) })
  ]);

  container.appendChild(ui.card({
    title: t('users.title'),
    flush: true,
    content: [toolbar, tableHolder],
    tools: [
      sessionsBadge,
      ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() })
    ]
  }));

  let users = [];
  let sessionCount = 0;

  function draw() {
    const rows = users.filter((user) => {
      if (!filters.q) return true;
      return [user.username, user.display_name, user.role, user.id]
        .join(' ').toLowerCase().includes(filters.q);
    });

    ui.clear(tableHolder);
    ui.clear(sessionsBadge);
    sessionsBadge.textContent = t('users.sessions', { n: fmt.formatNumber(sessionCount) });

    if (!users.length) {
      tableHolder.appendChild(ui.emptyState(t('users.empty')));
      return;
    }
    if (!rows.length) {
      tableHolder.appendChild(ui.emptyState(t('channels.no_match')));
      return;
    }

    tableHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row) => row.id,
      rowClass: (row) => (row.enabled ? '' : 'is-off'),
      rows,
      columns: [
        {
          label: t('users.col_user'),
          render: (row) => ui.div('chan-meta', [
            ui.el('span', { class: 'chan-meta__name', text: row.display_name || row.username }),
            ui.el('span', { class: 'small muted', text: `${row.username} · ${row.id}` })
          ])
        },
        {
          label: t('users.col_role'),
          render: (row) => ui.badge(t(`users.role_${row.role}`), row.role === 'admin' ? 'brand' : 'info')
        },
        {
          label: t('users.col_state'),
          render: (row) => ui.div('col', [
            ui.stateBadge(row.enabled !== false, t('common.enabled'), t('common.disabled')),
            row.failed_logins ? ui.badge(`${t('audit.result_fail')}: ${row.failed_logins}`, 'warn') : null
          ])
        },
        { label: t('users.col_expires'), render: (row) => expiryText(row) },
        {
          label: t('users.col_connections'),
          className: 'num',
          render: (row) => (row.max_connections ? t('users.connections_n', { n: row.max_connections }) : t('users.connections_unlimited'))
        },
        {
          label: t('users.col_last_login'),
          render: (row) => (row.last_login ? fmt.formatDate(row.last_login) : t('common.never'))
        },
        {
          label: t('users.col_token'),
          render: (row) => ui.el('span', {
            class: 'mono small truncate',
            title: row.stream_token || '',
            text: row.stream_token ? `${String(row.stream_token).slice(0, 10)}…` : '—'
          })
        },
        {
          label: t('common.actions'),
          className: 'actions',
          render: (row) => ui.div('row row--tight row--end', [
            ui.iconButton(`${t('common.edit')} ${row.username}`, { symbol: 'edit', onClick: () => openEditor(ctx, row) }),
            ui.iconButton(t('users.rotate'), { symbol: 'key', onClick: () => rotate(ctx, row) }),
            ui.iconButton(t('users.reset_password'), {
              symbol: 'shield',
              onClick: () => openPasswordDialog(ctx, row)
            }),
            ui.iconButton(t('users.copy_m3u'), {
              symbol: 'copy',
              onClick: () => ui.copyText(row.m3u_url, t('common.copied'))
            }),
            ui.iconButton(`${t('common.delete')} ${row.username}`, {
              symbol: 'trash',
              onClick: () => remove(ctx, row)
            })
          ])
        }
      ]
    }));
  }

  function expiryText(row) {
    const expires = fmt.num(row.expires_at, 0);
    if (!expires) return ui.el('span', { class: 'muted', text: t('users.no_expiry') });
    const now = Math.floor(Date.now() / 1000);
    return ui.el('span', {
      class: expires < now ? 'badge badge--danger' : '',
      text: expires < now ? t('users.expired') : t('users.expires_on', { date: fmt.formatDate(expires, { dateOnly: true }) })
    });
  }

  async function load() {
    try {
      const data = await api.get('/api/users');
      users = data.users || [];
      sessionCount = data.sessions || 0;
      draw();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  // La lista de canales permitidos necesita el catálogo de canales.
  async function loadChannels() {
    try {
      const data = await api.get('/api/channels');
      state.channels = data.channels || [];
    } catch (error) {
      // Sin catálogo el editor sigue funcionando (sólo pierde la lista).
    }
  }

  async function rotate(context, row) {
    const yes = await ui.confirmDialog({
      title: t('users.rotate'),
      message: t('users.rotate_confirm', { name: row.username }),
      acceptLabel: t('users.rotate'),
      danger: true
    });
    if (!yes) return;
    try {
      await context.api.post(`/api/users/${encodeURIComponent(row.id)}/token/rotate`);
      ui.notify.ok(t('users.rotate_done'));
      await load();
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  async function remove(context, row) {
    const yes = await ui.confirmDialog({
      title: t('common.delete'),
      message: t('users.delete_confirm', { name: row.username }),
      acceptLabel: t('common.delete'),
      danger: true
    });
    if (!yes) return;
    try {
      await context.api.delete(`/api/users/${encodeURIComponent(row.id)}`);
      ui.notify.ok(t('users.deleted'));
      await load();
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  load();
  loadChannels();

  return { destroy() {} };
}

/* ==========================================================================
   Alta y edición de usuario
   ========================================================================== */

function openEditor(ctx, user) {
  const isNew = !user;
  const model = user
    ? JSON.parse(JSON.stringify(user))
    : {
      username: '', display_name: '', role: 'viewer', enabled: true,
      all_channels: true, allowed_channels: [], max_connections: 1, expires_at: 0
    };

  const username = ui.input({
    value: model.username || '',
    readOnly: !isNew,
    spellcheck: 'false',
    on: { input: (event) => { model.username = event.target.value.trim().toLowerCase(); } }
  });
  const display = ui.input({
    value: model.display_name || '',
    on: { input: (event) => { model.display_name = event.target.value; } }
  });
  const role = ui.select((ctx.state.enums && ctx.state.enums.roles ? ctx.state.enums.roles : ['admin', 'operator', 'viewer'])
    .map((value) => ({ value, label: t(`users.role_${value}`), selected: value === model.role })));
  role.addEventListener('change', () => { model.role = role.value; });

  const enabled = ui.checkbox(t('users.field_enabled'), model.enabled !== false, {
    on: { change: (event) => { model.enabled = event.target.checked; } }
  });

  const allChannels = ui.checkbox(t('users.field_all_channels'), model.all_channels !== false, {
    on: {
      change: (event) => {
        model.all_channels = event.target.checked;
        channelListWrapper.hidden = !!model.all_channels;
      }
    }
  });

  const channelList = ui.div('repeat');
  const channelListWrapper = ui.field(t('users.field_channels'), channelList, t('users.field_channels_hint'));
  channelListWrapper.hidden = model.all_channels !== false;

  const channels = (ctx.state.channels || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
  if (!channels.length) {
    channelList.appendChild(ui.el('span', { class: 'muted small', text: t('channels.empty') }));
  }
  channels.forEach((channel) => {
    const selected = (model.allowed_channels || []).includes(channel.id);
    const box = ui.checkbox(`${channel.name || channel.id}`, selected);
    box.input.addEventListener('change', () => {
      const set = new Set(model.allowed_channels || []);
      if (box.input.checked) set.add(channel.id);
      else set.delete(channel.id);
      model.allowed_channels = Array.from(set);
    });
    channelList.appendChild(box);
  });

  const maxConn = ui.input({
    type: 'number', min: 0, max: 100,
    value: String(model.max_connections === undefined ? 1 : model.max_connections),
    on: {
      input: (event) => { model.max_connections = fmt.toInt(event.target.value, 0); }
    }
  });

  const now = Math.floor(Date.now() / 1000);
  const currentDays = model.expires_at && model.expires_at > now
    ? Math.max(1, Math.round((model.expires_at - now) / 86400))
    : 0;
  const expiresDays = ui.input({
    type: 'number', min: 0, max: 3650, value: String(currentDays),
    on: {
      input: (event) => {
        model.expires_enabled = true;
        model.expires_days = fmt.toInt(event.target.value, 0);
      }
    }
  });

  const password = ui.input({
    type: 'password',
    autocomplete: 'new-password',
    placeholder: isNew ? '' : '••••••••',
    on: { input: (event) => { model.password = event.target.value; } }
  });

  const content = [
    ui.div('grid-2', [
      ui.field(t('users.field_username'), username, isNew ? t('users.field_username_hint') : null),
      ui.field(t('users.field_display'), display)
    ]),
    ui.div('grid-3', [
      ui.field(t('users.field_role'), role),
      ui.field(t('users.field_max_conn'), maxConn, t('users.field_max_conn_hint')),
      ui.field(t('users.field_expires'), expiresDays, t('users.field_expires_days_hint'))
    ]),
    ui.div('row', [enabled, allChannels]),
    channelListWrapper,
    ui.field(
      t('users.field_password'),
      password,
      isNew ? t('users.field_password_hint') : t('users.field_password_keep')
    )
  ];

  if (!isNew) {
    content.push(ui.el('fieldset', { class: 'group' }, [
      ui.el('legend', { text: t('users.token') }),
      ui.copyField(t('users.m3u_url'), model.m3u_url || ''),
      ui.copyField(t('users.stream_url'), model.stream_url_template || '')
    ]));
  }

  const form = ui.formModal({
    title: isNew ? t('users.new_title') : t('users.edit_title', { name: model.username }),
    size: 'md',
    content,
    submitLabel: isNew ? t('common.create') : t('common.save'),
    onSubmit: async (handle) => {
      ui.busyButton(form.submit, true);
      try {
        const payload = {
          username: model.username,
          display_name: model.display_name,
          role: model.role,
          enabled: model.enabled !== false,
          all_channels: model.all_channels !== false,
          allowed_channels: model.allowed_channels || [],
          max_connections: fmt.toInt(model.max_connections, 0),
          expires_enabled: true,
          expires_days: fmt.toInt(model.expires_days, currentDays)
        };
        if (model.password) payload.password = model.password;
        if (isNew) {
          await ctx.api.post('/api/users', payload);
          ui.notify.ok(t('users.created'));
        } else {
          await ctx.api.put(`/api/users/${encodeURIComponent(model.id)}`, payload);
          ui.notify.ok(t('users.saved'));
        }
        handle.close();
        window.dispatchEvent(new Event('hashchange'));
      } catch (error) {
        ui.showApiError(error, t('common.server_error'));
      } finally {
        ui.busyButton(form.submit, false);
      }
    }
  });
}

/* ==========================================================================
   Restablecer contraseña
   ========================================================================== */

function openPasswordDialog(ctx, user) {
  const password = ui.input({ type: 'password', autocomplete: 'new-password' });
  const form = ui.formModal({
    title: t('users.reset_title', { name: user.username }),
    size: 'sm',
    content: [ui.field(t('settings.password_new'), password, t('users.field_password_hint'))],
    submitLabel: t('users.reset_password'),
    onSubmit: async (handle) => {
      ui.busyButton(form.submit, true);
      try {
        await ctx.api.post(`/api/users/${encodeURIComponent(user.id)}/password`, { password: password.value });
        ui.notify.ok(t('users.reset_done'));
        handle.close();
      } catch (error) {
        ui.showApiError(error, t('common.server_error'));
      } finally {
        ui.busyButton(form.submit, false);
      }
    }
  });
}
