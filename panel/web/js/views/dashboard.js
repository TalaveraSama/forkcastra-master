/**
 * views/dashboard.js — panel de control en vivo.
 *
 * Consume:
 *   GET  /api/system/status            (tarjetas del host, astra, panel y canales)
 *   GET  /api/system/history?minutes=N (sparkline del histórico)
 *   GET  /api/channels                 (tabla de canales con estado)
 *   POST /api/service/restart-astra    (reinicio de astra, si está disponible)
 *   SSE  /api/events  (evento `status`) para actualizar sin recargar
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

const HISTORY_MINUTES = 60;
const REFRESH_MS = 30000;

/** Colores de las series (coinciden con los tokens del tema). */
const COLORS = {
  bitrate: '#38bdf8',
  viewers: '#22c55e',
  cpu: '#f59e0b',
  mem: '#a78bfa'
};

export function render(container, ctx) {
  const { api, state, subscribe } = ctx;
  const disposes = [];
  let status = null;
  let samples = [];

  ctx.setTitle(
    t('nav.dashboard'),
    state.user ? `${state.user.display_name || state.user.username} · ${t(`users.role_${state.user.role}`)}` : ''
  );

  /* ------------------------------------------------------------ contenedores */
  /* ------------------------------------------- puesta en marcha (simple) */
  // Un solo botón: libera el sintonizador, escanea los transponders del satélite y
  // registra todos los canales con el softcam elegido.
  const guiaEstado = ui.div('hint');
  const guiaSoftcam = ui.select([{ value: '', label: t('simple.softcam_default') }], {});
  const guiaBoton = ui.button(t('simple.go'), { variant: 'primary', icon: 'check',
    onClick: () => lanzarGuia() });

  (async () => {
    try {
      const datos = await api.get('/api/softcams');
      guiaSoftcam.innerHTML = '';
      guiaSoftcam.appendChild(new Option(t('simple.softcam_default'), ''));
      guiaSoftcam.appendChild(new Option(t('simple.softcam_none'), '__none'));
      for (const perfil of (datos.softcams || [])) {
        guiaSoftcam.appendChild(new Option(perfil.name, perfil.id));
      }
    } catch (error) { /* sin softcams guardados: se usa el de Ajustes */ }
  })();

  async function lanzarGuia() {
    guiaBoton.disabled = true;
    try {
      guiaEstado.textContent = t('simple.step_disable');
      const canales = (await api.get('/api/channels')).channels || [];
      for (const ch of canales) {
        const detalle = (await api.get(`/api/channels/${encodeURIComponent(ch.id)}`)).channel || {};
        await api.put(`/api/channels/${encodeURIComponent(ch.id)}`, {
          name: detalle.name, enabled: false, inputs: detalle.inputs || [],
          outputs: detalle.outputs || [], group: detalle.group, tags: detalle.tags || []
        });
      }
      guiaEstado.textContent = t('simple.step_scan');
      const sat = ((await api.get('/api/dvb/satellites')).satellites || [])[0];
      if (!sat) { guiaEstado.textContent = t('simple.no_satellite'); return; }
      const tps = (await api.get('/api/dvb/transponders?satellite_id=' + encodeURIComponent(sat.id))).transponders || [];
      let total = 0;
      for (const tp of tps) {
        const inicio = await api.post('/api/dvb/scan', {
          adapter: 0, device: 0, duration: 16, transponders: [tp]
        });
        let scan = {};
        for (let i = 0; i < 40; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 3000));
          scan = (await api.get(`/api/dvb/scan/${encodeURIComponent(inicio.scan_id)}`)).scan || {};
          if (scan.state === 'done' || scan.state === 'stopped') break;
        }
        const servicios = (scan.services || []).length ? scan.services
          : ((scan.results || []).reduce((acc, r) => acc.concat(r.services || []), []));
        if (!servicios.length) continue;
        for (const s of servicios) {
          s.transponder = Object.assign({ type: 'dvb', delivery: 'S2', frequency: tp.frequency,
            polarization: tp.polarization, symbolrate: tp.symbolrate }, s.transponder || {});
        }
        const alta = await api.post('/api/dvb/scan/apply', {
          services: servicios, adapter: 0, device: 0,
          group: (sat.name || 'DVB') + ' ' + tp.frequency,
          softcam_id: (guiaSoftcam.value === '__none' ? '' : guiaSoftcam.value)
        });
        total += (alta.created || 0);
      }
      guiaEstado.textContent = t('simple.done') + ': ' + total + ' ' + t('simple.channels');
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    } finally {
      guiaBoton.disabled = false;
    }
  }

  container.appendChild(ui.card({
    title: t('simple.title'),
    subtitle: t('simple.subtitle'),
    content: [ui.div('hint', t('simple.explain')),
              ui.field(t('simple.softcam'), guiaSoftcam), guiaBoton, guiaEstado]
  }));


  const statsHost = ui.div('stats');
  const statsStream = ui.div('stats');
  const statsPanel = ui.div('stats');

  const sparkStreams = ui.sparkline({
    label: t('dashboard.history', { n: HISTORY_MINUTES }),
    emptyText: t('dashboard.no_samples'),
    maxLabel: (value) => fmt.formatBitrateShort(value),
    xLabel: `-${HISTORY_MINUTES} min`,
    height: 148,
    series: [
      { name: t('dashboard.series_bitrate'), color: COLORS.bitrate, values: [], fill: 'rgba(56,189,248,0.12)' },
      { name: t('dashboard.series_viewers'), color: COLORS.viewers, values: [], width: 1.5 }
    ]
  });

  const sparkHost = ui.sparkline({
    label: t('dashboard.history', { n: HISTORY_MINUTES }),
    emptyText: t('dashboard.no_samples'),
    maxLabel: (value) => `${Math.round(value)} %`,
    xLabel: `-${HISTORY_MINUTES} min`,
    height: 140,
    series: [
      { name: t('dashboard.series_cpu'), color: COLORS.cpu, values: [] },
      { name: t('dashboard.series_mem'), color: COLORS.mem, values: [] }
    ]
  });

  const restartButton = ui.button(t('dashboard.restart_astra'), {
    variant: 'danger',
    icon: 'restart',
    onClick: () => restartAstra()
  });
  restartButton.hidden = !state.serviceControl;

  const channelsHolder = ui.div('card__body card__body--flush');

  /* ----------------------------------------------------------------- dibujo */

  function renderStatus() {
    if (!status) return;
    const host = status.host || {};
    const astra = status.astra || {};
    const panel = status.panel || {};
    const agent = status.agent || { ok: false };
    const mem = host.mem || {};
    const disk = host.disk || {};
    const net = host.net || {};
    const cpu = host.cpu || {};

    ui.clear(statsHost);
    statsHost.appendChild(ui.usageCard(
      t('dashboard.cpu'),
      cpu.usage || 0,
      `${t('dashboard.model')}: ${cpu.model || '—'} · ${t('dashboard.cores', { n: cpu.count || 0 })}`
    ));
    statsHost.appendChild(ui.statCard({
      label: t('dashboard.mem'),
      value: `${fmt.formatNumber(mem.percent || 0)} %`,
      meter: mem.percent || 0,
      meta: `${fmt.formatBytes(mem.used || 0)} / ${fmt.formatBytes(mem.total || 0)}`
        + (mem.swap_total ? ` · ${t('dashboard.swap')}: ${fmt.formatBytes(mem.swap_used || 0)}` : '')
    }));
    statsHost.appendChild(ui.statCard({
      label: `${t('dashboard.net')} (${net.interface || '—'})`,
      value: fmt.formatRate(net.rx_rate || 0),
      small: true,
      meta: `${t('dashboard.net_rx')}: ${fmt.formatBytes(net.rx || 0)} · ${t('dashboard.net_tx')}: ${fmt.formatBytes(net.tx || 0)}`
    }));
    statsHost.appendChild(ui.statCard({
      label: t('dashboard.disk'),
      value: `${fmt.formatNumber(disk.percent || 0)} %`,
      meter: disk.percent || 0,
      meta: `${t('common.used')} ${fmt.formatBytes(disk.used || 0)} · ${t('common.free')} ${fmt.formatBytes(disk.free || 0)}`
    }));
    statsHost.appendChild(ui.statCard({
      label: t('dashboard.host_uptime'),
      value: fmt.formatDuration(host.uptime, { short: true }),
      small: true,
      meta: `${t('dashboard.load')}: ${(host.load || []).join(' · ') || '—'}`
    }));
    statsHost.appendChild(ui.statCard({
      label: t('dashboard.os'),
      value: (host.os && host.os.name) || '—',
      small: true,
      meta: `${(host.os && host.os.kernel) || ''} ${(host.os && host.os.arch) || ''}`
    }));

    ui.clear(statsStream);
    const running = astra.channels_running || 0;
    const total = astra.channels_total || 0;
    statsStream.appendChild(ui.statCard({
      label: t('dashboard.bitrate_total'),
      value: fmt.formatBitrate(astra.bitrate_total || 0),
      meta: astra.config_error ? `${t('common.warning')}: ${astra.config_error}` : t('dashboard.live')
    }));
    statsStream.appendChild(ui.statCard({
      label: t('dashboard.viewers'),
      value: fmt.formatNumber(astra.viewers || 0),
      meta: `${t('dashboard.channels')}: ${fmt.formatNumber(total)}`
    }));
    statsStream.appendChild(ui.statCard({
      label: t('dashboard.channels_running'),
      value: `${fmt.formatNumber(running)} / ${fmt.formatNumber(total)}`,
      meter: total ? Math.round((running * 100) / total) : 0,
      meta: astra.version ? t('dashboard.astra_bin', { version: astra.version }) : ''
    }));
    statsStream.appendChild(ui.statCard({
      label: t('dashboard.astra_uptime'),
      value: fmt.formatDuration(astra.uptime || 0, { short: true }),
      small: true,
      meta: astra.pid ? `PID ${astra.pid} · RSS ${fmt.formatBytes((astra.rss_kb || 0) * 1024)}` : ''
    }));

    ui.clear(statsPanel);
    statsPanel.appendChild(ui.statCard({
      label: t('dashboard.agent'),
      value: agent.ok ? t('dashboard.agent_ok') : t('common.agent_down'),
      small: true,
      variant: agent.ok ? 'ok' : 'danger',
      meta: agent.ok ? t('dashboard.agent_latency', { ms: agent.latency_ms || 0 }) : (agent.error || '')
    }));
    statsPanel.appendChild(ui.statCard({
      label: t('dashboard.panel'),
      value: panel.version || state.version || '—',
      small: true,
      meta: `${t('dashboard.panel_uptime')}: ${fmt.formatDuration(panel.uptime || 0, { short: true })}`
    }));
    statsPanel.appendChild(ui.statCard({
      label: t('dashboard.sessions'),
      value: fmt.formatNumber(panel.sessions || 0),
      small: true,
      meta: `${t('dashboard.users')}: ${fmt.formatNumber(panel.users || 0)}`
    }));
    statsPanel.appendChild(ui.statCard({
      label: t('dashboard.channels'),
      value: fmt.formatNumber(panel.channels || 0),
      small: true,
      meta: panel.service_control ? t('settings.section_service') : t('common.not_available')
    }));

    restartButton.hidden = !(state.serviceControl && panel.service_control);
  }

  function renderChannels() {
    const rows = (state.channels || []).slice();
    ui.clear(channelsHolder);
    if (!rows.length) {
      channelsHolder.appendChild(ui.emptyState(t('dashboard.no_channels')));
      return;
    }
    rows.sort((a, b) => {
      if (!!b.on_air !== !!a.on_air) return (b.on_air ? 1 : 0) - (a.on_air ? 1 : 0);
      return String(a.name || '').localeCompare(String(b.name || ''));
    });
    channelsHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row) => row.id,
      rows,
      columns: [
        {
          label: t('channels.col_channel'),
          render: (row) => ui.div('chan-name', [channelLogo(row), chanMeta(row)])
        },
        {
          label: t('channels.col_state'),
          render: (row) => ui.badge(
            row.on_air
              ? t('dashboard.on_air')
              : (row.running ? t('channels.state_running') : t('channels.state_stopped')),
            row.on_air ? 'ok' : (row.running ? 'info' : 'off'),
            { dot: row.on_air ? 'ok' : 'off' }
          )
        },
        { label: t('channels.col_bitrate'), className: 'num', render: (row) => fmt.formatBitrate(row.bitrate || 0) },
        { label: t('channels.col_viewers'), className: 'num', render: (row) => fmt.formatNumber(row.viewers || 0) },
        {
          label: t('channels.col_errors'),
          className: 'num small',
          render: (row) => {
            const errors = row.errors || {};
            const total = (errors.cc || 0) + (errors.sc || 0) + (errors.pes || 0);
            return total ? ui.badge(String(total), 'warn') : ui.el('span', { class: 'muted', text: '0' });
          }
        },
        {
          label: t('common.actions'),
          className: 'actions',
          render: (row) => {
            // Menú «⋯» como en Astra: detalles nativos, sin dependencias.
            const idCanal = encodeURIComponent(row.id);
            const caja = ui.el('details', { class: 'dropdown' });
            const resumen = ui.el('summary');
            resumen.appendChild(ui.el('i', { class: 'button icon icon-more small' }));
            caja.appendChild(resumen);
            const menu = ui.div('menu');
            const item = (etiqueta, fn, clase) => {
              const enlace = ui.el('a', { class: 'item' + (clase ? ' ' + clase : ''), href: '#', text: etiqueta });
              enlace.addEventListener('click', (evento) => {
                evento.preventDefault();
                caja.open = false;
                fn();
              });
              menu.appendChild(enlace);
            };
            const detalle = async () => (await api.get(`/api/channels/${idCanal}`)).channel || {};
            item(t('common.edit'), () => { window.location.hash = `#/channels/${encodeURIComponent(row.id)}`; });
            item(t('dashboard.act_restart'), async () => {
              try { await api.post(`/api/channels/${idCanal}/action`, { action: 'restart' }); } catch (error) { ui.showApiError(error, t('common.server_error')); }
            });
            item(row.enabled === false ? t('dashboard.act_enable') : t('dashboard.act_disable'), async () => {
              try {
                const canal = await detalle();
                await api.put(`/api/channels/${idCanal}`, {
                  name: canal.name, enabled: canal.enabled === false, inputs: canal.inputs || [],
                  outputs: canal.outputs || [], group: canal.group, tags: canal.tags || []
                });
                load();
              } catch (error) { ui.showApiError(error, t('common.server_error')); }
            });
            menu.appendChild(ui.el('hr'));
            item(t('dashboard.act_astra_restart'), async () => {
              try { await api.post('/api/service/restart-astra', {}); } catch (error) { ui.showApiError(error, t('common.server_error')); }
            });
            item(t('common.delete'), async () => {
              if (!window.confirm(t('dashboard.act_confirm_delete') + ' ' + (row.name || row.id))) return;
              try { await api.delete(`/api/channels/${idCanal}`); load(); } catch (error) { ui.showApiError(error, t('common.server_error')); }
            }, 'text-alert');
            caja.appendChild(menu);
            return caja;
          }
        }
      ]
    }));
    const tablaCanales = channelsHolder.querySelector('table');
    if (tablaCanales) tablaCanales.classList.add('table--tiles');
  }

  function chanMeta(row) {
    return ui.div('chan-meta', [
      ui.el('span', { class: 'chan-meta__name', text: row.name || row.id }),
      ui.el('span', { class: 'small muted', text: row.group || '' })
    ]);
  }

  function channelLogo(row) {
    if (row.logo && fmt.isSafeImageUrl(row.logo)) {
      const img = ui.el('img', { class: 'chan-logo', src: row.logo, alt: '', loading: 'lazy' });
      img.addEventListener('error', () => img.replaceWith(placeholderLogo(row)));
      return img;
    }
    return placeholderLogo(row);
  }

  function placeholderLogo(row) {
    const letter = String(row.name || row.id || '?').trim().charAt(0).toUpperCase() || '?';
    return ui.el('span', { class: 'chan-logo chan-logo--ph', 'aria-hidden': 'true', text: letter });
  }

  /* -------------------------------------------------------------- histórico */

  function drawSparklines() {
    sparkStreams.series[0].values = samples.map((sample) => fmt.num(sample.bitrate, 0));
    sparkStreams.series[1].values = samples.map((sample) => fmt.num(sample.viewers, 0));
    sparkHost.series[0].values = samples.map((sample) => fmt.num(sample.cpu, 0));
    sparkHost.series[1].values = samples.map((sample) => fmt.num(sample.mem, 0));
    sparkStreams.draw();
    sparkHost.draw();
  }

  async function loadHistory() {
    try {
      const data = await api.get(`/api/system/history?minutes=${HISTORY_MINUTES}`);
      samples = data.samples || [];
      drawSparklines();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('dashboard.no_samples'));
    }
  }

  async function loadStatus() {
    try {
      const data = await api.get('/api/system/status');
      status = data;
      state.channels = data.channels || state.channels;
      state.astra = data.astra || state.astra;
      // La cabecera y el aviso de "agente caído" los gobierna la aplicación.
      if (data.agent && typeof ctx.setAgent === 'function') ctx.setAgent(data.agent);
      renderStatus();
      renderChannels();
      ctx.refreshChrome();
    } catch (error) {
      if (error.status === 401) return;
      if (error.status === 502) ui.notify.warn(error.message);
      else ui.showApiError(error, t('common.server_error'));
    }
  }

  async function restartAstra() {
    const yes = await ui.confirmDialog({
      title: t('dashboard.restart_astra'),
      message: t('dashboard.restart_confirm'),
      acceptLabel: t('dashboard.restart_astra'),
      danger: true
    });
    if (!yes) return;
    ui.busyButton(restartButton, true);
    try {
      const data = await api.post('/api/service/restart-astra');
      ui.notify.ok(data.output ? `${t('dashboard.restart_done')}: ${data.output}` : t('dashboard.restart_done'));
      setTimeout(loadStatus, 2500);
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    } finally {
      ui.busyButton(restartButton, false);
    }
  }

  /* ---------------------------------------------------------------- montaje */

  const hostCard = ui.card({
    title: t('dashboard.os'),
    subtitle: state.hostname || '',
    content: [statsHost, sparkHost],
    tools: [ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => loadStatus() })]
  });

  const streamCard = ui.card({
    title: `${t('dashboard.bitrate_total')} · ${t('dashboard.agent')}`,
    subtitle: t('dashboard.live'),
    content: [statsStream, statsPanel]
  });

  const historyCard = ui.card({
    title: t('dashboard.history', { n: HISTORY_MINUTES }),
    subtitle: t('dashboard.series_bitrate'),
    content: [sparkStreams],
    tools: [
      ui.badge(t('dashboard.live'), 'brand', { dot: 'ok' }),
      ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => loadHistory() })
    ]
  });

  const channelsCard = ui.card({
    title: t('dashboard.channels'),
    flush: true,
    content: [channelsHolder],
    tools: [
      ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => loadStatus() }),
      restartButton
    ]
  });

  container.appendChild(hostCard);
  container.appendChild(streamCard);
  container.appendChild(historyCard);
  container.appendChild(channelsCard);

  /* ----------------------------------------------------------- suscripciones */

  disposes.push(subscribe(['status'], (payload) => {
    if (!payload) return;
    status = status || {};
    if (payload.host) status.host = { ...(status.host || {}), ...payload.host };
    status.astra = payload.astra || status.astra;
    status.agent = payload.agent || status.agent;
    renderStatus();
    renderChannels();
  }));

  const poll = setInterval(() => {
    if (document.hidden) return;
    loadStatus();
    loadHistory();
  }, REFRESH_MS);

  loadStatus();
  loadHistory();

  return {
    destroy() {
      disposes.forEach((off) => off());
      clearInterval(poll);
      if (sparkStreams.destroyObserver) sparkStreams.destroyObserver();
      if (sparkHost.destroyObserver) sparkHost.destroyObserver();
    }
  };
}
