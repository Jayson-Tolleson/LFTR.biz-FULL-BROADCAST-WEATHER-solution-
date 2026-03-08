from server.app_factory import create_asgi_app, create_quart_app


app = create_quart_app()
asgi_app = create_asgi_app()
