/* Astra WebUI — decoración de la rejilla de canales.
 *
 * Agrupa los cuadritos por mux (el campo `group`, que es la frecuencia) y pone en
 * cada encabezado el monitoreo real: bitrate sumado, canales al aire, espectadores,
 * CC/PES/SC y la señal del sintonizador (S y Q con barras, BER, UNC y LOCK).
 *
 * Lo usan la vista Canales y el Panel, para que la rejilla sea idéntica en las dos.
 */

import * as ui from './ui.js';
import * as fmt from './format.js';
import { t } from './i18n.js';

export function erroresDeCanal(canal) {
  const e = canal && canal.errors;
  if (!e) return { cc: 0, pes: 0, sc: 0 };
  if (typeof e === 'number') return { cc: e, pes: 0, sc: 0 };
  return {
    cc: Number(e.cc || e.cc_error || 0),
    pes: Number(e.pes || e.pes_error || 0),
    sc: Number(e.sc || 0)
  };
}

export function barraSenal(etiqueta, valor, bloqueado) {
  const caja = ui.div('sig');
  caja.appendChild(ui.el('span', { class: 'sig__label', text: etiqueta }));
  const pista = ui.div('sig__track');
  const relleno = ui.div('sig__fill' + (bloqueado ? '' : ' is-bad'));
  relleno.style.width = Math.max(2, Math.min(100, Number(valor || 0))) + '%';
  pista.appendChild(relleno);
  caja.appendChild(pista);
  caja.appendChild(ui.el('span', {
    class: 'sig__value',
    text: (valor === null || valor === undefined) ? '\u2014' : valor + '%'
  }));
  return caja;
}

function pintarSenal(cabeceras, ctx) {
  const adaptadores = Array.from(new Set(cabeceras.map((c) => c.adapter)));
  if (!adaptadores.length) return;
  ctx.api.get('/api/tuners').then((datos) => {
    const tuners = (datos && datos.tuners) || {};
    cabeceras.forEach((cab) => {
      const senal = tuners[String(cab.adapter)];
      ui.clear(cab.nodo);
      if (!cab.aire) {
        cab.nodo.appendChild(ui.el('span', { class: 'mux-head__stat is-off', text: t('dashboard.tuner_idle') }));
        return;
      }
      if (!senal || !senal.available) {
        cab.nodo.appendChild(ui.el('span', {
          class: 'mux-head__stat is-off',
          text: (senal && senal.reason) ? senal.reason : t('dashboard.tuner_unknown')
        }));
        return;
      }
      cab.nodo.appendChild(barraSenal('S', senal.signal, senal.locked));
      cab.nodo.appendChild(barraSenal('Q', senal.snr, senal.locked));
      cab.nodo.appendChild(ui.el('span', {
        class: 'mux-head__stat',
        text: 'BER ' + (senal.ber === null || senal.ber === undefined ? '\u2014' : fmt.formatNumber(senal.ber))
      }));
      cab.nodo.appendChild(ui.el('span', {
        class: 'mux-head__stat',
        text: 'UNC ' + (senal.unc === null || senal.unc === undefined ? '\u2014' : fmt.formatNumber(senal.unc))
      }));
      cab.nodo.appendChild(ui.el('span', {
        class: 'mux-head__stat' + (senal.locked ? ' is-ok' : ' is-bad'),
        text: senal.locked ? t('dashboard.tuner_lock') : t('dashboard.tuner_nolock')
      }));
      if (senal.frontend) cab.nodo.title = senal.frontend;
    });
  }).catch(() => {});
}

