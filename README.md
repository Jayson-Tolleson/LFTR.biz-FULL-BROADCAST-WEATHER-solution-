# LFTR Broadcast + GFS Globe Stack

## Short Overview
LFTR Broadcast + GFS Globe is a real-time marine broadcast and weather intelligence platform. The stack combines a Python Quart backend, WebRTC broadcast/watch flows, Socket.IO signaling, and a Google Maps JavaScript 3D globe experience for marine situational awareness.

The system provides fish marker intelligence, bait/weather overlays, and a popup HUD with report, upload, and live media context. It is built for practical deployment with modular install scripts, nginx, systemd, TLS, and TURN.

## Core Capabilities
- Live broadcast studio page for source operators.
- Viewer/watch page for remote playback.
- Google 3D globe weather interface (`/gfs`).
- Fish location intelligence and marker workflows.
- Bait activity polygons/overlays.
- Weather context overlays (cloud/precipitation-related layers and supporting visuals).
- Popup HUD with reports, uploads, and media/live context.
- Deployable server stack with nginx + systemd + TLS + TURN.

## Repository Layout
```text
.
├── broadcast.sh
├── README.md
├── main.py
├── requirements.txt
├── deploy/
│   ├── install.sh
│   ├── lib/
│   └── templates/
│       └── app.env.template
├── server/
│   ├── config.py
│   ├── routes.py
│   └── gfs_service.py
└── static/
    ├── indexgfs.html
    ├── broadcast.html
    ├── watch.html
    └── data/
        └── fishloclist.csv
```

## Requirements
Recommended deployment baseline:
- Ubuntu/Debian host.
- Public domain name.
- Ports `80` and `443` open.
- Root/sudo access.
- Google Maps JavaScript API key for the 3D globe path.

## Installation
Example install flow:

```bash
git clone <your-repo-url>
mv LFTR.biz-FULL-BROADCAST-WEATHER-solution- broadcast
cd broadcast
sudo bash broadcast.sh
```

The installer delegates to the modular deploy pipeline and configures:
- System packages.
- nginx.
- TLS/cert provisioning flow.
- coturn/TURN config.
- Python virtual environment and dependencies.
- Service files.
- Application environment file.
- App startup/restart.

## Installer Prompts

### Domain Name
The installer/deploy flow uses your domain for nginx and TLS-related configuration.

### Google Maps JavaScript API Key
- Required for the `/gfs` globe experience.
- Prompted during install when not already set in the deployed environment.
- Written into the app runtime environment file.
- Must **not** be hardcoded in frontend source.
- Should be restricted in Google Cloud (referrer/domain restrictions).

Use a key that is valid for the current globe implementation path (Maps JavaScript + maps3d usage in this repo).

## Runtime Configuration
The deployed app environment file created by the installer stores runtime values, including:

```env
GOOGLE_MAPS_API_KEY=YOUR_KEY_HERE
```

Behavior in this stack:
- Backend reads `GOOGLE_MAPS_API_KEY` from environment.
- Frontend receives the key through backend config payload.
- If the key is empty/invalid, Google 3D globe loading will fail or degrade.

## Application Routes
| Route | Purpose |
|---|---|
| `/` | Main index/entry page. |
| `/broadcast` | Broadcast studio/operator page. |
| `/watch` | Viewer/watch playback page. |
| `/gfs` | GFS globe / marine intelligence interface. |
| `/gfs/api/scene` | Scene payload compatibility endpoint. |
| `/gfs/tile/<layer>/<z>/<x>/<y>` | Layer tile payload endpoint used by tile-mode globe loading. |
| `/status-dashboard` | Runtime/status dashboard page. |

## Broadcast Workflow
- `/broadcast`: operator-facing page for live capture/broadcast workflows.
- `/watch`: viewer-facing page for playback/consumption.

The flow uses WebRTC transport and backend signaling/services to connect broadcaster and viewers.

## GFS Globe / Marine Intelligence Page
The `/gfs` page is the Google Maps 3D globe interface for marine context:
- Fish marker intelligence.
- Bait and weather overlays.
- Popup HUD interactions.
- Report, upload, and media/live context tools.

A valid Google Maps API configuration is required for full globe functionality.

The `/gfs/api/cloud_tiles` payload explicitly carries source quality metadata (`source` + `payload_state`) so operators can distinguish between live NOMADS data, cached real payloads, and synthetic fallback output.

The globe now prefers tile-mode weather loading and falls back to scene payload mode if tile requests are unavailable or empty.

## Fish Location Data
Fish locations are sourced from:

```text
static/data/fishloclist.csv
```

Marker and popup/HUD detail behavior depends on this data and supporting backend route logic.

## 3D Globe Implementation Notes
For developers working on `static/indexgfs.html` and related map logic:
- `Polygon3DElement` altitude modes must be set using the Google Maps 3D API enums/constants in code.
- Do **not** reintroduce raw string polygon altitude assignments such as:
  - `altitudeMode: 'absolute'`
  - `setAttribute('altitude-mode', 'absolute')`
- Bait/surface polygons should align with ground/surface behavior.
- Polygon rendering must stay defensive so one malformed layer does not crash overall globe initialization.

## Service Management
Common operations:

```bash
systemctl status <app-service-name>
systemctl restart <app-service-name>
nginx -t
systemctl reload nginx
```

## Updating the Deployment
Typical update workflow:

```bash
cd /path/to/broadcast
git pull
sudo bash broadcast.sh
```

Re-running `broadcast.sh` is the safest way to refresh deployment scripts/templates/config in place when needed.

## Troubleshooting

### Google 3D globe not loading
- Verify backend config route returns a non-empty map key.
- Confirm `/gfs` status text reports expected source quality (`live`, `cached`, or `synthetic`).
- Check browser console for Maps JS load/import errors.

### Missing/invalid Google Maps API key
- Confirm `GOOGLE_MAPS_API_KEY` is present in deployed app env.
- Restart app service after env changes.

### API restrictions/referrer issues
- Confirm the key allows your deployed domain/referrer pattern.
- Confirm the key is enabled for the APIs used by this globe path.

### Altitude mode errors on polygons
If you see errors like `InvalidValueError` related to `gmp-polygon-3d` `altitudeMode`, polygon altitude handling was likely changed incorrectly in frontend code. Revert to enum-based altitude mode handling for `Polygon3DElement`.

### Weather source transparency
- `source=gfs_nomads` with `payload_state=live` means direct real model ingestion succeeded.
- `source=gfs_nomads` with `payload_state=cached` means a recent cached real payload is serving after NOMADS failure.
- `payload_state=synthetic` indicates heuristic fallback and should be treated as degraded guidance.
### nginx/service restart checks
- Validate nginx config with `nginx -t`.
- Check app service status/logs via systemd and restart as needed.

## Development Notes
- Backend: Quart app and route/service modules under `server/`.
- Frontend: static pages under `static/` (`broadcast`, `watch`, `gfs`, etc.).
- Deployment: modular shell scripts and templates under `deploy/`.
- Google Maps 3D frontend depends on backend-delivered runtime config.
- AI endpoints return explicit provider-state responses unless providers are configured (`OPENAI_API_KEY`, `TTS_PROVIDER`, `SERPAPI_API_KEY`).

## License / Maintainer
Project maintained by Jayson Tolleson.
