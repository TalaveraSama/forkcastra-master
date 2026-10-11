/**
 * app.js — arranque de Astra WebUI.
 *
 * Responsabilidades:
 *   - cargar la sesión (`GET /api/auth/me`) y decidir login vs. aplicación,
 *   - mantener el estado global (usuario, capacidades, ajustes de UI, agente),
 *   - enrutar por hash (`#/dashboard`, `#/channels`, `#/channels/<id>`, …),
 *   - abrir un único `EventSource('/api/events')` y repartirlo por eventos,
 *   - gestionar el idioma, el tema y el aviso de agente caído.
 */

import { ApiClient, ApiError } from './api.js';
import * as fmt from './format.js';
import { applyTranslations, getLanguage, onLanguageChange, setLanguage, t } from './i18n.js';
import * as ui from './ui.js';

/* ==========================================================================
   Estado global y bus de eventos
   ========================================================================== */

const api = new ApiClient('');

const state = {
  ready: false,
  authenticated: false,
  user: null,
  csrf: '',
  version: '',
  hostname: '',
  serverTime: 0,
  ui: { title: 'Astra WebUI', language: 'es', refresh_ms: 2000, theme: 'dark' },
  stream: { port: 8001, public_base: '' },
  serviceControl: false,
  capabilities: { channels: true, users: false, settings: false, dvb: true, logs: true },
  enums: null,
  agent: { ok: true, error: '' },
  astra: null,
  channels: [],
  host: null,
  route: { name: 'dashboard', params: [] },
  eventsConnected: false,
  secondsSinceEvent: 0
};

/** Bus de eventos mínimo: las vistas se suscriben y se limpian al desmontarse. */
const bus = {
  listeners: new Map(),
  on(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(fn);
    return () => this.off(name, fn);
  },
  off(name, fn) {
    const set = this.listeners.get(name);
    if (set) set.delete(fn);
  },
  emit(name, payload) {
    const set = this.listeners.get(name);
    if (!set) return;
    Array.from(set).forEach((fn) => {
      try {
        fn(payload);
      } catch (error) {
        console.error('[astra-webui] error en el suscriptor %s', name, error);
      }
    });
  }
};

/** Suscribe `fn` a varios eventos y devuelve una única función de baja. */
function subscribe(names, fn) {
  const offs = names.map((name) => bus.on(name, fn));
  return () => offs.forEach((off) => off());
}

/* ==========================================================================
   Temas e idioma
   ========================================================================== */

function applyTheme(theme) {
  const value = ['dark', 'midnight', 'light'].includes(theme) ? theme : 'dark';
  document.documentElement.setAttribute('data-theme', value);
  state.ui.theme = value;
}

function applyUiSettings(settings) {
  if (!settings || typeof settings !== 'object') return;
  state.ui = { ...state.ui, ...settings };
  document.title = state.ui.title || 'Astra WebUI';
  applyTheme(state.ui.theme);
  if (settings.language) setLanguage(settings.language);
  const select = document.getElementById('lang-select');
  if (select) select.value = getLanguage();
}

/* ==========================================================================
   Cabecera, agente y estado de conexión
   ========================================================================== */

function renderAgentBadge() {
  const node = document.getElementById('agent-badge');
  if (!node) return;
  const ok = state.agent.ok && state.agent.error === '';
  ui.clear(node);
  node.className = `badge ${ok ? 'badge--ok' : 'badge--danger'}`;
  node.appendChild(document.createTextNode(ok ? '● astra' : '● astra ✕'));
  node.title = ok ? t('dashboard.agent_ok') : (state.agent.error || t('common.agent_down'));
  if (!ok) node.classList.add('is-down');
}

