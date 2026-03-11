import { jget } from './api.js';

let loaderPromise = null;

function injectMapsScript(key) {
  if (loaderPromise) return loaderPromise;
  loaderPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-gmaps-loader="1"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(true), { once: true });
      existing.addEventListener('error', reject, { once: true });
      return;
    }

    const s = document.createElement('script');
    s.dataset.gmapsLoader = '1';
    s.async = true;
    s.defer = true;
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=beta&libraries=maps3d,marker&loading=async`;
    s.onload = () => resolve(true);
    s.onerror = (err) => reject(err);
    document.head.appendChild(s);
  });
  return loaderPromise;
}

export async function ensureMaps3D(_globeEl, fallbackEl) {
  const cfg = await jget('/gfs/api/config').catch(() => ({ google_maps_api_key: '' }));
  if (!cfg.google_maps_api_key) {
    fallbackEl.classList.remove('hidden');
    return false;
  }

  try {
    await injectMapsScript(cfg.google_maps_api_key);
  } catch {
    fallbackEl.classList.remove('hidden');
    return false;
  }

  if (!window.google?.maps?.importLibrary) {
    fallbackEl.classList.remove('hidden');
    return false;
  }
  fallbackEl.classList.add('hidden');
  return true;
}

export async function libs() {
  const maps3d = await google.maps.importLibrary('maps3d');
  return { maps3d };
}
