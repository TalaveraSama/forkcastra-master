/**
 * ui.js — componentes de interfaz de Astra WebUI (sin frameworks).
 *
 * Todo se construye con la API del DOM (`createElement`, `textContent`,
 * `setAttribute`): nunca se inyecta HTML de datos sin escapar. Las únicas
 * cadenas que van a atributos son las que produce este propio módulo.
 */

import { t } from './i18n.js';
import {
  escapeHtml, formatBytes, formatDuration, formatNumber, num
} from './format.js';

/* ==========================================================================
   Constructores de elementos
   ========================================================================== */

/**
 * Crea un elemento.
 *
 * `attrs` admite: `class`, `text` (texto seguro), `dataset`, `on` (mapa de
 * eventos) y cualquier atributo o propiedad. No existe una vía `html`: todo el
 * contenido dinámico se inserta con `textContent`/nodos para no permitir XSS.
 */
export function el(tag, attrs, children) {
  const node = document.createElement(tag);
  const options = attrs || {};
  Object.keys(options).forEach((key) => {
    const value = options[key];
    if (value === undefined || value === null || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'on') Object.keys(value).forEach((event) => node.addEventListener(event, value[event]));
    else if (key === 'value') node.value = value;
    else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'hidden') node[key] = !!value;
    else node.setAttribute(key, value === true ? '' : String(value));
  });
  append(node, children);
  return node;
}

/** Añade hijos (elementos, cadenas o arrays anidados) a un nodo. */
export function append(parent, children) {
  if (children === undefined || children === null || children === false) return parent;
  if (Array.isArray(children)) {
    children.forEach((child) => append(parent, child));
    return parent;
  }
  parent.appendChild(children instanceof Node ? children : document.createTextNode(String(children)));
  return parent;
}

/** Atajo: `<div class=...>`. */
export function div(className, children, attrs) {
  return el('div', { class: className, ...(attrs || {}) }, children);
}

export function span(className, children) {
  return el('span', { class: className }, children);
}

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Etiqueta de formulario con su texto y ayuda. */
export function field(labelText, control, hint) {
  return el('label', { class: 'field' }, [
    el('span', { class: 'field__label', text: labelText }),
    control,
    hint ? el('span', { class: 'field__hint', text: hint }) : null
  ]);
}

export function input(attrs) {
  return el('input', { class: 'input', ...(attrs || {}) });
}

export function select(options, attrs) {
  const node = el('select', { class: 'input', ...(attrs || {}) });
  (options || []).forEach((item) => {
    const option = el('option', {
      value: item.value,
      text: item.label,
      selected: item.selected === true,
      disabled: item.disabled === true
    });
    node.appendChild(option);
  });
  return node;
}

export function checkbox(labelText, checked, attrs) {
  const box = el('input', { type: 'checkbox', checked: !!checked, ...(attrs || {}) });
  const wrap = el('label', { class: 'check' }, [box, el('span', { text: labelText })]);
  wrap.input = box;
  return wrap;
}

export function textarea(attrs) {
  return el('textarea', { class: 'input', ...(attrs || {}) });
}

export function button(label, options = {}) {
  const node = el('button', {
    type: options.type || 'button',
    class: `btn${options.variant ? ` btn--${options.variant}` : ''}${options.size ? ` btn--${options.size}` : ''}${options.block ? ' btn--block' : ''}`,
    title: options.title,
    'aria-label': options.ariaLabel || (options.iconOnly ? label : undefined)
  }, [
    options.icon ? iconSpan(options.icon) : null,
    label ? el('span', { text: label }) : null
  ]);
  if (options.onClick) node.addEventListener('click', options.onClick);
  return node;
}

/** Botón de sólo icono con `aria-label` obligatorio. */
export function iconButton(label, options = {}) {
  const node = el('button', {
    type: 'button',
    class: `btn btn--ghost btn--icon${options.size === 'sm' ? ' btn--sm' : ''}`,
    title: options.title || label,
    'aria-label': label
  }, [iconSpan(options.symbol || '•')]);
  if (options.onClick) node.addEventListener('click', options.onClick);
  return node;
}

