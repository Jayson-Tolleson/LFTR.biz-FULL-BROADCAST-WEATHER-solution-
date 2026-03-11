from fastapi import Request


def get_settings(request: Request):
    return request.app.state.settings


def get_app_state(request: Request):
    return request.app.state.app_state


def get_rtc(request: Request):
    return request.app.state.rtc


def get_gfs(request: Request):
    return request.app.state.gfs
