# LFTR Broadcast + GFS Globe Stack

A real-time marine broadcast and weather intelligence platform built with:

- Python Quart
- WebRTC broadcasting
- Socket.IO signaling
- Google Maps 3D Photorealistic Globe
- Fish location intelligence system
- Near-real-time bait and weather context
- Popup HUD with reports, uploads, and live media

The application runs as one Quart server with nginx reverse proxy.

## Quick Install

Deploy the stack on a fresh Linux server.

Recommended environment:

- Ubuntu / Debian
- Public domain name
- Ports 80 / 443 open
- Root or sudo access

### Installation

Clone the repository and run the installer.

```bash
sudo git clone https://github.com/Jayson-Tolleson/LFTR.biz-FULL-BROADCAST-WEATHER-solution-.git
sudo mv LFTR.biz-FULL-BROADCAST-WEATHER-solution- broadcast
cd broadcast
sudo bash broadcast.sh
```

The installer will automatically:

- install system dependencies
- configure nginx
- configure TLS certificates
- configure TURN server for WebRTC
- install Python runtime environment
- install the Socket.IO client via npm
- configure the application service
- start the broadcast server

## Installer Prompts

During installation you may be prompted for:

### Domain name

Used for nginx configuration and TLS certificates.

Example:

```text
lftr.biz
```

### Google Maps API Key

Used by the `/gfs` globe page to load the Google Maps JavaScript API with Photorealistic 3D Tiles.

Enable:

- Maps JavaScript API

Example prompt:

```text
Enter Google Maps JavaScript API key:
```

The key will be stored in the app environment file.

## After Installation

The following URLs will be available:

| URL | Description |
| --- | --- |
| `/` | viewer page |
| `/broadcast` | broadcast studio |
| `/watch` | viewer player |
| `/gfs` | marine intelligence globe |
| `/status-dashboard` | server diagnostics |

Example:

```text
https://lftr.biz
```

## Core Features

### Broadcast Studio

- WebRTC live streaming
- AI chat tools
- speech-to-text controls
- media uploads

### GFS Marine Intelligence Globe

Powered by Google Maps 3D Photorealistic Tiles.

Features:

- interactive globe
- fish marker intelligence
- bait activity indicators
- weather overlays
- popup HUD system
- report submission
- video uploads
- live stream links by location

### Popup HUD

Each fish marker supports a location intelligence panel:

- report history
- uploaded videos
- live media links
- bait summary
- weather context

## Data Sources

The system loads fish locations from:

```text
static/data/fishloclist.csv
```

Uploaded videos are stored in:

```text
static/fishvid/
```

## Service Management

The broadcast server runs as a systemd service.

Check status:

```bash
sudo systemctl status broadcast
```

Restart server:

```bash
sudo systemctl restart broadcast
```

## Nginx Configuration

The installer configures nginx automatically.

Test configuration:

```bash
sudo nginx -t
```

Reload nginx:

```bash
sudo systemctl reload nginx
```

## Updating the Application

To update from GitHub:

```bash
cd ~/broadcast
sudo git pull
sudo bash broadcast.sh
```

The installer will safely update the deployment.

## Project Architecture

```text
Quart Application
│
├── broadcast UI
├── watch UI
├── GFS globe page
│
├── /gfs/api/*
├── /gfs/ws/*
│
├── WebRTC signaling
├── AI services
└── fish intelligence system
```

## License

Project maintained by Jayson Tolleson.