/**
 * Iconos SVG monocromos dibujados con la API del DOM (sin innerHTML).
 * `name` es una clave de ICONS; si no existe se usa un punto.
 */
const ICONS = {
  play: ['M4 3l10 5.5L4 14z'],
  stop: ['M3.5 3.5h9v9h-9z'],
  restart: ['M9 2.6a6.4 6.4 0 1 1-6 4.5', 'M2 2v4.2h4.2'],
  copy: ['M5.5 5.5V2.5h10v10h-3', 'M2.5 5.5h10v10h-10z'],
  trash: ['M3 4.5h12', 'M6.5 4.5V2.8h5v1.7', 'M4.7 4.5l.9 11.2h6.8l.9-11.2'],
  edit: ['M3 14.5h3l8.2-8.2-3-3L3 11.5z', 'M12.4 2.6l3 3'],
  plus: ['M9 3.5v11', 'M3.5 9h11'],
  minus: ['M3.5 9h11'],
  up: ['M9 14.5V4', 'M4.5 8.5L9 4l4.5 4.5'],
  down: ['M9 3.5V14', 'M4.5 9.5L9 14l4.5-4.5'],
  refresh: ['M15 9a6 6 0 1 1-1.8-4.3', 'M15.4 2.4v4.2h-4.2'],
  close: ['M4 4l10 10', 'M14 4L4 14'],
  check: ['M3.5 9.5l3.5 3.5 7.5-8'],
  download: ['M9 2.5v9', 'M5 8l4 4 4-4', 'M3 15.5h12'],
  upload: ['M9 15.5v-9', 'M5 10l4-4 4 4', 'M3 2.5h12'],
  eye: ['M1.5 9S4.5 4.5 9 4.5 16.5 9 16.5 9 13.5 13.5 9 13.5 1.5 9 1.5 9z', 'M9 11.2A2.2 2.2 0 1 0 9 6.8a2.2 2.2 0 0 0 0 4.4z'],
  list: ['M6.5 4.5h10', 'M6.5 9h10', 'M6.5 13.5h10', 'M2.5 4.5h1.2', 'M2.5 9h1.2', 'M2.5 13.5h1.2'],
  key: ['M11.5 2.5a4 4 0 1 0-1.4 6.9L4 15.5v1h2.5v-2H9v-2h2.4l.7-.7a4 4 0 0 0 3.4-3.3'],
  shield: ['M9 1.8l6 2.2v5.4c0 3.4-2.4 6-6 6.8-3.6-.8-6-3.4-6-6.8V4z'],
  warning: ['M9 2.5l7 12.5H2z', 'M9 7v4', 'M9 13.2v.3']
};

export function icon(name, size = 16) {
  const paths = ICONS[name] || ICONS.list;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 18 18');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  paths.forEach((d) => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  });
  return svg;
}

/** Icono envuelto en un `<span>` para usarlo dentro de botones. */
function iconSpan(name) {
  const wrap = el('span', { class: 'ico', 'aria-hidden': 'true' });
  wrap.appendChild(icon(name));
  return wrap;
}

/* ==========================================================================
   Badges, avisos y estados vacíos
   ========================================================================== */

export function badge(text, variant = '', options = {}) {
  const node = el('span', {
    class: `badge${variant ? ` badge--${variant}` : ''}${options.large ? ' badge--lg' : ''}`,
    title: options.title
  });
  if (options.dot) node.appendChild(el('span', { class: `dot${options.dot === true ? '' : ` dot--${options.dot}`}` }));
  node.appendChild(el('span', { text }));
  return node;
}

export function stateBadge(on, onText, offText) {
  return badge(on ? onText : offText, on ? 'ok' : 'off', { dot: on ? 'ok' : 'off' });
}

