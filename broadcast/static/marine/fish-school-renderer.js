import { fetchBaitZones } from './bait-engine.js';
import { baitColor } from './bait-visualization.js';

export async function renderBaitSchools(globe) {
  const zones = (await fetchBaitZones()).slice(0, 400);
  for (const zone of zones) {
    const count = Math.min(zone.spheres.length, 60);
    for (const p of zone.spheres.slice(0, count)) {
      const m = new google.maps.maps3d.Marker3DElement({
        altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
        position: { lat: p.lat, lng: p.lng, altitude: 3 },
        label: `Bait ${zone.classification} ${zone.bait_score}`
      });
      m.fillColor = baitColor(zone.bait_score);
      globe.append(m);
      window.BROADCAST.layers.bait.push(m);
    }
  }
}
