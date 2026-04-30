"""商家券存储管理"""
import os
import sqlite3
import time
from typing import Optional, Dict, List
import threading
from contextlib import contextmanager
from utils.path_utils import resolve_runtime_data_path


class MerchantCouponStorage:
    """商家券存储管理器"""
    
    def __init__(self, db_path: str = None):
        """初始化存储管理器"""
        self.storage_dir = None
        if db_path is None:
            self.storage_dir = os.fspath(resolve_runtime_data_path('merchant_coupons').resolve())
            self._ensure_storage_dir_ready()
            db_path = os.path.join(self.storage_dir, 'merchant_coupons.db')
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))
        
        self.db_path = db_path
        self._lock = threading.Lock()          
        if self.storage_dir:
            print(f"[INFO] 商家券数据库路径: active={self.db_path}", flush=True)
        self._init_database()

    def _ensure_storage_dir_ready(self):
        if not self.storage_dir:
            return
        os.makedirs(self.storage_dir, exist_ok=True)
    
    def _init_database(self):
        """初始化数据库"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS merchant_coupons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    to_user_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    poi_value TEXT NOT NULL,
                    allowance TEXT NOT NULL,
                    ad_activity_flag TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_coupons 
                ON merchant_coupons(to_user_name, user_id, created_at DESC)
            """)
            
            cursor.execute("PRAGMA journal_mode=WAL")
            
            cursor.execute("PRAGMA synchronous=NORMAL")            
            cursor.execute("PRAGMA cache_size=-64000")                            
            cursor.execute("PRAGMA temp_store=MEMORY")              
            
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row                    
        try:
            yield conn
        finally:
            conn.close()
    
    def add(self, to_user_name: str, user_id: str, poi_value: str, allowance: str, ad_activity_flag: str, title: str) -> bool:
        """添加商家券（超出10条删除最旧）"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT id FROM merchant_coupons
                    WHERE to_user_name = ? AND user_id = ? 
                    AND poi_value = ? AND allowance = ? AND ad_activity_flag = ?
                    LIMIT 1
                """, (to_user_name, user_id, poi_value, allowance, ad_activity_flag))
                
                if cursor.fetchone():
                    return False
                
                created_at = int(time.time())
                cursor.execute("""
                    INSERT INTO merchant_coupons 
                    (to_user_name, user_id, poi_value, allowance, ad_activity_flag, title, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (to_user_name, user_id, poi_value, allowance, ad_activity_flag, title, created_at))
                
                cursor.execute("""
                    DELETE FROM merchant_coupons
                    WHERE to_user_name = ? AND user_id = ?
                    AND id NOT IN (
                        SELECT id FROM merchant_coupons
                        WHERE to_user_name = ? AND user_id = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT 10
                    )
                """, (to_user_name, user_id, to_user_name, user_id))
                
                conn.commit()
                return True
    
    def get_all(self, to_user_name: str, user_id: str) -> List[Dict]:
        """获取用户的商家券列表（按时间倒序）"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT poi_value, allowance, ad_activity_flag, title, created_at
                    FROM merchant_coupons
                    WHERE to_user_name = ? AND user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                """, (to_user_name, user_id))
                
                coupons = []
                for row in cursor.fetchall():
                    coupons.append({
                        "poi_value": row["poi_value"],
                        "allowance": row["allowance"],
                        "ad_activity_flag": row["ad_activity_flag"],
                        "title": row["title"],
                        "created_at": row["created_at"]
                    })
                
                return coupons
    
    def get_by_index(self, to_user_name: str, user_id: str, index: int) -> Optional[Dict]:
        """根据索引获取商家券（索引从1开始）"""
        coupons = self.get_all(to_user_name, user_id)
        if 1 <= index <= len(coupons):
            return coupons[index - 1]
        return None
    
    def delete_by_index(self, to_user_name: str, user_id: str, index: int) -> bool:
        """根据索引删除商家券"""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT id FROM merchant_coupons
                    WHERE to_user_name = ? AND user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                """, (to_user_name, user_id))
                
                all_ids = [row[0] for row in cursor.fetchall()]
                
                if 1 <= index <= len(all_ids):
                    id_to_delete = all_ids[index - 1]
                    cursor.execute("""
                        DELETE FROM merchant_coupons
                        WHERE id = ?
                    """, (id_to_delete,))
                    
                    conn.commit()
                    return True
                
                return False


_storage = None


def get_merchant_coupon_storage() -> MerchantCouponStorage:
    """获取商家券存储管理器单例"""
    global _storage
    if _storage is None:
        _storage = MerchantCouponStorage()
    return _storage
