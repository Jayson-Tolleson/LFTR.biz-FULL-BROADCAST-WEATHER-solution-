export function baitHudHtml(zone) {
  if (!zone) return '<div>No bait zone selected.</div>';
  return `<h3>BAIT INTELLIGENCE</h3>
  <div>Density: ${zone.density}</div>
  <div>Direction: ${zone.direction}°</div>
  <div>Speed: ${zone.velocity}</div>
  <div>Depth: ${zone.depth}m</div>
  <div>Predator Probability: ${zone.predator_probability}</div>
  <div>Boil Probability: ${zone.boil_probability}</div>
  <div>Confidence Score: ${zone.bait_score}</div>`;
}
