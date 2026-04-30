"""
Scene 存储管理

将用户的 scene 保存到配置文件中
"""
import json
import os
import tempfile
import threading
import time
from typing import Dict, Optional

from utils.path_utils import resolve_runtime_data_path


class SceneStorage:
    """
    Scene 存储管理器

    数据结构：
    {
        "scenes": {
            "user_id": {
                "current": "scene_id",
                "values": {
                    "scene_id": {
                        "scene": "实际scene值",
                        "alias": "别名",
                        "created_at": timestamp
                    }
                }
            }
        }
    }
    """

    def __init__(self, config_file: str = None):
        if config_file is None:
            config_file = "scenes.json"

        self.config_file = self._resolve_config_path(config_file)
        self._lock = threading.RLock()
        self._cache: Dict[str, Dict] = {}
        self._last_save_time = 0.0
        self._last_loaded_mtime_ns: Optional[int] = None
        self._ensure_config_dir_ready()
        print(f"[INFO] Scene文件路径: active={self.config_file}", flush=True)
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
        with self._lock:
            try:
                if os.path.exists(self.config_file):
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    new_cache = data.get("scenes", {})
                    for user_id, user_data in new_cache.items():
                        if isinstance(user_data, str):
                            scene_id = "default"
                            new_cache[user_id] = {
                                "current": scene_id,
                                "values": {
                                    scene_id: {
                                        "scene": user_data,
                                        "alias": "默认",
                                        "created_at": int(time.time()),
                                    }
                                },
                            }
                        elif isinstance(user_data, dict):
                            user_data.setdefault("current", None)
                            user_data.setdefault("values", {})
                    self._cache = new_cache
                    self._last_loaded_mtime_ns = self._get_config_mtime_ns()
                    if is_external_change:
                        print("[INFO] Scene配置文件已从外部更新，已重新加载")
                elif not is_external_change:
                    self._cache = {}
            except Exception as e:
                if is_external_change:
                    error_kind = self._classify_storage_error(e)
                    if error_kind == "emfile":
                        print(f"[WARNING] 检测到Scene配置文件被修改，但重载失败（打开文件过多）: {e}")
                    elif error_kind == "format":
                        print(f"[WARNING] 检测到Scene配置文件被修改，但重载失败（格式错误）: {e}")
                    else:
                        print(f"[WARNING] 检测到Scene配置文件被修改，但重载失败（读写异常）: {e}")
                    print("[WARNING] 继续使用内存中的配置，请检查文件状态")
                else:
                    print(f"[WARNING] 加载Scene配置文件失败: {e}")
                    self._cache = {}

    def _save(self):
        with self._lock:
            try:
                self._last_save_time = time.time()
                data = {"scenes": self._cache}
                dir_path = os.path.dirname(self.config_file)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(
                    dir=dir_path or ".",
                    prefix=f".{os.path.basename(self.config_file)}.",
                    suffix=".tmp",
                    text=True,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.config_file)
                self._last_loaded_mtime_ns = self._get_config_mtime_ns()
            except Exception as e:
                try:
                    if "temp_path" in locals() and os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception:
                    pass
                error_kind = self._classify_storage_error(e)
                if error_kind == "emfile":
                    print(f"[ERROR] 保存Scene配置文件失败（打开文件过多）: {e}")
                else:
                    print(f"[ERROR] 保存Scene配置文件失败: {e}")

    def get_current(self, user_id: str) -> Optional[str]:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        current_id = user_data.get("current")
        if not current_id:
            return None
        scene_info = user_data.get("values", {}).get(current_id)
        if not scene_info:
            return None
        return scene_info.get("scene")

    def add(self, user_id: str, scene: str, alias: str = None, scene_id: str = None) -> str:
        self._maybe_reload_external_change()
        import uuid

        if user_id not in self._cache:
            self._cache[user_id] = {"current": None, "values": {}}

        user_data = self._cache[user_id]
        if not scene_id:
            scene_id = str(uuid.uuid4())[:8]
        if not alias:
            alias = f"scene{len(user_data.get('values', {})) + 1}"
        user_data.setdefault("values", {})
        user_data["values"][scene_id] = {
            "scene": scene,
            "alias": alias,
            "created_at": int(time.time()),
        }
        if not user_data.get("current"):
            user_data["current"] = scene_id
        self._save()
        return scene_id

    def update(
        self,
        user_id: str,
        scene_id: str,
        scene: str = None,
        alias: str = None,
    ) -> bool:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        values = user_data.get("values", {})
        if scene_id not in values:
            return False
        scene_info = values[scene_id]
        if scene is not None:
            scene_info["scene"] = scene
        if alias is not None:
            scene_info["alias"] = alias
        self._save()
        return True

    def delete(self, user_id: str, scene_id: str = None) -> bool:
        self._maybe_reload_external_change()
        if user_id not in self._cache:
            return False
        user_data = self._cache[user_id]
        if scene_id is None:
            del self._cache[user_id]
            self._save()
            return True
        values = user_data.get("values", {})
        if scene_id not in values:
            return False
        del values[scene_id]
        if user_data.get("current") == scene_id:
            remaining_ids = list(values.keys())
            user_data["current"] = remaining_ids[0] if remaining_ids else None
        if not values:
            del self._cache[user_id]
        self._save()
        return True

    def set_current(self, user_id: str, scene_id: str = None) -> bool:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        values = user_data.get("values", {})
        if not values:
            return False
        if scene_id is None:
            scene_id = list(values.keys())[0]
        if scene_id not in values:
            return False
        user_data["current"] = scene_id
        self._save()
        return True

    def get_all(self, user_id: str) -> Dict[str, Dict]:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return {}
        return user_data.get("values", {}).copy()

    def get_current_id(self, user_id: str) -> Optional[str]:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        return user_data.get("current")

    def has(self, user_id: str) -> bool:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return False
        return len(user_data.get("values", {})) > 0

    def find_by_alias(self, user_id: str, alias: str) -> Optional[str]:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        for scene_id, scene_info in user_data.get("values", {}).items():
            if scene_info.get("alias") == alias:
                return scene_id
        return None

    def find_by_scene(self, user_id: str, scene: str) -> Optional[str]:
        self._maybe_reload_external_change()
        user_data = self._cache.get(user_id)
        if not user_data:
            return None
        for scene_id, scene_info in user_data.get("values", {}).items():
            if scene_info.get("scene") == scene:
                return scene_id
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


def get_scene_storage() -> SceneStorage:
    global _storage
    if _storage is None:
        _storage = SceneStorage()
    return _storage
