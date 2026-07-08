import sqlite3
import os

# 数据库文件路径存放在项目根目录下
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

def init_db():
    """初始化数据库，创建符合 IDC 商业标准的业务管理表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ecs_business (
            instance_id TEXT PRIMARY KEY,
            reset_day INTEGER DEFAULT 1,         -- 账单锚点日 (比如每月 8 号)
            traffic_limit_gb INTEGER DEFAULT 500, -- 流量限制 (随时可改以解封)
            expire_time TEXT DEFAULT '',         -- 客户到期日 (如 '2026-08-08')
            traffic_start_time TEXT DEFAULT ''   -- ⭐ 核心：流量统计起点，精确到秒，用于中途清零
        )
    """)
    conn.commit()
    conn.close()

def get_business_data(instance_id: str) -> dict:
    """获取某台机器的本地商业计费数据，如果没有则自动初始化一条默认数据"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT reset_day, traffic_limit_gb, expire_time FROM ecs_business WHERE instance_id = ?", 
        (instance_id,)
    )
    row = cursor.fetchone()
    
    if row:
        conn.close()
        return {
            "reset_day": row[0],
            "traffic_limit_gb": row[1],
            "expire_time": row[2]
        }
    else:
        # 如果新开的机器本地还没记账，默认初始化：开机时间为当前，到期时间为1个月后
        from datetime import datetime, timedelta
        default_expire = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        default_reset = datetime.now().day
        
        cursor.execute("""
            INSERT INTO ecs_business (instance_id, reset_day, traffic_limit_gb, expire_time)
            VALUES (?, ?, ?, ?)
        """, (instance_id, default_reset, 500, default_expire))
        conn.commit()
        conn.close()
        
        return {
            "reset_day": default_reset,
            "traffic_limit_gb": 500,
            "expire_time": default_expire
        }

def update_business_data(instance_id: str, field: str, value):
    """通用修改函数，用来修改重置时间、流量限制、到期时间等"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE ecs_business SET {field} = ? WHERE instance_id = ?", (value, instance_id))
    conn.commit()
    conn.close()
