/**
 * views/settings.js — ajustes del panel, control del servicio y respaldo.
 *
 * Consume:
 *   GET  /api/settings                 (panel.json; sólo admin)
 *   PUT  /api/settings                 (valida con validators.sanitize_settings)
 *   POST /api/service/{start|stop|restart|status}-astra
 *   GET  /api/export                   → {bundle}
 *   POST /api/import                   ({bundle, import_users})
 *   POST /api/auth/password            ({current, password})
 *   GET  /api/auth/sessions
 *   POST /api/auth/sessions/purge
 */

import { t, setLanguage } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

export function render(container, ctx) {
  const { api, state } = ctx;
  let settings = null;
  let original = null;
  let serviceAvailable = false;
  let serviceOutput = null;

  ctx.setTitle(t('settings.title'), `${t('common.version')} ${state.version || ''}`.trim());

  const model = {};
  const restartNotice = ui.div('col');
  const errorBox = ui.div('form-error', null, { hidden: true });
  const formHolder = ui.div('col');
  const serviceHolder = ui.div('col');
  const serviceCard = ui.card({ title: t('settings.section_service'), content: [serviceHolder] });

  /* --------------------------------------------------------- tarjeta / CAM */
  // Estado de la tarjeta (OSCam + lector PC/SC) y configuración del CAM en un sitio:
  // se guarda una vez y se aplica a todos los canales DVB.
  const camHolder = ui.div('col');
  const camMsg = ui.div('hint');
  const camCard = ui.card({ title: t('settings.section_cam'), content: [camHolder, camMsg] });
  const camModel = { host: '127.0.0.1', port: 5000, user: '', pass: '', key: '' };

  async function renderCam() {
    ui.clear(camHolder);
    camMsg.textContent = '';
    let data = null;
    try {
      data = await api.get('/api/cam');
    } catch (error) {
      camHolder.appendChild(ui.div('hint', t('settings.cam_unavailable')));
      return;
    }
    const cfg = data.config || {};
    const canales = data.channels || {};
    if (cfg.host) camModel.host = cfg.host;
    if (cfg.port) camModel.port = cfg.port;
    if (cfg.user) camModel.user = cfg.user;

    const ok = t('settings.cam_ok');
    const mal = t('settings.cam_down');
    const lineas = [
      [t('settings.cam_newcamd'), (data.newcamd && data.newcamd.open) ? ok : mal],
      [t('settings.cam_channels'), String(canales.decoding || 0) + ' ' + t('settings.cam_decoding') +
        ' / ' + String(canales.with_cam || 0) + ' ' + t('settings.cam_with_cam') +
        ' · ' + String(canales.no_keys || 0) + ' ' + t('settings.cam_no_keys') +
        ' · ' + String(canales.total || 0) + ' ' + t('settings.cam_total')]
    ];
    for (const par of lineas) {
      camHolder.appendChild(ui.div('hint', par[0] + ': ' + par[1]));
    }
    const abiertos = (canales.decoding_names || []);
    if (abiertos.length) {
      camHolder.appendChild(ui.div('hint', t('settings.cam_open_now') + ': ' + abiertos.slice(0, 12).join(', ')));
    }

    camHolder.appendChild(ui.field(t('settings.cam_host'), textField(camModel, 'host')));
    camHolder.appendChild(ui.field(t('settings.cam_port'), numberField(camModel, 'port', 1, 65535)));
    camHolder.appendChild(ui.field(t('settings.cam_user'), textField(camModel, 'user')));
    camHolder.appendChild(ui.field(t('settings.cam_pass'), textField(camModel, 'pass')));
    camHolder.appendChild(ui.field(t('settings.cam_key'), textField(camModel, 'key'),
      t('settings.cam_key_hint')));
    camHolder.appendChild(ui.button(t('settings.cam_apply'), {
      variant: 'primary', icon: 'check',
      onClick: async () => {
        try {
          ui.setBusy(true);
          const cuerpo = Object.assign({ apply: true }, camModel);
          const res = await api.post('/api/cam', cuerpo);
          camMsg.textContent = t('settings.cam_applied') + ': ' + String(res.applied || 0);
          await renderCam();
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        } finally {
          ui.setBusy(false);
        }
      }
    }));
  }

  const saveButton = ui.button(t('settings.save'), { variant: 'primary', icon: 'check', onClick: () => save() });
  const reloadButton = ui.button(t('common.reload'), { icon: 'refresh', onClick: () => load() });

  container.appendChild(ui.card({
    title: t('settings.title'),
    subtitle: state.user ? (state.user.display_name || state.user.username) : '',
    content: [restartNotice, errorBox, formHolder],
    tools: [reloadButton, saveButton]
  }));

  /* ------------------------------------------------------------------ carga */

  async function load() {
    ui.setBusy(true);
    try {
      const data = await api.get('/api/settings');
      settings = data.settings || {};
      original = JSON.parse(JSON.stringify(settings));
      serviceAvailable = !!data.service_available;
      Object.assign(model, JSON.parse(JSON.stringify(settings)));
      ui.clear(errorBox);
      errorBox.hidden = true;
      renderForm(data);
      renderRestartNotice();
      renderService();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    } finally {
      ui.setBusy(false);
    }
  }

  function currentForm() {
    return {
      listen: model.listen,
      port: fmt.toInt(model.port, 8000),
      session_hours: fmt.toInt(model.session_hours, 12),
      base_url: model.base_url || '',
      stream: {
        host: (model.stream || {}).host || '',
        port: fmt.toInt((model.stream || {}).port, 8001),
        public_base: (model.stream || {}).public_base || ''
      },
      ui: {
        title: (model.ui || {}).title || 'Astra WebUI',
        language: (model.ui || {}).language || 'es',
        refresh_ms: fmt.toInt((model.ui || {}).refresh_ms, 2000),
        theme: (model.ui || {}).theme || 'dark'
      },
      security: {
        max_login_attempts: fmt.toInt((model.security || {}).max_login_attempts, 5),
        login_window: fmt.toInt((model.security || {}).login_window, 300),
        trust_proxy: !!((model.security || {}).trust_proxy),
        allowed_networks: (model.security || {}).allowed_networks || []
      },
      history: {
        points: fmt.toInt((model.history || {}).points, 720),
        interval: fmt.toInt((model.history || {}).interval, 5)
      },
      service_control: {
        enabled: !!((model.service_control || {}).enabled),
        unit: (model.service_control || {}).unit || 'astra.service'
      }
    };
  }

  function needsRestart() {
    if (!settings || !original) return false;
    return fmt.num(model.port, 0) !== fmt.num(original.port, 0)
      || String(model.listen || '') !== String(original.listen || '');
  }

  function renderRestartNotice() {
    ui.clear(restartNotice);
    if (needsRestart()) {
      restartNotice.appendChild(ui.alert('warn', t('settings.restart_hint'), t('settings.restart_required'), { dismissible: true }));
    }
  }

  /* --------------------------------------------------------------- formulario */

  function renderForm(data) {
    ui.clear(formHolder);
    const uiSection = settings.ui || {};
    const security = settings.security || {};

    formHolder.appendChild(ui.card({
      title: t('settings.section_panel'),
      content: [
        ui.div('grid-3', [
          ui.field(t('settings.listen'), textField(model, 'listen'), t('settings.listen_hint')),
          ui.field(t('settings.port'), numberField(model, 'port', 1, 65535)),
          ui.field(t('settings.session_hours'), numberField(model, 'session_hours', 1, 720))
        ]),
        ui.field(t('settings.base_url'), textField(model, 'base_url'), t('settings.base_url_hint'))
      ]
    }));

    formHolder.appendChild(ui.card({
      title: t('settings.section_stream'),
      content: [
        ui.div('grid-2', [
          ui.field(t('settings.stream_host'), nestedTextField(model, 'stream', 'host')),
          ui.field(t('settings.stream_port'), nestedNumberField(model, 'stream', 'port', 1, 65535))
        ]),
        ui.field(t('settings.stream_public'), nestedTextField(model, 'stream', 'public_base'))
      ]
    }));

    const languageSelect = ui.select([
      { value: 'es', label: 'Español', selected: (uiSection.language || 'es') === 'es' },
      { value: 'en', label: 'English', selected: uiSection.language === 'en' }
    ]);
    languageSelect.addEventListener('change', () => {
      model.ui = model.ui || {};
      model.ui.language = languageSelect.value;
      // Vista previa inmediata (el guardado lo persiste en panel.json).
      setLanguage(languageSelect.value);
    });

    const themeSelect = ui.select(['dark', 'midnight', 'light'].map((value) => ({
      value, label: t(`settings.theme_${value}`), selected: (uiSection.theme || 'dark') === value
    })));
    themeSelect.addEventListener('change', () => {
      model.ui = model.ui || {};
      model.ui.theme = themeSelect.value;
      document.documentElement.setAttribute('data-theme', themeSelect.value);
    });

    formHolder.appendChild(ui.card({
      title: t('settings.section_ui'),
      content: [
        ui.div('grid-4', [
          ui.field(t('settings.ui_title'), nestedTextField(model, 'ui', 'title')),
          ui.field(t('settings.language'), languageSelect),
          ui.field(t('settings.refresh_ms'), nestedNumberField(model, 'ui', 'refresh_ms', 500, 60000)),
          ui.field(t('settings.theme'), themeSelect)
        ])
      ]
    }));

    const trustProxy = ui.checkbox(t('settings.trust_proxy'), !!security.trust_proxy);
    trustProxy.input.addEventListener('change', () => {
      model.security = model.security || {};
      model.security.trust_proxy = trustProxy.input.checked;
    });

    const networks = ui.input({ value: (security.allowed_networks || []).join(', ') });
    networks.addEventListener('input', () => {
      model.security = model.security || {};
      model.security.allowed_networks = networks.value.split(',').map((item) => item.trim()).filter(Boolean);
    });

    formHolder.appendChild(ui.card({
      title: t('settings.section_security'),
      content: [
        ui.div('grid-2', [
          ui.field(t('settings.max_attempts'), nestedNumberField(model, 'security', 'max_login_attempts', 1, 100)),
          ui.field(t('settings.login_window'), nestedNumberField(model, 'security', 'login_window', 30, 86400))
        ]),
        trustProxy,
        ui.field(t('settings.allowed_networks'), networks, t('settings.allowed_networks_hint'))
      ]
    }));

    formHolder.appendChild(ui.card({
      title: t('settings.section_history'),
      content: [
        ui.div('grid-2', [
          ui.field(t('settings.history_points'), nestedNumberField(model, 'history', 'points', 60, 20000)),
          ui.field(t('settings.history_interval'), nestedNumberField(model, 'history', 'interval', 1, 300))
        ]),
        ui.alert('info', null, `${t('settings.agent_url')}: ${(settings.agent || {}).url || '—'} · `
          + `${t('settings.agent_timeout')}: ${(settings.agent || {}).timeout ?? '—'} · `
          + `${t('settings.agent_token')}: ${(settings.agent || {}).token_file || '—'}`)
      ]
    }));

    const serviceEnabled = ui.checkbox(t('settings.service_enabled'), !!((settings.service_control || {}).enabled));
    serviceEnabled.input.addEventListener('change', () => {
      model.service_control = model.service_control || {};
      model.service_control.enabled = serviceEnabled.input.checked;
    });
    const unitInput = ui.input({ value: (settings.service_control || {}).unit || 'astra.service' });
    unitInput.addEventListener('input', () => {
      model.service_control = model.service_control || {};
      model.service_control.unit = unitInput.value;
    });

    formHolder.appendChild(ui.card({
      title: t('settings.section_service'),
      content: [ui.div('grid-2', [serviceEnabled, ui.field(t('settings.unit'), unitInput)])]
    }));

    formHolder.appendChild(ui.card({
      title: t('settings.raw_settings'),
      content: [
        ui.keyValues([
          [t('settings.config_path'), data.config_path || '—'],
          [(settings.paths || {}).channels ? t('common.channels') : t('settings.paths'), (settings.paths || {}).channels || '—'],
          [t('common.version'), (settings.dev ? 'dev · ' : '') + (state.version || '—')]
        ]),
        ui.el('pre', {
          class: 'small mono',
          text: JSON.stringify({
            listen: settings.listen,
            port: settings.port,
            base_url: settings.base_url,
            stream: settings.stream,
            ui: settings.ui,
            security: settings.security,
            history: settings.history,
            service_control: settings.service_control
          }, null, 2)
        })
      ]
    }));
  }

  async function save() {
    errorBox.hidden = true;
    ui.busyButton(saveButton, true);
    const payload = currentForm();
    try {
      const data = await api.put('/api/settings', payload);
      settings = data.settings || settings;
      original = JSON.parse(JSON.stringify(settings));
      Object.assign(model, JSON.parse(JSON.stringify(settings)));
      ui.notify.ok(t('settings.saved'));
      renderForm(data);
      renderRestartNotice();
      if (payload.ui.language) setLanguage(payload.ui.language);
      if (payload.ui.theme) document.documentElement.setAttribute('data-theme', payload.ui.theme);
      state.ui = { ...state.ui, ...payload.ui };
    } catch (error) {
      if (error.status === 400) {
        ui.clear(errorBox);
        errorBox.appendChild(ui.el('strong', { text: error.message }));
        errorBox.hidden = false;
      } else {
        ui.showApiError(error, t('common.server_error'));
      }
    } finally {
      ui.busyButton(saveButton, false);
    }
  }

  /* ------------------------------------------------------------------ servicio */

  function renderService() {
    ui.clear(serviceHolder);
    if (!serviceAvailable) {
      serviceHolder.appendChild(ui.alert('warn', null, t('settings.service_unavailable')));
    }
    serviceOutput = ui.el('pre', { class: 'small mono', text: '' });
    serviceHolder.appendChild(ui.div('row row--tight', [
      ui.button(t('settings.svc_start'), { size: 'sm', variant: 'ok', icon: 'play', onClick: () => serviceAction('start') }),
      ui.button(t('settings.svc_stop'), { size: 'sm', variant: 'danger', icon: 'stop', onClick: () => serviceAction('stop') }),
      ui.button(t('settings.svc_restart'), { size: 'sm', variant: 'danger', icon: 'restart', onClick: () => serviceAction('restart') }),
      ui.button(t('settings.svc_status'), { size: 'sm', icon: 'refresh', onClick: () => serviceAction('status') })
    ]));
    serviceHolder.appendChild(ui.el('div', { class: 'field__label', text: t('settings.svc_output') }));
    serviceHolder.appendChild(serviceOutput);
  }

  async function serviceAction(action) {
    if (action === 'stop' || action === 'restart') {
      const yes = await ui.confirmDialog({
        title: t(`settings.svc_${action}`),
        message: action === 'stop' ? t('settings.svc_confirm_stop') : t('settings.svc_confirm_restart'),
        acceptLabel: t(`settings.svc_${action}`),
        danger: true
      });
      if (!yes) return;
    }
    try {
      const data = await api.post(`/api/service/${action}-astra`, {});
      if (serviceOutput) serviceOutput.textContent = data.output || JSON.stringify(data, null, 2);
      ui.notify.ok(`${t(`settings.svc_${action}`)}: ${data.unit || ''}`);
    } catch (error) {
      if (serviceOutput) serviceOutput.textContent = error.message;
      ui.showApiError(error, t('common.server_error'));
    }
  }

  /* ------------------------------------------------------------------ respaldo */

  function renderBackup() {
    const exportOutput = ui.div('col');
    const exportButton = ui.button(t('settings.export_do'), {
      icon: 'download',
      onClick: async () => {
        ui.busyButton(exportButton, true);
        try {
          const data = await api.get('/api/export');
          const blob = new Blob([JSON.stringify(data.bundle || data, null, 2)], { type: 'application/json' });
          api.saveBlob(blob, `astra-webui-backup-${new Date().toISOString().slice(0, 10)}.json`);
          ui.notify.ok(t('settings.export_done'));
          ui.clear(exportOutput);
          exportOutput.appendChild(ui.alert('ok', null, t('settings.export_done')));
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        } finally {
          ui.busyButton(exportButton, false);
        }
      }
    });

    const importText = ui.textarea({ rows: 6, placeholder: '{"version":1,"channels":[…]}' });
    const importUsers = ui.checkbox(t('settings.import_users'), false);
    const importButton = ui.button(t('settings.import_do'), {
      variant: 'danger',
      icon: 'upload',
      onClick: async () => {
        let bundle = null;
        try {
          bundle = JSON.parse(importText.value);
        } catch (error) {
          ui.notify.error(t('settings.import_invalid'));
          return;
        }
        const yes = await ui.confirmDialog({
          title: t('settings.import'),
          message: t('settings.import_hint'),
          acceptLabel: t('settings.import_do'),
          danger: true
        });
        if (!yes) return;
        ui.busyButton(importButton, true);
        try {
          const data = await api.post('/api/import', { bundle, import_users: importUsers.input.checked });
          const summary = data.summary || {};
          ui.notify.ok(t('settings.import_done', {
            channels: summary.channels || 0,
            satellites: summary.satellites || 0,
            users: summary.users || 0
          }));
          if (data.agent_error) ui.notify.warn(data.agent_error);
          importText.value = '';
          await load();
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        } finally {
          ui.busyButton(importButton, false);
        }
      }
    });

    container.appendChild(ui.card({
      title: t('settings.section_backup'),
      content: [
        ui.div('col', [
          ui.alert('info', null, t('settings.export_hint')),
          ui.div('row', [exportButton]),
          exportOutput
        ]),
        ui.div('col', [
          ui.alert('warn', null, t('settings.import_hint')),
          ui.field(t('settings.import'), importText),
          ui.div('row', [importUsers, ui.div('grow'), importButton])
        ])
      ]
    }));
  }

  /* ---------------------------------------------------------------- contraseña */

  function renderPassword() {
    const current = ui.input({ type: 'password', autocomplete: 'current-password' });
    const next = ui.input({ type: 'password', autocomplete: 'new-password' });
    const repeat = ui.input({ type: 'password', autocomplete: 'new-password' });
    const change = ui.button(t('settings.password_change'), {
      variant: 'primary',
      icon: 'shield',
      onClick: async () => {
        if (next.value !== repeat.value) {
          ui.notify.error(t('settings.password_mismatch'));
          return;
        }
        ui.busyButton(change, true);
        try {
          await api.post('/api/auth/password', { current: current.value, password: next.value });
          ui.notify.ok(t('settings.password_changed'));
          current.value = '';
          next.value = '';
          repeat.value = '';
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        } finally {
          ui.busyButton(change, false);
        }
      }
    });
    container.appendChild(ui.card({
      title: t('settings.section_password'),
      content: [
        ui.div('grid-3', [
          ui.field(t('settings.password_current'), current),
          ui.field(t('settings.password_new'), next, t('users.field_password_hint')),
          ui.field(t('settings.password_repeat'), repeat)
        ]),
        ui.div('row row--end', [change])
      ]
    }));
  }

  /* ------------------------------------------------------------------ sesiones */

  function renderSessions() {
    const table = ui.div('card__body card__body--flush');
    const purge = ui.button(t('settings.purge_sessions'), {
      size: 'sm',
      icon: 'trash',
      onClick: async () => {
        try {
          const data = await api.post('/api/auth/sessions/purge', {});
          ui.notify.ok(t('settings.purge_done', { n: data.removed || 0 }));
          refreshSessions(table);
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        }
      }
    });
    container.appendChild(ui.card({
      title: t('settings.sessions_list'),
      flush: true,
      content: [table],
      tools: [purge]
    }));
    refreshSessions(table);
  }

  async function refreshSessions(table) {
    ui.clear(table);
    table.appendChild(ui.div('card__body', [ui.spinner('sm')]));
    try {
      const data = await api.get('/api/auth/sessions');
      const sessions = data.sessions || [];
      ui.clear(table);
      table.appendChild(ui.dataTable({
        compact: true,
        rows: sessions,
        columns: [
          { label: t('audit.col_user'), render: (row) => row.username || '—' },
          { label: t('audit.col_ip'), render: (row) => ui.el('span', { class: 'mono small', text: row.ip || '—' }) },
          { label: t('settings.session_created'), render: (row) => fmt.formatDate(row.created) },
          { label: t('settings.session_seen'), render: (row) => fmt.formatAgo(row.last_seen) },
          { label: t('users.col_expires'), render: (row) => fmt.formatDate(row.expires) }
        ],
        empty: t('common.empty')
      }));
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  /* ------------------------------------------------------------------ montaje */

  container.appendChild(serviceCard);
  renderBackup();
  renderPassword();
  renderSessions();
  load();
  container.appendChild(camCard);
  renderCam();

  return { destroy() {} };
}

/* ==========================================================================
   Campos reutilizables (ligados por referencia al modelo)
   ========================================================================== */

function textField(target, key) {
  const control = ui.input({ value: target[key] === undefined || target[key] === null ? '' : String(target[key]) });
  control.addEventListener('input', () => { target[key] = control.value; });
  return control;
}

function numberField(target, key, minimum, maximum) {
  const control = ui.input({
    type: 'number', min: minimum, max: maximum,
    value: target[key] === undefined || target[key] === null ? '' : String(target[key])
  });
  control.addEventListener('input', () => { target[key] = fmt.toInt(control.value, minimum || 0); });
  return control;
}

function nestedTextField(target, section, key) {
  target[section] = target[section] || {};
  const control = ui.input({
    value: target[section][key] === undefined || target[section][key] === null ? '' : String(target[section][key])
  });
  control.addEventListener('input', () => { target[section][key] = control.value; });
  return control;
}

function nestedNumberField(target, section, key, minimum, maximum) {
  target[section] = target[section] || {};
  const control = ui.input({
    type: 'number', min: minimum, max: maximum,
    value: target[section][key] === undefined || target[section][key] === null ? '' : String(target[section][key])
  });
  control.addEventListener('input', () => { target[section][key] = fmt.toInt(control.value, minimum || 0); });
  return control;
}
