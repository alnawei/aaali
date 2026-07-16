import sqlite3
import calendar
import logging
from datetime import datetime
import config  # 统一从 config 获取配置与路径
import os

DB_PATH = config.DB_PATH  # ⭐ 就是加在这里！导出给 tasks.py 等外部模块调用

logger = logging.getLogger("MG_Bot.DB")

def get_connection():
    """
    获取数据库连接。
    加入 timeout=5.0 防锁，并自动开启 Row 工厂以支持字段名访问（可选）。
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=5.0)
    return conn

def init_db():
    """初始化数据库，开启 WAL 并并发优化模式，创建 IDC 核心表"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # ⭐ 核心性能优化：开启 SQLite WAL (预写式日志) 模式，大幅提高异步并发读写能力
        cursor.execute("PRAGMA journal_mode=WAL;")
        
        # 1. 业务管理表 (补全 region_id, ip, name 等机器基础元数据，契合 node.py 架构)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ecs_business (
                instance_id TEXT PRIMARY KEY,
                name TEXT DEFAULT '无名节点',           -- 服务器备注名
                region_id TEXT DEFAULT 'cn-hongkong',   -- 归属地域
                ip TEXT DEFAULT '0.0.0.0',              -- 公网 IP
                reset_day INTEGER DEFAULT 1,            -- 账单锚点日 (例如每月 8 号)
                traffic_limit_gb INTEGER DEFAULT 500,   -- 流量配额 (GB)
                expire_time TEXT DEFAULT '',            -- 客户业务到期日 ('YYYY-MM-DD')
                traffic_start_time TEXT DEFAULT ''      -- 流量统计起点 (中途重置时清零记账)
            )
        """)
        
        # 2. 系统全局配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sys_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # 3. 启动模板表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                region_id TEXT PRIMARY KEY,
                region_name TEXT,
                template_id TEXT
            )
        """)
        
        # 写入默认全局重装密码
        cursor.execute("INSERT OR IGNORE INTO sys_config (key, value) VALUES ('default_password', '@QS00008')")
        conn.commit()
        logger.info("✅ 数据库表结构与 WAL 高并发优化模式已成功加载！")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise e
    finally:
        conn.close()

def get_active_servers(user_id: int = 0) -> list:
    """
    查询本地所有在管服务器列表。
    为适应 node.py，返回字典列表: [{'instance_id':..., 'name':..., 'region':..., 'ip':...}]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT instance_id, name, region_id, ip FROM ecs_business")
        rows = cursor.fetchall()
        return [
            {
                "instance_id": r[0],
                "name": r[1] or f"实例-{r[0][-4:]}",
                "region": r[2] or "cn-hongkong",
                "ip": r[3] or "0.0.0.0"
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"查询服务器列表失败: {e}")
        return []
    finally:
        conn.close()

def get_business_data(instance_id: str) -> dict:
    """获取某台机器的本地计费数据；若无记录则自动初始化默认计费周期"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reset_day, traffic_limit_gb, expire_time, traffic_start_time FROM ecs_business WHERE instance_id = ?", 
            (instance_id,)
        )
        row = cursor.fetchone()
        
        if row:
            return {
                "reset_day": row[0],
                "traffic_limit_gb": row[1],
                "expire_time": row[2],
                "traffic_start_time": row[3]
            }
        else:
            # 自动计算初始化自然月周期
            now = datetime.now()
            year, month, day = now.year, now.month, now.day
            
            if month == 12:
                next_month, next_year = 1, year + 1
            else:
                next_month, next_year = month + 1, year
                
            last_day_of_next_month = calendar.monthrange(next_year, next_month)[1]
            next_day = min(day, last_day_of_next_month)
            
            default_expire = now.replace(year=next_year, month=next_month, day=next_day).strftime("%Y-%m-%d")
            default_reset = min(day, 28)  # 默认不要超过28号，防止2月没有
            default_start = now.strftime("%Y-%m-%d %H:%M:%S")
            
            # ⭐ 补全初始化时对 region_id, ip 等字段的默认兼容
            cursor.execute("""
                INSERT INTO ecs_business (instance_id, reset_day, traffic_limit_gb, expire_time, traffic_start_time)
                VALUES (?, ?, ?, ?, ?)
            """, (instance_id, default_reset, 500, default_expire, default_start))
            conn.commit()
            
            return {
                "reset_day": default_reset,
                "traffic_limit_gb": 500,
                "expire_time": default_expire,
                "traffic_start_time": default_start
            }
    except Exception as e:
        logger.error(f"获取/初始化业务数据失败 instance_id={instance_id}: {e}")
        return {}
    finally:
        conn.close()

def update_business_data(instance_id: str, field: str, value):
    """通用单字段更新函数，带有 SQL 白名单保护"""
    ALLOWED_FIELDS = ['name', 'region_id', 'ip', 'reset_day', 'traffic_limit_gb', 'expire_time', 'traffic_start_time']
    if field not in ALLOWED_FIELDS:
        logger.error(f"非法尝试修改非白名单字段: {field}")
        raise ValueError(f"Invalid field name: {field}")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE ecs_business SET {field} = ? WHERE instance_id = ?", (value, instance_id))
        conn.commit()
    except Exception as e:
        logger.error(f"更新业务数据失败 instance_id={instance_id}, field={field}: {e}")
        raise e
    finally:
        conn.close()

def add_template(region_id: str, region_name: str, template_id: str):
    """向数据库添加或更新指定地域的启动模板"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO launch_templates (region_id, region_name, template_id) VALUES (?, ?, ?)", 
                       (region_id, region_name, template_id))
        conn.commit()
    except Exception as e:
        logger.error(f"添加模板失败 region_id={region_id}: {e}")
        raise e
    finally:
        conn.close()

def get_all_templates() -> list:
    """读取所有启动模板"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT region_id, region_name, template_id FROM launch_templates")
        rows = cursor.fetchall()
        return [{"region_id": r[0], "region_name": r[1], "template_id": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"获取所有模板失败: {e}")
        return []
    finally:
        conn.close()

def get_template(region_id: str) -> str:
    """根据 region_id 获取对应启动模板 ID"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT template_id FROM launch_templates WHERE region_id = ?", (region_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"获取模板失败 region_id={region_id}: {e}")
        return None
    finally:
        conn.close()

def delete_template(region_id: str):
    """⭐ 补全 system.py 中依赖的删除模板函数"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM launch_templates WHERE region_id = ?", (region_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"删除模板失败 region_id={region_id}: {e}")
        raise e
    finally:
        conn.close()
