"""
日志工具模块 - 支持Windows彩色输出
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .logging_filters import MaxLevelFilter

                 
_LOG_ENABLED = False
_FILE_HANDLER: logging.Handler | None = None
DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5


def _get_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def enable_logging():
    """启用日志输出（由命令行参数 -log 触发）"""
    global _LOG_ENABLED
    _LOG_ENABLED = True


def setup_ansi_colors():
    """设置ANSI颜色支持"""
    if sys.platform == "win32":
        try:
            import colorama
            colorama.init(autoreset=True)
        except ImportError:
                                                  
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass


def _get_shared_file_handler(formatter: logging.Formatter) -> logging.Handler | None:
    global _FILE_HANDLER
    if _FILE_HANDLER is not None:
        return _FILE_HANDLER
    try:
        log_dir = os.getenv("WX_SERVICE_LOG_DIR", "").strip() or os.getenv("LOGS_DIRECTORY", "").strip()
        if log_dir:
            log_path = Path(log_dir).expanduser().resolve()
        else:
            from .path_utils import get_log_dir
            log_path = get_log_dir()
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path / "app.log",
            maxBytes=_get_env_int("WX_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES, 1024 * 1024, 200 * 1024 * 1024),
            backupCount=_get_env_int("WX_LOG_BACKUP_COUNT", DEFAULT_LOG_BACKUP_COUNT, 1, 20),
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        _FILE_HANDLER = file_handler
        return _FILE_HANDLER
    except Exception:
        return None


def setup_logger(name: str = __name__) -> logging.Logger:
    """
    设置日志记录器，支持Windows彩色输出
    所有日志输出到标准输出，由 nssm 自动捕获并写入文件
    
    Args:
        name: 日志记录器名称
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    
                      
    if _LOG_ENABLED:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARN)
    
                        
    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.INFO)
        stdout_handler.addFilter(MaxLevelFilter(logging.WARNING))
        stdout_handler.setFormatter(formatter)

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.ERROR)
        stderr_handler.setFormatter(formatter)

        logger.addHandler(stdout_handler)
        logger.addHandler(stderr_handler)
        file_handler = _get_shared_file_handler(formatter)
        if file_handler is not None:
            logger.addHandler(file_handler)

    logger.propagate = False
    
    return logger
