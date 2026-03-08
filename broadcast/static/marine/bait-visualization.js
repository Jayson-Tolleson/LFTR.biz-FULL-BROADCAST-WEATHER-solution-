export function baitColor(score) {
  if (score < 30) return '#4f76ff';
  if (score < 60) return '#55dd8a';
  if (score < 80) return '#ffd24a';
  return '#ff5b5b';
}
