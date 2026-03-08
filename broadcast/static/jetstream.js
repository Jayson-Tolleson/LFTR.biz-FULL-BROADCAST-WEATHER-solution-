const MAX_STREAMLINES = 120;
const MAX_NODES = 60;

const streams = [];

function colorBySpeed(speed) {
  if (speed < 30) return 'rgba(255,255,255,0.35)';
  if (speed < 40) return 'rgba(130,255,255,0.42)';
  if (speed < 50) return 'rgba(120,255,140,0.45)';
  if (speed < 60) return 'rgba(255,235,120,0.5)';
  if (speed < 70) return 'rgba(255,176,95,0.56)';
  return 'rgba(255,96,96,0.62)';
}

function detailByAltitude(globe) {
  const altitude = globe?.center?.altitude ?? 18000000;
  if (altitude > 5000000) return 32;
  if (altitude > 1200000) return 44;
  return 60;
}

function velocityAt(node, step, flowOffset) {
  const wave = Math.sin((node.lat + flowOffset * 280 + step) * Math.PI / 180) * 0.35;
  return {
    u: node.wind_u * (1 + wave * 0.18),
    v: node.wind_v * (1 - wave * 0.15)
  };
}

function generateStreamline(node, flowOffset, steps) {
  const coords = [{ lat: node.lat, lng: node.lng, altitude: node.altitude }];
  let lat = node.lat;
  let lng = node.lng;
  for (let i = 0; i < steps; i++) {
    const vel = velocityAt(node, i, flowOffset);
    lat += vel.v * 0.00042;
    lng += vel.u * 0.00042;
    if (lng > 180) lng -= 360;
    if (lng < -180) lng += 360;
    coords.push({ lat, lng, altitude: node.altitude + Math.sin((i + flowOffset * 100) * 0.2) * 90 });
  }
  return coords;
}

function toRibbon(streamline, speed) {
  const width = speed * 0.0003;
  const left = [];
  const right = [];

  for (let i = 0; i < streamline.length; i++) {
    const p = streamline[i];
    const next = streamline[Math.min(i + 1, streamline.length - 1)];
    const dx = next.lng - p.lng;
    const dy = next.lat - p.lat;
    const length = Math.max(1e-6, Math.sqrt(dx * dx + dy * dy));
    const nx = -dy / length;
    const ny = dx / length;

    left.push({ lat: p.lat + nx * width, lng: p.lng + ny * width, altitude: p.altitude });
    right.push({ lat: p.lat - nx * width, lng: p.lng - ny * width, altitude: p.altitude });
  }

  return [...left, ...right.reverse()];
}

export async function renderJetStreams(globe) {
  const resp = await fetch('/gfs/api/jetstream');
  const payload = await resp.json();
  const nodes = (payload.items || []).slice(0, MAX_STREAMLINES);

  const steps = Math.min(MAX_NODES, detailByAltitude(globe));
  for (const node of nodes) {
    const path = toRibbon(generateStreamline(node, 0, steps), node.speed);
    const ribbon = new google.maps.maps3d.Polygon3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      strokeWidth: 0,
      fillColor: colorBySpeed(node.speed),
      outerCoordinates: path
    });

    globe.append(ribbon);
    streams.push({ node, ribbon });
    window.BROADCAST.layers.jetstream.push(ribbon);
  }

  let flowOffset = 0;
  function updateJetStreams() {
    flowOffset += 0.002;
    const stepCount = Math.min(MAX_NODES, detailByAltitude(globe));
    for (const stream of streams) {
      const path = toRibbon(generateStreamline(stream.node, flowOffset, stepCount), stream.node.speed);
      stream.ribbon.outerCoordinates = path;
    }
    requestAnimationFrame(updateJetStreams);
  }
  requestAnimationFrame(updateJetStreams);
}
