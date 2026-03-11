function warn(prefix, detail) {
  console.warn(`[gfs/api] ${prefix}`, detail || '');
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

export async function getJsonSafe(url, fallback = null) {
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
      warn('GET non-ok', { url, status: res.status });
      return fallback;
    }
    return await parseJsonSafe(res, fallback, `GET ${url}`);
  } catch (err) {
    warn('GET network failure', { url, err: err?.message || err });
    return fallback;
  }
}

export async function postJsonSafe(url, payload = {}, fallback = null) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
    const res = await fetch(url, { method: 'POST', body: fd, credentials: 'same-origin' });
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
