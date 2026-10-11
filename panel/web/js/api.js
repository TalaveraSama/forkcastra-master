/**
 * api.js — cliente HTTP del panel Astra WebUI.
 *
 * Responsabilidades:
 *   - adjuntar la cookie de sesión (`credentials: same-origin`),
 *   - enviar la cabecera `X-CSRF-Token` en toda mutación,
 *   - normalizar los errores del backend `{ok:false,error}` en `ApiError`,
 *   - avisar a la aplicación de un `401` (sesión caducada) y de un `403`,
 *   - exponer ayudas para SSE y descargas.
 *
 * El token CSRF vive SÓLO en memoria: nunca se guarda en localStorage.
 */

/** Error de la API con el mensaje exacto devuelto por el backend. */
export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message || 'error desconocido');
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload || {};
    this.warnings = Array.isArray(this.payload.warnings) ? this.payload.warnings : [];
    this.errors = Array.isArray(this.payload.errors) ? this.payload.errors : [];
  }

  /** true cuando el agente Astra no responde (502) o la API lo marca caído. */
  get isAgentDown() {
    return this.status === 502;
  }
}

const JSON_HEADERS = { Accept: 'application/json, text/plain, */*' };

function looksLikeJson(response) {
  const type = response.headers.get('content-type') || '';
  return type.includes('json');
}

export class ApiClient {
  constructor(base = '') {
    this.base = base;
    this.csrf = '';
    this.onUnauthorized = null;
    this.onForbidden = null;
    this.onAgentDown = null;
    this.lastAgentState = { ok: true, error: '' };
  }

  /** Guarda el CSRF de la sesión (llega del login y de `/api/auth/me`). */
  setCsrf(token) {
    this.csrf = token || '';
    return this.csrf;
  }

  clearCsrf() {
    this.csrf = '';
  }

  /**
   * Petición genérica. Devuelve el cuerpo ya parseado.
   * Lanza `ApiError` en cualquier respuesta no 2xx.
   */
  async request(method, path, body, options = {}) {
    const init = {
      method: method.toUpperCase(),
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { ...JSON_HEADERS, ...(options.headers || {}) }
    };
    if (options.signal) init.signal = options.signal;
    if (body !== undefined && body !== null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const needsCsrf = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(init.method);
    if (needsCsrf && this.csrf && options.csrf !== false) {
      init.headers['X-CSRF-Token'] = this.csrf;
    }

    let response;
    try {
      response = await fetch(this.base + path, init);
    } catch (cause) {
      if (cause && cause.name === 'AbortError') throw cause;
      throw new ApiError(0, 'no se pudo contactar con el panel (red o servidor caído)', {});
    }

    if (response.status === 204) return { ok: true };
    if (response.status === 304) return { ok: true, _cached: true };

    let payload = null;
    const text = await response.text();
    if (text) {
      if (looksLikeJson(response)) {
        try {
          payload = JSON.parse(text);
        } catch (error) {
          payload = { ok: false, error: 'respuesta JSON inválida del servidor' };
        }
      } else {
        payload = { ok: response.ok, _raw: text };
      }
    } else {
      payload = { ok: response.ok };
    }

    if (!response.ok) {
      const message = (payload && (payload.error || payload.message)) || `error HTTP ${response.status}`;
      const apiError = new ApiError(response.status, message, payload || {});
      if (response.status === 401 && typeof this.onUnauthorized === 'function') this.onUnauthorized(apiError);
      if (response.status === 403 && typeof this.onForbidden === 'function') this.onForbidden(apiError);
      if (response.status === 502 && typeof this.onAgentDown === 'function') this.onAgentDown(apiError);
      throw apiError;
    }

    if (payload && payload.ok === false) {
      // 200 con ok:false (p. ej. el agente responde ok:false)
      const apiError = new ApiError(200, payload.error || 'la operación no se pudo completar', payload);
      if (typeof this.onAgentDown === 'function' && payload.agent && payload.agent.ok === false) {
        this.onAgentDown(apiError);
      }
      throw apiError;
    }
    return payload;
  }

  get(path, options) { return this.request('GET', path, undefined, options); }
  post(path, body, options) { return this.request('POST', path, body === undefined ? {} : body, options); }
  put(path, body, options) { return this.request('PUT', path, body === undefined ? {} : body, options); }
  delete(path, body, options) { return this.request('DELETE', path, body, options); }

  /** Descarga autenticada (respeta cookies) y devuelve un Blob. */
  async download(path) {
    const response = await fetch(this.base + path, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: '*/*' }
    });
    if (!response.ok) {
      let detail = `error HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload && payload.error) detail = payload.error;
      } catch (error) { /* sin cuerpo JSON */ }
      throw new ApiError(response.status, detail, {});
    }
    return { blob: await response.blob(), response };
  }

  /** Guarda un Blob en el disco del usuario con `a[download]`. */
  saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  /**
   * Abre un canal SSE. Devuelve el `EventSource` para poder cerrarlo.
   * `handlers` es un mapa `{nombreEvento: fn(datos, evento)}`; `open` y
   * `error` reciben el evento tal cual.
   */
  sse(path, handlers = {}) {
    const source = new EventSource(this.base + path, { withCredentials: true });
    Object.keys(handlers).forEach((name) => {
      if (name === 'open' || name === 'error') return;
      source.addEventListener(name, (event) => {
        let data = null;
        try {
          data = event.data ? JSON.parse(event.data) : null;
        } catch (error) {
          data = { raw: event.data };
        }
        handlers[name](data, event);
      });
    });
    if (handlers.open) source.addEventListener('open', handlers.open);
    if (handlers.error) source.addEventListener('error', handlers.error);
    return source;
  }

  /** Construye una query string omitiendo valores vacíos. */
  static query(params) {
    const search = new URLSearchParams();
    Object.keys(params || {}).forEach((key) => {
      const value = params[key];
      if (value === undefined || value === null || value === '') return;
      search.set(key, String(value));
    });
    const text = search.toString();
    return text ? `?${text}` : '';
  }
}

export default ApiClient;
