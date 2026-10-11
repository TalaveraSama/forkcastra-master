/**
 * views/dvb.js — adaptadores, satélites/transponders y asistente de escaneo.
 *
 * Consume:
 *   GET    /api/dvb/adapters                                  (proxy del agente)
 *   GET    /api/dvb/satellites | /api/dvb/transponders
 *   POST   /api/dvb/satellites | PUT/DELETE /api/dvb/satellites/{id}
 *   POST   /api/dvb/satellites/{id}/transponders
 *   DELETE /api/dvb/satellites/{id}/transponders/{tpId}
 *   POST   /api/dvb/scan            → {scan_id}
 *   GET    /api/dvb/scan/{id}       (polling cada 1,5 s)
 *   POST   /api/dvb/scan/{id}/stop
 *   POST   /api/dvb/scan/apply      (crea canales desde los servicios marcados)
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';
import { announceAgentResult } from './channels.js';

const SCAN_POLL_MS = 1500;
const UHF_MIN = 21;
const UHF_MAX = 69;

export function render(container, ctx, params) {
  const initialTab = (params && params[0]) || 'adapters';
  let activeTab = ['adapters', 'satellites', 'scan'].includes(initialTab) ? initialTab : 'adapters';

  ctx.setTitle(t('nav.dvb'), t('dvb.tab_satellites'));

  const panel = ui.div('stack');
  container.appendChild(panel);

  const cleanups = [];

  function draw() {
    cleanups.splice(0).forEach((fn) => {
      try { fn(); } catch (error) { /* ignorado */ }
    });
    ui.clear(panel);
    panel.appendChild(ui.tabs([
      { key: 'adapters', label: t('dvb.tab_adapters') },
      { key: 'satellites', label: t('dvb.tab_satellites') },
      { key: 'scan', label: t('dvb.tab_scan') }
    ], activeTab, (key) => {
      activeTab = key;
      window.location.hash = `#/dvb/${key}`;
      draw();
    }));

    if (activeTab === 'adapters') cleanups.push(renderAdapters(panel, ctx));
    else if (activeTab === 'satellites') cleanups.push(renderSatellites(panel, ctx));
    else cleanups.push(renderScan(panel, ctx));
  }

  draw();

  return {
    destroy() {
      cleanups.splice(0).forEach((fn) => {
        try { fn(); } catch (error) { /* ignorado */ }
      });
    }
  };
}

/* ==========================================================================
   Adaptadores
   ========================================================================== */

function renderAdapters(container, ctx) {
  const holder = ui.div('stats');
  const assignedHolder = ui.div('card__body card__body--flush');
  const notice = ui.div('col');

  container.appendChild(notice);
  container.appendChild(ui.card({
    title: t('dvb.adapters'),
    content: [holder],
    tools: [ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() })]
  }));
  container.appendChild(ui.card({
    title: t('dvb.col_channels_assigned'),
    flush: true,
    content: [assignedHolder]
  }));

  async function load() {
    ui.clear(notice);
    ui.clear(holder);
    holder.appendChild(ui.el('div', { class: 'stat' }, [ui.spinner('sm')]));
    try {
      const data = await ctx.api.get('/api/dvb/adapters');
      ui.clear(holder);
      ui.clear(assignedHolder);

      const adapters = data.adapters || [];
      if (!adapters.length) {
        holder.appendChild(ui.emptyState(t('dvb.adapters_empty')));
      }
      const assigned = data.assigned || {};

      adapters.forEach((adapter) => {
        const key = `${adapter.adapter}:${adapter.device ?? 0}`;
        holder.appendChild(ui.statCard({
          label: t('dvb.adapter_n', { n: adapter.adapter }),
          value: adapter.frontend || t('common.unknown'),
          small: true,
          variant: adapter.busy ? 'warn' : 'ok',
          meta: [
            `#${key}`,
            adapter.mac ? `${t('dvb.mac')} ${adapter.mac}` : null,
            adapter.busy ? t('dvb.busy') : t('dvb.free'),
            assigned[key] ? t('dvb.assigned_channels', { n: assigned[key] }) : null,
            adapter.error || null
          ].filter(Boolean).join(' · ')
        }));
      });

      const rows = Object.keys(assigned).map((key) => ({ key, count: assigned[key] }));
      assignedHolder.appendChild(ui.dataTable({
        compact: true,
        rows,
        columns: [
          {
            label: `${t('channels.opt_adapter')}/${t('channels.opt_device')}`,
            render: (row) => ui.el('span', { class: 'mono', text: row.key })
          },
          {
            label: t('dvb.col_channels_assigned'),
            className: 'num',
            render: (row) => fmt.formatNumber(row.count)
          }
        ],
        empty: ui.emptyState(t('common.empty'))
      }));
    } catch (error) {
      ui.clear(holder);
      if (error.status === 502) {
        notice.appendChild(ui.alert('error', t('common.agent_down'), error.message));
        holder.appendChild(ui.emptyState(t('dvb.adapters_empty')));
      } else if (error.status !== 401) {
        ui.showApiError(error, t('common.server_error'));
      }
    }
  }

  load();
  return () => {};
}

