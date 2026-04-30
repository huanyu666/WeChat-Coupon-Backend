"""
商家券存储管理 - SQLite版本

采用最优设计：
- 单个SQLite数据库文件，所有公众号共享
- 使用复合索引优化查询性能
- WAL模式提高并发性能
- 紧凑的字段类型设计
"""
import os
import sqlite3
import time
from typing import Optional, Dict, List
import threading
from contextlib import contextmanager
from utils.path_utils import resolve_runtime_data_path


class MerchantCouponStorageSQLite:
    """
    商家券存储管理器（SQLite版本）
    
    数据库设计：
    - 单个数据库文件存储所有公众号的数据
    - 表结构：merchant_coupons
      - id: INTEGER PRIMARY KEY AUTOINCREMENT（主键，最小占用）
      - to_user_name: TEXT（公众号ID）
      - user_id: TEXT（用户ID）
      - poi_value: TEXT（POI值）
      - allowance: TEXT（allowance_alliance_scenes参数）
      - ad_activity_flag: TEXT（ad_activity_flag参数）
      - title: TEXT（店铺名称）
      - created_at: INTEGER（时间戳，整数最小）
    
    索引设计：
    - 主索引：PRIMARY KEY (id)
    - 查询索引：CREATE INDEX idx_user_coupons ON merchant_coupons(to_user_name, user_id, created_at DESC)
      用于快速查询用户的商家券列表（按时间倒序）
    
    性能优化：
    - 使用WAL模式提高并发性能
    - 使用事务批量操作
    - 合理的页面大小设置
    """
    
    def __init__(self, db_path: str = None):
        """
        初始化存储管理器
        
        Args:
            db_path: 数据库文件路径，如果不提供则使用默认路径
        """
        if db_path is None:
            db_path = os.fspath(resolve_runtime_data_path('merchant_coupons', 'merchant_coupons.db').resolve())
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))
        
        self.db_path = db_path
        self._lock = threading.Lock()          
        self._ensure_db_dir_ready()
        print(f"[INFO] 商家券SQLite路径: active={self.db_path}", flush=True)
        self._init_database()

    def _ensure_db_dir_ready(self):
        if not self.db_path:
            return
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    
    def _init_database(self):
        """初始化数据库，创建表和索引"""
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
        """
        获取数据库连接的上下文管理器
        
        使用连接池模式，但SQLite的连接很轻量，每次操作创建新连接
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row                    
        try:
            yield conn
        finally:
            conn.close()
    
    def add(self, to_user_name: str, user_id: str, poi_value: str, allowance: str, ad_activity_flag: str, title: str) -> bool:
        """
        添加商家券
        
        从头部插入（最新的在第一位），如果超过10个，删除最旧的
        
        Args:
            to_user_name: 公众号ID
            user_id: 用户ID
            poi_value: POI值
            allowance: allowance_alliance_scenes参数值
            ad_activity_flag: ad_activity_flag参数值
            title: 店铺名称
            
        Returns:
            是否添加成功（如果已存在相同商家券则返回False）
        """
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
                    SELECT id FROM merchant_coupons
                    WHERE to_user_name = ? AND user_id = ?
                    ORDER BY created_at ASC, id ASC
                """, (to_user_name, user_id))
                
                all_ids = [row[0] for row in cursor.fetchall()]
                if len(all_ids) > 10:
                                                     
                    ids_to_delete = all_ids[:-10]            
                    placeholders = ','.join('?' * len(ids_to_delete))
                    cursor.execute(f"""
                        DELETE FROM merchant_coupons
                        WHERE id IN ({placeholders})
                    """, ids_to_delete)
                
                conn.commit()
                return True
    
    def get_all(self, to_user_name: str, user_id: str) -> List[Dict]:
        """
        获取用户的所有商家券
        
        按创建时间倒序排列（最新的在前）
        
        Args:
            to_user_name: 公众号ID
            user_id: 用户ID
            
        Returns:
            商家券列表，按创建时间倒序排列（最新的在前）
        """
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
        """
        根据索引获取商家券（索引从1开始）
        
        Args:
            to_user_name: 公众号ID
            user_id: 用户ID
            index: 索引（1-10）
            
        Returns:
            商家券信息，如果不存在则返回None
        """
        coupons = self.get_all(to_user_name, user_id)
        if 1 <= index <= len(coupons):
            return coupons[index - 1]
        return None
    
    def delete_by_index(self, to_user_name: str, user_id: str, index: int) -> bool:
        """
        根据索引删除商家券
        
        Args:
            to_user_name: 公众号ID
            user_id: 用户ID
            index: 索引（1-10）
            
        Returns:
            是否删除成功
        """
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


def get_merchant_coupon_storage() -> MerchantCouponStorageSQLite:
    """获取商家券存储管理器单例（SQLite版本）"""
    global _storage
    if _storage is None:
        _storage = MerchantCouponStorageSQLite()
    return _storage


