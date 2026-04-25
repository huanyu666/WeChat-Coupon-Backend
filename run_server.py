import os

import uvicorn

from main import app, get_uvicorn_log_config

DEFAULT_WX_SERVICE_SOCKET_PATH = "/run/wx_service-python/wx_service.sock"


if __name__ == "__main__":
    socket_path = os.getenv("WX_SERVICE_SOCKET_PATH", DEFAULT_WX_SERVICE_SOCKET_PATH).strip()
    if socket_path:
        os.makedirs(os.path.dirname(socket_path), exist_ok=True)
        try:
            os.remove(socket_path)
        except FileNotFoundError:
            pass
        previous_umask = os.umask(0)
        try:
            uvicorn.run(
                app,
                uds=socket_path,
                log_config=get_uvicorn_log_config(),
                access_log=True,
            )
        finally:
            os.umask(previous_umask)
    else:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=80,
            log_config=get_uvicorn_log_config(),
            access_log=True,
        )
