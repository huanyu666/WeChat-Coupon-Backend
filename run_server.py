import os

import uvicorn

from main import app, get_uvicorn_log_config
from utils.path_utils import get_service_socket_path, prepare_unix_socket_path


if __name__ == "__main__":
    socket_path = get_service_socket_path()
    if socket_path:
        prepare_unix_socket_path(socket_path)
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