/** Caja de aviso. `kind` ∈ error|warn|ok|info. */
export function alert(kind, title, message, options = {}) {
  const box = el('div', { class: `alert alert--${kind}`, role: kind === 'error' ? 'alert' : 'status' });
  const symbols = { error: '✕', warn: '!', ok: '✓', info: 'i' };
  box.appendChild(el('span', { class: 'alert__icon', 'aria-hidden': 'true', text: symbols[kind] || 'i' }));
  const body = div('alert__body', [
    title ? el('strong', { class: 'alert__title', text: title }) : null
  ]);
  if (message instanceof Node) body.appendChild(message);
  else if (message) body.appendChild(el('div', { text: message }));
  box.appendChild(body);
  if (options.dismissible) {
    const close = iconButton(t('common.close'), { symbol: 'close', size: 'sm' });
    close.classList.add('alert__close');
    close.addEventListener('click', () => box.remove());
    box.appendChild(close);
  }
  return box;
}

export function emptyState(message, hint) {
  return div('empty', [
    el('div', { class: 'empty__icon', 'aria-hidden': 'true', text: '◍' }),
    el('div', { text: message }),
    hint ? el('div', { class: 'small muted', text: hint }) : null
  ]);
}

export function spinner(size) {
  return el('span', { class: `spinner${size === 'sm' ? ' spinner--inline' : ''}`, role: 'status' });
}

/** Muestra/oculta el indicador de carga global. */
export function setBusy(busy) {
  const node = document.getElementById('loading');
  if (node) node.hidden = !busy;
}

/** Pone un botón en estado "trabajando" y lo deshabilita. */
export function busyButton(node, busy) {
  if (!node) return;
  node.disabled = !!busy;
  node.classList.toggle('is-busy', !!busy);
}

/* ==========================================================================
   Toasts
   ========================================================================== */

export function toast(kind, message, options = {}) {
  const root = document.getElementById('toast-root');
  if (!root) return null;
  const titles = { ok: t('common.accept'), error: t('common.error'), warn: t('common.warning'), info: 'i' };
  const node = div(`toast toast--${kind}`, [
    div('toast__body', [
      el('div', { class: 'toast__title', text: options.title || titles[kind] || '' }),
      el('div', { class: 'toast__msg', text: message || '' })
    ])
  ]);
  const close = iconButton(t('common.close'), { symbol: 'close', size: 'sm' });
  close.classList.add('toast__close');
  close.addEventListener('click', () => node.remove());
  node.appendChild(close);
  root.appendChild(node);
  const timeout = options.timeout === undefined ? (kind === 'error' ? 9000 : 4500) : options.timeout;
  if (timeout > 0) setTimeout(() => node.remove(), timeout);
  return node;
}

export const notify = {
  ok: (message, options) => toast('ok', message, options),
  error: (message, options) => toast('error', message, options),
  warn: (message, options) => toast('warn', message, options),
  info: (message, options) => toast('info', message, options)
};

/**
 * Traduce cualquier fallo a un texto mostrable.
 *
 * - `ApiError` (status > 0) → mensaje exacto del backend.
 * - error de red interno del cliente (status 0) → su mensaje.
 * - cualquier otra excepción (un `TypeError` interno, por ejemplo) → el texto
 *   de reserva recibido, para no filtrar mensajes técnicos al usuario.
 * El detalle original se deja siempre en la consola para poder depurarlo.
 */
export function errorText(error, fallback) {
  const text = fallback || t('common.server_error');
  if (!error) return text;
  if (typeof error.status === 'number' && error.status > 0 && error.message) return error.message;
  console.error('[astra-webui] error inesperado en la interfaz', error);
  if (error.name === 'ApiError' && error.message) return error.message;
  return text;
}

