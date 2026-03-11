import { jget } from './api.js';

let loaderPromise = null;

function injectMapsScript(key) {
  if (loaderPromise) return loaderPromise;
  loaderPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-gmaps-loader="1"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(true), { once: true });
      existing.addEventListener('error', () => reject(new Error('maps_script_existing_error')), { once: true });
      return;
    }

    const s = document.createElement('script');
    s.dataset.gmapsLoader = '1';
    s.async = true;
    s.defer = true;
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=beta&libraries=maps3d,marker&loading=async`;
    s.onload = () => resolve(true);
    s.onerror = () => reject(new Error('maps_script_load_error'));
    document.head.appendChild(s);
  });
  return loaderPromise;
}

export async function ensureMaps3D(_globeEl, fallbackEl) {
  const cfg = await jget('/gfs/api/config').catch(() => ({ google_maps_api_key: '', runtime: {} }));
  const key = cfg.google_maps_api_key || '';

  if (!key) {
    const reason = 'missing_google_maps_api_key';
    console.error('[gfs/globe] maps3d init failed:', reason, cfg.runtime || {});
    fallbackEl.textContent = '3D globe unavailable: GOOGLE_MAPS_API_KEY missing in app runtime.';
    fallbackEl.classList.remove('hidden');
    return { ok: false, reason };
  }

  try {
    await injectMapsScript(key);
  } catch (err) {
    const reason = err?.message || 'maps_script_injection_failed';
    console.error('[gfs/globe] maps script load failed:', reason, err);
    fallbackEl.textContent = '3D globe unavailable: Maps script failed to load (check network/key restrictions).';
    fallbackEl.classList.remove('hidden');
    return { ok: false, reason };
  }

  if (!window.google?.maps?.importLibrary) {
    const reason = 'importLibrary_missing';
    console.error('[gfs/globe] maps3d init failed:', reason);
    fallbackEl.textContent = '3D globe unavailable: google.maps.importLibrary not available.';
    fallbackEl.classList.remove('hidden');
    return { ok: false, reason };
  }

  try {
    await window.google.maps.importLibrary('maps3d');
  } catch (err) {
    const reason = 'maps3d_library_rejected';
    console.error('[gfs/globe] maps3d import failed:', reason, err);
    fallbackEl.textContent = '3D globe unavailable: maps3d library rejected (key restrictions or API enablement).';
    fallbackEl.classList.remove('hidden');
    return { ok: false, reason };
  }

  fallbackEl.classList.add('hidden');
  return { ok: true, reason: 'ready' };
}

export async function libs() {
  const maps3d = await google.maps.importLibrary('maps3d');
  return { maps3d };
}
