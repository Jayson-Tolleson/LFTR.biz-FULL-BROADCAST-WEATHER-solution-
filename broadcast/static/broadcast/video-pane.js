export async function updateVideoPane(el, lat, lng) {
  const active = await (await fetch('/broadcast/api/status')).json();
  if ((active.active_streams || []).some((s) => Math.abs(s.lat - lat) < 0.25 && Math.abs(s.lng - lng) < 0.25)) {
    el.textContent = 'LIVE REPORT active near this location';
    return;
  }
  const archive = await (await fetch(`/broadcast/api/archive?lat=${lat}&lng=${lng}`)).json();
  el.textContent = archive.items?.[0] ? `Latest clip: ${archive.items[0]}` : 'No archived reports yet';
}
