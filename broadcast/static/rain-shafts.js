const MAX_SHAFTS = 200;
const MAX_PARTICLES = 8000;

function rainColor(rate) {
  if (rate <= 1) return 'rgba(255,255,255,0.55)';
  if (rate <= 5) return 'rgba(80,150,255,0.55)';
  if (rate <= 15) return 'rgba(60,210,120,0.6)';
  if (rate <= 30) return 'rgba(255,220,80,0.65)';
  if (rate <= 50) return 'rgba(255,145,65,0.72)';
  if (rate <= 80) return 'rgba(255,80,80,0.78)';
  return 'rgba(20,20,20,0.82)';
}

function cameraLodMultiplier(globe) {
  const altitude = globe?.center?.altitude ?? 18000000;
  if (altitude > 5000000) return 10;
  if (altitude > 1200000) return 25;
  return 60;
}

function respawnParticle(particle, shaft) {
  const t = Math.random();
  const radius = shaft.radiusTop + (shaft.radiusBottom - shaft.radiusTop) * t;
  const theta = Math.random() * Math.PI * 2;
  particle.lat = shaft.lat + Math.cos(theta) * radius;
  particle.lng = shaft.lng + Math.sin(theta) * radius;
  particle.altitude = shaft.top;
  particle.velocity = 60 + Math.random() * 60;
}

function createShaft(tile, globe, particlesBudget) {
  const lowHumidityVirga = tile.density < 0.55 && Math.random() > tile.density;
  const top = tile.base_altitude_m;
  const bottom = lowHumidityVirga ? 300 + Math.random() * 1200 : 0;
  const radiusTop = 0.03 + tile.precip_rate * 0.001;
  const radiusBottom = radiusTop * 1.4;

  const lod = cameraLodMultiplier(globe);
  const target = Math.max(6, Math.floor(tile.precip_rate * lod));
  const count = Math.min(target, particlesBudget);
  if (count <= 0) return null;

  const color = rainColor(tile.precip_rate);
  const particles = [];
  for (let i = 0; i < count; i++) {
    const marker = new google.maps.maps3d.Marker3DElement({
      altitudeMode: google.maps.maps3d.AltitudeMode.ABSOLUTE,
      label: ''
    });
    marker.scale = 0.3;
    marker.fillColor = color;

    const particle = { lat: tile.lat, lng: tile.lng, altitude: top, velocity: 60 + Math.random() * 60, marker };
    respawnParticle(particle, { lat: tile.lat, lng: tile.lng, top, bottom, radiusTop, radiusBottom });

    marker.position = { lat: particle.lat, lng: particle.lng, altitude: particle.altitude };
    globe.append(marker);
    particles.push(particle);
  }

  return {
    tile,
    lat: tile.lat,
    lng: tile.lng,
    top,
    bottom,
    radiusTop,
    radiusBottom,
    particles
  };
}

export function startRainShaftSystem(globe, cloudTiles) {
  const candidates = cloudTiles.filter((tile) => tile.precip_rate > 0.5).slice(0, MAX_SHAFTS);
  const shafts = [];
  let particleBudget = MAX_PARTICLES;

  for (const tile of candidates) {
    const shaft = createShaft(tile, globe, particleBudget);
    if (!shaft) continue;
    shafts.push(shaft);
    particleBudget -= shaft.particles.length;
    window.BROADCAST.layers.rain.push(...shaft.particles.map((p) => p.marker));
    if (particleBudget <= 0) break;
  }

  let prevTs = performance.now();
  function updateRain(now) {
    const dt = Math.max(0.016, (now - prevTs) / 1000);
    prevTs = now;

    for (const shaft of shafts) {
      shaft.lat = shaft.tile.lat;
      shaft.lng = shaft.tile.lng;
      shaft.top = shaft.tile.base_altitude_m;

      for (const particle of shaft.particles) {
        particle.altitude -= particle.velocity * dt;
        particle.lat += shaft.tile.wind_v * dt * 0.00001;
        particle.lng += shaft.tile.wind_u * dt * 0.00001;

        if (particle.altitude <= shaft.bottom) {
          respawnParticle(particle, shaft);
        }

        particle.marker.position = { lat: particle.lat, lng: particle.lng, altitude: particle.altitude };
      }
    }

    requestAnimationFrame(updateRain);
  }

  requestAnimationFrame(updateRain);
  return shafts;
}
