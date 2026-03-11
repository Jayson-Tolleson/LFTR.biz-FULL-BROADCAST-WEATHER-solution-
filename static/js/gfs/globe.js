import { jget } from './api.js';

export async function ensureMaps3D(globeEl, fallbackEl) {
  const cfg = await jget('/gfs/api/config').catch(() => ({ google_maps_api_key: '' }));
  if (!cfg.google_maps_api_key) {
    fallbackEl.classList.remove('hidden');
    return false;
  }
  await new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(cfg.google_maps_api_key)}&v=beta&libraries=maps3d,marker`;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  }).catch(() => false);
  if (!window.google?.maps?.importLibrary) {
    fallbackEl.classList.remove('hidden');
    return false;
  }
  fallbackEl.classList.add('hidden');
  return true;
}

export async function libs() {
  const maps3d = await google.maps.importLibrary('maps3d');
  const marker = await google.maps.importLibrary('marker');
  return { maps3d, marker };
}