/** Muestra el error exacto devuelto por el backend. */
export function showApiError(error, fallback) {
  const status = error && typeof error.status === 'number' ? error.status : 0;
  // status 0 = no hubo respuesta: el panel está caído, reiniciándose o se perdió la red.
  const sinRespuesta = status === 0;
  const message = sinRespuesta ? t('common.server_down') : errorText(error, fallback);
  const node = toast('error', message, {
    title: sinRespuesta ? t('common.no_connection') : `${t('common.error')} ${status}`
  });
  if (error && Array.isArray(error.warnings) && error.warnings.length && node) {
    const list = el('ul', { class: 'small' });
    error.warnings.forEach((warning) => list.appendChild(el('li', { text: warning })));
    node.querySelector('.toast__body').appendChild(list);
  }
  return node;
}

/* ==========================================================================
   Modales
   ========================================================================== */

const modalStack = [];

/**
 * Abre un modal accesible:
 *  - `Esc` cierra el modal superior,
 *  - el foco queda atrapado dentro,
 *  - al cerrar se devuelve el foco al elemento que lo abrió.
 *
 * `options.content` puede ser un nodo o un array de nodos.
 * `options.footer` es un nodo (normalmente una barra de botones).
 */
export function openModal(options = {}) {
  const previousFocus = document.activeElement;
  const modal = el('div', {
    class: `modal${options.size ? ` modal--${options.size}` : ''}`,
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': options.title || t('common.details')
  });

  if (options.title) {
    const close = iconButton(t('common.close'), { symbol: 'close' });
    close.addEventListener('click', () => closeModal(handle));
    modal.appendChild(div('modal__head', [
      el('h2', { class: 'modal__title', text: options.title }),
      div('card__tools', [close])
    ]));
  }

  const body = div('modal__body', options.content || null);
  modal.appendChild(body);
  if (options.footer) modal.appendChild(div('modal__foot', options.footer));

  const backdrop = el('div', { class: 'modal-backdrop' }, [modal]);
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop && options.closeOnBackdrop !== false) closeModal(handle);
  });

  const handle = {
    backdrop,
    modal,
    body,
    previousFocus,
    close: (result) => closeModal(handle, result),
    onClose: options.onClose || null,
    focusables: () => Array.from(modal.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter((node) => node.offsetParent !== null || node === document.activeElement),
    keydown: (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        if (options.closeOnEscape !== false) closeModal(handle);
        return;
      }
      if (event.key !== 'Tab') return;
      const items = handle.focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  };

  document.addEventListener('keydown', handle.keydown, true);
  document.getElementById('modal-root').appendChild(backdrop);
  modalStack.push(handle);

  const target = options.initialFocus
    ? modal.querySelector(options.initialFocus)
    : handle.focusables()[0];
  if (target) {
    try { target.focus(); } catch (error) { /* elemento no enfocable */ }
  }
  return handle;
}

export function closeModal(handle, result) {
  const index = modalStack.indexOf(handle);
  if (index === -1) return;
  modalStack.splice(index, 1);
  document.removeEventListener('keydown', handle.keydown, true);
  handle.backdrop.remove();
  if (typeof handle.onClose === 'function') handle.onClose(result);
  const restore = handle.previousFocus || null;
  if (restore && typeof restore.focus === 'function' && document.contains(restore)) restore.focus();
}

export function closeAllModals() {
  while (modalStack.length) closeModal(modalStack[modalStack.length - 1]);
}

/** Modal de confirmación basado en promesas. */
export function confirmDialog(options = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const accept = button(options.acceptLabel || t('common.accept'), {
      variant: options.danger ? 'danger' : 'primary',
      onClick: () => {
        finish(true);
        handle.close();
      }
    });
    const cancel = button(options.cancelLabel || t('common.cancel'), {
      onClick: () => {
        finish(false);
        handle.close();
      }
    });
    const handle = openModal({
      title: options.title || t('common.confirm_title'),
      size: options.size || 'sm',
      content: el('p', { text: options.message || '' }),
      footer: [div('grow'), cancel, accept],
      onClose: () => finish(false)
    });
  });
}