/** Convierte la tabla en la rejilla por mux con su monitoreo. */
export function agruparPorMux(tabla, filas, ctx, alTocar) {
  if (!tabla) return;
  const cuerpo = tabla.querySelector('tbody');
  if (!cuerpo) return;
  tabla.classList.add('table--tiles');

  const porId = new Map((filas || []).map((canal) => [String(canal.id), canal]));
  const resumen = {};
  (filas || []).forEach((canal) => {
    const mux = String(canal.group || '\u2014');
    const r = resumen[mux] || (resumen[mux] = { canales: 0, kbit: 0, cc: 0, pes: 0, sc: 0, aire: 0, viewers: 0 });
    const errores = erroresDeCanal(canal);
    r.canales += 1;
    r.kbit += Number(canal.bitrate || 0);
    r.cc += errores.cc;
    r.pes += errores.pes;
    r.sc += errores.sc;
    r.viewers += Number(canal.viewers || 0);
    if (canal.on_air) r.aire += 1;
  });

  const adaptadorDe = (mux) => {
    const canal = (filas || []).find((c) => String(c.group || '\u2014') === mux
      && (c.inputs || []).some((e) => e && e.type === 'dvb'));
    if (!canal) return 0;
    const entrada = (canal.inputs || []).find((e) => e && e.type === 'dvb') || {};
    return Number(entrada.adapter || 0);
  };

  const mayor = Math.max(1, ...Object.values(resumen).map((r) => r.kbit));
  const cabeceras = [];
  let actual = null;
  Array.from(cuerpo.children).forEach((fila) => {
    const clave = String((fila.dataset && fila.dataset.key) || '');
    const canal = porId.get(clave);
    // Tocar el cuadro abre sus opciones; los controles de dentro no
    if (typeof alTocar === 'function') {
      fila.addEventListener('click', (evento) => {
        if (evento.target && evento.target.closest('input, button, details, summary, a')) return;
        const elegido = porId.get(clave);
        if (elegido) alTocar(elegido);
      });
    }
    const mux = String((canal && canal.group) || '\u2014');
    if (mux === actual) return;
    actual = mux;
    const r = resumen[mux] || { canales: 0, kbit: 0, cc: 0, pes: 0, sc: 0, aire: 0, viewers: 0 };

    const cabecera = ui.el('tr', { class: 'mux-head' });
    const celda = ui.el('td', { colSpan: 99 });

    const linea = ui.div('mux-head__line');
    linea.appendChild(ui.el('span', { class: 'mux-head__name', text: mux }));
    linea.appendChild(ui.el('span', { class: 'mux-head__count', text: t('dashboard.mux_channels', { count: r.canales }) }));
    celda.appendChild(linea);

    const barra = ui.div('mux-head__bar');
    const relleno = ui.div('mux-head__bar-fill');
    relleno.style.width = Math.max(2, Math.round((r.kbit / mayor) * 100)) + '%';
    barra.appendChild(relleno);
    celda.appendChild(barra);

    const info = ui.div('mux-head__info');
    info.appendChild(ui.el('span', { class: 'mux-head__stat', text: t('dashboard.mux_bitrate', { value: fmt.formatBitrate(r.kbit * 1000) }) }));
    info.appendChild(ui.el('span', { class: 'mux-head__stat' + (r.aire ? ' is-ok' : ' is-off'), text: t('dashboard.mux_onair', { count: r.aire }) }));
    info.appendChild(ui.el('span', { class: 'mux-head__stat' + (r.viewers ? ' is-ok' : ''), text: t('dashboard.mux_viewers', { count: r.viewers }) }));
    info.appendChild(ui.el('span', { class: 'mux-head__stat' + (r.cc ? ' is-bad' : ''), text: 'CC ' + fmt.formatNumber(r.cc) }));
    info.appendChild(ui.el('span', { class: 'mux-head__stat' + (r.pes ? ' is-bad' : ''), text: 'PES ' + fmt.formatNumber(r.pes) }));
    info.appendChild(ui.el('span', { class: 'mux-head__stat' + (r.sc ? ' is-warn' : ''), text: 'SC ' + fmt.formatNumber(r.sc) }));
    celda.appendChild(info);

    const senal = ui.div('mux-head__signal');
    celda.appendChild(senal);
    cabeceras.push({ adapter: adaptadorDe(mux), nodo: senal, aire: r.aire });

    cabecera.appendChild(celda);
    cuerpo.insertBefore(cabecera, fila);
    cabeceras.push({ adapter: adaptadorDe(mux), nodo: senal, aire: r.aire, cabecera });
  });

  // Plegar y desplegar cada mux: el encabezado se toca y sus canales se esconden
  cabeceras.forEach((cab) => {
    cab.cabecera.classList.add('mux-head--plegable');
    cab.cabecera.addEventListener('click', () => {
      const plegado = cab.cabecera.classList.toggle('is-pletado');
      let nodo = cab.cabecera.nextElementSibling;
      while (nodo && !nodo.classList.contains('mux-head')) {
        nodo.classList.toggle('is-oculto', plegado);
        nodo = nodo.nextElementSibling;
      }
    });
  });

  pintarSenal(cabeceras, ctx);
}

