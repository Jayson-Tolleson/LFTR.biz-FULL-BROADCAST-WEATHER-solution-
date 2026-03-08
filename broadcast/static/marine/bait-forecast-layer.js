export async function renderBaitForecast(globe) {
  const payload = await (await fetch('/marine/api/bait_forecast?limit=800')).json();
  for (const cell of (payload.cells || []).slice(0, 1200)) {
    if (cell.probability < 0.55) continue;
    const color = cell.probability > 0.85 ? 'rgba(255,60,60,0.38)' : cell.probability > 0.7 ? 'rgba(255,220,80,0.28)' : 'rgba(80,220,120,0.24)';
    const poly = new google.maps.maps3d.Polygon3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      fillColor: color,
      strokeWidth: 0,
      outerCoordinates: [
        { lat: cell.lat - 0.2, lng: cell.lng - 0.2, altitude: 15 },
        { lat: cell.lat + 0.2, lng: cell.lng - 0.2, altitude: 15 },
        { lat: cell.lat + 0.2, lng: cell.lng + 0.2, altitude: 15 },
        { lat: cell.lat - 0.2, lng: cell.lng + 0.2, altitude: 15 }
      ]
    });
    globe.append(poly);
  }
}
