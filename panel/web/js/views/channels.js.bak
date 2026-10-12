/**
 * views/channels.js — lista + editor completo de canales.
 *
 * Consume:
 *   GET    /api/channels                      (lista con estado en vivo)
 *   GET    /api/channels/{id}                 (canal + estado)
 *   POST   /api/channels                      (crear)
 *   PUT    /api/channels/{id}                 (editar)
 *   DELETE /api/channels/{id}                 (borrar)
 *   POST   /api/channels/{id}/action          (start | stop | restart)
 *   POST   /api/channels/{id}/duplicate
 *   POST   /api/channels/reorder              ({ids:[…]})
 *   POST   /api/channels/import               ({text, format:"m3u", group})
 *   GET    /api/channels/{id}/psi             (tablas PSI del agente)
 *   GET    /api/channels/export.m3u8          (descarga M3U)
 *   GET    /api/enums                         (listas de validators.py)
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

const REFRESH_MS = 5000;

export function render(container, ctx, params) {
  const id = params && params[0];
  if (id) return renderEditor(container, ctx, decodeURIComponent(id));
  return renderList(container, ctx);
}

/* ==========================================================================
   Lista de canales
   ========================================================================== */

function renderList(container, ctx) {
  const { api, state, subscribe } = ctx;
  const disposes = [];
  const filters = { q: '', group: '' };

  ctx.setTitle(t('channels.title'), t('common.channels'));

  const tableHolder = ui.div('card__body card__body--flush');
  const countBadge = ui.badge('0', '');

  const searchInput = ui.input({ type: 'search', placeholder: t('channels.search_ph'), 'aria-label': t('common.search') });
  searchInput.addEventListener('input', () => {
    filters.q = searchInput.value.trim().toLowerCase();
    renderTable();
  });

  const groupSelect = ui.select([{ value: '', label: t('common.all') }], { 'aria-label': t('channels.filter_group') });
  groupSelect.addEventListener('change', () => {
    filters.group = groupSelect.value;
    renderTable();
  });

  const toolbar = ui.div('toolbar', [
    ui.labeled(t('common.search'), searchInput, 'grow'),
    ui.labeled(t('channels.filter_group'), groupSelect),
    ui.div('grow'),
    ui.button(t('channels.import'), { icon: 'upload', onClick: () => openImportDialog(ctx) }),
    ui.button(t('channels.export'), { icon: 'download', onClick: () => exportM3u(ctx) }),
    ui.button(t('channels.new'), {
      variant: 'primary',
      icon: 'plus',
      onClick: () => { window.location.hash = '#/channels/new'; }
    })
  ]);

  const card = ui.card({
    title: t('channels.title'),
    flush: true,
    content: [toolbar, tableHolder],
    tools: [
      countBadge,
      ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() })
    ]
  });
  container.appendChild(card);

  function groups() {
    const set = new Set();
    (state.channels || []).forEach((channel) => {
      if (channel.group) set.add(channel.group);
    });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }

  function refreshGroupFilter() {
    const current = filters.group;
    const options = [{ value: '', label: t('common.all') }].concat(
      groups().map((group) => ({ value: group, label: group }))
    );
    ui.clear(groupSelect);
    options.forEach((item) => {
      groupSelect.appendChild(ui.el('option', { value: item.value, text: item.label, selected: item.value === current }));
    });
    groupSelect.value = current;
  }

  function visibleChannels() {
    return (state.channels || []).filter((channel) => {
      if (filters.group && channel.group !== filters.group) return false;
      if (!filters.q) return true;
      const haystack = [
        channel.name, channel.group, channel.id, channel.epg_id,
        (channel.tags || []).join(' ')
      ].join(' ').toLowerCase();
      return haystack.includes(filters.q);
    });
  }

  function renderTable() {
    refreshGroupFilter();
    const rows = visibleChannels();
    const running = (state.channels || []).filter((channel) => channel.on_air).length;
    ui.clear(countBadge);
    countBadge.textContent = `${fmt.formatNumber(running)}/${fmt.formatNumber((state.channels || []).length)}`;
    countBadge.title = t('dashboard.channels_running');

    ui.clear(tableHolder);
    if (!state.channels || !state.channels.length) {
      tableHolder.appendChild(ui.emptyState(t('channels.empty')));
      return;
    }
    if (!rows.length) {
      tableHolder.appendChild(ui.emptyState(t('channels.no_match')));
      return;
    }

    const all = state.channels || [];
    tableHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row) => row.id,
      rowClass: (row) => (row.enabled ? '' : 'is-off'),
      rows,
      columns: [
        {
          label: t('channels.col_channel'),
          render: (row) => ui.div('chan-name', [channelLogo(row), ui.div('chan-meta', [
            ui.el('a', { href: `#/channels/${encodeURIComponent(row.id)}`, text: row.name || row.id }),
            ui.el('span', {
              class: 'small muted',
              text: [row.group || '', row.id, (row.tags || []).join(' · ')].filter(Boolean).join(' · ')
            })
          ])])
        },
        {
          label: t('channels.col_state'),
          render: (row) => ui.div('col', [
            ui.badge(
              row.on_air ? t('dashboard.on_air') : (row.running ? t('channels.state_running') : t('channels.state_stopped')),
              row.on_air ? 'ok' : (row.running ? 'info' : 'off'),
              { dot: row.on_air ? 'ok' : 'off' }
            ),
            row.enabled ? null : ui.badge(t('common.disabled'), 'off')
          ])
        },
        { label: t('channels.col_bitrate'), className: 'num', render: (row) => fmt.formatBitrate(row.bitrate || 0) },
        { label: t('channels.col_viewers'), className: 'num', render: (row) => fmt.formatNumber(row.viewers || 0) },
        {
          label: t('channels.col_inputs'),
          render: (row) => ioSummary(row)
        },
        {
          label: t('channels.col_errors'),
          className: 'num small',
          render: (row) => errorSummary(row)
        },
        {
          label: t('channels.order'),
          className: 'nowrap',
          render: (row) => ui.div('row row--tight', [
            ui.iconButton(t('common.up'), {
              symbol: 'up',
              onClick: () => move(row, -1)
            }),
            ui.iconButton(t('common.down'), {
              symbol: 'down',
              onClick: () => move(row, 1)
            })
          ])
        },
        {
          label: t('common.actions'),
          className: 'actions',
          render: (row) => ui.div('row row--tight row--end', [
            ui.button(row.enabled ? t('channels.stop') : t('channels.start'), {
              size: 'xs',
              icon: row.enabled ? 'stop' : 'play',
              variant: row.enabled ? 'danger' : 'ok',
              onClick: () => action(row, row.enabled ? 'stop' : 'start')
            }),
            ui.button(t('channels.restart'), {
              size: 'xs',
              icon: 'restart',
              onClick: () => action(row, 'restart')
            }),
            ui.button(t('channels.psi'), {
              size: 'xs',
              icon: 'eye',
              onClick: () => openPsiDialog(ctx, row)
            }),
            ui.button(t('channels.duplicate'), {
              size: 'xs',
              icon: 'copy',
              onClick: () => duplicate(row)
            }),
            ui.iconButton(`${t('common.delete')} ${row.name || row.id}`, {
              symbol: 'trash',
              onClick: () => remove(row)
            })
          ])
        }
      ],
      empty: t('channels.empty')
    }));
  }

  function channelLogo(row) {
    if (row.logo && fmt.isSafeImageUrl(row.logo)) {
      const img = ui.el('img', { class: 'chan-logo', src: row.logo, alt: '', loading: 'lazy' });
      // La CSP del panel permite `img-src 'self' data: https:`: los logos por
      // http:// se bloquean y se sustituyen por el marcador de posición.
      const fallback = () => {
        if (img.parentNode) img.replaceWith(placeholderLogo(row));
      };
      img.addEventListener('error', fallback);
      return img;
    }
    return placeholderLogo(row);
  }

  function placeholderLogo(row) {
    const letter = String(row.name || row.id || '?').trim().charAt(0).toUpperCase() || '?';
    return ui.el('span', { class: 'chan-logo chan-logo--ph', 'aria-hidden': 'true', text: letter });
  }

  function ioSummary(row) {
    const inputs = row.inputs || [];
    const outputs = row.outputs_config || row.outputs || [];
    const list = ui.div('io-list');
    inputs.slice(0, 3).forEach((item, index) => {
      const config = (row.inputs_config || [])[index] || item;
      const live = inputs[index] || {};
      list.appendChild(ui.div('io-item', [
        ui.badge(fmt.inputTypeLabel(config.type || item.type), live.on_air ? 'ok' : 'off'),
        ui.el('span', { class: 'truncate', text: fmt.inputSummary(config), title: fmt.inputSummary(config) }),
        live.error ? ui.badge(live.error, 'danger') : null
      ]));
    });
    if (inputs.length > 3) list.appendChild(ui.el('span', { class: 'muted small', text: `+${inputs.length - 3}` }));
    outputs.slice(0, 2).forEach((output) => {
      list.appendChild(ui.div('io-item', [
        ui.badge('out', 'info'),
        ui.el('span', { class: 'truncate', text: fmt.outputSummary(output), title: fmt.outputSummary(output) })
      ]));
    });
    if (!inputs.length) list.appendChild(ui.el('span', { class: 'muted small', text: t('channels.no_inputs') }));
    return list;
  }

  function errorSummary(row) {
    const errors = row.errors || {};
    const total = (errors.cc || 0) + (errors.sc || 0) + (errors.pes || 0);
    if (!total) return ui.el('span', { class: 'muted', text: '0' });
    return ui.el('span', {
      class: 'mono',
      title: `CC ${errors.cc || 0} · SC ${errors.sc || 0} · PES ${errors.pes || 0}`,
      text: `${errors.cc || 0}/${errors.sc || 0}/${errors.pes || 0}`
    });
  }

  /* --------------------------------------------------------------- acciones */

  async function load(silent) {
    try {
      const data = await api.get('/api/channels');
      state.channels = data.channels || [];
      state.agent = data.agent || state.agent;
      renderTable();
      ctx.refreshChrome();
    } catch (error) {
      if (error.status === 401) return;
      if (error.status === 502 && !silent) ui.notify.warn(error.message);
      else if (!silent) ui.showApiError(error, t('common.server_error'));
    }
  }

  async function action(row, name) {
    try {
      const data = await api.post(`/api/channels/${encodeURIComponent(row.id)}/action`, { action: name });
      const messages = {
        start: t('channels.started', { name: row.name || row.id }),
        stop: t('channels.stopped', { name: row.name || row.id }),
        restart: t('channels.restarted', { name: row.name || row.id })
      };
      ui.notify.ok(messages[name] || t('common.accept'));
      announceAgentResult(ctx, data);
      await load(true);
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  async function duplicate(row) {
    try {
      const data = await api.post(`/api/channels/${encodeURIComponent(row.id)}/duplicate`);
      ui.notify.ok(t('channels.duplicated', { name: (data.channel || {}).name || '' }));
      announceAgentResult(ctx, data);
      await load(true);
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  async function remove(row) {
    const yes = await ui.confirmDialog({
      title: t('common.delete'),
      message: t('channels.delete_confirm', { name: row.name || row.id }),
      acceptLabel: t('common.delete'),
      danger: true
    });
    if (!yes) return;
    try {
      const data = await api.delete(`/api/channels/${encodeURIComponent(row.id)}`);
      ui.notify.ok(t('channels.deleted'));
      announceAgentResult(ctx, data);
      await load(true);
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  async function move(row, delta) {
    const ids = (state.channels || []).map((channel) => channel.id);
    const index = ids.indexOf(row.id);
    const target = index + delta;
    if (index === -1 || target < 0 || target >= ids.length) return;
    ids.splice(target, 0, ids.splice(index, 1)[0]);
    try {
      await api.post('/api/channels/reorder', { ids });
      ui.notify.ok(t('channels.reordered'));
      await load(true);
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  /* ----------------------------------------------------------- actualización */

  disposes.push(subscribe(['status', 'channels'], (payload) => {
    if (payload && payload.channels) state.channels = payload.channels;
    renderTable();
  }));

  const poll = setInterval(() => {
    if (!document.hidden) load(true);
  }, REFRESH_MS);

  load(true);

  return {
    destroy() {
      disposes.forEach((off) => off());
      clearInterval(poll);
    }
  };
}

/* ==========================================================================
   Diálogo de importación M3U
   ========================================================================== */

function openImportDialog(ctx) {
  const textarea = ui.textarea({ placeholder: '#EXTM3U\n#EXTINF:-1 tvg-id="canal" group-title="Grupo",Canal\nhttp://origen/canal.ts' });
  const group = ui.input({ value: 'Importado' });
  const modal = ui.openModal({
    title: t('channels.import_title'),
    size: 'md',
    content: [
      ui.alert('info', null, t('channels.import_hint')),
      ui.field(t('channels.import_text'), textarea),
      ui.field(t('channels.import_group'), group)
    ],
    footer: []
  });
  const cancel = ui.button(t('common.cancel'), { onClick: () => modal.close() });
  const submit = ui.button(t('channels.import_do'), {
    variant: 'primary',
    icon: 'upload',
    onClick: async () => {
      if (!textarea.value.trim()) return;
      ui.busyButton(submit, true);
      try {
        const data = await ctx.api.post('/api/channels/import', {
          text: textarea.value,
          format: 'm3u',
          group: group.value || 'Importado'
        });
        ui.notify.ok(t('channels.import_result', { n: data.imported || 0 }));
        if (data.errors && data.errors.length) {
          ui.notify.warn(`${t('channels.import_errors', { n: data.errors.length })}: ${data.errors.slice(0, 3).join(' · ')}`);
        }
        announceAgentResult(ctx, data);
        modal.close();
        await reloadRoute();
      } catch (error) {
        ui.showApiError(error, t('common.server_error'));
      } finally {
        ui.busyButton(submit, false);
      }
    }
  });
  modal.modal.querySelector('.modal__foot').appendChild(ui.div('grow'));
  modal.modal.querySelector('.modal__foot').appendChild(cancel);
  modal.modal.querySelector('.modal__foot').appendChild(submit);
}

/** Exporta la lista M3U mediante descarga autenticada. */
async function exportM3u(ctx) {
  try {
    const { blob } = await ctx.api.download('/api/channels/export.m3u8');
    ctx.api.saveBlob(blob, 'canales.m3u');
    ui.notify.ok(t('settings.export_done'));
  } catch (error) {
    ui.showApiError(error, t('common.server_error'));
  }
}

/* ==========================================================================
   Editor de canal
   ========================================================================== */

function renderEditor(container, ctx, channelId) {
  const { api, state } = ctx;
  const isNew = channelId === 'new';
  let model = null;
  let stateInfo = null;
  const enums = state.enums || {};

  const formError = ui.div('form-error', null, { hidden: true });
  const formWarn = ui.div('form-warn', null, { hidden: true });
  const body = ui.div('card__body');

  ctx.setTitle(isNew ? t('channels.new_title') : t('channels.edit_title', { name: channelId }), t('channels.title'));

  const back = ui.button(`← ${t('channels.title')}`, { onClick: () => { window.location.hash = '#/channels'; } });
  const saveButton = ui.button(t('common.save'), { variant: 'primary', icon: 'check', onClick: () => save() });
  const reloadButton = ui.button(t('common.reload'), { icon: 'refresh', onClick: () => load() });
  const psiButton = ui.button(t('channels.psi'), {
    icon: 'eye',
    onClick: () => openPsiDialog(ctx, { id: channelId, name: model ? model.name : channelId })
  });

  // Barra de acciones sobre el canal (arrancar, parar, duplicar, borrar).
  const actionTools = ui.div('row row--tight');
  const actionCard = ui.card({
    title: t('channels.section_state'),
    content: [actionTools]
  });

  const titleCard = ui.card({
    title: isNew ? t('channels.new_title') : t('channels.edit_title', { name: channelId }),
    content: [formError, formWarn, body],
    tools: [psiButton, reloadButton, saveButton, back]
  });
  container.appendChild(titleCard);
  container.appendChild(actionCard);

  /* --------------------------------------------------------------- carga */

  async function load() {
    ui.setBusy(true);
    try {
      if (isNew) {
        model = blankChannel();
      } else {
        const data = await api.get(`/api/channels/${encodeURIComponent(channelId)}`);
        model = data.channel;
        stateInfo = data.state;
        ctx.setTitle(t('channels.edit_title', { name: model.name || channelId }), t('channels.title'));
      }
      renderForm();
      renderActions();
    } catch (error) {
      if (error.status === 404) {
        ui.clear(body);
        body.appendChild(ui.alert('error', t('common.error'), error.message));
      } else if (error.status !== 401) {
        ui.showApiError(error, t('common.server_error'));
      }
    } finally {
      ui.setBusy(false);
    }
  }

  function blankChannel() {
    return {
      id: '',
      name: '',
      group: 'General',
      logo: '',
      epg_id: '',
      enabled: true,
      tags: [],
      notes: '',
      inputs: [blankInput('http')],
      outputs: [blankOutput('http')],
      analyze: { enabled: true }
    };
  }

  function blankInput(type) {
    const base = { type, options: {} };
    if (type === 'http') base.url = 'http://';
    if (type === 'udp') Object.assign(base, { addr: '239.0.0.1', port: 1234, localaddr: '', rtp: false, socket_size: 0, renew: 0 });
    if (type === 'file') Object.assign(base, { filename: '/srv/media/', loop: false });
    if (type === 'dvb') Object.assign(base, {
      delivery: 'S2', adapter: 0, device: 0, frequency: 11500, polarization: 'H', symbolrate: 27500,
      lof1: 9750, lof2: 10600, slof: 11700, diseqc: 0, tone: 0, lnb_sharing: 0, budget: 0,
      buffer_size: 0, stream_id: 0, fec: 'AUTO', modulation: '', bandwidth: 'AUTO',
      guardinterval: 'AUTO', transmitmode: 'AUTO', hierarchy: 'AUTO', rolloff: 'AUTO', cam: false
    });
    return base;
  }

  function blankOutput(type) {
    if (type === 'udp') return { type, addr: '239.0.0.1', port: 1234, ttl: 32, localaddr: '', rtp: false, sync: 0, socket_size: 0 };
    if (type === 'file') return { type, filename: '/var/lib/astra-webui/recordings/canal.ts', m2ts: false, buffer_size: 0 };
    return { type: 'http', uri: '/canal', public: false, cors: true };
  }

  /* -------------------------------------------------------------- formulario */

  function renderForm() {
    const general = ui.card({
      title: t('channels.section_general'),
      content: [
        ui.div('editor-grid', [
          ui.field(t('channels.field_id'), ui.input({
            value: model.id || '',
            readOnly: !isNew,
            spellcheck: 'false',
            on: { input: (event) => { model.id = event.target.value.trim(); } }
          }), isNew ? t('channels.field_id_hint') : null),
          ui.field(t('channels.field_name'), ui.input({
            value: model.name || '',
            required: true,
            on: { input: (event) => { model.name = event.target.value; } }
          })),
          ui.field(t('channels.field_group'), groupField()),
          ui.field(t('channels.field_logo'), ui.input({
            value: model.logo || '',
            placeholder: 'https://…',
            on: { input: (event) => { model.logo = event.target.value.trim(); } }
          })),
          ui.field(t('channels.field_epg'), ui.input({
            value: model.epg_id || '',
            on: { input: (event) => { model.epg_id = event.target.value.trim(); } }
          })),
          ui.field(t('channels.field_tags'), ui.input({
            value: (model.tags || []).join(', '),
            on: { input: (event) => { model.tags = fmt.parseTags(event.target.value); } }
          }), t('channels.field_tags_hint'))
        ]),
        ui.checkbox(t('common.enabled'), model.enabled !== false, {
          on: { change: (event) => { model.enabled = event.target.checked; } }
        }),
        ui.field(t('channels.field_notes'), ui.textarea({
          value: model.notes || '',
          rows: 2,
          on: { input: (event) => { model.notes = event.target.value; } }
        }))
      ]
    });

    const inputsCard = ui.card({
      title: t('channels.section_inputs'),
      content: [inputsHolder],
      tools: [addInputButton()]
    });

    const outputsCard = ui.card({
      title: t('channels.section_outputs'),
      content: [outputsHolder],
      tools: [addOutputButton()]
    });

    const analyzeCard = ui.card({
      title: t('channels.section_analyze'),
      content: [
        ui.checkbox(t('channels.analyze_enabled'), !model.analyze || model.analyze.enabled !== false, {
          on: {
            change: (event) => {
              model.analyze = model.analyze || {};
              model.analyze.enabled = event.target.checked;
            }
          }
        })
      ]
    });

    ui.clear(body);
    body.appendChild(general);
    body.appendChild(inputsCard);
    body.appendChild(outputsCard);
    body.appendChild(analyzeCard);
    if (!isNew && stateInfo) body.appendChild(renderStateCard());

    renderInputs();
    renderOutputs();
    hide(formError);
    hide(formWarn);
  }

  function groupField() {
    const groups = new Set(['General']);
    (state.channels || []).forEach((channel) => { if (channel.group) groups.add(channel.group); });
    const control = ui.input({ value: model.group || 'General', list: 'groups-list' });
    control.addEventListener('input', (event) => { model.group = event.target.value; });
    const list = ui.el('datalist', { id: 'groups-list' });
    Array.from(groups).sort().forEach((group) => list.appendChild(ui.el('option', { value: group })));
    const wrap = ui.div('row row--tight', [control, list]);
    wrap.style.display = 'block';
    return wrap;
  }

  const inputsHolder = ui.div('repeat');
  const outputsHolder = ui.div('repeat');

  function addInputButton() {
    const control = ui.select((enums.input_types || ['http', 'udp', 'file', 'dvb']).map((type) => ({
      value: type,
      label: fmt.inputTypeLabel(type)
    })), { 'aria-label': t('channels.input_add') });
    return ui.div('row row--tight', [
      control,
      ui.button(t('channels.input_add'), {
        size: 'sm',
        variant: 'primary',
        icon: 'plus',
        onClick: () => {
          model.inputs.push(blankInput(control.value));
          renderInputs();
        }
      })
    ]);
  }

  function addOutputButton() {
    const control = ui.select((enums.output_types || ['http', 'udp', 'file']).map((type) => ({
      value: type,
      label: fmt.inputTypeLabel(type)
    })), { 'aria-label': t('channels.output_add') });
    return ui.div('row row--tight', [
      control,
      ui.button(t('channels.output_add'), {
        size: 'sm',
        variant: 'primary',
        icon: 'plus',
        onClick: () => {
          model.outputs.push(blankOutput(control.value));
          renderOutputs();
        }
      })
    ]);
  }

  function renderInputs() {
    ui.clear(inputsHolder);
    if (!model.inputs.length) {
      inputsHolder.appendChild(ui.alert('warn', null, t('channels.no_inputs')));
      return;
    }
    model.inputs.forEach((input, index) => {
      inputsHolder.appendChild(inputCard(input, index));
    });
  }

  function renderOutputs() {
    ui.clear(outputsHolder);
    if (!model.outputs.length) {
      outputsHolder.appendChild(ui.alert('warn', null, t('channels.no_outputs')));
      return;
    }
    model.outputs.forEach((output, index) => {
      outputsHolder.appendChild(outputCard(output, index));
    });
  }

  function cardShell(title, index, onMove, onRemove, content, extraTools) {
    return ui.div('repeat__item', [
      ui.div('repeat__head', [
        ui.el('span', { class: 'repeat__title', text: title }),
        index > 0 ? ui.el('span', { class: 'input-card__prio', text: t('channels.input_priority', { n: index + 1 }) }) : null,
        ui.div('repeat__tools', [
          ...(extraTools || []),
          ui.iconButton(t('common.up'), { symbol: 'up', onClick: () => onMove(-1) }),
          ui.iconButton(t('common.down'), { symbol: 'down', onClick: () => onMove(1) }),
          ui.iconButton(t('common.remove'), { symbol: 'minus', onClick: onRemove })
        ])
      ]),
      content
    ]);
  }

  function inputCard(input, index) {
    const options = input.options || (input.options = {});
    const content = ui.div('col');

    if (input.type === 'http') {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_http_url'), boundText(input, 'url', 'http://origen:8080/canal.ts'), null)
      ]));
    } else if (input.type === 'udp') {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_udp_addr'), boundText(input, 'addr', '239.0.0.1')),
        ui.field(t('channels.opt_udp_port'), boundNumber(input, 'port', 1, 65535)),
        ui.field(t('channels.opt_udp_localaddr'), boundText(input, 'localaddr', ''), t('channels.opt_udp_localaddr_hint')),
        ui.field(t('channels.opt_socket_size'), boundNumber(input, 'socket_size', 0, 262144)),
        ui.field(t('channels.opt_renew'), boundNumber(input, 'renew', 0, 86400))
      ]));
      content.appendChild(ui.checkbox(t('channels.opt_rtp'), !!input.rtp, {
        on: { change: (event) => { input.rtp = event.target.checked; } }
      }));
    } else if (input.type === 'file') {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_file'), boundText(input, 'filename', '/srv/media/loop.ts'))
      ]));
      content.appendChild(ui.checkbox(t('channels.opt_loop'), !!input.loop, {
        on: { change: (event) => { input.loop = event.target.checked; } }
      }));
    } else if (input.type === 'dvb') {
      content.appendChild(dvbFields(input));
    }

    content.appendChild(inputOptions(input, options));

    return cardShell(
      t('channels.input_n', { n: index + 1 }),
      index,
      (delta) => moveItem(model.inputs, index, delta, renderInputs),
      () => {
        model.inputs.splice(index, 1);
        renderInputs();
      },
      content,
      [ui.badge(fmt.inputTypeLabel(input.type), 'brand')]
    );
  }

  function dvbFields(input) {
    const wrapper = ui.div('col');
    wrapper.appendChild(ui.div('subgrid', [
      ui.field(t('channels.opt_delivery'), selectField(input, 'delivery', enums.delivery_systems || ['S', 'S2', 'T', 'T2', 'C'])),
      ui.field(t('channels.opt_adapter'), boundNumber(input, 'adapter', 0, 63)),
      ui.field(t('channels.opt_device'), boundNumber(input, 'device', 0, 63)),
      ui.field(t('channels.opt_frequency'), boundNumber(input, 'frequency', 0, 100000000), t('channels.opt_frequency_hint')),
      ui.field(t('channels.opt_polarization'), selectField(input, 'polarization', enums.polarizations || ['H', 'V', 'R', 'L'])),
      ui.field(t('channels.opt_symbolrate'), boundNumber(input, 'symbolrate', 0, 100000)),
      ui.field(t('channels.opt_lof1'), boundNumber(input, 'lof1', 0, 100000)),
      ui.field(t('channels.opt_lof2'), boundNumber(input, 'lof2', 0, 100000)),
      ui.field(t('channels.opt_slof'), boundNumber(input, 'slof', 0, 100000)),
      ui.field(t('channels.opt_diseqc'), boundNumber(input, 'diseqc', 0, 4)),
      ui.field(t('channels.opt_tone'), boundNumber(input, 'tone', 0, 2)),
      ui.field(t('channels.opt_lnb_sharing'), boundNumber(input, 'lnb_sharing', 0, 1)),
      ui.field(t('channels.opt_budget'), boundNumber(input, 'budget', 0, 1)),
      ui.field(t('channels.opt_buffer'), boundNumber(input, 'buffer_size', 0, 262144)),
      ui.field(t('channels.opt_stream_id'), boundNumber(input, 'stream_id', 0, 65535)),
      ui.field(t('channels.opt_fec'), selectField(input, 'fec', enums.fec || ['AUTO'])),
      ui.field(t('channels.opt_modulation'), selectField(input, 'modulation', [''].concat(enums.modulations || []))),
      ui.field(t('channels.opt_bandwidth'), selectField(input, 'bandwidth', enums.bandwidths || ['AUTO'])),
      ui.field(t('channels.opt_guard'), selectField(input, 'guardinterval', enums.guard_intervals || ['AUTO'])),
      ui.field(t('channels.opt_transmitmode'), selectField(input, 'transmitmode', enums.transmit_modes || ['AUTO'])),
      ui.field(t('channels.opt_hierarchy'), selectField(input, 'hierarchy', enums.hierarchies || ['AUTO'])),
      ui.field(t('channels.opt_rolloff'), selectField(input, 'rolloff', enums.rolloffs || ['AUTO']))
    ]));
    wrapper.appendChild(ui.checkbox(t('channels.opt_cam'), !!input.cam, {
      on: { change: (event) => { input.cam = event.target.checked; } }
    }));
    return wrapper;
  }

  function inputOptions(input, options) {
    const group = ui.el('fieldset', { class: 'group' }, [
      ui.el('legend', { text: t('channels.section_analyze') })
    ]);
    group.appendChild(ui.div('subgrid', [
      ui.field(t('channels.opt_pnr'), boundNumber(options, 'pnr', 0, 65535, input)),
      ui.field(t('channels.opt_set_pnr'), boundNumber(options, 'set_pnr', 0, 65535, input)),
      ui.field(t('channels.opt_ecm_pid'), boundText(options, 'ecm_pid', '', input)),
      ui.field(t('channels.opt_biss'), boundText(options, 'biss', '', input), t('channels.opt_biss_hint')),
      ui.field(t('channels.opt_filter'), boundText(options, 'filter', '', input), t('channels.opt_filter_hint'))
    ]));

    // CAM (newcamd) de esta entrada. Vacío = este canal no usa tarjeta.
    // Ajustes -> CAM es el valor por defecto de los canales nuevos; aquí se puede
    // cambiar por canal (con varios servidores CAM, cada uno apunta al suyo).
    const camGroup = ui.el('fieldset', { class: 'group' }, [
      ui.el('legend', { text: t('channels.opt_cam_section') })
    ]);

      // opt_cam_softcam_selector: elige un softcam guardado (Astra 5.1) o personaliza
      const perfilSelect = ui.select(
        [{ value: '', label: t('channels.opt_cam_none') },
         { value: '__custom', label: t('channels.opt_cam_custom') }].concat(
          SOFTCAMS.map((perfil) => ({ value: perfil.id, label: perfil.name }))),
        { on: { change: async (event) => {
          const elegido = event.target.value;
          if (elegido === '') {
            delete options.cam;
          } else if (elegido !== '__custom') {
            try {
              const datos = await ctx.api.get(`/api/softcams/${encodeURIComponent(elegido)}/full`);
              options.cam = Object.assign({}, datos.cam || {});
            } catch (error) {
              ui.showApiError(error, t('common.server_error'));
            }
          }
          Object.keys(camFields).forEach((clave) => {
            const valor = (options.cam && options.cam[clave] !== undefined) ? options.cam[clave] : '';
            camFields[clave].value = String(valor);
          });
        } } });
      const guardado = options.cam && options.cam.softcam;
      const coincide = SOFTCAMS.find((perfil) => perfil.name === guardado);
      perfilSelect.value = options.cam ? (coincide ? coincide.id : '__custom') : '';
      camGroup.insertBefore(ui.field(t('channels.opt_cam_softcam'), perfilSelect,
        t('channels.opt_cam_softcam_hint')), camGroup.firstChild);
    const camFields = {};
    function camField(clave, etiqueta, pista) {
      const nodo = ui.input({
        value: (options.cam && options.cam[clave] !== undefined) ? String(options.cam[clave]) : '',
        on: { input: (event) => {
          const valor = event.target.value.trim();
          options.cam = options.cam || {};
          if (valor === '') delete options.cam[clave]; else options.cam[clave] = valor;
          if (!Object.keys(options.cam).length) delete options.cam;
        } }
      });
      camFields[clave] = nodo;
      camGroup.appendChild(ui.field(etiqueta, nodo, pista));
    }
    camField('host', t('channels.opt_cam_host'));
    camField('port', t('channels.opt_cam_port'));
    camField('user', t('channels.opt_cam_user'));
    camField('pass', t('channels.opt_cam_pass'));
    camField('key', t('channels.opt_cam_key'), t('channels.opt_cam_key_hint'));
    camGroup.appendChild(ui.checkbox(t('channels.opt_cam_each_input'),
      !!(options.cam && options.cam.each_input), {
        on: { change: (event) => {
          options.cam = options.cam || {};
          if (event.target.checked) options.cam.each_input = 1; else delete options.cam.each_input;
        } }
      }));
    camGroup.appendChild(ui.checkbox(t('channels.opt_cam_allow_emm'),
      !(options.cam && options.cam.disable_emm), {
        on: { change: (event) => {
          options.cam = options.cam || {};
          if (event.target.checked) delete options.cam.disable_emm; else options.cam.disable_emm = 1;
        } }
      }));
    camGroup.appendChild(ui.button(t('channels.opt_cam_use_settings'), {
      icon: 'refresh',
      onClick: async () => {
        try {
          const data = await ctx.api.get('/api/cam');
          const cfg = data.config || {};
          options.cam = { host: cfg.host || '127.0.0.1', port: cfg.port || 5000, user: cfg.user || '' };
          if (cfg.each_input) options.cam.each_input = 1;
          if (cfg.allow_emm === false) options.cam.disable_emm = 1;
          Object.keys(camFields).forEach((clave) => {
            camFields[clave].value = options.cam[clave] === undefined ? '' : String(options.cam[clave]);
          });
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        }
      }
    }));
    camGroup.appendChild(ui.el('p', { class: 'small muted', text: t('channels.opt_cam_hint') }));
    group.appendChild(camGroup);
    const checks = ui.div('row', [
      ui.checkbox(t('channels.opt_no_analyze'), !!options.no_analyze, {
        on: { change: (event) => { setOption(input, 'no_analyze', event.target.checked); } }
      }),
      ui.checkbox(t('channels.opt_filter_reverse'), !!options.filter_reverse, {
        on: { change: (event) => { setOption(input, 'filter_reverse', event.target.checked); } }
      })
    ]);
    group.appendChild(checks);
    return group;
  }

  function outputCard(output, index) {
    const content = ui.div('col');
    if (output.type === 'http') {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_uri'), boundText(output, 'uri', '/canal'), t('channels.opt_uri_hint'))
      ]));
      content.appendChild(ui.div('row', [
        ui.checkbox(t('channels.opt_public'), !!output.public, {
          on: { change: (event) => { output.public = event.target.checked; } }
        }),
        ui.checkbox(t('channels.opt_cors'), output.cors !== false, {
          on: { change: (event) => { output.cors = event.target.checked; } }
        })
      ]));
    } else if (output.type === 'udp') {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_udp_addr'), boundText(output, 'addr', '239.0.0.1')),
        ui.field(t('channels.opt_udp_port'), boundNumber(output, 'port', 1, 65535)),
        ui.field(t('channels.opt_ttl'), boundNumber(output, 'ttl', 1, 255)),
        ui.field(t('channels.opt_udp_localaddr'), boundText(output, 'localaddr', '')),
        ui.field(t('channels.opt_sync'), boundNumber(output, 'sync', 0, 1000)),
        ui.field(t('channels.opt_socket_size'), boundNumber(output, 'socket_size', 0, 262144))
      ]));
      content.appendChild(ui.checkbox(t('channels.opt_rtp'), !!output.rtp, {
        on: { change: (event) => { output.rtp = event.target.checked; } }
      }));
    } else {
      content.appendChild(ui.div('subgrid', [
        ui.field(t('channels.opt_file'), boundText(output, 'filename', '/var/lib/astra-webui/recordings/canal.ts')),
        ui.field(t('channels.opt_buffer'), boundNumber(output, 'buffer_size', 0, 262144))
      ]));
      content.appendChild(ui.checkbox(t('channels.opt_m2ts'), !!output.m2ts, {
        on: { change: (event) => { output.m2ts = event.target.checked; } }
      }));
    }

    return cardShell(
      t('channels.output_n', { n: index + 1 }),
      index,
      (delta) => moveItem(model.outputs, index, delta, renderOutputs),
      () => {
        model.outputs.splice(index, 1);
        renderOutputs();
      },
      content,
      [ui.badge(fmt.inputTypeLabel(output.type), 'info')]
    );
  }

  function renderStateCard() {
    const rows = stateInfo.inputs || [];
    const errors = stateInfo.errors || {};
    const list = ui.div('col');
    list.appendChild(ui.div('row', [
      ui.stateBadge(!!stateInfo.on_air, t('dashboard.on_air'), t('channels.state_stopped')),
      ui.badge(`${t('channels.col_bitrate')}: ${fmt.formatBitrate(stateInfo.bitrate || 0)}`, 'info'),
      ui.badge(`${t('channels.col_viewers')}: ${fmt.formatNumber(stateInfo.viewers || 0)}`, 'brand'),
      ui.badge(`CC ${errors.cc || 0} · SC ${errors.sc || 0} · PES ${errors.pes || 0}`, (errors.cc || errors.sc || errors.pes) ? 'warn' : 'off')
    ]));
    list.appendChild(ui.dataTable({
      compact: true,
      rows,
      columns: [
        { label: '#', render: (row) => String(row.index || '') },
        { label: t('common.type'), render: (row) => fmt.inputTypeLabel(row.type) },
        {
          label: t('common.state'),
          render: (row) => ui.stateBadge(!!row.on_air, t('channels.input_on_air'), t('channels.state_stopped'))
        },
        { label: t('channels.col_bitrate'), className: 'num', render: (row) => fmt.formatBitrate(row.bitrate || 0) },
        {
          label: t('channels.col_errors'),
          className: 'num',
          render: (row) => `${row.cc_error || 0}/${row.sc_error || 0}/${row.pes_error || 0}`
        },
        {
          label: t('common.error'),
          render: (row) => (row.error ? ui.badge(row.error, 'danger') : ui.el('span', { class: 'muted', text: '—' }))
        }
      ],
      empty: t('common.empty')
    }));
    return ui.card({ title: t('channels.section_state'), content: [list] });
  }

  function renderActions() {
    ui.clear(actionTools);
    if (isNew) {
      actionTools.appendChild(ui.el('span', { class: 'muted small', text: t('common.optional') }));
      return;
    }
    const current = (state.channels || []).find((channel) => channel.id === channelId) || {};
    const running = !!(stateInfo && stateInfo.running);
    actionTools.appendChild(ui.button(running ? t('channels.stop') : t('channels.start'), {
      size: 'sm',
      variant: running ? 'danger' : 'ok',
      icon: running ? 'stop' : 'play',
      onClick: () => labelAction(running ? 'stop' : 'start')
    }));
    actionTools.appendChild(ui.button(t('channels.restart'), {
      size: 'sm',
      icon: 'restart',
      onClick: () => labelAction('restart')
    }));
    actionTools.appendChild(ui.button(t('channels.duplicate'), {
      size: 'sm',
      icon: 'copy',
      onClick: async () => {
        try {
          const data = await api.post(`/api/channels/${encodeURIComponent(channelId)}/duplicate`);
          ui.notify.ok(t('channels.duplicated', { name: (data.channel || {}).name || '' }));
          announceAgentResult(ctx, data);
          window.location.hash = `#/channels/${encodeURIComponent((data.channel || {}).id || '')}`;
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        }
      }
    }));
    actionTools.appendChild(ui.button(t('common.delete'), {
      size: 'sm',
      variant: 'danger',
      icon: 'trash',
      onClick: async () => {
        const yes = await ui.confirmDialog({
          title: t('common.delete'),
          message: t('channels.delete_confirm', { name: model.name || channelId }),
          acceptLabel: t('common.delete'),
          danger: true
        });
        if (!yes) return;
        try {
          await api.delete(`/api/channels/${encodeURIComponent(channelId)}`);
          ui.notify.ok(t('channels.deleted'));
          window.location.hash = '#/channels';
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        }
      }
    }));
    actionTools.appendChild(ui.el('span', {
      class: 'muted small',
      text: current.updated_at ? `${t('audit.col_time')}: ${fmt.formatDate(current.updated_at)}` : ''
    }));
  }

  async function labelAction(name) {
    try {
      const data = await api.post(`/api/channels/${encodeURIComponent(channelId)}/action`, { action: name });
      const messages = {
        start: t('channels.started', { name: model.name || channelId }),
        stop: t('channels.stopped', { name: model.name || channelId }),
        restart: t('channels.restarted', { name: model.name || channelId })
      };
      ui.notify.ok(messages[name] || t('common.accept'));
      announceAgentResult(ctx, data);
      await load();
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  /* ------------------------------------------------------------------ guardar */

  async function save() {
    hide(formError);
    hide(formWarn);
    ui.busyButton(saveButton, true);
    const payload = collect();
    try {
      const data = isNew
        ? await api.post('/api/channels', payload)
        : await api.put(`/api/channels/${encodeURIComponent(channelId)}`, payload);
      const saved = data.channel || {};
      ui.notify.ok(isNew ? t('channels.created') : t('channels.saved'));
      showList(formWarn, data.warnings);
      announceAgentResult(ctx, data);
      if (isNew && saved.id) {
        window.location.hash = `#/channels/${encodeURIComponent(saved.id)}`;
      } else {
        await load();
      }
    } catch (error) {
      if (error.status === 400 || error.status === 409) {
        showErrors(error);
      } else {
        ui.showApiError(error, t('common.server_error'));
      }
    } finally {
      ui.busyButton(saveButton, false);
    }
  }

  function collect() {
    const payload = {
      name: (model.name || '').trim(),
      group: (model.group || '').trim(),
      logo: (model.logo || '').trim(),
      epg_id: (model.epg_id || '').trim(),
      enabled: model.enabled !== false,
      tags: model.tags || [],
      notes: model.notes || '',
      inputs: model.inputs.map(cleanInput),
      outputs: model.outputs.map(cleanOutput),
      analyze: { enabled: !model.analyze || model.analyze.enabled !== false }
    };
    if (isNew && (model.id || '').trim()) payload.id = model.id.trim();
    return payload;
  }

  function cleanInput(input) {
    const out = { type: input.type, options: {} };
    const options = input.options || {};
    Object.keys(options).forEach((key) => {
      const value = options[key];
      if (value === '' || value === undefined || value === null || value === false || value === 0) {
        if (key === 'filter_reverse' || key === 'no_analyze') {
          if (value === true) out.options[key] = true;
        }
        return;
      }
      out.options[key] = value;
    });
    if (input.type === 'http') out.url = input.url;
    if (input.type === 'udp') {
      Object.assign(out, {
        addr: input.addr, port: fmt.toInt(input.port, 1234), localaddr: input.localaddr || '',
        rtp: !!input.rtp, socket_size: fmt.toInt(input.socket_size, 0), renew: fmt.toInt(input.renew, 0)
      });
    }
    if (input.type === 'file') Object.assign(out, { filename: input.filename, loop: !!input.loop });
    if (input.type === 'dvb') {
      ['delivery', 'adapter', 'device', 'frequency', 'polarization', 'symbolrate', 'lof1', 'lof2', 'slof',
        'diseqc', 'tone', 'lnb_sharing', 'budget', 'buffer_size', 'stream_id', 'fec', 'modulation',
        'bandwidth', 'guardinterval', 'transmitmode', 'hierarchy', 'rolloff', 'cam'].forEach((key) => {
        out[key] = input[key];
      });
    }
    return out;
  }

  function cleanOutput(output) {
    const out = { type: output.type };
    if (output.type === 'http') {
      Object.assign(out, { uri: output.uri, public: !!output.public, cors: output.cors !== false });
    } else if (output.type === 'udp') {
      Object.assign(out, {
        addr: output.addr, port: fmt.toInt(output.port, 1234), ttl: fmt.toInt(output.ttl, 32),
        localaddr: output.localaddr || '', rtp: !!output.rtp, sync: fmt.toInt(output.sync, 0),
        socket_size: fmt.toInt(output.socket_size, 0)
      });
    } else {
      Object.assign(out, { filename: output.filename, m2ts: !!output.m2ts, buffer_size: fmt.toInt(output.buffer_size, 0) });
    }
    return out;
  }

  function showErrors(error) {
    ui.clear(formError);
    formError.appendChild(ui.el('strong', { text: error.message || t('common.error') }));
    if (error.warnings && error.warnings.length) {
      const list = ui.el('ul');
      error.warnings.forEach((warning) => list.appendChild(ui.el('li', { text: warning })));
      formError.appendChild(list);
    }
    formError.hidden = false;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function showList(node, messages) {
    if (!messages || !messages.length) return;
    ui.clear(node);
    node.appendChild(ui.el('strong', { text: t('common.warnings') }));
    const list = ui.el('ul');
    messages.forEach((message) => list.appendChild(ui.el('li', { text: message })));
    node.appendChild(list);
    node.hidden = false;
  }

  load();

  return {
    destroy() {
      hide(formError);
    }
  };
}

/* ==========================================================================
   PSI
   ========================================================================== */

/** Abre el diálogo de análisis PSI y consulta `GET /api/channels/{id}/psi`. */
export async function openPsiDialog(ctx, channel) {
  const holder = ui.div('col');
  const modal = ui.openModal({
    title: t('psi.title', { name: channel.name || channel.id }),
    size: 'md',
    content: [holder],
    footer: []
  });
  const analyze = ui.button(t('psi.analyze'), { variant: 'primary', icon: 'refresh' });
  const close = ui.button(t('common.close'), { onClick: () => modal.close() });
  modal.modal.querySelector('.modal__foot').appendChild(ui.div('grow'));
  modal.modal.querySelector('.modal__foot').appendChild(close);
  modal.modal.querySelector('.modal__foot').appendChild(analyze);

  async function run() {
    ui.clear(holder);
    holder.appendChild(ui.div('row', [ui.spinner('sm'), ui.el('span', { text: t('psi.analyzing') })]));
    ui.busyButton(analyze, true);
    try {
      const payload = await ctx.api.get(`/api/channels/${encodeURIComponent(channel.id)}/psi`);
      ui.clear(holder);
      holder.appendChild(psiContent(payload));
    } catch (error) {
      ui.clear(holder);
      holder.appendChild(ui.alert('error', t('common.error'), error.message));
    } finally {
      ui.busyButton(analyze, false);
    }
  }

  analyze.addEventListener('click', run);
  run();
}

/** Interpreta la respuesta del agente (`tables`, `pids`, `services`, `stats`). */
export function psiContent(payload) {
  const tables = Array.isArray(payload.tables) ? payload.tables : [];
  const pids = Array.isArray(payload.pids) ? payload.pids : [];
  const services = Array.isArray(payload.services) ? payload.services : [];
  const stats = payload.stats || null;
  const wrapper = ui.div('col');

  if (stats) {
    wrapper.appendChild(ui.div('row', [
      ui.badge(`${t('psi.bitrate')}: ${fmt.formatBitrate(stats.bitrate || 0)}`, 'info'),
      ui.stateBadge(!!stats.on_air, t('dashboard.on_air'), t('channels.state_stopped'))
    ]));
  }

  if (!tables.length && !pids.length && !services.length) {
    wrapper.appendChild(ui.emptyState(t('psi.empty')));
    return wrapper;
  }

  if (tables.length) {
    const grid = ui.div('psi-grid');
    tables.forEach((table) => grid.appendChild(psiTableCard(table)));
    wrapper.appendChild(grid);
  }

  if (services.length) {
    wrapper.appendChild(ui.card({
      title: t('psi.services'),
      subtitle: t('psi.count_services', { n: services.length }),
      content: [servicesTable(services)]
    }));
  }

  if (pids.length) {
    wrapper.appendChild(ui.card({
      title: t('psi.bitrate_pid'),
      content: [pidsTable(pids)]
    }));
  }

  return wrapper;
}

function psiTableCard(table) {
  const kind = String(table.psi || table.type || 'psi').toLowerCase();
  const body = ui.div('col');
  body.appendChild(ui.keyValues([
    [t('psi.table'), ui.el('span', { class: 'psi-table__kind', text: kind.toUpperCase() })],
    ['input', table.input_index],
    [t('psi.tsid'), table.tsid],
    [t('psi.crc32'), table.crc32]
  ]));

  if (Array.isArray(table.programs) && table.programs.length) {
    body.appendChild(ui.el('div', { class: 'field__label', text: t('psi.count_programs', { n: table.programs.length }) }));
    body.appendChild(ui.dataTable({
      compact: true,
      rows: table.programs,
      columns: [
        { label: t('psi.program'), render: (row) => String(row.pnr ?? row.program ?? '') },
        { label: t('psi.pid'), render: (row) => String(row.pid ?? '') },
        { label: t('psi.pmt_pid'), render: (row) => String(row.pmt_pid ?? '') }
      ],
      empty: t('psi.no_detail')
    }));
  }

  if (Array.isArray(table.descriptors) && table.descriptors.length) {
    body.appendChild(descriptorList(table.descriptors));
  }

  if (Array.isArray(table.streams) && table.streams.length) {
    body.appendChild(ui.dataTable({
      compact: true,
      rows: table.streams,
      columns: [
        { label: t('psi.pid'), render: (row) => String(row.pid ?? '') },
        { label: t('common.type'), render: (row) => String(row.type_name || row.type || '') },
        { label: 'type_id', render: (row) => (row.type_id === undefined ? '' : `0x${Number(row.type_id).toString(16)}`) }
      ],
      empty: t('psi.no_detail')
    }));
  }

  if (Array.isArray(table.services) && table.services.length) {
    body.appendChild(servicesTable(table.services));
  }

  if (kind === 'cat' && Array.isArray(table.descriptors)) {
    body.appendChild(descriptorList(table.descriptors));
  }

  return ui.card({ title: kind.toUpperCase(), content: [body] });
}

function descriptorList(descriptors) {
  const list = ui.el('ul', { class: 'small mono' });
  descriptors.forEach((descriptor) => {
    const parts = [];
    Object.keys(descriptor || {}).forEach((key) => {
      if (['type_name', 'descriptors', 'streams'].includes(key)) return;
      parts.push(`${key}=${descriptor[key]}`);
    });
    const name = descriptor && descriptor.type_name ? `${descriptor.type_name}: ` : '';
    list.appendChild(ui.el('li', { text: `${name}${parts.join(' ')}` }));
  });
  return list;
}

function servicesTable(services) {
  return ui.dataTable({
    compact: true,
    rows: services,
    columns: [
      { label: t('psi.sid'), render: (row) => String(row.sid ?? row.pnr ?? '') },
      { label: t('psi.pnr'), render: (row) => String(row.pnr ?? '') },
      { label: t('psi.name'), render: (row) => row.name || '—' },
      { label: t('psi.provider'), render: (row) => row.provider || '—' },
      { label: t('psi.pmt_pid'), render: (row) => String(row.pmt_pid ?? '') },
      { label: t('psi.video_pid'), render: (row) => String(row.video_pid ?? '') },
      { label: t('psi.audio_pid'), render: (row) => String(row.audio_pid ?? '') },
      {
        label: t('psi.scrambled'),
        render: (row) => ui.badge(
          row.scrambled ? t('psi.scrambled_yes') : t('psi.scrambled_no'),
          row.scrambled ? 'warn' : 'ok'
        )
      }
    ],
    empty: t('psi.no_detail')
  });
}

function pidsTable(pids) {
  return ui.dataTable({
    compact: true,
    rows: pids,
    columns: [
      { label: t('psi.pid'), render: (row) => String(row.pid ?? row.index ?? '') },
      { label: t('common.type'), render: (row) => String(row.type_name || row.type || '') },
      { label: t('psi.bitrate'), className: 'num', render: (row) => fmt.formatBitrate(row.bitrate || 0) },
      { label: t('psi.cc_error'), className: 'num', render: (row) => String(row.cc_error ?? 0) }
    ],
    empty: t('psi.no_detail')
  });
}

/* ==========================================================================
   Ayudas de formulario
   ========================================================================== */

/** Campo de texto ligado directamente a una propiedad. */
function boundText(target, key, placeholder, host) {
  const control = ui.input({ value: target[key] === undefined || target[key] === null ? '' : String(target[key]), placeholder: placeholder || '' });
  control.addEventListener('input', () => {
    target[key] = control.value;
    if (host) markOption(host, key, control.value);
  });
  return control;
}

/** Campo numérico ligado a una propiedad (usa `fmt.toInt`, permite vacío). */
function boundNumber(target, key, minimum, maximum, host) {
  const control = ui.input({
    type: 'number',
    value: target[key] === undefined || target[key] === null ? '' : String(target[key]),
    min: minimum, max: maximum, inputmode: 'numeric'
  });
  control.addEventListener('input', () => {
    const raw = control.value.trim();
    if (raw === '') {
      target[key] = '';
      if (host) markOption(host, key, '');
      return;
    }
    const value = fmt.toInt(raw, minimum || 0);
    target[key] = value;
    if (host) markOption(host, key, value);
  });
  return control;
}

/** Desplegable ligado a una propiedad. */
function selectField(target, key, values) {
  const current = target[key] === undefined || target[key] === null ? '' : String(target[key]);
  const control = ui.select(values.map((value) => ({
    value: String(value),
    label: String(value) || '—',
    selected: String(value) === current
  })));
  control.addEventListener('change', () => { target[key] = control.value; });
  return control;
}

function markOption(input, key, value) {
  const options = input.options || (input.options = {});
  if (value === '' || value === 0 || value === false || value === undefined) delete options[key];
  else options[key] = value;
}

function setOption(input, key, value) {
  const options = input.options || (input.options = {});
  if (value) options[key] = true;
  else delete options[key];
}

function moveItem(list, index, delta, rerender) {
  const target = index + delta;
  if (target < 0 || target >= list.length) return;
  const [item] = list.splice(index, 1);
  list.splice(target, 0, item);
  rerender();
}

function hide(node) {
  node.hidden = true;
}

/** Avisa si el agente no aplicó el cambio o si no pudo arrancar algún canal. */
export function announceAgentResult(ctx, data) {
  if (!data) return;
  if (data.agent_error) {
    ui.notify.warn(`${t('channels.agent_partial')}: ${data.agent_error}`);
    return;
  }
  const agent = data.agent || {};
  if (Array.isArray(agent.failed) && agent.failed.length) {
    const list = agent.failed.map((item) => (typeof item === 'string' ? item : (item.id || JSON.stringify(item))));
    ui.notify.warn(t('channels.failed_agent', { list: list.join(', ') }));
  }
  if (agent.auth_error) ui.notify.warn(agent.auth_error);
}

/** Fuerza el remontaje de la ruta actual (la app escucha `hashchange`). */
function reloadRoute() {
  window.dispatchEvent(new Event('hashchange'));
}
