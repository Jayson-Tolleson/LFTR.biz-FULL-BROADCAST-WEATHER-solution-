const ribbons = [];

function color(speed) {
  if (speed < 30) return 'rgba(255,255,255,0.35)';
  if (speed < 40) return 'rgba(120,255,255,0.42)';
  if (speed < 50) return 'rgba(110,255,140,0.45)';
  if (speed < 60) return 'rgba(255,230,110,0.5)';
  if (speed < 70) return 'rgba(255,170,90,0.57)';
  return 'rgba(255,95,95,0.62)';
}

function ribbonPath(n, offset = 0) {
  const pts = [];
  const right = [];
  let lat = n.lat;
  let lng = n.lng;
  for (let i = 0; i < 40; i++) {
    lat += n.wind_v * 0.00042;
    lng += n.wind_u * 0.00042;
    const width = n.speed * 0.0003;
    const wobble = Math.sin(offset + i * 0.2) * 0.003;
    pts.push({ lat: lat + wobble + width, lng: lng + wobble, altitude: n.altitude });
    right.push({ lat: lat + wobble - width, lng: lng - wobble, altitude: n.altitude });
  }
  return [...pts, ...right.reverse()];
}

export async function renderJetstreamRibbons(globe) {
  const nodes = (await (await fetch('/gfs/api/jetstream')).json()).items.slice(0, 120);
  for (const n of nodes) {
    const poly = new google.maps.maps3d.Polygon3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      fillColor: color(n.speed),
      strokeWidth: 0,
      outerCoordinates: ribbonPath(n, 0)
    });
    globe.append(poly);
    ribbons.push({ node: n, poly, offset: 0 });
    window.BROADCAST.layers.jetstream.push(poly);
  }
  function animate() {
    for (const r of ribbons) {
      r.offset += 0.002;
      r.poly.outerCoordinates = ribbonPath(r.node, r.offset * 1000);
    }
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);
}
