# Broadcast Weather System (Python + Google Photorealistic 3D)

This project provides a clean Python 3 Quart application with a Bash installer and a Google Photorealistic 3D globe frontend.

## Features

- Google 3D globe (`maps3d`) rendered from `indexgfs.html`
- Weather overlays rendered procedurally on the client:
  - clouds (Polygon3DElement)
  - rain spheres/markers
  - jet balloons from wind vectors
  - fish location markers
  - HUD + broadcast panel
- Compact cloud tile API designed to keep payload small (<1 MB)

## Installation

From the `broadcast/` directory:

```bash
./install/create-broadcast.sh
```

Installer actions:

1. Creates required directories
2. Creates Python virtual environment
3. Installs dependencies from `requirements.txt`
4. Prompts for Google Maps API key
5. Writes `.env` with:
   - `GOOGLE_MAPS_API_KEY`
   - `PORT=8000`
6. Starts the Quart server

You can also manually copy `.env.example` to `.env`.

## NOAA GFS Source

The backend service is structured for NOAA GFS ingestion and currently provides compact, tile-oriented weather output suitable for client-side procedural rendering.

- `/gfs/api/cloud_tiles?limit=200&compact=1`
- `/gfs/api/rain`
- `/gfs/api/balloons`
- `/gfs/api/fish`

## Architecture

### Server

- `server/app.py`: Quart app bootstrap
- `server/config.py`: environment loading via `python-dotenv`
- `server/routes.py`: API and static routes
- `server/gfs_service.py`: weather data pipeline and payload generation
- `server/cloud_renderer.py`: converts weather grids to compact cloud tiles
- `server/fish_service.py`: fish location provider

### Frontend weather layering

Weather layers are rendered **only after** the map emits `gmp-steadystate`.

- `static/globe.js` waits for map steady state and loads layers
- `static/clouds.js` fetches compact cloud tiles and creates client-side polygons
- `static/rain.js` renders rain markers with precipitation color scale
- `static/balloons.js` renders jet balloons at 10,000 ft
- `static/fish.js` renders fish markers and connects to HUD
- `static/hud.js` shows location, wind, cloud cover, and video stream

## Google Maps Key

A valid key with Maps JavaScript API access is required for `maps3d`.
Set it in `.env`:

```env
GOOGLE_MAPS_API_KEY=YOUR_KEY
PORT=8000
```
