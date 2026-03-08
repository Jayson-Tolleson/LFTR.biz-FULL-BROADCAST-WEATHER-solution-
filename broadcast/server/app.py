from __future__ import annotations

from quart import Quart

from . import config
from .routes import register_routes

app = Quart(__name__, static_folder='../static')
register_routes(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=config.PORT)
