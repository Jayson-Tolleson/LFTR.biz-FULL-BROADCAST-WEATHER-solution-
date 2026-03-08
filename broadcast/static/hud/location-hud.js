export function locationHudHtml(loc) {
  if (!loc) return '<div>Select a location marker.</div>';
  return `<h3>LOCATION HUD</h3>
    <div>Location: ${loc.name || 'Open Ocean'}</div>
    <div>Wind: ${loc.wind || 'N/A'}</div>
    <div>Water temperature: ${loc.waterTemp || 'N/A'}</div>
    <div>Wave height: ${loc.waveHeight || 'N/A'}</div>
    <div>Current speed: ${loc.current || 'N/A'}</div>
    <div>Moon phase: ${loc.moon || 'N/A'}</div>
    <div>Tide stage: ${loc.tide || 'N/A'}</div>`;
}