/** Sello de la versión que está sirviendo el navegador (el "?v=" de app.js). */
export function selloVersion() {
  const etiqueta = document.querySelector('script[src*="app.js"]');
  const coincidencia = etiqueta && etiqueta.src ? etiqueta.src.match(/[?&]v=([^&]+)/) : null;
  return coincidencia ? coincidencia[1] : '';
}

export async function openPlayDialog(ctx, row) {
  // La lista pública la construye el servidor: trae la IP y el puerto correctos.
  let enlaces = [];
  try {
    const respuesta = await fetch('/api/playlists/public.m3u8', { credentials: 'same-origin' });
    if (respuesta.ok) {
      const texto = await respuesta.text();
      enlaces = texto.split(/\r?\n/).map((l) => l.trim())
        .filter((l) => l && l.charAt(0) !== '#');
    }
  } catch (error) {
    enlaces = [];
  }
  let detalle = row;
  try {
    detalle = (await ctx.api.get(`/api/channels/${encodeURIComponent(row.id)}`)).channel || row;
  } catch (error) { /* nos quedamos con la fila */ }
  const salidas = detalle.outputs_config || detalle.outputs || [];
  const suyos = enlaces.filter((url) => salidas.some((o) => o.uri && url.indexOf(o.uri) !== -1));

  const contenido = [];
  if (!suyos.length) {
    contenido.push(ui.alert('info', t('dashboard.play_private_title'), t('dashboard.play_private')));
    contenido.push(ui.div('row row--tight', [
      ui.button(t('dashboard.play_publish'), {
        size: 'sm',
        variant: 'primary',
        icon: 'upload',
        onClick: async () => {
          try {
            await ctx.api.post('/api/channels/outputs', {
              ids: [row.id],
              output: { type: 'http', uri: '/' + String(row.id || 'canal'), public: true, cors: true }
            });
            ui.notify.ok(t('dashboard.play_published'));
            if (handle) handle.close();
          } catch (error) {
            ui.showApiError(error, t('common.server_error'));
          }
        }
      })
    ]));
  }
  suyos.forEach((url) => {
    contenido.push(ui.div('play-url', [
      ui.el('a', { href: url, target: '_blank', rel: 'noopener', text: url, class: 'mono' })
    ]));
    const botones = ui.div('row row--tight');
    botones.appendChild(ui.button(t('common.copy'), {
      size: 'sm',
      icon: 'copy',
      onClick: () => ui.copyText(url, t('dashboard.play_copied'))
    }));
    if (url.indexOf('.m3u8') !== -1) {
      botones.appendChild(ui.button(t('dashboard.play_open'), {
        size: 'sm',
        onClick: () => window.open(url, '_blank', 'noopener')
      }));
    }
    contenido.push(botones);
  });
  if (suyos.length && suyos[0].indexOf('.m3u8') !== -1) {
    const video = ui.el('video', { controls: true, autoplay: true, muted: true, class: 'play-video' });
    video.src = suyos[0];
    contenido.push(video);
  }
  contenido.push(ui.div('hint', [ui.el('span', { text: t('dashboard.play_hint') })]));
  contenido.push(ui.div('row row--tight', [
    ui.button(t('dashboard.play_playlist'), {
      size: 'sm',
      icon: 'list',
      onClick: () => window.open('/api/playlists/public.m3u8', '_blank', 'noopener')
    })
  ]));

  let handle = null;
  handle = ui.openModal({
    title: t('dashboard.play_title', { name: row.name || row.id }),
    size: 'sm',
    content: ui.div('col', contenido),
    footer: [ui.div('grow'), ui.button(t('common.close'), { onClick: () => handle.close() })]
  });
}
