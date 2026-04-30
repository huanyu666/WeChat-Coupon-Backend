"""
P值存储管理

将用户的P值保存到配置文件中
"""
import os
import json
import tempfile
import time
from typing import Optional, Dict
import threading
from utils.path_utils import resolve_runtime_data_path


class PValueStorage:
    """
    P值存储管理器
    
    支持每个用户绑定多个P值，每个P值可以设置别名
    数据结构：
    {
        "p_values": {
            "user_id": {
                "current": "p_value_id",  # 当前使用的P值ID
                "values": {
                    "p_value_id": {
                        "p_value": "实际的P值",
                        "alias": "别名",
                        "created_at": timestamp
                    }
                }
            }
        }
    }
    """
    
    def __init__(self, config_file: str = None):
        """
        初始化存储管理器
        
        Args:
            config_file: P值配置文件路径
        """
        if config_file is None:
            config_file = "p_values.json"

        self.config_file = self._resolve_config_path(config_file)
        self._lock = threading.RLock()
        self._cache: Dict[str, Dict] = {}
        self._last_save_time = 0
        self._last_loaded_mtime_ns: Optional[int] = None
        self._ensure_config_dir_ready()
        print(f"[INFO] P值文件路径: active={self.config_file}", flush=True)
        self._load()

    def _resolve_config_path(self, config_file: str) -> str:
        config_path = os.fspath(config_file)
        if os.path.isabs(config_path):
            return config_path
        return os.fspath(resolve_runtime_data_path(config_path).resolve())

    def _ensure_config_dir_ready(self):
        config_dir = os.path.dirname(self.config_file)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
    
    def _load(self, is_external_change: bool = False):
        """
        加载P值配置文件
        
        Args:
            is_external_change: 是否是外部修改触发的加载
        """
        with self._lock:
            try:
                if os.path.exists(self.config_file):
                    with open(self.config_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        new_cache = data.get('p_values', {})
                        
                                        
                        for user_id, user_data in new_cache.items():
                            if isinstance(user_data, str):
                                p_value_id = "default"
                                new_cache[user_id] = {
                                    "current": p_value_id,
                                    "values": {
                                        p_value_id: {
                                            "p_value": user_data,
                                            "alias": "默认",
                                            "created_at": int(time.time())
                                        }
                                    }
                                }
                            elif isinstance(user_data, dict):
                                if "current" not in user_data:
                                    user_data["current"] = None
                                if "values" not in user_data:
                                    user_data["values"] = {}
                        
                        self._cache = new_cache
                        self._last_loaded_mtime_ns = self._get_config_mtime_ns()
                        if is_external_change:
                            print(f"[INFO] P值配置文件已从外部更新，已重新加载")
                else:
                    if not is_external_change:                 
                        self._cache = {}
            except Exception as e:
                if is_external_change:
                    error_kind = self._classify_storage_error(e)
                    if error_kind == "emfile":
                        print(f"[WARNING] 检测到P值配置文件被修改，但重载失败（打开文件过多）: {e}")
                    elif error_kind == "format":
                        print(f"[WARNING] 检测到P值配置文件被修改，但重载失败（格式错误）: {e}")
                    else:
                        print(f"[WARNING] 检测到P值配置文件被修改，但重载失败（读写异常）: {e}")
                    print(f"[WARNING] 继续使用内存中的配置，请检查文件状态")
                else:
                    print(f"[WARNING] 加载P值配置文件失败: {e}")
                    self._cache = {}
    
    def _save(self):
        """保存P值到配置文件"""
        with self._lock:
            try:
                self._last_save_time = time.time()
                
                data = {
                    'p_values': self._cache
                }
                
                dir_path = os.path.dirname(self.config_file)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)

                fd, temp_path = tempfile.mkstemp(
                    dir=dir_path or ".",
                    prefix=f".{os.path.basename(self.config_file)}.",
                    suffix=".tmp",
                    text=True,
                )
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.config_file)
                self._last_loaded_mtime_ns = self._get_config_mtime_ns()
                
            except Exception as e:
                try:
                    if 'temp_path' in locals() and os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception:
                    pass
                error_kind = self._classify_storage_error(e)
                if error_kind == "emfile":
                    print(f"[ERROR] 保存P值配置文件失败（打开文件过多）: {e}")
                else:
                    print(f"[ERROR] 保存P值配置文件失败: {e}")
    
    def get_current(self, user_id: str) -> Optional[str]:
        """
        获取用户当前使用的P值
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        
        current_id = user_data.get("current")
        if not current_id:
            return None
        
        values = user_data.get("values", {})
        p_value_info = values.get(current_id)
        if not p_value_info:
            return None
        
        return p_value_info.get("p_value")
    
    
    def add(self, user_id: str, p_value: str, alias: str = None, p_value_id: str = None) -> str:
        """
        添加新的P值
        
        """
        self._maybe_reload_external_change()
        import uuid
        
        if user_id not in self._cache:
            self._cache[user_id] = {
                "current": None,
                "values": {}
            }
        
        user_data = self._cache[user_id]
        if not p_value_id:
            p_value_id = str(uuid.uuid4())[:8]
        if not alias:
            alias = f"P值{len(user_data.get('values', {})) + 1}"
        if "values" not in user_data:
            user_data["values"] = {}
        
        user_data["values"][p_value_id] = {
            "p_value": p_value,
            "alias": alias,
            "created_at": int(time.time())
        }
        if not user_data.get("current"):
            user_data["current"] = p_value_id
        
        self._save()
        return p_value_id
    
    def update(self, user_id: str, p_value_id: str, p_value: str = None, alias: str = None) -> bool:
        """
        更新P值信息
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        
        values = user_data.get("values", {})
        if p_value_id not in values:
            return False
        
        p_value_info = values[p_value_id]
        if p_value is not None:
            p_value_info["p_value"] = p_value
        if alias is not None:
            p_value_info["alias"] = alias
        
        self._save()
        return True
    
    def delete(self, user_id: str, p_value_id: str = None) -> bool:
        """
        删除P值
        
        """
        self._maybe_reload_external_change()
        if user_id not in self._cache:
            return False
        
        user_data = self._cache[user_id]
        
        if p_value_id is None:
            del self._cache[user_id]
            self._save()
            return True
        
        values = user_data.get("values", {})
        if p_value_id not in values:
            return False
        
        del values[p_value_id]
        
        if user_data.get("current") == p_value_id:
            remaining_ids = list(values.keys())
            if remaining_ids:
                user_data["current"] = remaining_ids[0]
            else:
                user_data["current"] = None
        if not values:
            del self._cache[user_id]
        
        self._save()
        return True
    
    def set_current(self, user_id: str, p_value_id: str = None) -> bool:
        """
        设置当前使用的P值
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        
        values = user_data.get("values", {})
        if not values:
            return False
        
        if p_value_id is None:
            p_value_id = list(values.keys())[0]
        
        if p_value_id not in values:
            return False
        
        user_data["current"] = p_value_id
        self._save()
        return True
    
    def get_all(self, user_id: str) -> Dict[str, Dict]:
        """
        获取用户的所有P值
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return {}
        
        return user_data.get("values", {}).copy()
    
    def get_current_id(self, user_id: str) -> Optional[str]:
        """
        获取当前使用的P值ID
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        
        return user_data.get("current")
    
    def has(self, user_id: str) -> bool:
        """
        检查用户是否有保存的P值
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        
        values = user_data.get("values", {})
        return len(values) > 0
    
    def find_by_alias(self, user_id: str, alias: str) -> Optional[str]:
        """
        根据别名查找P值ID
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        
        values = user_data.get("values", {})
        for p_value_id, p_value_info in values.items():
            if p_value_info.get("alias") == alias:
                return p_value_id
        
        return None
    
    def find_by_p_value(self, user_id: str, p_value: str) -> Optional[str]:
        """
        根据P值查找P值ID
        
        """
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        
        values = user_data.get("values", {})
        for p_value_id, p_value_info in values.items():
            if p_value_info.get("p_value") == p_value:
                return p_value_id
        
        return None
    
    def _get_config_mtime_ns(self) -> Optional[int]:
        try:
            return os.stat(self.config_file).st_mtime_ns
        except FileNotFoundError:
            return None

    def _classify_storage_error(self, exc: Exception) -> str:
        message = str(exc).lower()
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 24:
            return "emfile"
        if "too many open files" in message or "emfile" in message:
            return "emfile"
        if isinstance(exc, json.JSONDecodeError):
            return "format"
        return "io"

    def _maybe_reload_external_change(self) -> None:
        with self._lock:
            current_mtime_ns = self._get_config_mtime_ns()
            if current_mtime_ns is None or current_mtime_ns == self._last_loaded_mtime_ns:
                return
            if abs(time.time() - self._last_save_time) <= 1.0:
                self._last_loaded_mtime_ns = current_mtime_ns
                return
            self._load(is_external_change=True)

    def get_watcher_count(self) -> int:
        return 0

_storage = None


def get_p_value_storage() -> PValueStorage:
    global _storage
    if _storage is None:
        _storage = PValueStorage()
    return _storage