function renderFooter() {
  const info = document.getElementById('footer-info');
  const agent = document.getElementById('footer-agent');
  if (info) {
    info.textContent = `${state.ui.title} ${state.version || ''} · ${state.hostname || ''}`.trim();
  }
  if (agent) {
    const ok = state.agent.ok && state.agent.error === '';
    agent.textContent = ok
      ? `${t('dashboard.agent_ok')} · ${t('nav.logout') === 'Salir' ? 'conexión en vivo' : 'live connection'}`
      : (state.agent.error || t('common.agent_down'));
  }
}

function renderUserChip() {
  const chip = document.getElementById('user-chip');
  if (!chip || !state.user) return;
  ui.clear(chip);
  chip.appendChild(ui.el('div', { class: 'user-chip__name', text: state.user.display_name || state.user.username }));
  chip.appendChild(ui.el('div', { class: 'user-chip__role', text: t(`users.role_${state.user.role}`) }));
}

function renderSidebarStatus() {
  const node = document.getElementById('sidebar-status');
  if (!node) return;
  ui.clear(node);
  if (!state.astra) {
    node.appendChild(ui.el('div', { text: t('common.loading') }));
    return;
  }
  const running = state.astra.channels_running || 0;
  const total = state.astra.channels_total || 0;
  node.appendChild(ui.el('div', { text: `${t('dashboard.channels_running')}: ${fmt.formatNumber(running)}/${fmt.formatNumber(total)}` }));
  node.appendChild(ui.el('div', { text: `${t('dashboard.bitrate_total')}: ${fmt.formatBitrate(state.astra.bitrate_total || 0)}` }));
  node.appendChild(ui.el('div', { text: `${t('dashboard.viewers')}: ${fmt.formatNumber(state.astra.viewers || 0)}` }));
}

/** Capa de avisos persistente (por ejemplo, agente caído). */
const alertLayer = {
  nodes: new Map(),
  show(key, kind, title, message, options = {}) {
    this.hide(key);
    const node = ui.alert(kind, title, message, options);
    document.getElementById('alert-layer').appendChild(node);
    this.nodes.set(key, node);
    return node;
  },
  hide(key) {
    const node = this.nodes.get(key);
    if (node) node.remove();
    this.nodes.delete(key);
  }
};

function announceAgentState() {
  const ok = state.agent.ok && state.agent.error === '';
  if (ok) {
    alertLayer.hide('agent');
  } else {
    alertLayer.show('agent', 'error', t('common.agent_down'), state.agent.error || t('common.agent_down_hint'));
  }
}

/**
 * Aplica un estado del agente recién recibido y avisa si ha cambiado.
 * El aviso se muestra SIEMPRE que el agente está caído (también al montar una
 * vista nueva), no sólo cuando hay una transición.
 */
function applyAgentState(agent) {
  if (!agent) return;
  state.agent = agent;
  renderAgentBadge();
  renderFooter();
  announceAgentState();
}

/* ==========================================================================
   Navegación
   ========================================================================== */

const NAV_ITEMS = [
  { key: 'dashboard', icon: 'list', label: 'nav.dashboard', hash: '#/dashboard', capability: null },
  { key: 'channels', icon: 'play', label: 'nav.channels', hash: '#/channels', capability: 'channels' },
  { key: 'users', icon: 'key', label: 'nav.users', hash: '#/users', capability: 'users' },
  { key: 'dvb', icon: 'shield', label: 'nav.dvb', hash: '#/dvb', capability: 'dvb' },
  { key: 'logs', icon: 'list', label: 'nav.logs', hash: '#/logs', capability: 'logs' },
  { key: 'audit', icon: 'shield', label: 'nav.audit', hash: '#/audit', capability: null },
  { key: 'settings', icon: 'restart', label: 'nav.settings', hash: '#/settings', capability: 'settings' }
];

function renderNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;
  ui.clear(nav);
  NAV_ITEMS.forEach((item) => {
    if (item.capability && !state.capabilities[item.capability]) return;
    const link = ui.el('a', {
      class: `nav__link${state.route.name === item.key ? ' is-active' : ''}`,
      href: item.hash,
      dataset: { nav: item.key }
    });
    const iconWrap = ui.el('span', { class: 'nav__icon', 'aria-hidden': 'true' });
    iconWrap.appendChild(ui.icon(item.icon, 17));
    link.appendChild(iconWrap);
    link.appendChild(ui.el('span', { text: t(item.label) }));
    if (item.key === 'channels' && state.channels.length) {
      link.appendChild(ui.el('span', {
        class: 'nav__badge',
        text: `${state.astra ? state.astra.channels_running || 0 : 0}/${state.channels.length}`
      }));
    }
    link.addEventListener('click', () => ui.closeSidebar());
    nav.appendChild(link);
  });
}

function setActiveNav() {
  document.querySelectorAll('[data-nav]').forEach((link) => {
    link.classList.toggle('is-active', link.dataset.nav === state.route.name);
  });
}

/* ==========================================================================
   Router por hash
   ========================================================================== */

const VIEWS = {
  dashboard: () => import('./views/dashboard.js'),
  channels: () => import('./views/channels.js'),
  users: () => import('./views/users.js'),
  dvb: () => import('./views/dvb.js'),
  logs: () => import('./views/logs.js'),
  audit: () => import('./views/audit.js'),
  settings: () => import('./views/settings.js')
};

let currentView = null;
let viewToken = 0;

function parseHash() {
  const raw = (window.location.hash || '').replace(/^#\/?/, '');
  const parts = raw.split('/').filter(Boolean).map((part) => decodeURIComponent(part));
  const name = parts.shift() || 'dashboard';
  return { name, params: parts };
}

function defaultRouteFor(name) {
  const item = NAV_ITEMS.find((entry) => entry.key === name);
  if (!item) return '#/dashboard';
  if (item.capability && !state.capabilities[item.capability]) return '#/dashboard';
  return `#/${name}`;
}

async function renderRoute() {
  const route = parseHash();
  const item = NAV_ITEMS.find((entry) => entry.key === route.name);
  if (!item || (item.capability && !state.capabilities[item.capability])) {
    window.location.hash = '#/dashboard';
    return;
  }
  state.route = route;
  setActiveNav();
  renderNav();

  // 1) desmontar la vista anterior
  if (currentView && typeof currentView.destroy === 'function') {
    try {
      currentView.destroy();
    } catch (error) {
      console.error('[astra-webui] error al desmontar la vista', error);
    }
  }
  currentView = null;

  const container = document.getElementById('view');
  ui.clear(container);
  const token = (viewToken += 1);

  let module;
  try {
    module = await VIEWS[route.name]();
  } catch (error) {
    container.appendChild(ui.alert('error', t('common.server_error'), String(error && error.message)));
    return;
  }
  if (token !== viewToken) return;

  const context = {
    api,
    state,
    bus,
    subscribe,
    notify: ui.notify,
    showApiError: ui.showApiError,
    ui,
    fmt,
    t,
    // Permite a las vistas pedir un redibujado del marco (título, nav, badge).
    refreshChrome: () => {
      renderNav();
      renderSidebarStatus();
    },
    // Las vistas que consultan el estado del agente lo notifican aquí para que
    // la cabecera y el aviso de "agente caído" se mantengan al día.
    setAgent: (agent) => applyAgentState(agent),
    setTitle: (title, subtitle) => {
      const titleNode = document.getElementById('page-title');
      const subtitleNode = document.getElementById('page-subtitle');
      if (titleNode) titleNode.textContent = title;
      if (subtitleNode) subtitleNode.textContent = subtitle || '';
    },
    serviceControl: () => state.serviceControl
  };

  try {
    currentView = module.render(container, context, route.params) || null;
  } catch (error) {
    console.error('[astra-webui] error al montar la vista', error);
    container.appendChild(ui.alert('error', t('common.server_error'), String(error && error.message)));
  }
  ui.closeSidebar();
  container.focus({ preventScroll: true });
}

/* ==========================================================================
   SSE: estado global en vivo
   ========================================================================== */

let eventSource = null;

function startEvents() {
  stopEvents();
  eventSource = api.sse('/api/events', {
    open: () => {
      state.eventsConnected = true;
      state.secondsSinceEvent = 0;
      bus.emit('connection', { connected: true });
    },
    error: () => {
      state.eventsConnected = false;
      bus.emit('connection', { connected: false });
    },
    status: (payload) => {
      state.secondsSinceEvent = 0;
      if (!payload) return;
      state.astra = payload.astra || state.astra;
      state.channels = payload.channels || state.channels;
      if (payload.host) state.host = { ...(state.host || {}), ...payload.host };
      applyAgentState(payload.agent || { ok: true, error: '' });
      renderSidebarStatus();
      bus.emit('status', payload);
    }
  });
  return eventSource;
}

function stopEvents() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  state.eventsConnected = false;
}

