import sqlite3
import config
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from db import get_active_servers  # 导入你的真实查询函数

router = Router()

# ================= 🌍 地域名称映射字典 =================
REGION_MAP = {
    "cn-hongkong": "香港",
    "ap-northeast-1": "东京",
    "ap-southeast-1": "新加坡",
    "us-west-1": "硅谷",
    "cn-shanghai": "上海",
}

# ================= 🛠️ 工具函数与智能数据自愈 =================
def get_servers_data(user_id: int):
    """
    统一获取服务器列表数据：
    1. 优先调用主库 get_active_servers(user_id)
    2. 如果为空或 IP 为 0.0.0.0，自动开启 SQLite 跨表查真 IP 并修复底层脏数据
    """
    try:
        servers = get_active_servers(user_id)
    except Exception:
        servers = []

    # 🟢 兜底查询：如果主库查询结果为空，自动扫遍系统里常见的所有机器资产表
    if not servers:
        try:
            db_path = getattr(config, 'DB_PATH', '/srv/aali/mg_core.db')
            conn = sqlite3.connect(db_path, timeout=3.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            for table in ["servers", "ecs_instances", "ecs_business", "instances", "launch_templates"]:
                try:
                    cursor.execute(f"SELECT instance_id, ip, region_id as region FROM {table} WHERE ip != '0.0.0.0' AND ip IS NOT NULL")
                    rows = [dict(r) for r in cursor.fetchall()]
                    if rows:
                        servers = rows
                        break
                except Exception:
                    continue
            conn.close()
        except Exception:
            pass

    # ⭐ 核心自愈引擎：如果发现 IP 依然是 0.0.0.0，自动去其他表捞取你开机后分配到的真 IP，并覆写修复
    for srv in servers:
        inst_id = srv.get("instance_id", "")
        ip_val = srv.get("ip", "0.0.0.0")
        if ip_val == "0.0.0.0" or not ip_val:
            try:
                db_path = getattr(config, 'DB_PATH', '/srv/aali/mg_core.db')
                conn = sqlite3.connect(db_path, timeout=3.0)
                cursor = conn.cursor()
                for table in ["servers", "ecs_instances", "ecs_business", "instances"]:
                    try:
                        cursor.execute(f"SELECT ip FROM {table} WHERE instance_id = ? AND ip != '0.0.0.0' AND ip IS NOT NULL LIMIT 1", (inst_id,))
                        row = cursor.fetchone()
                        if row and row[0]:
                            real_ip = row[0].strip()
                            srv["ip"] = real_ip
                            # 顺手将脏数据表里的 0.0.0.0 永久抹除，同步为真 IP
                            cursor.execute(f"UPDATE {table} SET ip = ? WHERE instance_id = ?", (real_ip, inst_id))
                            conn.commit()
                            break
                    except Exception:
                        continue
                conn.close()
            except Exception:
                pass

    return servers if servers else []

def build_servers_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """构建第一步的服务器键盘 (智能展示真 IP，过滤 0.0.0.0)"""
    servers = get_servers_data(user_id)
    builder = []
    
    for srv in servers:
        inst_id = srv.get("instance_id", "")
        ip_display = srv.get("ip", "0.0.0.0")
        
        # 🟢 如果刚开机一瞬间真的还没拿到公网 IP，优雅显示等待提示，绝不展示死板的 0.0.0.0
        if ip_display == "0.0.0.0" or not ip_display:
            ip_display = "⏳ IP分配中..."
            
        cb_data = f"srv_sel:{inst_id}"
        builder.append([InlineKeyboardButton(text=f"🖥 {ip_display}", callback_data=cb_data)])
    
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= 🚀 第一步：接收主菜单点击 =================
@router.message(F.text == "⚙️ 节点配置")
async def show_node_list(message: Message):
    keyboard = build_servers_keyboard(message.from_user.id)
    
    await message.answer(
        "⚙️ **节点配置中心 (第一步)**\n\n请在下方悬浮菜单中选择你要操作的服务器：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ================= 🚀 第二步：选中服务器，展示文本与脚本清单 =================
@router.callback_query(F.data.startswith("srv_sel:"))
async def show_script_options(call: CallbackQuery):
    try:
        _, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("数据解析异常！", show_alert=True)
    
    servers = get_servers_data(call.from_user.id)
    srv = next((s for s in servers if s["instance_id"] == instance_id), None)
    
    if not srv:
        await call.answer("获取服务器信息失败，请重试", show_alert=True)
        return
        
    region_id = srv.get("region", "未知地域")
    region_name = REGION_MAP.get(region_id, region_id)
    public_ip = srv.get("ip", "0.0.0.0")
    
    if public_ip == "0.0.0.0" or not public_ip:
        public_ip = "⏳ 阿里云分配IP中..."
    
    available_scripts = [
        {"id": "bbr", "name": "🟢 bbr加速"},
        {"id": "xui", "name": "🔴 x-ui面板"},
        {"id": "mgui", "name": "🔴 MG 私有面板"},
    ]
    
    builder = []
    for script in available_scripts:
        cb_data = f"run_sh:{script['id']}:{instance_id}"
        builder.append([InlineKeyboardButton(text=script["name"], callback_data=cb_data)])
    
    builder.append([InlineKeyboardButton(text="🔙 返回服务器列表", callback_data="back_to_srv_list")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=builder)
    
    await call.message.edit_text(
        f"⚙️ **节点配置中心 (第二步)**\n\n"
        f"选中实例: `{instance_id}`\n"
        f"所属地域: {region_name}\n"
        f"公网IP：`{public_ip}`\n\n"
        f"👉 请选择要向该服务器下发的 Shell 脚本：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()

# ================= ↩️ 附加步：返回按钮逻辑 =================
@router.callback_query(F.data == "back_to_srv_list")
async def back_to_servers(call: CallbackQuery):
    keyboard = build_servers_keyboard(call.from_user.id)
    await call.message.edit_text(
        "⚙️ **节点配置中心 (第一步)**\n\n请在下方悬浮菜单中选择你要操作的服务器：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()
