/**
 * views/logs.js — visor del log de astra y del panel.
 *
 * Consume:
 *   GET /api/logs/astra?lines=N&level=&q=        (lectura con filtros)
 *   GET /api/logs/panel?lines=N&level=&q=
 *   GET /api/logs/astra/stream?level=&q=         (SSE `log` en vivo)
 *
 * El modo "en vivo" sólo existe para el log de astra (SSE del backend); para el
 * log del panel se hace polling cada 2 s con los mismos filtros.
 */

import { t } from '../i18n.js';
import * as fmt from '../format.js';
import * as ui from '../ui.js';

const MAX_DOM_LINES = 1500;
const PANEL_POLL_MS = 2000;
const LEVELS = ['error', 'warning', 'info', 'debug'];

export function render(container, ctx) {
  const { api } = ctx;
  const options = { source: 'astra', level: 'all', q: '', lines: 200, live: false, wrap: true };
  let entries = [];
  let eventSource = null;
  let panelPoll = null;
  let paused = false;
  let autoScroll = true;

  ctx.setTitle(t('logs.title'), t('logs.source_astra'));

  /* ------------------------------------------------------------------ barra */

  const sourceSelect = ui.select([
    { value: 'astra', label: t('logs.source_astra') },
    { value: 'panel', label: t('logs.source_panel') }
  ], { 'aria-label': t('logs.source') });

  const levelSelect = ui.select([{ value: 'all', label: t('logs.level_all') }].concat(
    LEVELS.map((level) => ({ value: level, label: t(`logs.level_${level}`) }))
  ), { 'aria-label': t('logs.level') });

  const linesSelect = ui.select([100, 200, 500, 1000, 2000, 5000].map((value) => ({
    value: String(value), label: String(value), selected: value === options.lines
  })), { 'aria-label': t('logs.lines') });

  const searchInput = ui.input({ type: 'search', placeholder: t('logs.search_ph'), 'aria-label': t('common.search') });

  const liveButton = ui.button(t('logs.live'), { icon: 'play', onClick: () => toggleLive() });
  const pauseButton = ui.button(t('common.pause'), { icon: 'stop', onClick: () => togglePause() });
  const scrollButton = ui.button(t('logs.scroll_lock'), { size: 'sm', onClick: () => toggleScroll() });
  const wrapButton = ui.button(t('logs.wrap'), { size: 'sm', onClick: () => toggleWrap() });
  const reloadButton = ui.button(t('common.refresh'), { size: 'sm', icon: 'refresh', onClick: () => load() });

  const statusBadge = ui.badge(t('logs.live_off'), 'off');
  const statsLabel = ui.el('span', { class: 'small muted' });

  const toolbar = ui.div('toolbar', [
    ui.labeled(t('logs.source'), sourceSelect),
    ui.labeled(t('logs.level'), levelSelect),
    ui.labeled(t('logs.lines'), linesSelect),
    ui.labeled(t('common.search'), searchInput, 'grow'),
    ui.div('grow'),
    liveButton,
    pauseButton
  ]);

  const tools = ui.div('row row--tight', [
    statusBadge,
    statsLabel,
    wrapButton,
    scrollButton,
    reloadButton
  ]);

  const view = ui.div('log-view', null, { role: 'log', tabindex: '0', 'aria-label': t('logs.title') });

  container.appendChild(ui.card({
    title: t('logs.title'),
    subtitle: '',
    flush: true,
    content: [toolbar, view],
    tools: [tools]
  }));

  /* ------------------------------------------------------------------ pintado */

  function levelClass(level) {
    const value = String(level || 'other').toLowerCase();
    return LEVELS.includes(value) ? value : 'other';
  }

  function lineNode(entry) {
    return ui.div(`log-line log-line--${levelClass(entry.level)}`, [
      ui.el('span', { class: 'log-line__ts', text: entry.timestamp || '' }),
      ui.el('span', { class: 'log-line__level', text: String(entry.level || 'other').toUpperCase() }),
      ui.el('span', { class: 'log-line__msg', text: entry.message || entry.raw || '' })
    ]);
  }

  function appendEntries(list, prepend) {
    if (!list || !list.length) return;
    const fragment = document.createDocumentFragment();
    list.forEach((entry) => fragment.appendChild(lineNode(entry)));
    if (prepend) view.insertBefore(fragment, view.firstChild);
    else view.appendChild(fragment);
    while (view.childElementCount > MAX_DOM_LINES) {
      if (prepend) view.removeChild(view.lastChild);
      else view.removeChild(view.firstChild);
    }
    if (autoScroll && !prepend) view.scrollTop = view.scrollHeight;
  }

  function drawAll() {
    ui.clear(view);
    if (!entries.length) {
      view.appendChild(ui.el('div', { class: 'empty', text: t('logs.empty') }));
      return;
    }
    appendEntries(entries, false);
    view.scrollTop = view.scrollHeight;
  }

  function updateStats(stats) {
    if (!stats) return;
    statsLabel.textContent = `${t('logs.file_size', { size: fmt.formatBytes(stats.size || 0) })}`
      + (stats.mtime ? ` · ${t('logs.mtime', { date: fmt.formatDate(stats.mtime) })}` : '');
  }

  function setStatus(live) {
    ui.clear(statusBadge);
    statusBadge.className = `badge ${live ? 'badge--ok' : 'badge--off'}`;
    statusBadge.appendChild(ui.el('span', { class: `dot ${live ? 'dot--ok dot--live' : 'dot--off'}` }));
    statusBadge.appendChild(ui.el('span', { text: live ? t('logs.live_on') : t('logs.live_off') }));
  }

  /* ------------------------------------------------------------------- carga */

  function query(extra) {
    const params = new URLSearchParams();
    params.set('lines', String(options.lines));
    if (options.level && options.level !== 'all') params.set('level', options.level);
    if (options.q) params.set('q', options.q);
    Object.keys(extra || {}).forEach((key) => params.set(key, extra[key]));
    return params.toString();
  }

  async function load() {
    try {
      const data = await api.get(`/api/logs/${options.source}?${query()}`);
      entries = data.lines || [];
      updateStats(data.stats);
      drawAll();
    } catch (error) {
      if (error.status !== 401) ui.showApiError(error, t('common.server_error'));
    }
  }

  /* -------------------------------------------------------------------- vivo */

  function stopLive() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (panelPoll) {
      clearInterval(panelPoll);
      panelPoll = null;
    }
    options.live = false;
    setStatus(false);
    ui.clear(liveButton);
    liveButton.appendChild(ui.el('span', { text: t('logs.live') }));
  }

  async function toggleLive() {
    if (options.live) {
      stopLive();
      return;
    }
    options.live = true;
    setStatus(true);
    ui.clear(liveButton);
    liveButton.appendChild(ui.el('span', { text: t('logs.live_off') }));

    if (options.source === 'astra') {
      // SSE: sólo se añaden las líneas nuevas que llegan del backend.
      eventSource = api.sse(`/api/logs/astra/stream?${query()}`, {
        log: (payload) => {
          if (paused || !payload || !payload.lines) return;
          appendEntries(payload.lines, false);
          if (payload.closed) {
            stopLive();
            ui.notify.info(t('logs.stopped'));
          }
        },
        error: () => {
          // EventSource reintenta por sí solo; se refleja el estado.
          setStatus(false);
        },
        open: () => setStatus(true)
      });
    } else {
      // El log del panel no tiene SSE: se relee el fichero cada 2 s.
      panelPoll = setInterval(async () => {
        if (paused || document.hidden) return;
        try {
          const data = await api.get(`/api/logs/panel?${query()}`);
          updateStats(data.stats);
          const incoming = data.lines || [];
          const known = new Set(entries.slice(-600).map((entry) => entry.raw));
          const fresh = incoming.filter((entry) => !known.has(entry.raw));
          if (fresh.length) {
            entries = entries.concat(fresh).slice(-2000);
            appendEntries(fresh, false);
          }
        } catch (error) {
          if (error.status !== 401) setStatus(false);
        }
      }, PANEL_POLL_MS);
    }
  }

  function togglePause() {
    paused = !paused;
    ui.clear(pauseButton);
    pauseButton.appendChild(ui.el('span', { text: paused ? t('common.resume') : t('common.pause') }));
    pauseButton.classList.toggle('btn--ok', paused);
  }

  function toggleScroll() {
    autoScroll = !autoScroll;
    ui.clear(scrollButton);
    scrollButton.appendChild(ui.el('span', { text: autoScroll ? t('logs.scroll_lock') : t('logs.scroll_unlock') }));
    if (autoScroll) view.scrollTop = view.scrollHeight;
  }

  function toggleWrap() {
    options.wrap = !options.wrap;
    view.style.whiteSpace = options.wrap ? 'pre-wrap' : 'pre';
  }

  /* ------------------------------------------------------------------ eventos */

  sourceSelect.addEventListener('change', () => {
    options.source = sourceSelect.value;
    ctx.setTitle(t('logs.title'), options.source === 'astra' ? t('logs.source_astra') : t('logs.source_panel'));
    stopLive();
    load();
  });
  levelSelect.addEventListener('change', () => {
    options.level = levelSelect.value;
    if (options.live) {
      stopLive();
      toggleLive();
    } else {
      load();
    }
  });
  linesSelect.addEventListener('change', () => {
    options.lines = fmt.toInt(linesSelect.value, 200);
    load();
  });
  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      options.q = searchInput.value.trim();
      if (options.live) {
        stopLive();
        toggleLive();
      } else {
        load();
      }
    }, 350);
  });

  // Si el usuario desplaza hacia arriba se deja de forzar el autoscroll.
  view.addEventListener('scroll', () => {
    const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 30;
    if (!atBottom && autoScroll) {
      autoScroll = false;
      ui.clear(scrollButton);
      scrollButton.appendChild(ui.el('span', { text: t('logs.scroll_unlock') }));
    }
  });

  setStatus(false);
  load();

  return {
    destroy() {
      clearTimeout(searchTimer);
      stopLive();
    }
  };
}
