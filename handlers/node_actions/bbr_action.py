import asyncio
import sqlite3
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import config

router = Router()

# ================= 🛠️ 底层工具：获取服务器真 IP =================
def get_server_ip(instance_id: str) -> str:
    """从数据库中查询实例的公网 IP"""
    try:
        db_path = getattr(config, 'DB_PATH', '/srv/aali/bot_data.db')
        conn = sqlite3.connect(db_path, timeout=3.0)
        cursor = conn.cursor()
        for table in ["ecs_business", "servers", "ecs_instances", "instances"]:
            try:
                cursor.execute(f"SELECT ip FROM {table} WHERE instance_id = ? LIMIT 1", (instance_id,))
                row = cursor.fetchone()
                if row and row[0] and "0.0.0" not in str(row[0]):
                    conn.close()
                    return row[0].strip()
            except Exception:
                continue
        conn.close()
    except Exception:
        pass
    return "127.0.0.1" # 兜底本地测试 IP


# ================= 🔍 核心算法：通过 SSH 实时探测当前 BBR 模版 =================
def sync_check_bbr_status(ip: str) -> str:
    """
    同步执行 SSH 探活，返回当前生效的模版 ID:
    'cubic' / 'fq' / 'fq_pie' / 'cake' / 'bbrplus' / 'unknown'
    """
    if not ip or "0.0.0" in ip or "分配中" in ip:
        return "unknown"
        
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pwd = getattr(config, 'SSH_PASSWORD', '@QS00008')
        
        # 1.5秒极速连接并查询内核拥塞控制和队列算法
        client.connect(hostname=ip, port=22, username="root", password=pwd, timeout=1.5)
        cmd = "sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=1.5)
        output = stdout.read().decode('utf-8').lower()
        client.close()
        
        # 按照优先级解析内核返回结果
        if "cubic" in output:
            return "cubic"
        elif "bbrplus" in output:
            return "bbrplus"
        elif "bbr" in output:
            if "fq_pie" in output: return "fq_pie"
            if "cake" in output: return "cake"
            return "fq" # 默认标准 fq
        else:
            return "cubic"
    except Exception:
        return "unknown" # SSH 超时或失败


# ================= 🚀 底层执行：一键修改内核参数并热加载 =================
def sync_apply_bbr_script(ip: str, target_template: str) -> tuple[bool, str]:
    """通过 SSH 执行 sysctl 修改并立刻生效，无需重启！"""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pwd = getattr(config, 'SSH_PASSWORD', '@QS00008')
        client.connect(hostname=ip, port=22, username="root", password=pwd, timeout=3.0)
        
        # 清理旧的参数配置
        clean_cmd = "sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf && sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf"
        client.exec_command(clean_cmd)
        
        # 构建新的内核配置指令
        if target_template == "cubic":
            apply_cmd = "echo 'net.ipv4.tcp_congestion_control=cubic' >> /etc/sysctl.conf && echo 'net.core.default_qdisc=fq_codel' >> /etc/sysctl.conf"
        elif target_template == "fq":
            apply_cmd = "echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && echo 'net.core.default_qdisc=fq' >> /etc/sysctl.conf"
        elif target_template == "fq_pie":
            apply_cmd = "echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && echo 'net.core.default_qdisc=fq_pie' >> /etc/sysctl.conf"
        elif target_template == "cake":
            apply_cmd = "echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && echo 'net.core.default_qdisc=cake' >> /etc/sysctl.conf"
        elif target_template == "bbrplus":
            apply_cmd = "echo 'net.ipv4.tcp_congestion_control=bbrplus' >> /etc/sysctl.conf && echo 'net.core.default_qdisc=fq' >> /etc/sysctl.conf"
        else:
            return False, "未知参数"

        # 写入并重新加载 sysctl
        full_cmd = f"{apply_cmd} && sysctl -p"
        stdin, stdout, stderr = client.exec_command(full_cmd, timeout=3.0)
        err = stderr.read().decode('utf-8')
        client.close()
        
        if "No such file" in err or "cannot stat" in err:
            return False, "当前系统内核不支持该队列算法，请升级内核"
        return True, "配置已热加载生效"
    except Exception as e:
        return False, f"SSH连接或执行异常: {str(e)}"


