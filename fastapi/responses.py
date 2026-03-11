from quart import send_file


def FileResponse(path):
    return send_file(str(path))
