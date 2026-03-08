from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

PORT = int(os.getenv('PORT', '8000'))
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY', '')

DOMAIN = os.getenv('DOMAIN', '')
JWT_SECRET = os.getenv('JWT_SECRET', '')
TURN_URL = os.getenv('TURN_URL', '')
TURN_USERNAME = os.getenv('TURN_USERNAME', '')
TURN_PASSWORD = os.getenv('TURN_PASSWORD', '')
VERTEX_PROJECT_ID = os.getenv('VERTEX_PROJECT_ID', '')
MAX_CLOUD_TILES = int(os.getenv('MAX_CLOUD_TILES', '300'))
MAX_BAIT_SCHOOLS = int(os.getenv('MAX_BAIT_SCHOOLS', '400'))
VIDEO_ROOT = BASE_DIR / 'uploads' / 'video' / 'locations'
MODEL_DIR = BASE_DIR / 'models'
REPORT_EVENTS_FILE = BASE_DIR / 'data' / 'report_events.jsonl'
