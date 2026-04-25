"""
日志工具模块 - 支持Windows彩色输出
"""
import logging
import sys

from .logging_filters import MaxLevelFilter

                 
_LOG_ENABLED = False


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

    logger.propagate = False
    
    return logger