/** Refresca el estado cuando el agente se recupera o la pestaña vuelve al frente. */
async function refreshStatus() {
  try {
    const data = await api.get('/api/channels');
    state.channels = data.channels || [];
    applyAgentState(data.agent || { ok: true, error: '' });
    bus.emit('channels', state.channels);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.isAgentDown) {
      applyAgentState({ ok: false, error: error.message });
    } else if (error.status !== 401) {
      ui.showApiError(error, t('common.server_error'));
    }
  }
}

/* ==========================================================================
   Autenticación
   ========================================================================== */

function showScreen(name) {
  const login = document.getElementById('login-screen');
  const app = document.getElementById('app');
  login.hidden = name !== 'login';
  app.hidden = name !== 'app';
  if (name === 'login') {
    const username = document.getElementById('login-username');
    if (username) username.focus();
  }
}

function wireLogin() {
  const form = document.getElementById('login-form');
  const errorBox = document.getElementById('login-error');
  const submit = document.getElementById('login-submit');
  const username = document.getElementById('login-username');
  const password = document.getElementById('login-password');

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    if (!username.value.trim() || !password.value) {
      errorBox.textContent = t('login.missing_fields');
      errorBox.hidden = false;
      return;
    }
    ui.busyButton(submit, true);
    submit.querySelector('span').textContent = t('login.entering');
    try {
      const data = await api.post('/api/auth/login', {
        username: username.value.trim(),
        password: password.value
      }, { csrf: false });
      api.setCsrf(data.csrf);
      password.value = '';
      await bootstrapSession(data);
    } catch (error) {
      errorBox.textContent = error.message || t('common.server_error');
      errorBox.hidden = false;
      password.select();
    } finally {
      ui.busyButton(submit, false);
      submit.querySelector('span').textContent = t('login.submit');
    }
  });
}

async function logout() {
  try {
    await api.post('/api/auth/logout', {}, { csrf: false });
  } catch (error) {
    // aunque falle, se limpia el estado local
  }
  api.clearCsrf();
  state.authenticated = false;
  state.user = null;
  stopEvents();
  window.location.hash = '';
  showScreen('login');
  ui.notify.info(t('common.session_expired'));
}

/** Carga el perfil y arranca la aplicación. */
async function bootstrapSession(loginPayload) {
  if (loginPayload) {
    state.csrf = loginPayload.csrf || '';
    state.version = loginPayload.version || '';
    state.hostname = loginPayload.hostname || '';
  }
  const me = await api.get('/api/auth/me');
  api.setCsrf(me.csrf || api.csrf);
  state.csrf = api.csrf;
  state.authenticated = true;
  state.user = me.user;
  state.version = me.version || state.version;
  state.hostname = me.hostname || state.hostname;
  state.serverTime = me.server_time || 0;
  state.stream = me.stream || state.stream;
  state.serviceControl = !!me.service_control;
  state.capabilities = me.capabilities || state.capabilities;
  applyUiSettings(me.ui);

  renderUserChip();
  renderNav();
  renderAgentBadge();
  renderFooter();
  showScreen('app');

  await loadEnums();
  await refreshStatus();
  startEvents();

  if (!window.location.hash || window.location.hash === '#/') {
    window.location.hash = '#/dashboard';
  } else if (!window.location.hash.startsWith('#/')) {
    window.location.hash = '#/dashboard';
  } else {
    await renderRoute();
  }
  state.ready = true;
  bus.emit('ready', state);
}

