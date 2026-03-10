let ws;
let attempts = 0;

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${protocol}://${location.host}/watch`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'waiting_for_broadcaster') {
      console.log('Waiting for broadcaster');
    }
  };
  ws.onclose = () => {
    attempts += 1;
    const delay = Math.min(5000, 500 * (2 ** Math.min(attempts, 4)));
    setTimeout(connect, delay);
  };
}
connect();
