export function mountBroadcastPopup() {
  const btn = document.getElementById('report-live');
  if (!btn) return;
  btn.addEventListener('click', () => {
    window.open('/broadcast', '_blank', 'width=720,height=720');
  });
}