# ================= 🎨 UI 构建：动态生成互斥亮灯键盘 =================
def build_bbr_keyboard(instance_id: str, active_status: str) -> InlineKeyboardMarkup:
    """根据 active_status 动态构建 1 + 2 + 2 + 1 键盘矩阵"""
    
    # 1. 定义 4 个基础模版
    templates = [
        {"id": "fq", "name": "BBR+FQ (标准)"},
        {"id": "fq_pie", "name": "BBR+FQ_PIE (新内核)"},
        {"id": "cake", "name": "BBR+CAKE (抗丢包)"},
        {"id": "bbrplus", "name": "BBRplus 魔改"},
    ]
    
    # 2. 构建顶条按钮 (状态看板 / 卸载总开关)
    if active_status in ["cubic", "unknown"]:
        # 未开 BBR：呈现为不可点的状态看板（或点击提示下面选）
        top_btn_text = "🔴 当前状态: 默认 Cubic (请在下方选择模版开启)"
        top_btn_data = f"bbr_noop:{instance_id}"
    else:
        # 已开 BBR：呈现为红色的卸载总开关！
        active_name = next((t["name"].split()[0] for t in templates if t["id"] == active_status), "BBR")
        top_btn_text = f"🟢 运行中: {active_name} ［ 🔴 点击停用 / 恢复Cubic ］"
        top_btn_data = f"bbr_set:cubic:{instance_id}"
        
    builder = [[InlineKeyboardButton(text=top_btn_text, callback_data=top_btn_data)]]
    
    # 3. 构建中间 4 个模版按钮 (互斥单选：当前生效的亮🟢，其余全是⚪)
    row_temp = []
    for t in templates:
        icon = "🟢" if active_status == t["id"] else "⚪"
        btn_text = f"{icon} {t['name']}"
        cb_data = f"bbr_set:{t['id']}:{instance_id}"
        row_temp.append(InlineKeyboardButton(text=btn_text, callback_data=cb_data))
        
        if len(row_temp) == 2:
            builder.append(row_temp)
            row_temp = []
            
    # 4. 构建底部返回按钮
    builder.append([InlineKeyboardButton(text="🔙 返回上一级 (脚本清单)", callback_data=f"srv_sel:{instance_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= ⚡️ 回调路由 1：进入 BBR 控制中心 =================
@router.callback_query(F.data.startswith("run_sh:bbr:"))
async def show_bbr_center(call: CallbackQuery):
    """从脚本清单点击 [bbr加速] 时触发"""
    try:
        _, _, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("数据解析异常", show_alert=True)
        
    ip = get_server_ip(instance_id)
    
    # 先显示一个加载提示，防止 SSH 探活时卡顿
    await call.message.edit_text(
        f"⚡️ **BBR 网络吞吐与拥塞控制中心**\n\n"
        f"🖥 当前操作实例：`{instance_id}`\n"
        f"🌐 当前 IP：`{ip}`\n\n"
        f"⏳ *正在通过 SSH 实时探测远端 Linux 内核状态，请稍候...*",
        parse_mode="Markdown"
    )
    
    # 异步多线程执行探活
    active_status = await asyncio.to_thread(sync_check_bbr_status, ip)
    
    # 渲染最终动态界面
    keyboard = build_bbr_keyboard(instance_id, active_status)
    
    text = (
        f"⚡️ **BBR 网络吞吐与拥塞控制中心**\n\n"
        f"🖥 当前操作实例：`{instance_id}`\n"
        f"🌐 当前 IP：`{ip}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 **参数说明与运维指南：**\n"
        f"• **标准 BBR+FQ**：适合 90% 的跨境 Linux 场景，极大压榨带宽、平稳抗丢包。\n"
        f"• **FQ_PIE / CAKE**：针对高并发及严重丢包链路优化的现代 AQM 队列算法。\n"
        f"• **极速热加载**：本次调优采用 `sysctl` 内核动态注入，**修改瞬间生效，无需重启服务器**！\n\n"
        f"👇 *请在下方选单中点击相应模版进行动态热切换：*"
    )
    
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()


# ================= ⚡️ 回调路由 2：执行 BBR 模版切换 / 恢复 Cubic =================
@router.callback_query(F.data.startswith("bbr_set:"))
async def execute_bbr_switch(call: CallbackQuery):
    """点击具体模版或者顶部卸载开关时触发"""
    try:
        _, target_template, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("数据异常", show_alert=True)
        
    ip = get_server_ip(instance_id)
    
    # 交互反馈：正在下发指令
    temp_msg = await call.message.edit_text(
        f"🔄 **正在向服务器 `{ip}` 热加载内核参数...**\n请稍候 1-2 秒...",
        parse_mode="Markdown"
    )
    
    # 异步向服务器下发 sysctl 修改
    success, msg = await asyncio.to_thread(sync_apply_bbr_script, ip, target_template)
    
    if success:
        await call.answer("🎉 配置已修改并立刻生效！", show_alert=True)
    else:
        await call.answer(f"⚠️ 切换提示: {msg}", show_alert=True)
        
    # ⭐ 绝杀体验：执行完后，立刻在后台再探活一次，无缝刷新界面的绿灯位置！
    new_status = await asyncio.to_thread(sync_check_bbr_status, ip)
    keyboard = build_bbr_keyboard(instance_id, new_status)
    
    # 恢复主界面展示
    text = (
        f"⚡️ **BBR 网络吞吐与拥塞控制中心**\n\n"
        f"🖥 当前操作实例：`{instance_id}`\n"
        f"🌐 当前 IP：`{ip}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 **参数说明与运维指南：**\n"
        f"• **标准 BBR+FQ**：适合 90% 的跨境 Linux 场景，极大压榨带宽、平稳抗丢包。\n"
        f"• **FQ_PIE / CAKE**：针对高并发及严重丢包链路优化的现代 AQM 队列算法。\n"
        f"• **极速热加载**：本次调优采用 `sysctl` 内核动态注入，**修改瞬间生效，无需重启服务器**！\n\n"
        f"👇 *请在下方选单中点击相应模版进行动态热切换：*"
    )
    await temp_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ================= ⚡️ 回调路由 3：点击顶部纯提示看板时的防呆处理 =================
@router.callback_query(F.data.startswith("bbr_noop:"))
async def bbr_noop_handler(call: CallbackQuery):
    await call.answer("💡 当前未开启加速，请点击下方的白色 ⚪ 模版按钮开启！", show_alert=True)