/** Enumeraciones de `validators.py` (una sola vez, se reutilizan en formularios). */
async function loadEnums() {
  if (state.enums) return state.enums;
  try {
    state.enums = await api.get('/api/enums');
  } catch (error) {
    // Sin enumeraciones la UI sigue funcionando con listas de reserva.
    state.enums = {
      input_types: ['http', 'udp', 'file', 'dvb'],
      output_types: ['http', 'udp', 'file'],
      delivery_systems: ['S', 'S2', 'T', 'T2', 'C'],
      polarizations: ['H', 'V', 'R', 'L'],
      fec: ['AUTO'], modulations: ['NONE', 'AUTO'], bandwidths: ['AUTO'],
      guard_intervals: ['AUTO'], transmit_modes: ['AUTO'], hierarchies: ['AUTO'],
      rolloffs: ['AUTO'], roles: ['admin', 'operator', 'viewer']
    };
  }
  return state.enums;
}

/* ==========================================================================
   Arranque
   ========================================================================== */

function wireChrome() {
  const menu = document.getElementById('menu-btn');
  if (menu) menu.addEventListener('click', () => ui.toggleSidebar());

  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async () => {
      const yes = await ui.confirmDialog({
        title: t('nav.logout'),
        message: `${t('nav.logout')}?`,
        acceptLabel: t('nav.logout')
      });
      if (yes) logout();
    });
  }

  const language = document.getElementById('lang-select');
  if (language) {
    language.addEventListener('change', () => {
      setLanguage(language.value);
    });
  }

  onLanguageChange(() => {
    applyTranslations(document);
    renderNav();
    renderUserChip();
    renderSidebarStatus();
    renderAgentBadge();
    renderFooter();
    announceAgentState();
    // Las vistas se vuelven a montar para traducir su contenido dinámico.
    renderRoute();
  });

  window.addEventListener('hashchange', () => {
    renderRoute();
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && state.authenticated) refreshStatus();
  });

  // Vigila la conexión SSE y la refresca si se queda muda.
  setInterval(() => {
    if (!state.authenticated) return;
    state.secondsSinceEvent += 5;
    if (!state.eventsConnected && state.secondsSinceEvent > 20) {
      state.secondsSinceEvent = 0;
      startEvents();
      refreshStatus();
    }
  }, 5000);

  api.onUnauthorized = () => {
    if (!state.authenticated) return;
    state.authenticated = false;
    stopEvents();
    showScreen('login');
    ui.notify.warn(t('common.session_expired'));
  };
  api.onForbidden = (error) => {
    ui.notify.warn(error.message || t('common.forbidden'));
  };
  api.onAgentDown = (error) => {
    applyAgentState({ ok: false, error: error.message });
    bus.emit('agent', state.agent);
  };
}

async function main() {
  wireChrome();
  wireLogin();
  applyTranslations(document);
  applyTheme(state.ui.theme);
  try {
    await bootstrapSession();
    if (state.ready && !window.location.hash) window.location.hash = '#/dashboard';
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showScreen('login');
      const version = document.getElementById('login-version');
      if (version) version.textContent = '';
    } else {
      showScreen('login');
      const box = document.getElementById('login-error');
      if (box) {
        box.textContent = error.message || t('common.server_error');
        box.hidden = false;
      }
    }
  }
}

/** Utilidades expuestas a las vistas mediante el contexto (evita duplicar CDN). */
export { api, bus, state, subscribe };

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}
