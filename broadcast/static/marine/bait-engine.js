export async function fetchBaitZones() {
  const resp = await fetch('/marine/api/bait');
  return (await resp.json()).items || [];
}