/**
 * Modal con un formulario y botones Guardar/Cancelar; devuelve el modal.
 *
 * El `<form>` vive en el cuerpo del modal y los botones en el pie (fuera del
 * formulario), así que el botón de guardar NO puede ser `type="submit"`: se
 * dispara con un `click`. La tecla Intro dentro del formulario también guarda.
 */
export function formModal(options) {
  const form = el('form', { class: 'col', novalidate: true });
  append(form, options.content);
  let handle = null;
  const submit = button(options.submitLabel || t('common.save'), { variant: 'primary' });
  const cancel = button(t('common.cancel'), { onClick: () => handle.close() });
  const run = () => {
    if (typeof options.onSubmit === 'function') options.onSubmit(handle);
  };
  submit.addEventListener('click', run);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    run();
  });
  handle = openModal({
    title: options.title,
    size: options.size,
    content: form,
    footer: [options.extra || div('grow'), cancel, submit],
    onClose: options.onClose
  });
  if (options.initialFocus) {
    const target = form.querySelector(options.initialFocus);
    if (target) target.focus();
  }
  return { handle, form, submit };
}

/* ==========================================================================
   Tablas
   ========================================================================== */

/**
 * Tabla genérica.
 *
 * `columns`: [{ key, label, className, render(row), sortable }]
 * `rows`: array de objetos. `rowKey(row, index)` da el identificador.
 * `empty`: nodo o mensaje para el estado vacío.
 */
