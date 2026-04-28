from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import sys
import logging
import logging.config
import traceback
import atexit
import asyncio
from datetime import datetime
from pathlib import Path
import os
import resource
from utils.path_utils import get_service_socket_path, prepare_unix_socket_path, resolve_project_path

        
def exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
                 
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
            
    harmless_exceptions = (
        ConnectionResetError,            
        BrokenPipeError,             
    )
    tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    is_asyncio_callback_error = (
        'asyncio' in tb_str and 
        ('_call_connection_lost' in tb_str or '_ProactorBasePipeTransport' in tb_str) and
        issubclass(exc_type, (ConnectionResetError, BrokenPipeError, OSError))
    )
                            
    if is_asyncio_callback_error:
        return
    if issubclass(exc_type, harmless_exceptions):
        if isinstance(exc_value, OSError) and hasattr(exc_value, 'winerror'):
            if exc_value.winerror == 10054:            
                return
                       
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        error_msg = f"{timestamp} - WARNING - {exc_type.__name__}: {exc_value}"
        print(error_msg, file=sys.stderr)
        return
            
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
             
    error_msg = f"{timestamp} - ERROR - {exc_type.__name__}: {exc_value}"
    print(error_msg, file=sys.stderr)
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
    for line in tb_lines[-5:]:
        print(line.rstrip(), file=sys.stderr)

          
sys.excepthook = exception_handler

          
sys.excepthook = exception_handler

from utils import setup_ansi_colors, enable_logging
from utils.logger import setup_logger

if '-log' in sys.argv:
    enable_logging()
    
      
from routes import auth_router, material_router, wechat_router, christmas_hat_router, waimai_router, order_rankings_router, sbti_router, site_verification_router


                      
setup_ansi_colors()
      
logger = setup_logger(__name__)

                         
meituan_process = None


def _get_fd_open_count() -> int | None:
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return None


