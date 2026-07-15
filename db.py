import sqlite3
import os
import calendar
import logging
from datetime import datetime, timedelta

# ==========================================
# 日志与路径配置
# ==========================================
logger = logging.getLogger("MG_Bot.DB")

# 获取当前 db.py 所在的绝对目录，并拼凑出数据库文件路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bot_data.db') # 替换为你实际的数据库文件名

def get_connection():
    """
    获取数据库连接。
    增加 timeout=5.0：防止多异步 Handler 并发访问时抛出 database is locked 错误。
    """
    return sqlite3.connect(DB_PATH, timeout=5.0)

def get_active_servers(user_id: int):
    """查询用户所有有效的服务器，返回包含 instance_id, name, region_id 的字典列表"""
    # 你的 sqlite3 查询逻辑，例如：
    # SELECT instance_id, name, region_id FROM ecs_business WHERE user_id = ? AND status = 'Running'
    pass

def init_db():
    """初始化数据库，创建符合 IDC 商业标准的业务管理表"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 1. 业务管理表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ecs_business (
                instance_id TEXT PRIMARY KEY,
                reset_day INTEGER DEFAULT 1,         -- 账单锚点日 (比如每月 8 号)
                traffic_limit_gb INTEGER DEFAULT 500, -- 流量限制 (随时可改以解封)
                expire_time TEXT DEFAULT '',         -- 客户到期日 (如 '2026-08-08')
                traffic_start_time TEXT DEFAULT ''   -- ⭐ 核心：流量统计起点，精确到秒，用于中途清零
            )
        """)
        # 2. 系统全局配置表 (新增)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sys_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # 3. 启动模板表 (新增)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                region_id TEXT PRIMARY KEY,
                region_name TEXT,
                template_id TEXT
            )
        """)
        
        # 写入默认的全局设置 (如果不存在的话)
        cursor.execute("INSERT OR IGNORE INTO sys_config (key, value) VALUES ('default_password', '@QS00008')")
        conn.commit()
    except Exception as e:
        logger.error(f"init_db 数据库初始化失败: {e}")
    finally:
        conn.close()  # 确保无论如何都释放连接

def get_business_data(instance_id: str) -> dict:
    """获取某台机器的本地商业计费数据，如果没有则自动初始化一条默认数据"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reset_day, traffic_limit_gb, expire_time FROM ecs_business WHERE instance_id = ?", 
            (instance_id,)
        )
        row = cursor.fetchone()
        
        if row:
            return {
                "reset_day": row[0],
                "traffic_limit_gb": row[1],
                "expire_time": row[2]
            }
        else:
            # 初始化默认数据
            now = datetime.now()
            year, month, day = now.year, now.month, now.day
            
            if month == 12:
                next_month = 1
                next_year = year + 1
            else:
                next_month = month + 1
                next_year = year
                
            # 防止极端情况（如 1月31日 开机，下个月只有 28 天）
            last_day_of_next_month = calendar.monthrange(next_year, next_month)[1]
            next_day = min(day, last_day_of_next_month)
            
            expire_date = now.replace(year=next_year, month=next_month, day=next_day)
            default_expire = expire_date.strftime("%Y-%m-%d")
            default_reset = day
            
            cursor.execute("""
                INSERT INTO ecs_business (instance_id, reset_day, traffic_limit_gb, expire_time)
                VALUES (?, ?, ?, ?)
            """, (instance_id, default_reset, 500, default_expire))
            conn.commit()
            
            return {
                "reset_day": default_reset,
                "traffic_limit_gb": 500,
                "expire_time": default_expire
            }
    except Exception as e:
        logger.error(f"获取业务数据失败 instance_id={instance_id}: {e}")
        return {}
    finally:
        conn.close()

def update_business_data(instance_id: str, field: str, value):
    """通用修改函数，用来修改重置时间、流量限制、到期时间等"""
    # 加入白名单，防止 SQL 注入
    ALLOWED_FIELDS = ['reset_day', 'traffic_limit_gb', 'expire_time', 'traffic_start_time']
    if field not in ALLOWED_FIELDS:
        logger.error(f"试图更新非法的数据库字段: {field}")
        raise ValueError(f"Invalid field name: {field}")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE ecs_business SET {field} = ? WHERE instance_id = ?", (value, instance_id))
        conn.commit()
    except Exception as e:
        logger.error(f"更新业务数据失败 instance_id={instance_id}, field={field}: {e}")
    finally:
        conn.close()

def add_template(region_id, region_name, template_id):
    """向数据库添加或更新启动模板"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # 确保表存在，防止没初始化报错
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                region_id TEXT PRIMARY KEY,
                region_name TEXT,
                template_id TEXT
            )
        """)
        cursor.execute("REPLACE INTO launch_templates (region_id, region_name, template_id) VALUES (?, ?, ?)", 
                       (region_id, region_name, template_id))
        conn.commit()
    except Exception as e:
        logger.error(f"添加模板失败 region_id={region_id}: {e}")
    finally:
        conn.close()

def get_all_templates():
    """从数据库读取所有启动模板"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                region_id TEXT PRIMARY KEY,
                region_name TEXT,
                template_id TEXT
            )
        """)
        cursor.execute("SELECT region_id, region_name, template_id FROM launch_templates")
        rows = cursor.fetchall()
        return [{"region_id": r[0], "region_name": r[1], "template_id": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"获取所有模板失败: {e}")
        return []
    finally:
        conn.close()

def get_template(region_id):
    """根据 region_id 获取单个模板"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT template_id FROM launch_templates WHERE region_id = ?", (region_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        # 修复：打印异常，而不是默默吞噬异常返回 None
        logger.error(f"获取模板失败 region_id={region_id}: {e}")
        return None
    finally:
        conn.close()
