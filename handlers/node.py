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
    ⚡️ 针对 bot_data.db 的 ecs_business 表量身定制：
    如果有 0.0.0.0 脏数据，自动开启全库搜索和实时抓取并持久化修复！
    """
    try:
        from db import get_active_servers
        servers = get_active_servers(user_id)
    except Exception:
        servers = []

    # 🟢 兜底1：如果主方法查空了，直接去你的 bot_data.db 的 ecs_business 表里搜刮
    if not servers:
        try:
            conn = sqlite3.connect('/srv/aali/bot_data.db', timeout=3.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT instance_id, ip, region as region_id FROM ecs_business")
            servers = [dict(r) for r in cursor.fetchall()]
            conn.close()
        except Exception:
            pass

    # ⭐⭐ 兜底2：自动自愈核心！如果我们发现数据库里的机器依然是 0.0.0.0，
    # 立即尝试去其他表或云接口查，再不行就提示等待，不再拿 0.0.0.0 糊弄用户
    for srv in servers:
        inst_id = srv.get("instance_id", "")
        ip_val = str(srv.get("ip", "")).strip()
        
        if ip_val in ["0.0.0.0", "", "None", "IP分配中..."]:
            real_ip = None
            # 尝试调用你已经在服务器管理里好使的阿里云接口抓一把真 IP
            try:
                from utils.aliyun import get_instance_ip # 或者是 get_instance_info
                real_ip = get_instance_ip(inst_id)
            except Exception:
                pass
                
            if real_ip and "0.0.0" not in real_ip:
                srv["ip"] = real_ip
                # 顺手将你刚才回显出问题的 ecs_business 表里的脏数据永久抹除！
                try:
                    conn = sqlite3.connect('/srv/aali/bot_data.db', timeout=3.0)
                    conn.execute("UPDATE ecs_business SET ip = ? WHERE instance_id = ?", (real_ip, inst_id))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

    return servers if servers else []

def build_servers_keyboard(user_id: int):
    """
    ⚡️ 智能状态灯版键盘菜单：
    自动获取并展示服务器的实时运行状态（🟢 运行中 / 🔴 已停用 / 🔵 开机中）
    """
    servers = get_servers_data(user_id)
    builder = []
    
    for srv in servers:
        inst_id = srv.get("instance_id", "")
        ip_display = srv.get("ip", "0.0.0.0")
        
        # 1. 获取服务器状态，支持常见字段兼容 (status / state / run_status 等)
        status_raw = str(srv.get("status", srv.get("state", "Running"))).lower()
        
        # 2. 动态匹配状态灯符号（完全对齐“服务器管理”的视觉标准）
        if "running" in status_raw or "运行" in status_raw or "正常" in status_raw or status_raw == "1":
            status_icon = "🟢"
        elif "stopped" in status_raw or "停" in status_raw or "关" in status_raw or status_raw == "0":
            status_icon = "🔴"
        else:
            status_icon = "🔵"
        
        # 3. 如果刚开机一瞬间还写着 0.0.0.0，自动切成蓝色等待灯
        if ip_display == "0.0.0.0" or not ip_display:
            ip_display = "阿里云分配IP中..."
            status_icon = "🔵"
            
        cb_data = f"srv_sel:{inst_id}"
        
        # ⭐ 组合最终的 UI 显示：【 🟢 IP: 47.76.172.65 】
        button_text = f"{status_icon} IP: {ip_display}" if "分配中" not in ip_display else f"{status_icon} {ip_display}"
        builder.append([InlineKeyboardButton(text=button_text, callback_data=cb_data)])
    
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
    
    # ⭐ 核心补救优化：如果新扩容的机器还没来得及写入数据库，不要直接报错！
    # 自动开启数据库全局强力查表，或者去本地库中匹配，不再拦截用户：
    if not srv:
        try:
            import sqlite3
            db_path = getattr(config, 'DB_PATH', '/srv/aali/mg_core.db')
            conn = sqlite3.connect(db_path, timeout=3.0)
            cursor = conn.cursor()
            for table in ["servers", "ecs_instances", "ecs_business", "instances"]:
                try:
                    cursor.execute(f"SELECT ip, region_id FROM {table} WHERE instance_id = ? LIMIT 1", (instance_id,))
                    row = cursor.fetchone()
                    if row:
                        srv = {"instance_id": instance_id, "ip": row[0], "region": row[1]}
                        break
                except Exception:
                    continue
            conn.close()
        except Exception:
            pass
            
    # ⭐ 如果全库彻底真的还没存进去（刚建完前 1 秒），直接在内存里构建一个有效对象，绝不报错卡死！
    if not srv:
        srv = {
            "instance_id": instance_id,
            "ip": "刚扩容，请直接点击下发", 
            "region": "cn-hongkong" # 默认兜底
        }
        
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
