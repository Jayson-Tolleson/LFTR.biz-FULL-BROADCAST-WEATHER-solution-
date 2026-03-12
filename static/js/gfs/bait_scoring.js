function clamp01(x) {
  if (!Number.isFinite(x)) return 0;
  return Math.max(0, Math.min(1, x));
}

function bell(x, center, width) {
  const z = (x - center) / Math.max(width, 1e-6);
  return Math.exp(-(z * z));
}

export function computeBaseBaitScore(cell) {
  const windMag = Math.abs(Number(cell.wind_u || 0)) + Math.abs(Number(cell.wind_v || 0));
  const windScore = 1 - clamp01(windMag / 22);
  const pressureScore = bell(Number(cell.pressure_msl || 1013), 1014, 16);
  const rainScore = 1 - clamp01(Number(cell.precip_rate || 0) / 1.2);
  const cloudScore = bell(Number(cell.cloud_total || 0), 45, 35);
  const airTempScore = bell(Number(cell.air_temp || 20), 23, 8);
  const humidityScore = bell(Number(cell.rel_humidity || 60), 68, 22);

  const base =
    0.22 * windScore +
    0.18 * pressureScore +
    0.16 * rainScore +
    0.16 * cloudScore +
    0.14 * airTempScore +
    0.14 * humidityScore;
  return clamp01(base);
}

export function computeFinalBaitScore(baseCell, oceanCell) {
  const baseBait = computeBaseBaitScore(baseCell);
  const sst = Number(oceanCell?.sst || 0);
  const sstScore = bell(sst, 23, 4);
  const sstFrontScore = clamp01(Math.abs(Number(oceanCell?.optional_ssh_anomaly || 0)) / 0.25);
  const chlorophyllScore = bell(Number(oceanCell?.chlorophyll || 0), 0.9, 0.7);
  const waterColorBias = clamp01(Number(oceanCell?.water_color_index || 0));
  const currentConvergenceScore = clamp01((Math.abs(Number(oceanCell?.current_u || 0)) + Math.abs(Number(oceanCell?.current_v || 0))) / 2.2);

  const finalScore =
    0.40 * baseBait +
    0.20 * sstScore +
    0.20 * sstFrontScore +
    0.15 * ((chlorophyllScore * 0.75) + (waterColorBias * 0.25)) +
    0.05 * currentConvergenceScore;

  return clamp01(finalScore);
}

export function confidenceFromScore(score) {
  return clamp01(score);
}
