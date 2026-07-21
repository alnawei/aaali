import sqlite3
import config
from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from db import get_active_servers  
import asyncio
import paramiko
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ================= 1. 定义添加服务器的 FSM 状态机 =================
class MguiAddServerFSM(StatesGroup):
    wait_for_ip = State()
    wait_for_pwd = State()

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
                from utils.aliyun import get_instance_ip 
                real_ip = get_instance_ip(inst_id)
            except Exception:
                pass
                
            if real_ip and "0.0.0" not in real_ip:
                srv["ip"] = real_ip
                # 顺手将表里的脏数据永久抹除！
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
    ⚡️ 智能状态灯版键盘菜单 (已升级为多账号双列矩阵排版)：
    自动获取并按账号分组展示服务器，采用 2 个一行的高效排版。
    """
    servers = get_servers_data(user_id)
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    
    # 🌟 优化1：地域简称映射字典（解决按钮文字被截断的问题）
    REGION_SHORT = {
        "cn-hongkong": "HK", "hongkong": "HK",
        "ap-northeast-1": "JP", "tokyo": "JP",
        "ap-northeast-2": "KR", "seoul": "KR",
        "ap-southeast-1": "SG", "singapore": "SG",
        "us-west-1": "US", "us-east-1": "US",
        "eu-central-1": "DE", "frankfurt": "DE"
    }
    
    grouped_accounts = {}
    ssh_nodes = []
    
    for srv in servers:
        inst_id = str(srv.get("instance_id", ""))
        region_raw = srv.get("region_id", srv.get("region", ""))
        
        # 🌟 优化2：最精准的分类判断！
        # 如果带有密码字段，或者实例ID不是阿里云标志性的 'i-' 开头，或者根本没地域，统统归为 SSH！
        if "root_password" in srv or not inst_id.startswith("i-") or not region_raw:
            ssh_nodes.append(srv)
            continue
            
        # 走到这里的，才是真正的阿里云机器
        acc_id = srv.get('account_id', 1)
        acc_name = srv.get('account_alias', f'主账号 (ID:{acc_id})' if acc_id == 1 else f'账号 {acc_id}')
        
        if acc_id not in grouped_accounts:
            grouped_accounts[acc_id] = {'name': acc_name, 'nodes': []}
        grouped_accounts[acc_id]['nodes'].append(srv)
        
    # ================= 2. 动态渲染阿里云机器（按账号分组） =================
    for acc_id, acc_info in grouped_accounts.items():
        builder.row(InlineKeyboardButton(text=f"━━━ 🏢 阿里云：{acc_info['name']} ━━━", callback_data="ignore_click"))
        
        row_buttons = []
        for srv in acc_info['nodes']:
            inst_id = srv.get("instance_id", "")
            ip_display = srv.get("ip", "0.0.0.0")
            
            # 判断状态灯
            if ip_display in ["0.0.0.0", "", "None", "IP分配中..."]:
                ip_display = "分配中"
                status_icon = "🔵"
            else:
                status_raw = str(srv.get("status", srv.get("state", "Running"))).lower()
                if "running" in status_raw or "运行" in status_raw or "正常" in status_raw or status_raw == "1":
                    status_icon = "🟢"
                elif "stopped" in status_raw or "停" in status_raw or "关" in status_raw or status_raw == "0":
                    status_icon = "🔴"
                else:
                    status_icon = "🔵"
            
            # 获取地域简称 (找不到就截取末尾的两个字母)
            region = srv.get("region_id", srv.get("region", ""))
            r_short = REGION_SHORT.get(region, region.split('-')[-1][:2].upper() if region else "未知")
            
            # 拼装精简版按钮文字 (例如: 🟢 HK | 47.83.xx.xx)
            btn_text = f"{status_icon} {r_short} | {ip_display}"
            
            row_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"srv_sel:{inst_id}"))
            
            if len(row_buttons) == 2:
                builder.row(*row_buttons)
                row_buttons = []
                
        if row_buttons:
            builder.row(*row_buttons)

    # ================= 3. 渲染手动添加的 SSH 机器 =================
    if ssh_nodes:
        builder.row(InlineKeyboardButton(text="━━━ 🔌 自定义 SSH 服务器 ━━━", callback_data="ignore_click"))
        row_buttons = []
        for srv in ssh_nodes:
            inst_id = srv.get("instance_id", "")
            ip_display = srv.get("ip", "未知IP")
            btn_text = f"🟢 SSH | {ip_display}"
            
            row_buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"srv_sel:{inst_id}"))
            
            if len(row_buttons) == 2:
                builder.row(*row_buttons)
                row_buttons = []
        if row_buttons:
            builder.row(*row_buttons)
            
    # ================= 4. 底部挂载固定入口 =================
    builder.row(InlineKeyboardButton(text="➕ 添加自定义服务器 (SSH)", callback_data="custom_srv:add"))
    
    return builder.as_markup()

# ================= 🚀 第一步：接收主菜单点击 =================
@router.message(F.text == "⚙️ 节点配置")
async def show_node_list(message: types.Message):
    servers = get_servers_data(message.from_user.id)
    
    # 🌟 空列表防呆拦截：如果没有任何机器，直接拦截并提示
    if not servers:
        return await message.answer(
            "📭 **当前名下暂无可用机器！**\n\n"
            "请先前往【💻 服务器管理】开通新服务器，或点击下方按钮添加自定义节点。",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ 添加自定义服务器 (SSH)", callback_data="custom_srv:add")]
            ]),
            parse_mode="Markdown"
        )

    keyboard = build_servers_keyboard(message.from_user.id)
    await message.answer(
        "⚙️ **节点配置中心 (第一步)**\n\n请在下方悬浮菜单中选择你要操作的服务器：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ================= 🛠️ 辅助函数：【终极优化】一次握手，三合一极速探活 =================
def check_all_scripts_status(instance_id: str, ip: str) -> dict:
    """
    ⚡️ 性能优化：1 次握手 + 重试机制 + 1 条拼接指令完成所有探测！
    """
    res = {"bbr": False, "xui": False, "mgui": False}
    if not ip or ip in ["0.0.0.0", "阿里云分配IP中...", "未知IP"]:
        return res
        
    pwd = getattr(config, 'SSH_PASSWORD', getattr(config, 'ROOT_PASSWORD', '@QS00008'))
    if instance_id and instance_id.startswith("ssh_"):
        try:
            import db
            custom_pwd = db.get_custom_server_password(instance_id)
            if custom_pwd: pwd = custom_pwd
        except Exception: pass

    client = None
    # ⭐ 核心铠甲：专门针对 SSH Banner 读取失败和连接拒绝的重试机制
    for attempt in range(3):
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 延长 banner_timeout 到 15 秒，给服务器喘息的时间
            client.connect(
                hostname=ip, port=22, username="root", password=pwd, 
                timeout=8.0, banner_timeout=15.0, auth_timeout=8.0
            )
            # 如果连接成功，立刻跳出重试循环，往下执行
            break 
            
        except Exception as conn_e:
            if client: 
                client.close()
                client = None
                
            err_msg = str(conn_e).lower()
            # 如果命中 Banner 报错或连接重置，等待 1.5 秒后重试
            if attempt < 2 and ("banner" in err_msg or "closed" in err_msg or "refused" in err_msg):
                time.sleep(1.5)
                continue
            else:
                print(f"⚠️ [SSH连接彻底失败] IP: {ip} | 原因: {conn_e}")
                return res

    # 此时如果 client 还是为空，说明 3 次重试都失败了
    if not client:
        return res

    try:
        # ⭐ 核心删减法：用 bash 的 echo 打印特殊标识，一次性返回 3 个结果
        combined_cmd = """
        if sysctl net.ipv4.tcp_congestion_control 2>/dev/null | grep -qi bbr; then echo 'RES_bbr:1'; else echo 'RES_bbr:0'; fi
        if systemctl is-active --quiet x-ui || test -f /usr/local/x-ui/x-ui; then echo 'RES_xui:1'; else echo 'RES_xui:0'; fi
        if systemctl is-active --quiet mg-panel || systemctl is-active --quiet mgui || ps aux | grep -v grep | grep -qi mgui; then echo 'RES_mgui:1'; else echo 'RES_mgui:0'; fi
        """
        
        stdin, stdout, stderr = client.exec_command(combined_cmd, timeout=5.0)
        output = stdout.read().decode('utf-8')
        
        # 解析返回结果
        for line in output.splitlines():
            if "RES_bbr:1" in line: res["bbr"] = True
            if "RES_xui:1" in line: res["xui"] = True
            if "RES_mgui:1" in line: res["mgui"] = True
            
    except Exception as e:
        print(f"⚠️ [SSH执行探活异常] IP: {ip} | 原因: {str(e)}")
    finally:
        if client:
            client.close()
            
    return res

# ================= 🚀 第二步：选中服务器，展示动态状态与脚本清单 =================
@router.callback_query(F.data.startswith("srv_sel:"))
async def show_script_options(call: types.CallbackQuery):
    try:
        _, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("数据解析异常！", show_alert=True)
    
    servers = get_servers_data(call.from_user.id)
    srv = next((s for s in servers if s["instance_id"] == instance_id), None)
    
    # 🌟 修复开机直通车：如果缓存里没查到，直接去底层账本精准捞取，坚决不使用硬编码兜底！
    if not srv:
        try:
            import sqlite3
            conn = sqlite3.connect('/srv/aali/bot_data.db', timeout=3.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT instance_id, ip, region_id FROM ecs_business WHERE instance_id=?", (instance_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                srv = dict(row)
        except Exception:
            pass

    # 如果底层彻底查不到，弹窗报错并拦截
    if not srv:
        return await call.answer("❌ 无法在本地账本中定位该服务器，数据可能未同步！", show_alert=True)
        
    region_id = srv.get("region_id", srv.get("region", "cn-hongkong"))
    region_name = REGION_MAP.get(region_id, region_id)
    public_ip = srv.get("ip", "0.0.0.0")
    
    if public_ip == "0.0.0.0" or not public_ip:
        public_ip = "⏳ 阿里云分配IP中..."
    
    raw_scripts = [
        {"id": "bbr", "label": "bbr 加速"},
        {"id": "xui", "label": "x-ui 面板"},
        {"id": "mgui", "label": "MG 私有面板"},
    ]
    
    # ⭐ 性能优化：在此处仅发起 1 次后台线程，一次性拿到所有组件状态
    status_dict = await asyncio.to_thread(check_all_scripts_status, instance_id, public_ip)
    
    builder = []
    for script in raw_scripts:
        is_running = status_dict.get(script["id"], False)
        status_icon = "🟢" if is_running else "🔴"
        button_text = f"{status_icon} {script['label']}"
        cb_data = f"run_sh:{script['id']}:{instance_id}"
        builder.append([InlineKeyboardButton(text=button_text, callback_data=cb_data)])
    
    # ⭐ 核心逻辑：如果是自定义服务器 (不是 i- 开头)，则展示移除按钮
    if not instance_id.startswith("i-"):
        builder.append([InlineKeyboardButton(text="❌ 移除此自定义服务器", callback_data=f"del_custom_srv:{instance_id}")])

    builder.append([InlineKeyboardButton(text="🔙 返回服务器列表", callback_data="back_to_srv_list")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=builder)
    
    await call.message.edit_text(
        f"⚙️ **节点配置中心 (第二步)**\n\n"
        f"选中实例: `{instance_id}`\n"
        f"所属地域: {region_name}\n"
        f"公网IP：`{public_ip}`\n\n"
        f"👉 请选择要向该服务器下发的 Shell 脚本 *(🟢运行中 / 🔴未安装)*：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()

# ================= ↩️ 附加步：返回按钮逻辑 =================
@router.callback_query(F.data == "back_to_srv_list")
async def back_to_servers(call: types.CallbackQuery):
    servers = get_servers_data(call.from_user.id)
    
    if not servers:
        return await call.message.edit_text(
            "📭 **当前名下暂无可用机器！**\n\n"
            "请先前往【💻 服务器管理】开通新服务器，或点击下方按钮添加自定义节点。",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ 添加自定义服务器 (SSH)", callback_data="custom_srv:add")]
            ]),
            parse_mode="Markdown"
        )

    keyboard = build_servers_keyboard(call.from_user.id)
    await call.message.edit_text(
        "⚙️ **节点配置中心 (第一步)**\n\n请在下方悬浮菜单中选择你要操作的服务器：",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await call.answer()

# ================= ❌ 删除自定义服务器逻辑 =================
@router.callback_query(F.data.startswith("del_custom_srv:"))
async def process_del_custom_server(call: types.CallbackQuery):
    instance_id = call.data.split(":")[-1]
    
    try:
        import db
        db.delete_custom_server(instance_id)
    except Exception as e:
        return await call.answer(f"删除失败: {e}", show_alert=True)
    
    await call.answer("❌ 自定义服务器已彻底从控制台移除！", show_alert=True)
    
    await call.message.edit_text(
        f"✅ **操作成功**\n\n"
        f"实例 `{instance_id}` 的本地信息及业务计费数据已抹除。\n\n"
        f"*(此操作仅解除机器人的管控，不影响服务器本身的运行)*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回节点配置列表", callback_data="back_to_srv_list")]
        ]),
        parse_mode="Markdown"
    )

# =====================================================================
# ================= ➕ 添加自定义服务器逻辑 (FSM) =====================
# =====================================================================

# ================= 🛡️ 辅助探测函数 =================
async def test_ssh_connection(ip: str, password: str, port: int = 22) -> bool:
    """极速探测 SSH 是否可用 (超时 5 秒)"""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # 只做连接测试，不执行命令
        await asyncio.to_thread(client.connect, hostname=ip, port=port, username="root", password=password, timeout=5.0)
        client.close()
        return True
    except Exception:
        return False

# ================= 1. 拦截“添加”按钮点击 =================
@router.callback_query(F.data == "custom_srv:add")
async def add_custom_server_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != config.ADMIN_ID:
        return await call.answer("权限不足！", show_alert=True)
        
    await state.set_state(MguiAddServerFSM.wait_for_ip)
    await call.message.answer("➕ **添加自定义服务器 (SSH)**\n\n🌐 请输入服务器的公网 IP 地址：\n*(回复 0 取消操作)*", parse_mode="Markdown")
    await call.answer()

# ================= 2. 接收 IP =================
@router.message(MguiAddServerFSM.wait_for_ip)
async def add_custom_server_ip(message: types.Message, state: FSMContext):
    ip = message.text.strip()
    if ip == '0':
        await state.clear()
        return await message.answer("已取消操作。")
        
    await state.update_data(ip=ip)
    await state.set_state(MguiAddServerFSM.wait_for_pwd)
    await message.answer(f"✅ IP `{ip}` 已记录。\n\n🔑 请输入该服务器的 Root 密码：\n*(回复 0 取消操作)*", parse_mode="Markdown")

# ================= 3. 接收密码 (增加 SSH 探活拦截) =================
@router.message(MguiAddServerFSM.wait_for_pwd)
async def add_custom_server_pwd(message: types.Message, state: FSMContext):
    pwd = message.text.strip()
    if pwd == '0':
        await state.clear()
        return await message.answer("已取消操作。")

    data = await state.get_data()
    ip = data['ip']
    
    # 先发一条等待消息
    wait_msg = await message.answer(f"⏳ 正在探测服务器 `{ip}` 的连通性，请稍候...", parse_mode="Markdown")
    
    # 发起真实 SSH 连接探测
    is_connected = await test_ssh_connection(ip, pwd)
    
    if is_connected:
        # ✅ 连接成功：执行存库
        instance_id = f"ssh_{ip.replace('.', '_')}" 

        try:
            import db
            db.add_custom_server(instance_id, ip, pwd)
            await state.clear()
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 刷新节点配置列表", callback_data="back_to_srv_list")]
            ])
            
            await wait_msg.edit_text(
                f"🎉 **自定义服务器添加成功！**\n\n"
                f"✅ **SSH 测试通过**，已确认服务器存活！\n"
                f"🌐 IP: `{ip}`\n"
                f"🆔 实例标识: `{instance_id}`\n\n"
                f"已无缝接入节点配置中心，请点击下方按钮刷新面板。",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            await wait_msg.edit_text(f"❌ 添加失败，可能是数据库写入异常: {e}")
    else:
        # 🔴 连接失败：拦截保存，弹出重试或删除面板
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 重新填写密码", callback_data="custom_srv:retry_pwd")],
            [InlineKeyboardButton(text="🗑️ 取消添加 (放弃保存)", callback_data="custom_srv:cancel")]
        ])
        
        await wait_msg.edit_text(
            f"🔴 **连接失败！**\n\n无法通过 SSH 连上 `{ip}`。\n可能是密码错误、22端口未开或禁止 Root 登录。\n\n请选择后续操作：",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

# ================= 4. 处理重试与取消操作 =================
@router.callback_query(F.data == "custom_srv:retry_pwd")
async def retry_custom_server_pwd(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ip = data.get('ip')
    if not ip:
        return await call.message.edit_text("❌ 会话已过期，请重新发起添加操作。")
        
    await state.set_state(MguiAddServerFSM.wait_for_pwd)
    await call.message.edit_text(
        f"👉 请重新输入服务器 `{ip}` 的 SSH 密码 (root):\n*(回复 0 取消操作)*", 
        parse_mode="Markdown"
    )
    await call.answer()

@router.callback_query(F.data == "custom_srv:cancel")
async def cancel_custom_server_add(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回节点配置列表", callback_data="back_to_srv_list")]
    ])
    await call.message.edit_text("🗑️ 操作已取消，无效的服务器信息已被丢弃。", reply_markup=keyboard)
    await call.answer()
