import json
import os
import tempfile
import time
import threading
import uuid
from typing import Dict, Optional, Tuple, List
from datetime import datetime, timedelta
from utils.path_utils import resolve_runtime_data_path


INFINITE_USES_THRESHOLD = 1_000_000_000            
INFINITE_HOURS_THRESHOLD = 88888888                   


class ActivationCodeManager:
    
    def __init__(self, storage_file: str = "activation_codes.json"):
        self.storage_file = self._resolve_storage_path(storage_file)
        self._codes: Dict[str, list] = {}
        self._code_index: Dict[str, Dict] = {}
        self._lock = threading.RLock()
        self._last_save_time = 0
        self._last_loaded_mtime_ns: Optional[int] = None

        self._ensure_storage_dir_ready()
        print(f"[INFO] 激活码文件路径: active={self.storage_file}", flush=True)
        
        self._load()

    def _resolve_storage_path(self, storage_file: str) -> str:
        storage_path = os.fspath(storage_file)
        if os.path.isabs(storage_path):
            return storage_path
        return os.fspath(resolve_runtime_data_path(storage_path).resolve())

    def _ensure_storage_dir_ready(self):
        storage_dir = os.path.dirname(self.storage_file)
        if storage_dir and not os.path.exists(storage_dir):
            os.makedirs(storage_dir, exist_ok=True)
    
    def _load(self, is_external_change: bool = False):
        with self._lock:
            if os.path.exists(self.storage_file):
                try:
                    with open(self.storage_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        new_codes = data.get('codes', {})
                        
                        self._codes = new_codes
                        self._rebuild_index()
                        self._cleanup_expired_unlocked()
                        self._last_loaded_mtime_ns = self._get_storage_mtime_ns()
                        
                        if is_external_change:
                            print(f"[INFO] 激活码文件已从外部更新，已重新加载")
                except Exception as e:
                    if is_external_change:
                        error_kind = self._classify_storage_error(e)
                        if error_kind == "emfile":
                            print(f"[WARNING] 检测到激活码文件被修改，但重载失败（打开文件过多）: {e}")
                        elif error_kind == "format":
                            print(f"[WARNING] 检测到激活码文件被修改，但重载失败（格式错误）: {e}")
                        else:
                            print(f"[WARNING] 检测到激活码文件被修改，但重载失败（读写异常）: {e}")
                        print(f"[WARNING] 继续使用内存中的配置，请检查文件状态")
                    else:
                        print(f"[ERROR] 加载激活码文件失败: {e}")
                        self._codes = {}
                        self._code_index = {}
            else:
                if not is_external_change:
                    self._codes = {}
                    self._code_index = {}
    
    def _save_unlocked(self):
        try:
            self._last_save_time = time.time()
            
            data = {
                'codes': self._codes,
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            dir_path = os.path.dirname(self.storage_file)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            fd, temp_path = tempfile.mkstemp(
                dir=dir_path or ".",
                prefix=f".{os.path.basename(self.storage_file)}.",
                suffix=".tmp",
                text=True,
            )
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.storage_file)
            self._last_loaded_mtime_ns = self._get_storage_mtime_ns()
                    
        except Exception as e:
            try:
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
            error_kind = self._classify_storage_error(e)
            if error_kind == "emfile":
                print(f"[ERROR] 保存激活码文件失败（打开文件过多）: {e}")
            else:
                print(f"[ERROR] 保存激活码文件失败: {e}")
    
    def _save(self):
        with self._lock:
            self._save_unlocked()
    
    def _rebuild_index(self):
        self._code_index = {}
        for user_id, code_list in self._codes.items():
            for code_info in code_list:
                code_upper = code_info["code"].upper()
                self._code_index[code_upper] = {
                    "user_id": user_id,
                    "code_info": code_info
                }
    
    def _generate_secure_code_value(self, length: Optional[int] = None) -> str:
        """
        生成高熵激活码，优先使用UUIDv7，不支持时回退UUIDv4
        """
        if hasattr(uuid, "uuid7"):
            base_code = uuid.uuid7().hex
        else:
            base_code = uuid.uuid4().hex
        
        base_code = base_code.upper()
        if length and length > 0:
            return base_code[:length]
        return base_code
    
    def generate_code(self, user_id: str, duration_hours: int = 24, length: Optional[int] = None, max_uses: int = 1) -> str:
        self._maybe_reload_external_change()
        if duration_hours > INFINITE_HOURS_THRESHOLD:
            duration_hours = INFINITE_HOURS_THRESHOLD
        elif duration_hours < 1:
            duration_hours = 1
        
        if max_uses > INFINITE_USES_THRESHOLD:
            max_uses = INFINITE_USES_THRESHOLD
        elif max_uses < 1:
            max_uses = 1
        
        while True:
            code = self._generate_secure_code_value(length)
            code_upper = code.upper()
            
                        
            if user_id in self._codes:
                if any(c["code"].upper() == code_upper for c in self._codes[user_id]):
                    continue
            if code_upper in self._code_index:
                continue
            break
        
        expires_at = time.time() + (duration_hours * 3600)
        
        if user_id not in self._codes:
            self._codes[user_id] = []
        
        code_info = {
            "code": code,
            "expires_at": expires_at,
            "created_at": time.time(),
            "max_uses": max_uses,
            "used_count": 0,
            "bound_user": None
        }
        self._codes[user_id].append(code_info)
        
        self._code_index[code_upper] = {
            "user_id": user_id,
            "code_info": code_info
        }
        
        self._save()
        
        return code
    
    def verify_code(self, code: str, user_id: str = None, consume: bool = True) -> Tuple[bool, str]:
        self._maybe_reload_external_change()
        self._cleanup_expired()
        
        code_upper = code.upper().strip()
        
        if code_upper not in self._code_index:
            return False, "激活码不存在或已过期"
        
        index_entry = self._code_index[code_upper]
        matched_user_id = index_entry["user_id"]
        matched_code = index_entry["code_info"]
        
        if time.time() > matched_code["expires_at"]:
            self._remove_code(matched_user_id, matched_code)
            return False, "激活码已过期"
        
        max_uses = matched_code.get("max_uses", 1)
        used_count = matched_code.get("used_count", 0)
        
        if used_count >= max_uses:
            return False, "激活码使用次数已用完"
        
        bound_user = matched_code.get("bound_user")
        if bound_user and bound_user != user_id:
            return False, "该激活码已绑定其他用户"
        
        if consume:
            matched_code["used_count"] = used_count + 1
            if matched_code["used_count"] >= max_uses:
                self._remove_code(matched_user_id, matched_code)
            else:
                self._save()

        return True, "验证成功"

    def consume_code_uses(self, code: str, count: int, user_id: str = None) -> Tuple[bool, str]:
        self._maybe_reload_external_change()
        self._cleanup_expired()

        if count <= 0:
            return True, "无需扣除"

        code_upper = code.upper().strip()
        if code_upper not in self._code_index:
            return False, "激活码不存在或已过期"

        index_entry = self._code_index[code_upper]
        matched_user_id = index_entry["user_id"]
        matched_code = index_entry["code_info"]

        if time.time() > matched_code["expires_at"]:
            self._remove_code(matched_user_id, matched_code)
            return False, "激活码已过期"

        bound_user = matched_code.get("bound_user")
        if bound_user and bound_user != user_id:
            return False, "该激活码已绑定其他用户"

        max_uses = matched_code.get("max_uses", 1)
        used_count = matched_code.get("used_count", 0)
        remaining_uses = max_uses - used_count
        if remaining_uses < count:
            return False, "激活码使用次数已用完"

        matched_code["used_count"] = used_count + count
        if matched_code["used_count"] >= max_uses:
            self._remove_code(matched_user_id, matched_code)
        else:
            self._save()

        return True, "扣除成功"

    def consume_code_uses_partial(self, code: str, count: int, user_id: str = None) -> Tuple[int, bool, str]:
        self._maybe_reload_external_change()
        self._cleanup_expired()

        if count <= 0:
            return 0, False, "无需扣除"

        code_upper = code.upper().strip()
        if code_upper not in self._code_index:
            return 0, False, "激活码不存在或已过期"

        index_entry = self._code_index[code_upper]
        matched_user_id = index_entry["user_id"]
        matched_code = index_entry["code_info"]

        if time.time() > matched_code["expires_at"]:
            self._remove_code(matched_user_id, matched_code)
            return 0, False, "激活码已过期"

        bound_user = matched_code.get("bound_user")
        if bound_user and bound_user != user_id:
            return 0, False, "该激活码已绑定其他用户"

        max_uses = matched_code.get("max_uses", 1)
        used_count = matched_code.get("used_count", 0)
        remaining_uses = max_uses - used_count
        if remaining_uses <= 0:
            self._remove_code(matched_user_id, matched_code)
            return 0, True, "激活码使用次数已用完"

        consume_count = min(count, remaining_uses)
        matched_code["used_count"] = used_count + consume_count
        exhausted = matched_code["used_count"] >= max_uses

        if exhausted:
            self._remove_code(matched_user_id, matched_code)
        else:
            self._save()

        if consume_count < count:
            return consume_count, True, "激活码使用次数已用完"

        return consume_count, exhausted, "扣除成功"
    
    def bind_code_to_user(self, code: str, user_id: str) -> Tuple[bool, str]:
        self._maybe_reload_external_change()
        self._cleanup_expired()
        
        code_upper = code.upper().strip()
        
        if code_upper not in self._code_index:
            return False, "激活码不存在或已过期"
        
        index_entry = self._code_index[code_upper]
        matched_user_id = index_entry["user_id"]
        matched_code = index_entry["code_info"]
        
        if time.time() > matched_code["expires_at"]:
            self._remove_code(matched_user_id, matched_code)
            return False, "激活码已过期"
        
        max_uses = matched_code.get("max_uses", 1)
        used_count = matched_code.get("used_count", 0)
        
        if used_count >= max_uses:
            return False, "激活码使用次数已用完"
        
        bound_user = matched_code.get("bound_user")
        if bound_user and bound_user != user_id:
            return False, "该激活码已绑定其他用户"
        
        matched_code["bound_user"] = user_id
        self._save()
        
        return True, "绑定成功"
    
    def _remove_code(self, user_id: str, code_info: Dict, save: bool = True):
        code_upper = code_info["code"].upper()
        
        if user_id in self._codes:
            if code_info in self._codes[user_id]:
                self._codes[user_id].remove(code_info)
            if not self._codes[user_id]:
                del self._codes[user_id]
        
        if code_upper in self._code_index:
            del self._code_index[code_upper]
        
        if save:
            self._save()
    
    def has_valid_code(self, user_id: str) -> Optional[Dict]:
        self._maybe_reload_external_change()
        self._cleanup_expired()
        
        current_time = time.time()
        
        for code_upper, index_entry in self._code_index.items():
            code_info = index_entry["code_info"]
            bound_user = code_info.get("bound_user")
            
            if bound_user != user_id:
                continue
            
            if current_time > code_info["expires_at"]:
                continue
            
            max_uses = code_info.get("max_uses", 1)
            used_count = code_info.get("used_count", 0)
            
            if used_count < max_uses:
                return {
                    "code": code_info["code"],
                    "expires_at": code_info["expires_at"],
                    "max_uses": max_uses,
                    "used_count": used_count,
                    "remaining_uses": max_uses - used_count
                }
        
        return None
    
    def get_all_codes_for_user(self, user_id: str) -> list:
        self._maybe_reload_external_change()
        self._cleanup_expired()
        
        if user_id not in self._codes or not self._codes[user_id]:
            return []
        
        result = []
        current_time = time.time()
        
        for code_info in self._codes[user_id]:
            expires_at = code_info["expires_at"]
            is_infinite_time = (expires_at - current_time) >= (INFINITE_HOURS_THRESHOLD * 3600 * 0.99)
            if is_infinite_time:
                remaining_seconds = None
                remaining_hours = None
                remaining_minutes = None
                expires_at_display = "永久"
            else:
                remaining_seconds = max(0, int(expires_at - current_time))
                remaining_hours = remaining_seconds // 3600
                remaining_minutes = (remaining_seconds % 3600) // 60
                expires_at_display = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S")
            
            max_uses = code_info.get("max_uses", 1)
            used_count = code_info.get("used_count", 0)
            is_infinite_uses = max_uses >= INFINITE_USES_THRESHOLD
            remaining_uses = max_uses - used_count if not is_infinite_uses else INFINITE_USES_THRESHOLD
            bound_user = code_info.get("bound_user")
            
            if (not is_infinite_time) and current_time > expires_at:
                status = "已过期"
            elif (not is_infinite_uses) and remaining_uses <= 0:
                status = "已用完"
            else:
                status = "有效"
            
            result.append({
                "code": code_info["code"],
                "status": status,
                "expires_at": expires_at_display,
                "remaining_hours": remaining_hours,
                "remaining_minutes": remaining_minutes,
                "remaining_seconds": remaining_seconds,
                "max_uses": "无限" if is_infinite_uses else max_uses,
                "used_count": used_count,
                "remaining_uses": "无限" if is_infinite_uses else remaining_uses,
                "created_at": datetime.fromtimestamp(code_info["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
                "bound_user": bound_user
            })
        
        return result
    
    def get_code_info(self, user_id: str) -> Optional[Dict]:
        codes = self.get_all_codes_for_user(user_id)
        if not codes:
            return None
        
        for code in codes:
            if code["status"] == "有效":
                return code
        
        return codes[0] if codes else None
    
    def revoke_code(self, user_id: str, code: str = None) -> Tuple[bool, str]:
        self._maybe_reload_external_change()
        if user_id not in self._codes or not self._codes[user_id]:
            return False, "没有找到激活码"
        
        if code is None:
            count = len(self._codes[user_id])
            for code_info in self._codes[user_id]:
                code_upper = code_info["code"].upper()
                if code_upper in self._code_index:
                    del self._code_index[code_upper]
            del self._codes[user_id]
            self._save()
            return True, f"已删除 {count} 个激活码"
        
        code_upper = code.upper().strip()
        for code_info in self._codes[user_id]:
            if code_info["code"].upper() == code_upper:
                self._remove_code(user_id, code_info)
                return True, f"已删除激活码: {code}"
        
        return False, f"激活码 {code} 不存在"
    
    def export_code(self, code: str) -> Optional[Tuple[str, Dict]]:
        """
        从当前管理器中移除并返回指定激活码的信息
        """
        with self._lock:
            self._maybe_reload_external_change_unlocked()
            self._cleanup_expired_unlocked()
            code_upper = code.upper().strip()
            if code_upper not in self._code_index:
                return None
            
            entry = self._code_index[code_upper]
            user_id = entry["user_id"]
            code_info = entry["code_info"]
                                
            copied = dict(code_info)
                                     
            self._remove_code(user_id, code_info, save=False)
            self._save_unlocked()
            return user_id, copied
    
    def import_code(self, user_id: str, code_info: Dict) -> bool:
        """
        将已有的激活码信息导入当前管理器（用于跨池迁移）
        """
        with self._lock:
            self._maybe_reload_external_change_unlocked()
            self._cleanup_expired_unlocked()
            code_upper = code_info["code"].upper()
            if code_upper in self._code_index:
                return False
            
            if user_id not in self._codes:
                self._codes[user_id] = []
            
            self._codes[user_id].append(code_info)
            self._code_index[code_upper] = {
                "user_id": user_id,
                "code_info": code_info
            }
            self._save_unlocked()
            return True
    
    def _cleanup_expired_unlocked(self):
        current_time = time.time()
        users_to_remove = []
        modified = False
        
        for user_id, code_list in self._codes.items():
            valid_codes = []
            for code_info in code_list:
                if current_time <= code_info["expires_at"]:
                    valid_codes.append(code_info)
                else:
                    code_upper = code_info["code"].upper()
                    if code_upper in self._code_index:
                        del self._code_index[code_upper]
                    modified = True
            
            if not valid_codes:
                users_to_remove.append(user_id)
                modified = True
            elif len(valid_codes) != len(code_list):
                self._codes[user_id] = valid_codes
                modified = True
        
        for user_id in users_to_remove:
            del self._codes[user_id]
        
        if modified:
            self._save_unlocked()
    
    def _cleanup_expired(self):
        with self._lock:
            self._cleanup_expired_unlocked()
    
    def get_all_codes(self) -> Dict[str, list]:
        self._maybe_reload_external_change()
        self._cleanup_expired()
        
        result = {}
        for user_id in self._codes.keys():
            result[user_id] = self.get_all_codes_for_user(user_id)
        
        return result
    
    def _get_storage_mtime_ns(self) -> Optional[int]:
        try:
            return os.stat(self.storage_file).st_mtime_ns
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

    def _maybe_reload_external_change_unlocked(self) -> None:
        current_mtime_ns = self._get_storage_mtime_ns()
        if current_mtime_ns is None or current_mtime_ns == self._last_loaded_mtime_ns:
            return
        if abs(time.time() - self._last_save_time) <= 1.0:
            self._last_loaded_mtime_ns = current_mtime_ns
            return
        self._load(is_external_change=True)

    def _maybe_reload_external_change(self) -> None:
        with self._lock:
            self._maybe_reload_external_change_unlocked()

    def refresh(self):
        """
        主动从磁盘重新加载激活码（用于跨进程或外部写入后）
        """
        self._maybe_reload_external_change()

    def get_watcher_count(self) -> int:
        return 0


_activation_manager = ActivationCodeManager("activation_codes.json")        
                 
_activation_manager_link = ActivationCodeManager("activation_codes_link.json")
                   
_activation_manager_mt_order = ActivationCodeManager("activation_codes_meituan_order.json")


def get_verification_manager() -> ActivationCodeManager:
    return _activation_manager


def get_link_verification_manager() -> ActivationCodeManager:
    return _activation_manager_link


def get_mt_order_verification_manager() -> ActivationCodeManager:
    return _activation_manager_mt_order


def migrate_code_between_pools(
    code: str,
    user_id: str,
    source_manager: ActivationCodeManager,
    target_manager: ActivationCodeManager,
    logger=None
) -> Tuple[bool, str]:
    """
    将激活码从 source_manager 迁移到 target_manager，并绑定到 user_id
    逻辑：
    1) 如果目标池已存在且验证通过，则仅绑定并成功返回
    2) 否则尝试源池验证，验证失败则返回错误
    3) 源池导出 -> 目标池导入并绑定；失败则回滚
    """
    def _warn(message: str):
        if logger and hasattr(logger, "warning"):
            logger.warning(message)
        else:
            print(f"[activation][warning] {message}", flush=True)
    
    try:
                        
        try:
            source_manager.refresh()
            target_manager.refresh()
        except Exception as e:
            _warn(f"refresh failed before migrate: {e}")
        
                    
        ok, err = target_manager.verify_code(code, user_id=user_id, consume=False)
        if ok:
            bind_ok, bind_msg = target_manager.bind_code_to_user(code, user_id)
            if not bind_ok:
                _warn(f"target pool bind failed: {bind_msg}")
                return False, bind_msg
            return True, "已在目标池中绑定"
        
              
        ok, err = source_manager.verify_code(code, user_id=user_id, consume=False)
        if not ok:
            _warn(f"source pool verify failed for {code}: {err}")
            return False, err
        
        exported = source_manager.export_code(code)
        if not exported:
            _warn(f"export failed for {code} from source pool")
            return False, "激活码迁移失败"
        
        orig_user_id, code_info = exported
                 
        code_info["bound_user"] = user_id
        import_ok = target_manager.import_code(user_id, code_info)
        if not import_ok:
                
            source_manager.import_code(orig_user_id, code_info)
            _warn("import to target failed, rolled back")
            return False, "激活码迁移失败"
        
        return True, "迁移成功"
    except Exception as e:
        _warn(f"migrate exception: {e}")
        return False, f"迁移异常: {e}"


def parse_duration_string(duration_str: str) -> int:
    duration_str = duration_str.strip().lower()
    
    import re
    match = re.match(r'(\d+)\s*([a-z\u4e00-\u9fa5]+)', duration_str)
    
    if not match:
        raise ValueError(f"无效的有效期格式: {duration_str}")
    
    value = int(match.group(1))
    unit = match.group(2)
    
    unit_mapping = {
        'h': 1,
        'hour': 1,
        'hours': 1,
        '小时': 1,
        'd': 24,
        'day': 24,
        'days': 24,
        '天': 24,
        'w': 168,
        'week': 168,
        'weeks': 168,
        '周': 168,
        '星期': 168,
        'm': 1/60,
        'min': 1/60,
        'minute': 1/60,
        'minutes': 1/60,
        '分钟': 1/60,
        '分': 1/60,
    }
    
    if unit not in unit_mapping:
        raise ValueError(f"不支持的时间单位: {unit}")
    
    hours = value * unit_mapping[unit]
    
    if hours < 1:
        hours = 1
    elif hours > 88888888:
        hours = 88888888
    
    return int(hours)
