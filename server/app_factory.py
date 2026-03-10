from quart import Quart

from server.routes import bp


def create_app() -> Quart:
    app = Quart(__name__)
    app.register_blueprint(bp)
    return app
