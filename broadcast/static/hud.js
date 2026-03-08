export function initHUD() {
  const hud = document.getElementById('hud');
  hud.innerHTML = `
    <h2>Broadcast HUD</h2>
    <div class="hud-row"><span>Location:</span><strong id="hud-location">Global</strong></div>
    <div class="hud-row"><span>Wind:</span><strong id="hud-wind">Live GFS</strong></div>
    <div class="hud-row"><span>Cloud Cover:</span><strong id="hud-cloud">Loading...</strong></div>
    <video id="hud-video" controls autoplay muted playsinline></video>
  `;

  window.BROADCAST.onFishSelected = (fish) => {
    document.getElementById('hud-location').textContent = fish.name;
    document.getElementById('hud-wind').textContent = 'Surface + Jetstream vectors active';
    document.getElementById('hud-cloud').textContent = 'Procedural cloud tiles';

    const video = document.getElementById('hud-video');
    video.src = fish.video_url;
  };
}