def _get_fd_limit() -> int | None:
    try:
        return int(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
    except Exception:
        return None


def _get_runtime_diagnostics() -> dict:
    from utils import http_client
    from utils.go_local_api import get_go_runtime_diagnostics
    from utils.p_value_storage import get_p_value_storage
    from utils.proxy_utils import get_proxy_runtime_state
    from utils.redis_async import get_redis_runtime_diagnostics
    from utils.verification_code import (
        get_link_verification_manager,
        get_mt_order_verification_manager,
        get_verification_manager,
    )

    watcher_count = 0
    try:
        watcher_count += get_verification_manager().get_watcher_count()
        watcher_count += get_link_verification_manager().get_watcher_count()
        watcher_count += get_mt_order_verification_manager().get_watcher_count()
    except Exception:
        pass
    try:
        watcher_count += get_p_value_storage().get_watcher_count()
    except Exception:
        pass

    diagnostics = {
        "fd_open_count": _get_fd_open_count(),
        "fd_limit": _get_fd_limit(),
        "watcher_count": watcher_count,
    }
    diagnostics.update(http_client.get_client_stats())
    try:
        proxy_state = get_proxy_runtime_state()
        diagnostics["proxy_pool_size"] = int(proxy_state.get("pool_size") or 0)
        diagnostics["proxy_pool_valid_size"] = int(proxy_state.get("pool_valid_size") or 0)
    except Exception:
        pass
    try:
        diagnostics.update(get_redis_runtime_diagnostics())
    except Exception:
        pass
    try:
        diagnostics.update(get_go_runtime_diagnostics())
    except Exception:
        pass
    return diagnostics

           
def stop_meituan_service():
    """停止美团订单查询服务"""
    import subprocess
    global meituan_process
    if meituan_process:
        try:
            try:
                logger.info("正在关闭美团订单查询服务...")
            except:
                print("正在关闭美团订单查询服务...")
            meituan_process.terminate()
                    
            try:
                meituan_process.wait(timeout=5)
                try:
                    logger.info("✅ 美团订单查询服务已优雅关闭")
                except:
                    print("✅ 美团订单查询服务已优雅关闭")
            except subprocess.TimeoutExpired:
                try:
                    logger.warning("美团订单查询服务未在5秒内关闭，强制终止...")
                except:
                    print("美团订单查询服务未在5秒内关闭，强制终止...")
                meituan_process.kill()
                meituan_process.wait()
                try:
                    logger.info("✅ 美团订单查询服务已强制关闭")
                except:
                    print("✅ 美团订单查询服务已强制关闭")
            meituan_process = None
        except Exception as e:
            try:
                logger.error(f"关闭美团订单查询服务时出错: {e}")
            except:
                print(f"关闭美团订单查询服务时出错: {e}")


def should_auto_start_meituan_service() -> bool:
    explicit = os.getenv("WX_SERVICE_AUTO_START_GO", "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    return sys.platform == "win32"

def shutdown_runtime_services():
    stop_meituan_service()


atexit.register(shutdown_runtime_services)

                                   
def get_uvicorn_log_config():
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "max_warning": {
                "()": "utils.logging_filters.MaxLevelFilter",
                "level": "WARNING",
            },
            "exclude_healthchecks": {
                "()": "utils.logging_filters.HealthcheckAccessFilter",
            },
        },
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": "%(asctime)s - %(name)s - %(levelname)s - %(client_addr)s - \"%(request_line)s\" %(status_code)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "stdout": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["max_warning"],
            },
            "stderr": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "level": "ERROR",
            },
            "access_stdout": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["exclude_healthchecks"],
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["stdout", "stderr"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["stdout", "stderr"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access_stdout"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动和关闭时的处理"""
    import subprocess
            
                     
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    logging.config.dictConfig(get_uvicorn_log_config())
    for log in [uvicorn_logger, uvicorn_error_logger, uvicorn_access_logger]:
        log.setLevel(logging.INFO)
        log.propagate = False
    
                 
    from text_processors.stateful_processor import start_cleanup_task, stop_cleanup_task
    from utils.redis_async import ping_redis, close_redis_client
    from utils.proxy_utils import start_proxy_pool_prewarm_task, stop_proxy_pool_prewarm_task
    start_cleanup_task(logger)
    try:
        redis_ok = await ping_redis()
        logger.info("Redis连接检查完成: ok=%s", redis_ok)
    except Exception as e:
        logger.warning("Redis连接检查失败，应用将继续降级运行: %s", e)

                      
    global meituan_process
    from utils.path_utils import get_project_root, first_existing_path

    base_dir = get_project_root()
    exe_candidates = [
        base_dir / "meituan-query.exe",
        base_dir / "meituan-query",
        Path.cwd() / "meituan-query.exe",
        Path.cwd() / "meituan-query",
    ]

    if should_auto_start_meituan_service():
        exe_path = first_existing_path(exe_candidates)

        if exe_path is not None:
            try:
                logger.info(f"正在启动美团订单查询服务: {exe_path}")
                
                meituan_process = subprocess.Popen(
                    [str(exe_path)],
                    cwd=str(exe_path.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                await asyncio.sleep(1)
                if meituan_process.poll() is not None:
                    exit_code = meituan_process.returncode
                    logger.error(f"❌ 美团订单查询服务启动失败，进程已退出 (退出码: {exit_code})")
                    meituan_process = None
                else:
                    logger.info(f"✅ 美团订单查询服务已启动 (PID: {meituan_process.pid})")
            except Exception as e:
                logger.error(f"启动美团订单查询服务失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.warning("未找到美团订单查询服务可执行文件，尝试过的路径: %s", [str(item) for item in exe_candidates])
            logger.warning("美团订单查询服务将不会启动")
    else:
        logger.info(
            "当前平台默认不由 Python 自动拉起美团订单查询服务: platform=%s expected=systemd_or_external",
            sys.platform,
        )

    start_proxy_pool_prewarm_task()
    
    yield          
    
                            
    await stop_proxy_pool_prewarm_task()
    await stop_cleanup_task()
    try:
        await close_redis_client()
    except Exception as e:
        logger.warning("关闭 Redis 客户端失败: %s", e)
    try:
        from utils import http_client
        await http_client.aclose()
    except Exception as e:
        logger.warning("关闭 HTTP 客户端池失败: %s", e)
    if should_auto_start_meituan_service():
        stop_meituan_service()


app = FastAPI(title="微信公众号服务器", lifespan=lifespan)
app.mount("/web/static", StaticFiles(directory=str(resolve_project_path("web", "static"))), name="web_static")


@app.get("/healthz")
async def healthz():
    return JSONResponse({
        "ok": True,
        "service": "wx_service",
        "platform": sys.platform,
        **_get_runtime_diagnostics(),
    })


@app.get("/readyz")
async def readyz():
    from utils.go_local_api import GO_LOCAL_API_BASE_URL, GO_LOCAL_API_SOCKET_PATH
    from utils import http_client
    from utils.redis_async import ping_redis

    payload = {
        "ok": True,
        "service": "wx_service",
        "platform": sys.platform,
        "go_expected_external": not should_auto_start_meituan_service(),
        "go_base_url": GO_LOCAL_API_BASE_URL,
        "go_socket_path": GO_LOCAL_API_SOCKET_PATH,
        **_get_runtime_diagnostics(),
    }

    try:
        payload["redis_reachable"] = await ping_redis()
    except Exception as exc:
        payload["redis_reachable"] = False
        payload["redis_error"] = exc.__class__.__name__

    if not should_auto_start_meituan_service():
        try:
            response = await http_client.get(
                f"{GO_LOCAL_API_BASE_URL}/healthz",
                timeout=1.0,
                uds=GO_LOCAL_API_SOCKET_PATH,
            )
            payload["go_health_status"] = response.status_code
            payload["go_reachable"] = response.status_code == 200
            if response.status_code != 200:
                return JSONResponse(payload, status_code=503)
        except Exception as exc:
            payload["ok"] = False
            payload["go_reachable"] = False
            payload["error"] = f"go_health_check_failed: {exc.__class__.__name__}"
            return JSONResponse(payload, status_code=503)

    return JSONResponse(payload)


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)

      
app.include_router(auth_router)
app.include_router(material_router)
app.include_router(wechat_router)
app.include_router(christmas_hat_router)
app.include_router(waimai_router)
app.include_router(order_rankings_router)
app.include_router(sbti_router)
app.include_router(site_verification_router)

if __name__ == "__main__":
    import uvicorn

    socket_path = get_service_socket_path()
                          
    filtered_argv = [arg for arg in sys.argv if arg not in ['-log']]
    sys.argv = filtered_argv
    
                  
    if socket_path:
        prepare_unix_socket_path(socket_path)
        previous_umask = os.umask(0)
        try:
            uvicorn.run(
                app,
                uds=socket_path,
                log_config=get_uvicorn_log_config()
            )
        finally:
            os.umask(previous_umask)
    else:
        uvicorn.run(
            app, 
            host="0.0.0.0", 
            port=80,
            log_config=get_uvicorn_log_config()
        )