export function dataTable(options) {
  const columns = options.columns || [];
  const rows = options.rows || [];
  const wrap = div('table-wrap');

  if (!rows.length) {
    return options.empty instanceof Node
      ? options.empty
      : emptyState(typeof options.empty === 'string' ? options.empty : t('common.empty'), options.emptyHint);
  }

  const table = el('table', { class: `table${options.compact ? ' table--compact' : ''}` });
  const thead = el('thead');
  const headRow = el('tr');
  columns.forEach((column) => {
    headRow.appendChild(el('th', {
      class: column.className || '',
      scope: 'col',
      text: column.label || ''
    }));
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el('tbody');
  rows.forEach((row, index) => {
    const tr = el('tr', {
      dataset: options.rowKey ? { key: String(options.rowKey(row, index)) } : {},
      class: options.rowClass ? options.rowClass(row, index) : ''
    });
    columns.forEach((column) => {
      const cell = el('td', { class: column.className || '' });
      const value = column.render ? column.render(row, index) : row[column.key];
      if (value instanceof Node) cell.appendChild(value);
      else cell.textContent = value === undefined || value === null ? '' : String(value);
      tr.appendChild(cell);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

/* ==========================================================================
   Barra de progreso y métricas
   ========================================================================== */

export function meter(percent, options = {}) {
  const value = Math.max(0, Math.min(100, num(percent, 0)));
  const warnAt = options.warnAt === undefined ? 75 : options.warnAt;
  const dangerAt = options.dangerAt === undefined ? 90 : options.dangerAt;
  let variant = options.variant || '';
  if (!variant) {
    if (value >= dangerAt) variant = 'danger';
    else if (value >= warnAt) variant = 'warn';
    else variant = 'ok';
  }
  return div('meter', [
    el('div', { class: `meter__fill meter__fill--${variant}`, style: `width:${value}%` })
  ]);
}

/** Tarjeta de estadística con valor, meta y barra opcional. */
export function statCard(options) {
  const card = div(`stat${options.variant ? ` stat--${options.variant}` : ''}`, [
    el('div', { class: 'stat__label', text: options.label }),
    el('div', {
      class: `stat__value${options.small ? ' stat__value--sm' : ''}`,
      text: options.value === undefined ? '—' : String(options.value)
    })
  ]);
  if (options.meter !== undefined) card.appendChild(meter(options.meter, options.meterOptions));
  if (options.meta) card.appendChild(el('div', { class: 'stat__meta', text: options.meta }));
  return card;
}

/** Tarjeta genérica con cabecera y acciones. */
export function card(options) {
  const head = options.title || options.tools
    ? div('card__head', [
      div('col', [
        el('h2', { class: 'card__title', text: options.title || '' }),
        options.subtitle ? el('div', { class: 'card__subtitle', text: options.subtitle }) : null
      ]),
      options.tools ? div('card__tools', options.tools) : null
    ])
    : null;
  return el('section', { class: 'card' }, [
    head,
    div(`card__body${options.flush ? ' card__body--flush' : ''}`, options.content)
  ]);
}

/**
 * Sparkline en `<canvas>` (sin librerías).
 * `series`: [{ name, color, values: [n…], unit: 'bitrate'|'percent'|'number' }]
 */
export function sparkline(options = {}) {
  const canvas = el('canvas', { role: 'img', 'aria-label': options.label || 'gráfica' });
  const legend = div('spark__legend');
  const holder = div('spark', [canvas, legend]);
  const series = options.series || [];
  // Las vistas actualizan `series[i].values` y vuelven a llamar a `draw()`.
  holder.series = series;

  series.forEach((item) => {
    legend.appendChild(el('span', { class: 'spark__key' }, [
      el('span', { class: 'spark__swatch', style: `background:${item.color}` }),
      el('span', { text: item.name })
    ]));
  });

  holder.draw = () => {
    drawSparkline(canvas, series, options);
  };

  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(() => holder.draw());
    observer.observe(canvas);
    holder.setAttribute('data-observer', '1');
    holder.destroyObserver = () => observer.disconnect();
  }
  // Primer trazado en el siguiente frame para tener medidas reales.
  requestAnimationFrame(() => holder.draw());
  return holder;
}

function drawSparkline(canvas, series, options) {
  const cssWidth = canvas.clientWidth || 600;
  const cssHeight = options.height || canvas.clientHeight || 148;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(cssWidth * ratio));
  canvas.height = Math.max(1, Math.floor(cssHeight * ratio));
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const styles = getComputedStyle(document.documentElement);
  const gridColor = styles.getPropertyValue('--border-soft').trim() || '#1a2540';
  const textColor = styles.getPropertyValue('--text-muted').trim() || '#6f819d';

  const padding = { top: 10, right: 8, bottom: 18, left: 8 };
  const width = Math.max(10, cssWidth - padding.left - padding.right);
  const height = Math.max(10, cssHeight - padding.top - padding.bottom);

  const points = series.reduce((max, item) => Math.max(max, (item.values || []).length), 0);
  if (!points) {
    ctx.fillStyle = textColor;
    ctx.font = '12px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(options.emptyText || t('dashboard.no_samples'), cssWidth / 2, cssHeight / 2);
    return;
  }

  let max = 0;
  series.forEach((item) => (item.values || []).forEach((value) => { max = Math.max(max, num(value, 0)); }));
  max = max > 0 ? max * 1.12 : 1;

  // Rejilla horizontal
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padding.top + (height / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + width, y);
    ctx.stroke();
  }

  const xOf = (index) => padding.left + (points <= 1 ? width / 2 : (width * index) / (points - 1));
  const yOf = (value) => padding.top + height - (Math.max(0, num(value, 0)) / max) * height;

  series.forEach((item) => {
    const values = item.values || [];
    if (!values.length) return;
    ctx.beginPath();
    values.forEach((value, index) => {
      const x = xOf(index);
      const y = yOf(value);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = item.color;
    ctx.lineWidth = item.width || 1.8;
    ctx.lineJoin = 'round';
    ctx.stroke();
    if (item.fill) {
      ctx.lineTo(xOf(values.length - 1), padding.top + height);
      ctx.lineTo(xOf(0), padding.top + height);
      ctx.closePath();
      ctx.fillStyle = item.fill;
      ctx.fill();
    }
  });

  // Etiqueta del máximo
  ctx.fillStyle = textColor;
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'left';
  const label = options.maxLabel ? options.maxLabel(max) : String(Math.round(max));
  ctx.fillText(label, padding.left, padding.top + 10);
  ctx.textAlign = 'right';
  ctx.fillText(options.xLabel || '', padding.left + width, padding.top + height + 14);
}

/* ==========================================================================
   Varios
   ========================================================================== */

/** Copia texto al portapapeles con reserva para navegadores sin permiso. */
export async function copyText(text, successMessage) {
  const value = String(text || '');
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      const helper = el('textarea', { class: 'input', style: 'position:fixed;left:-9999px' });
      helper.value = value;
      document.body.appendChild(helper);
      helper.select();
      document.execCommand('copy');
      helper.remove();
    }
    notify.ok(successMessage || t('common.copied'));
    return true;
  } catch (error) {
    notify.warn(t('common.copy_failed'));
    return false;
  }
}

/** Campo de sólo lectura con botón de copiar. */
export function copyField(label, value) {
  const control = input({ value: value || '', readOnly: true, spellcheck: 'false' });
  control.classList.add('mono');
  const holder = el('div', { class: 'row row--tight' }, [
    control,
    button(t('common.copy'), {
      size: 'sm',
      onClick: () => copyText(control.value)
    })
  ]);
  holder.classList.add('grow');
  return field(label, holder);
}

/** Construye una lista `<dl>` de pares clave/valor. */
export function keyValues(pairs) {
  const list = el('dl', { class: 'kv' });
  (pairs || []).forEach((pair) => {
    if (!pair) return;
    list.appendChild(el('dt', { text: pair[0] }));
    const dd = el('dd');
    if (pair[1] instanceof Node) dd.appendChild(pair[1]);
    else dd.textContent = pair[1] === undefined || pair[1] === null || pair[1] === '' ? '—' : String(pair[1]);
    list.appendChild(dd);
  });
  return list;
}

/** Fila de pestañas; `onChange(key)` al pulsar. */
export function tabs(items, activeKey, onChange) {
  const bar = div('tabs', null, { role: 'tablist' });
  (items || []).forEach((item) => {
    const node = el('button', {
      type: 'button',
      class: `tab${item.key === activeKey ? ' is-active' : ''}`,
      role: 'tab',
      'aria-selected': item.key === activeKey ? 'true' : 'false',
      text: item.label
    });
    node.addEventListener('click', () => onChange(item.key));
    bar.appendChild(node);
  });
  return bar;
}

/** Texto "12 canales · 3 en marcha" reutilizable. */
export function channelCounts(running, total) {
  return `${formatNumber(running)}/${formatNumber(total)}`;
}

/** Porcentaje y detalle de memoria, disco o CPU en una tarjeta. */
export function usageCard(label, percent, detail, options = {}) {
  return statCard({
    label,
    value: `${Math.round(num(percent, 0))} %`,
    meter: percent,
    meta: detail,
    small: options.small,
    meterOptions: options.meterOptions
  });
}

/** Muestra bytes con formato y su porcentaje. */
export function bytesDetail(used, total) {
  return `${formatBytes(used)} / ${formatBytes(total)}`;
}

/** Duración legible. */
export function durationText(seconds) {
  return formatDuration(seconds);
}

/** Barra de herramientas con etiqueta y control. */
export function labeled(labelText, control, className) {
  return el('div', { class: `field ${className || ''}`.trim() }, [
    el('span', { class: 'field__label', text: labelText }),
    control
  ]);
}

/** Cierra el menú lateral en pantallas pequeñas. */
export function closeSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (sidebar) sidebar.classList.remove('is-open');
}

export function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (sidebar) sidebar.classList.toggle('is-open');
}

/** Crea una celda con texto monoespaciado. */
export function mono(text) {
  return el('span', { class: 'mono', text: text === undefined || text === null ? '—' : String(text) });
}

/** Texto seguro a partir de datos (para reutilizar `escapeHtml` sin innerHTML). */
export function safe(text) {
  return el('span', { text: text === undefined || text === null ? '' : String(text) });
}

export { escapeHtml };