/* ==========================================================================
   Satélites y transponders
   ========================================================================== */

function renderSatellites(container, ctx) {
  let satellites = [];
  let selectedId = null;
  let transponders = [];
  let assigned = {};
  let scans = [];

  const satList = ui.div('sat-list');
  const tpHolder = ui.div('card__body card__body--flush');
  const tpTitle = ui.el('h2', { class: 'card__title', text: t('dvb.transponders') });
  const scansHolder = ui.div('card__body card__body--flush');

  const newSatButton = ui.button(t('dvb.new_satellite'), {
    variant: 'primary', icon: 'plus', onClick: () => openSatelliteDialog(ctx, null, load)
  });
  const bulkButton = ui.button(t('dvb.tp_add'), { icon: 'plus', onClick: () => openTransponderDialog(ctx, selectedSatellite(), load) });

  container.appendChild(ui.card({
    title: t('dvb.satellites'),
    content: [satList],
    tools: [
      newSatButton,
      ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() })
    ]
  }));

  container.appendChild(ui.card({
    title: t('dvb.transponders'),
    flush: true,
    content: [tpHolder],
    tools: [bulkButton]
  }));

  container.appendChild(ui.card({
    title: t('dvb.history'),
    flush: true,
    content: [scansHolder]
  }));

  function selectedSatellite() {
    return satellites.find((satellite) => satellite.id === selectedId) || null;
  }

  function drawSatellites() {
    ui.clear(satList);
    if (!satellites.length) {
      satList.appendChild(ui.emptyState(t('dvb.satellites_empty')));
      return;
    }
    satellites.forEach((satellite) => {
      const item = ui.div(`sat-item${satellite.id === selectedId ? ' is-active' : ''}`, [
        ui.div('col', [
          ui.el('span', { class: 'sat-item__name', text: satellite.name || satellite.id }),
          ui.el('span', {
            class: 'sat-item__meta',
            text: [
              satellite.position || '',
              satellite.band || '',
              `LNB ${satellite.lnb ? `${satellite.lnb.lof1}/${satellite.lnb.lof2}/${satellite.lnb.slof}` : '—'}`,
              `${t('dvb.diseqc')} ${satellite.diseqc ?? 0}`,
              `${(satellite.transponders || []).length} TP`
            ].filter(Boolean).join(' · ')
          })
        ])
      ]);
      const choose = ui.button('›', {
        size: 'xs',
        onClick: () => {
          selectedId = satellite.id;
          drawAll();
        }
      });
      choose.setAttribute('aria-label', `${t('dvb.transponders')}: ${satellite.name || satellite.id}`);
      if (satellite.id === selectedId) choose.classList.add('btn--ok');
      const tools = ui.div('repeat__tools', [
        choose,
        ui.iconButton(t('common.edit'), {
          symbol: 'edit',
          onClick: () => openSatelliteDialog(ctx, satellite, load)
        }),
        ui.iconButton(t('common.delete'), {
          symbol: 'trash',
          onClick: async () => {
            const yes = await ui.confirmDialog({
              title: t('common.delete'),
              message: t('dvb.delete_sat_confirm', { name: satellite.name || satellite.id }),
              acceptLabel: t('common.delete'),
              danger: true
            });
            if (!yes) return;
            try {
              await ctx.api.delete(`/api/dvb/satellites/${encodeURIComponent(satellite.id)}`);
              ui.notify.ok(t('dvb.sat_deleted'));
              if (selectedId === satellite.id) selectedId = null;
              await load();
            } catch (error) {
              ui.showApiError(error, t('common.server_error'));
            }
          }
        })
      ]);
      item.appendChild(tools);
      satList.appendChild(item);
    });
  }

  function drawTransponders() {
    ui.clear(tpHolder);
    const satellite = selectedSatellite();
    if (!satellite) {
      tpHolder.appendChild(ui.emptyState(t('dvb.satellites_empty')));
      return;
    }
    if (!transponders.length) {
      tpHolder.appendChild(ui.emptyState(t('dvb.tp_empty')));
      return;
    }
    tpHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row) => row.id,
      rows: transponders,
      columns: [
        { label: t('dvb.col_delivery'), render: (row) => ui.badge(row.delivery || row.type || 'DVB', 'info') },
        { label: t('dvb.col_freq'), className: 'num', render: (row) => `${fmt.formatNumber(row.frequency || 0)}` },
        { label: t('dvb.col_pol'), render: (row) => row.polarization || '—' },
        { label: t('dvb.col_sr'), className: 'num', render: (row) => fmt.formatNumber(row.symbolrate || 0) },
        { label: t('channels.opt_adapter'), render: (row) => `${row.adapter ?? 0}/${row.device ?? 0}` },
        {
          label: t('dvb.col_channels_assigned'),
          className: 'num',
          render: (row) => fmt.formatNumber(assigned[`${row.adapter ?? 0}:${row.device ?? 0}`] || 0)
        },
        {
          label: t('common.actions'),
          className: 'actions',
          render: (row) => ui.iconButton(t('common.delete'), {
            symbol: 'trash',
            onClick: async () => {
              const yes = await ui.confirmDialog({
                title: t('common.delete'),
                message: t('dvb.tp_delete_confirm', { freq: row.frequency || '' }),
                acceptLabel: t('common.delete'),
                danger: true
              });
              if (!yes) return;
              try {
                await ctx.api.delete(`/api/dvb/satellites/${encodeURIComponent(satellite.id)}/transponders/${encodeURIComponent(row.id)}`);
                ui.notify.ok(t('dvb.tp_deleted'));
                await load();
              } catch (error) {
                ui.showApiError(error, t('common.server_error'));
              }
            }
          })
        }
      ]
    }));
  }

  function drawScans() {
    ui.clear(scansHolder);
    if (!scans.length) {
      scansHolder.appendChild(ui.emptyState(t('dvb.scan_none')));
      return;
    }
    scansHolder.appendChild(ui.dataTable({
      compact: true,
      rowKey: (row) => row.id,
      rows: scans.slice(0, 10),
      columns: [
        { label: t('common.id'), render: (row) => ui.el('span', { class: 'mono small', text: row.id }) },
        { label: t('audit.col_time'), render: (row) => fmt.formatDate(row.finished || row.started) },
        { label: t('common.state'), render: (row) => ui.badge(String(row.state || ''), row.state === 'done' ? 'ok' : 'info') },
        { label: t('dvb.scan_services_found'), className: 'num', render: (row) => fmt.formatNumber((row.services || []).length) },
        {
          label: t('common.actions'),
          className: 'actions',
          render: (row) => ui.button(t('common.show'), {
            size: 'xs',
            onClick: () => {
              showScanResults(ctx, row);
            }
          })
        }
      ]
    }));
  }

  function drawAll() {
    drawSatellites();
    drawTransponders();
    drawScans();
    bulkButton.disabled = !selectedId;
  }

  async function load() {
    try {
      const [satData, tpData] = await Promise.all([
        ctx.api.get('/api/dvb/satellites'),
        ctx.api.get('/api/dvb/transponders')
      ]);
      satellites = satData.satellites || [];
      scans = satData.scans || [];
      transponders = tpData.transponders || [];
      assigned = tpData.assigned || {};
      if (!selectedId && satellites.length) selectedId = satellites[0].id;
      if (selectedId) {
        transponders = transponders.filter((tp) => tp.satellite_id === selectedId);
      } else {
        transponders = [];
      }
      drawAll();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  load();
  return () => {};
}

/* ==========================================================================
   Asistente de escaneo
   ========================================================================== */

function renderScan(container, ctx) {
  const state = {
    scanId: null,
    scan: null,
    adapter: 0,
    device: 0,
    selection: new Set(),
    group: 'DVB',
    prefix: '',
    source: 'satellite'
  };
  let poll = null;

  const adaptersHolder = ui.div('stats');
  const formHolder = ui.div('card__body');
  const progressHolder = ui.div('col');
  const resultsHolder = ui.div('col');
  const selectionBadge = ui.badge('0', '');
  const errorBox = ui.div('form-error', null, { hidden: true });

  const startButton = ui.button(t('dvb.scan_start'), { variant: 'primary', icon: 'play', onClick: () => startScan() });
  const stopButton = ui.button(t('dvb.scan_stop'), { variant: 'danger', icon: 'stop', onClick: () => stopScan() });
  const applyButton = ui.button(t('dvb.scan_apply'), { variant: 'ok', icon: 'plus', onClick: () => applyScan() });
  stopButton.hidden = true;

  const toolbar = ui.div('row row--tight', [
    startButton,
    stopButton,
    ui.div('grow'),
    selectionBadge,
    applyButton
  ]);

  container.appendChild(ui.card({
    title: t('dvb.adapters'),
    content: [adaptersHolder],
    tools: [ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => loadAdapters() })]
  }));
  container.appendChild(ui.card({
    title: t('dvb.scan_title'),
    content: [errorBox, formHolder, toolbar]
  }));
  container.appendChild(ui.card({
    title: t('dvb.scan_progress'),
    content: [progressHolder]
  }));
  container.appendChild(ui.card({
    title: t('dvb.scan_results'),
    flush: true,
    content: [resultsHolder]
  }));

  /* --------------------------------------------------------------- formulario */

  const sourceSelect = ui.select([
    { value: 'satellite', label: t('dvb.scan_satellite') },
    { value: 'uhf', label: t('dvb.scan_uhf') },
    { value: 'manual', label: t('dvb.scan_manual') }
  ], { 'aria-label': t('dvb.scan_source') });
  const satelliteSelect = ui.select([], { 'aria-label': t('dvb.scan_satellite') });
  const firstChannel = ui.input({ type: 'number', min: UHF_MIN, max: UHF_MAX, value: String(UHF_MIN) });
  const lastChannel = ui.input({ type: 'number', min: UHF_MIN, max: UHF_MAX, value: String(UHF_MAX) });
  const manualText = ui.textarea({ placeholder: '11500 H 27500\n11700 V 27500', rows: 4 });
  const deliverySelect = ui.select(['S', 'S2', 'T', 'T2', 'C'].map((value) => ({
    value, label: value, selected: value === (ctx.state.enums ? 'S2' : 'S2')
  })), { 'aria-label': t('dvb.scan_delivery') });
  const durationInput = ui.input({ type: 'number', min: 3, max: 120, value: '8' });
  const adapterSelect = ui.select([], { 'aria-label': t('channels.opt_adapter') });

  const satelliteBlock = ui.field(t('dvb.scan_satellite'), satelliteSelect);
  const uhfBlock = ui.div('subgrid', [
    ui.field(t('dvb.scan_uhf_first'), firstChannel),
    ui.field(t('dvb.scan_uhf_last'), lastChannel)
  ]);
  const manualBlock = ui.field(t('dvb.scan_manual'), manualText, t('dvb.scan_manual_hint'));

  function refreshBlocks() {
    state.source = sourceSelect.value;
    satelliteBlock.hidden = state.source !== 'satellite';
    uhfBlock.hidden = state.source !== 'uhf';
    manualBlock.hidden = state.source !== 'manual';
    deliverySelect.parentElement.hidden = state.source === 'satellite';
  }

  sourceSelect.addEventListener('change', refreshBlocks);

  ui.clear(formHolder);
  formHolder.appendChild(ui.div('subgrid', [
    ui.field(t('dvb.scan_source'), sourceSelect),
    ui.field(t('channels.opt_adapter'), adapterSelect),
    ui.field(t('dvb.scan_duration'), durationInput, t('dvb.scan_duration_hint')),
    ui.field(t('dvb.scan_delivery'), deliverySelect)
  ]));
  formHolder.appendChild(satelliteBlock);
  formHolder.appendChild(uhfBlock);
  formHolder.appendChild(manualBlock);
  refreshBlocks();

  /* ------------------------------------------------------------- adaptadores */

  async function loadAdapters() {
    ui.clear(adaptersHolder);
    adaptersHolder.appendChild(ui.el('div', { class: 'stat' }, [ui.spinner('sm')]));
    try {
      const data = await ctx.api.get('/api/dvb/adapters');
      ui.clear(adaptersHolder);
      const adapters = data.adapters || [];
      if (!adapters.length) adaptersHolder.appendChild(ui.emptyState(t('dvb.adapters_empty')));
      adapters.forEach((adapter) => {
        adaptersHolder.appendChild(ui.statCard({
          label: t('dvb.adapter_n', { n: adapter.adapter }),
          value: adapter.frontend || t('common.unknown'),
          small: true,
          variant: adapter.busy ? 'warn' : 'ok',
          meta: `${adapter.mac || ''} · ${adapter.busy ? t('dvb.busy') : t('dvb.free')}`
        }));
      });
      ui.clear(adapterSelect);
      (adapters.length ? adapters : [{ adapter: 0, device: 0 }]).forEach((adapter) => {
        adapterSelect.appendChild(ui.el('option', {
          value: `${adapter.adapter}:${adapter.device ?? 0}`,
          text: `#${adapter.adapter}:${adapter.device ?? 0} ${adapter.frontend || ''}`
        }));
      });
    } catch (error) {
      ui.clear(adaptersHolder);
      if (error.status === 502) {
        adaptersHolder.appendChild(ui.alert('error', t('common.agent_down'), error.message));
        ui.clear(adapterSelect);
        adapterSelect.appendChild(ui.el('option', { value: '0:0', text: '#0:0' }));
      } else if (error.status !== 401) {
        ui.showApiError(error, t('common.server_error'));
      }
    }
  }

  async function loadSatellites() {
    try {
      const data = await ctx.api.get('/api/dvb/satellites');
      ui.clear(satelliteSelect);
      const satellites = data.satellites || [];
      if (!satellites.length) {
        satelliteSelect.appendChild(ui.el('option', { value: '', text: t('dvb.satellites_empty') }));
      }
      satellites.forEach((satellite) => {
        satelliteSelect.appendChild(ui.el('option', {
          value: satellite.id,
          text: `${satellite.name || satellite.id} (${(satellite.transponders || []).length} TP)`
        }));
      });
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  /* ----------------------------------------------------------------- arranque */

  function selectedAdapter() {
    const [adapter, device] = String(adapterSelect.value || '0:0').split(':');
    return { adapter: fmt.toInt(adapter, 0), device: fmt.toInt(device, 0) };
  }

  async function startScan() {
    errorBox.hidden = true;
    const payload = {
      ...selectedAdapter(),
      delivery: deliverySelect.value,
      duration: fmt.toInt(durationInput.value, 8)
    };
    if (state.source === 'satellite') {
      payload.satellite_id = satelliteSelect.value;
      if (!payload.satellite_id) {
        showScanError(t('dvb.scan_no_tps'));
        return;
      }
    } else if (state.source === 'uhf') {
      payload.band = 'uhf';
      payload.channel_first = fmt.toInt(firstChannel.value, UHF_MIN);
      payload.channel_last = fmt.toInt(lastChannel.value, UHF_MAX);
    } else {
      const parsed = fmt.parseTransponderLines(manualText.value);
      if (parsed.errors.length) {
        showScanError(parsed.errors.join(' · '));
        return;
      }
      if (!parsed.items.length) {
        showScanError(t('dvb.scan_no_tps'));
        return;
      }
      payload.transponders = parsed.items.map((item) => ({
        type: 'dvb',
        delivery: deliverySelect.value,
        adapter: payload.adapter,
        device: payload.device,
        frequency: item.frequency,
        polarization: item.polarization,
        symbolrate: item.symbolrate,
        lof1: 9750, lof2: 10600, slof: 11700, diseqc: 0
      }));
    }

    ui.busyButton(startButton, true);
    try {
      const data = await ctx.api.post('/api/dvb/scan', payload);
      state.scanId = data.scan_id;
      state.scan = null;
      state.selection.clear();
      updateSelectionBadge();
      stopButton.hidden = false;
      startButton.hidden = true;
      startPolling();
    } catch (error) {
      showScanError(error.message);
    } finally {
      ui.busyButton(startButton, false);
    }
  }

  async function stopScan() {
    if (!state.scanId) return;
    try {
      await ctx.api.post(`/api/dvb/scan/${encodeURIComponent(state.scanId)}/stop`);
      ui.notify.info(t('dvb.scan_stopped'));
      stopPolling();
      stopButton.hidden = true;
      startButton.hidden = false;
    } catch (error) {
      ui.showApiError(error, t('common.server_error'));
    }
  }

  function startPolling() {
    stopPolling();
    poll = setInterval(() => pollScan(), SCAN_POLL_MS);
    pollScan();
  }

  function stopPolling() {
    if (poll) {
      clearInterval(poll);
      poll = null;
    }
  }

  async function pollScan() {
    if (!state.scanId) return;
    try {
      const data = await ctx.api.get(`/api/dvb/scan/${encodeURIComponent(state.scanId)}`);
      state.scan = data.scan || {};
      drawProgress();
      drawResults();
      const scanState = state.scan.state;
      if (scanState === 'done' || scanState === 'stopped' || scanState === 'error') {
        stopPolling();
        stopButton.hidden = true;
        startButton.hidden = false;
        if (scanState === 'done') {
          ui.notify.ok(t('dvb.scan_finished', { n: (state.scan.services || []).length }));
        }
      }
    } catch (error) {
      stopPolling();
      stopButton.hidden = true;
      startButton.hidden = false;
      if (error.status === 502) ui.notify.warn(error.message);
      else if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  function showScanError(message) {
    ui.clear(errorBox);
    errorBox.appendChild(ui.el('strong', { text: message }));
    errorBox.hidden = false;
  }

  function drawProgress() {
    const scan = state.scan || {};
    ui.clear(progressHolder);
    const total = fmt.num(scan.total, 0) || 1;
    const index = Math.max(0, fmt.num(scan.index, 0) - (scan.state === 'running' ? 1 : 0));
    const percent = Math.min(100, Math.round((index / total) * 100));
    progressHolder.appendChild(ui.div('row', [
      ui.badge(String(scan.state || 'idle'), scan.state === 'running' ? 'info' : (scan.state === 'done' ? 'ok' : 'off'), {
        dot: scan.state === 'running' ? 'ok' : 'off'
      }),
      ui.el('span', { class: 'small muted', text: `${fmt.formatNumber(scan.index || 0)} ${t('common.of')} ${fmt.formatNumber(scan.total || 0)}` }),
      ui.el('span', { class: 'grow' }),
      scan.bitrate ? ui.badge(fmt.formatBitrate(scan.bitrate), 'info') : null
    ]));
    progressHolder.appendChild(ui.div('progress', [
      ui.el('div', { class: 'progress__fill', style: `width:${percent}%` })
    ]));
    if (scan.current) {
      progressHolder.appendChild(ui.keyValues([
        [t('dvb.scan_current'), `${scan.current.frequency || '?'} MHz ${scan.current.polarization || ''}`],
        [t('dvb.col_sr'), scan.current.symbolrate || '—'],
        [t('common.type'), scan.current.type || '—'],
        [t('dvb.scan_services_found'), (scan.services || []).length]
      ]));
    }
    if (scan.error) progressHolder.appendChild(ui.alert('warn', null, scan.error));
  }

  function drawResults() {
    const scan = state.scan || {};
    const results = scan.results || [];
    ui.clear(resultsHolder);
    if (!results.length) {
      resultsHolder.appendChild(ui.emptyState(t('dvb.scan_none')));
      return;
    }
    const box = ui.div('card__body result-tp');
    results.forEach((result) => box.appendChild(resultBlock(result)));
    resultsHolder.appendChild(box);
  }

  function resultBlock(result) {
    const services = result.services || [];
    const key = `${result.frequency}-${result.polarization}-${result.index}`;
    const allSelected = services.length > 0 && services.every((service) => state.selection.has(serviceKey(result, service)));
    const head = ui.div('result-tp__head', [
      ui.checkbox('', allSelected, { 'aria-label': t('common.select_all') }),
      ui.el('strong', {
        text: t('dvb.scan_tp_header', {
          index: result.index ?? '',
          freq: fmt.formatNumber(result.frequency || 0),
          pol: result.polarization || '',
          sr: fmt.formatNumber(result.symbolrate || 0),
          tsid: result.tsid || '—'
        })
      }),
      ui.badge(t('dvb.scan_tp_bitrate', { value: fmt.formatBitrate(result.bitrate || 0) }), 'info'),
      ui.badge(t('dvb.scan_services_found') + `: ${services.length}`, services.length ? 'ok' : 'off'),
      result.error ? ui.badge(result.error, 'danger') : null
    ]);
    const selectAll = head.querySelector('input');
    if (selectAll) {
      selectAll.addEventListener('change', () => {
        services.forEach((service) => {
          const id = serviceKey(result, service);
          if (selectAll.checked) state.selection.add(id);
          else state.selection.delete(id);
        });
        drawResults();
        updateSelectionBadge();
      });
    }

    const list = ui.dataTable({
      compact: true,
      rowKey: (service) => serviceKey(result, service),
      rows: services,
      columns: [
        {
          label: '',
          render: (service) => {
            const box = ui.checkbox('', state.selection.has(serviceKey(result, service)), {
              'aria-label': service.name || String(service.pnr)
            });
            box.input.addEventListener('change', () => {
              if (box.input.checked) state.selection.add(serviceKey(result, service));
              else state.selection.delete(serviceKey(result, service));
              updateSelectionBadge();
            });
            return box;
          }
        },
        { label: t('dvb.svc_pnr'), render: (service) => String(service.pnr ?? '') },
        { label: t('dvb.svc_name'), render: (service) => service.name || `SID ${service.sid}` },
        { label: t('dvb.svc_provider'), render: (service) => service.provider || '—' },
        {
          label: t('dvb.svc_scrambled'),
          render: (service) => ui.badge(
            service.scrambled ? t('psi.scrambled_yes') : t('psi.scrambled_no'),
            service.scrambled ? 'warn' : 'ok'
          )
        },
        { label: t('dvb.svc_video'), className: 'num', render: (service) => String(service.video_pid ?? '') },
        { label: t('dvb.svc_audio'), className: 'num', render: (service) => String(service.audio_pid ?? '') }
      ],
      empty: t('dvb.scan_no_results')
    });

    return ui.div('repeat__item', [head, list]);
  }

  function serviceKey(result, service) {
    return `${result.index}-${service.sid}-${service.pnr}`;
  }

  function selectedServices() {
    const scan = state.scan || {};
    const out = [];
    (scan.results || []).forEach((result) => {
      (result.services || []).forEach((service) => {
        if (state.selection.has(serviceKey(result, service))) out.push(service);
      });
    });
    if (!out.length && state.selection.size) {
      // Fallback: los servicios acumulados en `scan.services` sin agrupar.
      (scan.services || []).forEach((service, index) => {
        if (state.selection.has(`flat-${index}`)) out.push(service);
      });
    }
    return out;
  }

  function updateSelectionBadge() {
    ui.clear(selectionBadge);
    selectionBadge.textContent = String(state.selection.size);
    selectionBadge.title = t('common.selected_n', { n: state.selection.size });
    applyButton.disabled = state.selection.size === 0;
  }

  async function applyScan() {
    const services = selectedServices();
    if (!services.length) {
      ui.notify.warn(t('dvb.scan_apply_none'));
      return;
    }
    const adapter = selectedAdapter();
    const screen = ui.openModal({
      title: t('dvb.scan_apply'),
      size: 'sm',
      content: [
        ui.field(t('dvb.scan_apply_group'), ui.input({
          value: state.group,
          on: { input: (event) => { state.group = event.target.value; } }
        })),
        ui.field(t('dvb.scan_apply_prefix'), ui.input({
          value: state.prefix,
          on: { input: (event) => { state.prefix = event.target.value; } }
        })),
        ui.el('p', { class: 'small muted', text: t('dvb.scan_apply_n', { n: services.length }) })
      ],
      footer: []
    });
    const cancel = ui.button(t('common.cancel'), { onClick: () => screen.close() });
    const confirm = ui.button(t('dvb.scan_apply_n', { n: services.length }), {
      variant: 'ok',
      icon: 'plus',
      onClick: async () => {
        ui.busyButton(confirm, true);
        try {
          const data = await ctx.api.post('/api/dvb/scan/apply', {
            services,
            adapter: adapter.adapter,
            device: adapter.device,
            group: state.group || 'DVB',
            name_prefix: state.prefix || '',
            delivery: deliverySelect.value
          });
          ui.notify.ok(t('dvb.scan_apply_done', { n: data.created || 0 }));
          announceAgentResult(ctx, data);
          if (data.errors && data.errors.length) ui.notify.warn(data.errors.slice(0, 3).join(' · '));
          screen.close();
          state.selection.clear();
          updateSelectionBadge();
        } catch (error) {
          ui.showApiError(error, t('common.server_error'));
        } finally {
          ui.busyButton(confirm, false);
        }
      }
    });
    const foot = screen.modal.querySelector('.modal__foot');
    foot.appendChild(ui.div('grow'));
    foot.appendChild(cancel);
    foot.appendChild(confirm);
  }

  /* ------------------------------------------------------------------ montaje */

  updateSelectionBadge();
  applyButton.disabled = true;
  loadAdapters();
  loadSatellites();

  return () => stopPolling();
}

/** Muestra los resultados de un escaneo guardado (histórico). */
function showScanResults(ctx, record) {
  const services = record.services || [];
  const body = ui.div('col');
  if (!services.length) {
    body.appendChild(ui.emptyState(t('dvb.scan_no_results')));
  } else {
    body.appendChild(ui.keyValues([
      [t('common.id'), record.id],
      [t('common.state'), record.state],
      [t('audit.col_time'), fmt.formatDate(record.finished || record.started)],
      [t('dvb.scan_services_found'), services.length]
    ]));
    body.appendChild(ui.dataTable({
      compact: true,
      rows: services,
      columns: [
        { label: t('dvb.svc_pnr'), render: (row) => String(row.pnr ?? '') },
        { label: t('dvb.svc_name'), render: (row) => row.name || '—' },
        { label: t('dvb.svc_provider'), render: (row) => row.provider || '—' },
        {
          label: t('dvb.col_freq'),
          render: (row) => (row.transponder ? `${row.transponder.frequency || ''} ${row.transponder.polarization || ''}` : '—')
        },
        {
          label: t('dvb.svc_scrambled'),
          render: (row) => ui.badge(row.scrambled ? t('psi.scrambled_yes') : t('psi.scrambled_no'), row.scrambled ? 'warn' : 'ok')
        }
      ],
      empty: t('dvb.scan_no_results')
    }));
  }
  ui.openModal({ title: `${t('dvb.scan_results')} — ${record.id}`, size: 'md', content: [body] });
}

/* ==========================================================================
   Diálogos de satélite y transponders
   ========================================================================== */

function openSatelliteDialog(ctx, satellite, onDone) {
  const isNew = !satellite;
  const model = satellite
    ? JSON.parse(JSON.stringify(satellite))
    : { name: '', position: '', band: 'KU', lnb: { lof1: 9750, lof2: 10600, slof: 11700 }, diseqc: 0 };

  const form = ui.formModal({
    title: isNew ? t('dvb.new_satellite') : t('dvb.edit_satellite', { name: model.name || '' }),
    size: 'sm',
    content: [
      ui.field(t('dvb.sat_name'), ui.input({
        value: model.name || '',
        on: { input: (event) => { model.name = event.target.value; } }
      })),
      ui.div('grid-2', [
        ui.field(t('dvb.sat_position'), ui.input({
          value: model.position || '',
          placeholder: '19.2E',
          on: { input: (event) => { model.position = event.target.value; } }
        })),
        ui.field(t('dvb.sat_band'), ui.select([
          { value: 'KU', label: 'KU', selected: (model.band || 'KU') === 'KU' },
          { value: 'C', label: 'C', selected: model.band === 'C' }
        ], { on: { change: (event) => { model.band = event.target.value; } } }))
      ]),
      ui.el('fieldset', { class: 'group' }, [
        ui.el('legend', { text: t('dvb.lnb') }),
        ui.div('grid-3', [
          ui.field(t('channels.opt_lof1'), numberField(model.lnb, 'lof1', 0, 100000)),
          ui.field(t('channels.opt_lof2'), numberField(model.lnb, 'lof2', 0, 100000)),
          ui.field(t('channels.opt_slof'), numberField(model.lnb, 'slof', 0, 100000))
        ])
      ]),
      ui.field(t('dvb.diseqc'), numberField(model, 'diseqc', 0, 4))
    ],
    submitLabel: isNew ? t('common.create') : t('common.save'),
    onSubmit: async (handle) => {
      ui.busyButton(form.submit, true);
      try {
        const payload = {
          name: model.name,
          position: model.position,
          band: model.band,
          diseqc: fmt.toInt(model.diseqc, 0),
          lof1: fmt.toInt(model.lnb.lof1, 9750),
          lof2: fmt.toInt(model.lnb.lof2, 10600),
          slof: fmt.toInt(model.lnb.slof, 11700),
          lnb: {
            lof1: fmt.toInt(model.lnb.lof1, 9750),
            lof2: fmt.toInt(model.lnb.lof2, 10600),
            slof: fmt.toInt(model.lnb.slof, 11700)
          }
        };
        if (isNew) await ctx.api.post('/api/dvb/satellites', payload);
        else await ctx.api.put(`/api/dvb/satellites/${encodeURIComponent(model.id)}`, payload);
        ui.notify.ok(t('dvb.sat_saved'));
        handle.close();
        onDone();
      } catch (error) {
        ui.showApiError(error, t('common.server_error'));
      } finally {
        ui.busyButton(form.submit, false);
      }
    }
  });
}

function numberField(target, key, minimum, maximum) {
  const control = ui.input({
    type: 'number', min: minimum, max: maximum,
    value: target[key] === undefined || target[key] === null ? '' : String(target[key])
  });
  control.addEventListener('input', () => { target[key] = fmt.toInt(control.value, minimum || 0); });
  return control;
}

function openTransponderDialog(ctx, satellite, onDone) {
  if (!satellite) {
    ui.notify.warn(t('dvb.satellites_empty'));
    return;
  }
  const textarea = ui.textarea({ placeholder: '11500 H 27500\n11700 V 27500', rows: 6 });
  const delivery = ui.select(['S', 'S2', 'T', 'T2', 'C'].map((value) => ({
    value, label: value, selected: value === 'S2'
  })));
  const adapter = ui.input({ type: 'number', min: 0, max: 63, value: '0' });
  const device = ui.input({ type: 'number', min: 0, max: 63, value: '0' });
  const errorBox = ui.div('form-error', null, { hidden: true });

  const form = ui.formModal({
    title: `${t('dvb.tp_add')} — ${satellite.name || satellite.id}`,
    size: 'md',
    content: [
      errorBox,
      ui.div('grid-3', [
        ui.field(t('dvb.tp_delivery'), delivery),
        ui.field(t('channels.opt_adapter'), adapter),
        ui.field(t('channels.opt_device'), device)
      ]),
      ui.field(t('dvb.tp_bulk'), textarea, t('dvb.tp_bulk_hint'))
    ],
    submitLabel: t('dvb.tp_add'),
    onSubmit: async (handle) => {
      errorBox.hidden = true;
      const parsed = fmt.parseTransponderLines(textarea.value);
      if (parsed.errors.length) {
        ui.clear(errorBox);
        errorBox.appendChild(ui.el('strong', { text: parsed.errors.slice(0, 4).join(' · ') }));
        errorBox.hidden = false;
        return;
      }
      if (!parsed.items.length) {
        ui.clear(errorBox);
        errorBox.appendChild(ui.el('strong', { text: t('dvb.scan_no_tps') }));
        errorBox.hidden = false;
        return;
      }
      const lnb = satellite.lnb || {};
      const transponders = parsed.items.map((item) => ({
        type: 'dvb',
        delivery: delivery.value,
        adapter: fmt.toInt(adapter.value, 0),
        device: fmt.toInt(device.value, 0),
        frequency: item.frequency,
        polarization: item.polarization,
        symbolrate: item.symbolrate,
        lof1: fmt.toInt(lnb.lof1, 9750),
        lof2: fmt.toInt(lnb.lof2, 10600),
        slof: fmt.toInt(lnb.slof, 11700),
        diseqc: fmt.toInt(satellite.diseqc, 0)
      }));
      ui.busyButton(form.submit, true);
      try {
        const data = await ctx.api.post(
          `/api/dvb/satellites/${encodeURIComponent(satellite.id)}/transponders`,
          { transponders }
        );
        ui.notify.ok(t('dvb.tp_added', { n: data.added || 0 }));
        if (data.errors && data.errors.length) ui.notify.warn(data.errors.slice(0, 3).join(' · '));
        handle.close();
        onDone();
      } catch (error) {
        ui.showApiError(error, t('common.server_error'));
      } finally {
        ui.busyButton(form.submit, false);
      }
    }
  });
}
