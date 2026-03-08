from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

PORT = int(os.getenv('PORT', '8000'))
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY', '')
MAX_CLOUD_TILES = int(os.getenv('MAX_CLOUD_TILES', '300'))
MAX_BAIT_SCHOOLS = int(os.getenv('MAX_BAIT_SCHOOLS', '400'))
VIDEO_ROOT = BASE_DIR / 'uploads' / 'video' / 'locations'
MODEL_DIR = BASE_DIR / 'models'
REPORT_EVENTS_FILE = BASE_DIR / 'data' / 'report_events.jsonl'
