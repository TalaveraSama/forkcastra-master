/**
 * format.js — formateo de magnitudes y ayudas de texto de Astra WebUI.
 *
 * Todo el módulo es puro (no toca el DOM salvo `escapeHtml`, que es una función
 * de cadena). Los valores llegan tal cual los devuelve la API:
 *   - bitrate en kbit/s (entero)
 *   - bytes en bytes (entero)
 *   - uptime / duraciones en segundos
 *   - fechas en epoch (segundos) o cadenas ya formateadas por el backend
 */

import { getLanguage } from './i18n.js';

const LOCALES = { es: 'es-ES', en: 'en-GB' };

function locale() {
  return LOCALES[getLanguage()] || 'es-ES';
}

/** Convierte cualquier entrada en número finito (o `fallback`). */
export function num(value, fallback = 0) {
  const parsed = typeof value === 'number' ? value : parseFloat(String(value ?? '').replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Redondea a `digits` decimales y elimina ceros sobrantes ("1.50" → "1.5"). */
export function round(value, digits = 1) {
  const factor = Math.pow(10, digits);
  const out = Math.round(num(value) * factor) / factor;
  return String(out);
}

/**
 * Bitrate legible: kbit/s → kbit/s, Mbit/s o Gbit/s.
 * `formatBitrate(5340)` → "5.34 Mbit/s"
 */
export function formatBitrate(kbits, options = {}) {
  const value = num(kbits, 0);
  const unit = options.short ? '' : 'bit/s';
  const space = options.short ? '' : ' ';
  if (value >= 1000000) return `${round(value / 1000000, 2)}${space}G${unit}`;
  if (value >= 1000) return `${round(value / 1000, value >= 10000 ? 1 : 2)}${space}M${unit}`;
  return `${Math.round(value)}${space}k${unit}`;
}

/** Igual que `formatBitrate` pero sin unidad: útil para ejes de gráficas. */
export function formatBitrateShort(kbits) {
  return formatBitrate(kbits, { short: true });
}

/** Bytes legibles en base 1024 (B, KB, MB, GB, TB). */
export function formatBytes(bytes, digits) {
  const value = num(bytes, 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let index = 0;
  let size = Math.abs(value);
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const decimals = digits === undefined ? (index === 0 ? 0 : size >= 100 ? 0 : 1) : digits;
  return `${(value < 0 ? -size : size).toFixed(decimals)} ${units[index]}`;
}

/** Velocidad (bytes/s) reutilizando el formateo de bytes. */
export function formatRate(bytesPerSecond) {
  return `${formatBytes(bytesPerSecond)}/s`;
}

/** Duración en segundos → "3 d 4 h 12 min", "12 min 5 s" o "42 s". */
export function formatDuration(seconds, options = {}) {
  const total = Math.max(0, Math.floor(num(seconds, 0)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const parts = [];
  if (days) parts.push(`${days} d`);
  if (hours || (days && !options.short)) parts.push(`${hours} h`);
  if (minutes || (hours && !options.short)) parts.push(`${minutes} min`);
  if (!parts.length || (!days && !hours && options.seconds !== false)) parts.push(`${secs} s`);
  return parts.join(' ');
}

/** Fecha/hora a partir de epoch (s) o de una cadena ya formateada. */
export function formatDate(value, options = {}) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string' && !/^\d+$/.test(value)) return value;
  const seconds = num(value, 0);
  if (!seconds) return options.zero || '—';
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  const dateOpts = { year: 'numeric', month: '2-digit', day: '2-digit' };
  const timeOpts = { hour: '2-digit', minute: '2-digit', second: '2-digit' };
  return date.toLocaleString(locale(), options.dateOnly ? dateOpts : { ...dateOpts, ...timeOpts });
}

export function formatTime(value) {
  const seconds = num(value, 0);
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleTimeString(locale());
}

/** "hace 4 min", "en 2 h", "ahora". */
export function formatAgo(value, now) {
  const seconds = num(value, 0);
  if (!seconds) return '—';
  const reference = now === undefined ? Math.floor(Date.now() / 1000) : num(now, seconds);
  const delta = reference - seconds;
  const abs = Math.abs(delta);
  const english = getLanguage() === 'en';
  if (abs < 45) return english ? 'just now' : 'ahora mismo';
  const body = formatDuration(abs, { seconds: abs < 120 });
  if (delta > 0) return english ? `${body} ago` : `hace ${body}`;
  return english ? `in ${body}` : `dentro de ${body}`;
}

/** Número entero con separadores de millar. */
export function formatNumber(value, digits = 0) {
  return num(value, 0).toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Porcentaje "42 %". */
export function formatPercent(value, digits = 0) {
  return `${num(value, 0).toFixed(digits)} %`;
}

/** Escapa caracteres peligrosos para insertarlos en HTML. */
export function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Plantilla con interpolación: `template('Hola {name}', {name: 'X'})`. */
export function template(text, params) {
  return String(text).replace(/\{(\w+)\}/g, (match, key) => (
    params && Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match
  ));
}

/** Etiqueta legible del tipo de entrada/salida. */
export function inputTypeLabel(type) {
  const names = {
    http: 'HTTP', udp: 'UDP', file: 'Fichero', dvb: 'DVB'
  };
  return names[type] || String(type || '?').toUpperCase();
}

/**
 * Resumen corto de una entrada ya normalizada por el backend.
 */
export function inputSummary(input) {
  const item = input || {};
  switch (item.type) {
    case 'http': return item.url || '';
    case 'udp': return `${item.addr || '?'}:${item.port || ''}${item.rtp ? ' (RTP)' : ''}`;
    case 'file': return item.filename || '';
    case 'dvb': {
      const parts = [`${item.delivery || item.type || 'DVB'}`];
      if (item.frequency) parts.push(`${item.frequency} MHz`);
      if (item.polarization) parts.push(item.polarization);
      if (item.symbolrate) parts.push(`${item.symbolrate} kS/s`);
      parts.push(`ad${item.adapter ?? 0}/${item.device ?? 0}`);
      return parts.join(' ');
    }
    default: return item.type || '';
  }
}

/** Resumen corto de una salida. */
export function outputSummary(output) {
  const item = output || {};
  switch (item.type) {
    case 'http': return `http ${item.uri || ''}${item.public ? ' (público)' : ''}`;
    case 'udp': return `udp ${item.addr || '?'}:${item.port || ''}`;
    case 'file': return `file ${item.filename || ''}`;
    default: return item.type || '';
  }
}

/** ¿La URL parece reproducible en un <img> (evita inyecciones raras)? */
export function isSafeImageUrl(url) {
  const text = String(url || '').trim();
  if (!text) return false;
  return /^(https?:|data:image\/|\.{0,2}\/)/i.test(text);
}

/**
 * Convierte líneas "frecuencia polarización symbolrate" en objetos de
 * transponder. Acepta separadores espacio, tabulador, coma o punto y coma.
 * Devuelve `{items, errors}` con los errores ya descritos en español.
 */
export function parseTransponderLines(text) {
  const items = [];
  const errors = [];
  String(text || '').split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) return;
    const fields = line.split(/[\s,;]+/).filter(Boolean);
    if (fields.length < 3) {
      errors.push(`línea ${index + 1}: se esperan frecuencia, polarización y symbolrate`);
      return;
    }
    const frequency = Math.round(num(fields[0], 0));
    const polarization = String(fields[1] || 'H').toUpperCase().slice(0, 1);
    const symbolrate = Math.round(num(fields[2], 0));
    if (!frequency) {
      errors.push(`línea ${index + 1}: frecuencia inválida`);
      return;
    }
    if (!'HVRL'.includes(polarization)) {
      errors.push(`línea ${index + 1}: polarización inválida (${fields[1]})`);
      return;
    }
    if (!symbolrate) {
      errors.push(`línea ${index + 1}: symbolrate inválido`);
      return;
    }
    items.push({ frequency, polarization, symbolrate });
  });
  return { items, errors };
}

/** Convierte un valor en entero, devolviendo `fallback` si no es válido. */
export function toInt(value, fallback = 0) {
  const parsed = parseInt(String(value ?? '').trim(), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Lista de etiquetas separadas por comas a partir de texto o array. */
export function parseTags(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return String(value || '')
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 12);
}

export default {
  num, round, formatBitrate, formatBitrateShort, formatBytes, formatRate, formatDuration,
  formatDate, formatTime, formatAgo, formatNumber, formatPercent, escapeHtml, template,
  inputTypeLabel, inputSummary, outputSummary, isSafeImageUrl, parseTransponderLines, toInt, parseTags
};
