function warn(prefix, detail) {
  console.warn(`[gfs/api] ${prefix}`, detail || '');
}


const gfsAbortState = new Map();

function shouldAbortPreviousGet(url, options) {
  if (options?.abortPrevious === false) return false;
  return typeof url === 'string' && url.startsWith('/gfs/api/');
}

function gfsRequestKey(url) {
  try {
    const u = new URL(url, window.location.origin);
    return u.pathname;
  } catch (_) {
    return String(url || '');
  }
}

function buildGetSignal(url, options = {}) {
  const externalSignal = options?.signal || null;
  if (!shouldAbortPreviousGet(url, options)) {
    return { signal: externalSignal, controller: null, key: '' };
  }

  const key = gfsRequestKey(url);
  const prior = gfsAbortState.get(key);
  if (prior) {
    try { prior.abort(); } catch (_) {}
  }

  const controller = new AbortController();
  gfsAbortState.set(key, controller);

  if (externalSignal) {
    if (externalSignal.aborted) {
      try { controller.abort(); } catch (_) {}
    } else {
      externalSignal.addEventListener('abort', () => {
        try { controller.abort(); } catch (_) {}
      }, { once: true });
    }
  }

  return { signal: controller.signal, controller, key };
}

async function parseJsonSafe(res, fallback, context) {
  const ctype = (res.headers.get('content-type') || '').toLowerCase();
  if (!ctype.includes('application/json')) {
    warn(`${context}: non-json response`, { status: res.status, contentType: ctype });
    return fallback;
  }
  try {
    return await res.json();
  } catch (err) {
    warn(`${context}: json parse failed`, err?.message || err);
    return fallback;
  }
}

export async function getJsonSafe(url, fallback = null, options = {}) {
  const { signal, controller, key } = buildGetSignal(url, options);
  try {
    const res = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal,
    });
    if (!res.ok) {
      warn('GET non-ok', { url, status: res.status });
      return fallback;
    }
    return await parseJsonSafe(res, fallback, `GET ${url}`);
  } catch (err) {
    if (err?.name === 'AbortError') {
      return fallback;
    }
    warn('GET network failure', { url, err: err?.message || err });
    return fallback;
  } finally {
    if (controller && key && gfsAbortState.get(key) === controller) {
      gfsAbortState.delete(key);
    }
  }
}

export async function postJsonSafe(url, payload = {}, fallback = null) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload || {}),
      credentials: 'same-origin',
    });
    if (!res.ok) {
      warn('POST non-ok', { url, status: res.status });
      return fallback;
    }
    return await parseJsonSafe(res, fallback, `POST ${url}`);
  } catch (err) {
    warn('POST network failure', { url, err: err?.message || err });
    return fallback;
  }
}

export async function uploadSafe(url, file, fields = {}, fallback = null) {
  try {
    const fd = new FormData();
    Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
    fd.append('file', file);
    const res = await fetch(url, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      warn('UPLOAD non-ok', { url, status: res.status });
      return fallback;
    }
    return await parseJsonSafe(res, fallback, `UPLOAD ${url}`);
  } catch (err) {
    warn('UPLOAD network failure', { url, err: err?.message || err });
    return fallback;
  }
}

// Backward-compatible aliases for existing call sites.
export const jget = getJsonSafe;
export const jpost = postJsonSafe;
export const upload = uploadSafe;
