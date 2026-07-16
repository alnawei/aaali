import base64
import asyncio
import time
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models

import config
from db import get_active_servers

router = Router()

# ================= 🛠️ 底层客户端与工具函数 =================
def get_ecs_client(region_id: str) -> EcsClient:
    config_model = open_api_models.Config(
        access_key_id=config.ALIYUN_ACCESS_KEY_ID,      
        access_key_secret=config.ALIYUN_ACCESS_KEY_SECRET 
    )
    config_model.endpoint = f'ecs.{region_id}.aliyuncs.com'
    return EcsClient(config_model)

def encode_command(command: str) -> str:
    return base64.b64encode(command.encode('utf-8')).decode('utf-8')

def get_region_by_instance(user_id: int, instance_id: str) -> str:
    """辅助函数：根据 instance_id 去本地数据库查出所属地域"""
    try:
        servers = get_active_servers(user_id)
        for srv in servers:
            if srv["instance_id"] == instance_id:
                return srv["region"]
    except Exception:
        pass
    return "cn-hongkong"  # 默认 fallback

def fetch_command_output_sync(client: EcsClient, region_id: str, invoke_id: str) -> str:
    """同步轮询阿里云助手执行结果，获取远程机器终端回显"""
    req = ecs_models.DescribeInvocationResultsRequest(
        region_id=region_id,
        invoke_id=invoke_id
    )
    for _ in range(5):
        time.sleep(2)
        try:
            resp = client.describe_invocation_results(req)
            if resp.body.invocation and resp.body.invocation.invocation_results.invocation_result:
                res = resp.body.invocation.invocation_results.invocation_result[0]
                if res.invocation_state in ["Success", "Failed", "Finished"]:
                    output_b64 = res.output or ""
                    if not output_b64:
                        return "指令已执行，但终端无文字回显。"
                    return base64.b64decode(output_b64).decode('utf-8', errors='ignore').strip()
        except Exception as e:
            print(f"轮询命令回显发生局部错误: {e}")
            continue
    return "⏳ 查询超时：后台任务仍运行中，请稍候点击探测按钮刷新。"

def build_mgui_keyboard(instance_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    """⭐ 核心键盘构建器：支持根据真实服务状态，实现「启动/停止」按钮的动态互斥变色"""
    # 第三排左侧按钮：根据运行状态动态变色切换
    if is_running:
        toggle_btn = InlineKeyboardButton(text="🛑 停止面板后台服务", callback_data=f"mgui_cmd:stop:{instance_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="🟢 启动 / 重启面板服务", callback_data=f"mgui_cmd:start:{instance_id}")

    builder = [
        [InlineKeyboardButton(text="🔍 实时探测面板状态 & 查看访问凭证", callback_data=f"mgui_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 一键部署/重装 MG-UI", callback_data=f"mgui_cmd:install:{instance_id}"),
            InlineKeyboardButton(text="🔄 更新面板到最新版", callback_data=f"mgui_cmd:update:{instance_id}")
        ],
        [
            toggle_btn,
            InlineKeyboardButton(text="🤖 绑定监控/流量 Bot", callback_data=f"mgui_cmd:bind_bot:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔑 随机重置面板登录密码", callback_data=f"mgui_cmd:reset_pass:{instance_id}"),
            InlineKeyboardButton(text="🗑️ 彻底卸载 MG-UI 面板", callback_data=f"mgui_cmd:uninstall:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= 🚀 1. 渲染 MG-UI 专属控制面板 =================
@router.callback_query(F.data.startswith("run_sh:mgui:"))
async def show_mgui_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    # 默认初次展开时，按照运行中状态渲染键盘（后续用户点探测或操作后会精确校准）
    keyboard = build_mgui_keyboard(instance_id, is_running=True)
    
    text = (
        f"🔴 <b>MG 私有化管理面板 (MG-UI) 控制中心</b>\n\n"
        f"🖥 <b>当前操作实例</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>运维功能指南</b>：\n"
        f"• <b>一键部署</b>：自动配置 Python 环境、Web 面板及后台守护进程。\n"
        f"• <b>绑定 Bot</b>：极速向远程数据库注入当前中控 Token，免网页登录直接激活监控。\n"
        f"• <b>访问凭证</b>：点击最上方「🔍 实时探测」，即可获取最新端口与账号密码。\n\n"
        f"👇 <b>请选择要向该远程服务器下发的运维管理指令：</b>"
    )
    
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()


# ================= 🚀 2. 接收 MG-UI 指令并进行远程调度 =================
@router.callback_query(F.data.startswith("mgui_cmd:"))
async def execute_mgui_command(call: CallbackQuery):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据包解密异常！", show_alert=True)
    
    # UI 测试模式安全拦截
    if "testVirtualServer" in instance_id:
        await call.answer(f"UI 测试模式：已模拟向 MG-UI 执行【{action}】操作！", show_alert=True)
        return

    await call.message.edit_text(f"⏳ 正在向实例 <code>{instance_id}</code> 下发 MG-UI <code>{action}</code> 指令，请稍候...", parse_mode="HTML")
    
    # 根据交互需求构建具体执行的底层 Shell 命令
    if action == "check":
        # ⭐ 深度探针：检查 systemd 运行状态 + 读取面板文件账号密码端口 + 检查数据库 Bot 绑定状态
        shell_script = """python3 -c "
import os, sqlite3, re
status_panel = 'running' if os.system('systemctl is-active --quiet mg-panel') == 0 else 'stopped'
status_bot = 'running' if os.system('systemctl is-active --quiet mg-bot') == 0 else 'stopped'
user, pwd, port = '未知', '未知', '8888'
if os.path.exists('/root/mg_panel.py'):
    with open('/root/mg_panel.py') as f:
        content = f.read()
        u = re.search(r'PANEL_USER\s*=\s*[\"\'](.+?)[\"\']', content)
        p = re.search(r'PANEL_PASS\s*=\s*[\"\'](.+?)[\"\']', content)
        pt = re.search(r'PANEL_PORT\s*=\s*(\d+)', content)
        if u: user = u.group(1)
        if p: pwd = p.group(1)
        if pt: port = pt.group(1)
bot_bound = '未绑定'
if os.path.exists('/root/mg_core.db'):
    try:
        conn = sqlite3.connect('/root/mg_core.db')
        c = conn.cursor()
        c.execute(\"SELECT count(*) FROM mg_settings WHERE key IN ('bot_token', 'admin_id') AND value != ''\")
        if c.fetchone()[0] == 2: bot_bound = '🟢 已绑定监听'
        conn.close()
    except: pass
print(f'PANEL_STATUS={status_panel}')
print(f'BOT_STATUS={status_bot}')
print(f'USER={user}')
print(f'PASS={pwd}')
print(f'PORT={port}')
print(f'BOUND={bot_bound}')
" """
    elif action == "install":
        shell_script = "bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/install.sh)"
    elif action == "update":
        shell_script = "bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/update.sh)"
    elif action == "uninstall":
        shell_script = "bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/uninstall.sh)"
    elif action == "start":
        shell_script = "systemctl start mg-panel && systemctl restart mg-panel && echo 'SUCCESS'"
    elif action == "stop":
        shell_script = "systemctl stop mg-panel && echo 'SUCCESS'"
    elif action == "reset_pass":
        # 随机生成 10 位强密码并使用 sed 写入 Python 文件，随后重启生效
        shell_script = """
NEW_PASS=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9!@#$' | fold -w 10 | head -n 1)
sed -i "s/PANEL_PASS = .*/PANEL_PASS = \"$NEW_PASS\"/g" /root/mg_panel.py
systemctl restart mg-panel
echo "NEW_PASS=$NEW_PASS"
"""
    elif action == "bind_bot":
        # ⭐ 零感绑定：直接将当前主机器人的 Token 与 AdminID 注入节点数据库，一键激活监控！
        shell_script = f"""python3 -c "
import sqlite3
conn = sqlite3.connect('/root/mg_core.db')
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS mg_settings (key TEXT PRIMARY KEY, value TEXT)')
c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('bot_token', '{config.BOT_TOKEN}'))
c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('admin_id', '{config.ADMIN_ID}'))
conn.commit()
conn.close()
" && systemctl restart mg-bot && echo "BIND_SUCCESS" """
    else:
        shell_script = "echo 'Unknown MGUI command'"

    # ---------------- 发起阿里云异步 RunCommand 与回调处理 ----------------
    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        encoded_script = encode_command(shell_script)
        
        request = ecs_models.RunCommandRequest(
            region_id=region_id,
            type='RunShellScript',
            command_content=encoded_script,
            instance_id=[instance_id],
            name=f"MG_Bot_UI_{action}",
            timeout=180  # 安装部署可能耗时稍长，放宽至 3 分钟
        )
        
        # 将同步的调用放入后台线程池
        response = await asyncio.to_thread(client.run_command, request)
        invoke_id = response.body.invoke_id
        
        # ---------------- 针对不同动作进行回显分析与动态 UI 重载 ----------------
        if action == "check":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            
            # 格式化解析 Python 探针结果
            data_map = {}
            for line in real_output.split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    data_map[k.strip()] = v.strip()
            
            is_running = (data_map.get("PANEL_STATUS") == "running")
            panel_status_text = "🟢 运行中 (Running)" if is_running else "🔴 已停止 (Stopped)"
            bot_status_text = "🟢 正常运行" if data_map.get("BOT_STATUS") == "running" else "🔴 停止/异常"
            
            # 根据真实服务状态，刷新键盘互斥按钮
            keyboard = build_mgui_keyboard(instance_id, is_running=is_running)
            
            await call.message.edit_text(
                f"📡 <b>MG-UI 实时深度探针报告</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ <b>Web 面板状态</b>：{panel_status_text}\n"
                f"🤖 <b>管家 Bot 状态</b>：{bot_status_text} | {data_map.get('BOUND', '未绑定')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🌐 <b>默认访问端口</b>：<code>{data_map.get('PORT', '8888')}</code>\n"
                f"👤 <b>面板登录账号</b>：<code>{data_map.get('USER', '未知')}</code>\n"
                f"🔑 <b>面板登录密码</b>：<code>{data_map.get('PASS', '未知')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>提示：复制上方账号密码，在浏览器访问 http://该实例IP:端口 即可进入 Web 后台。</i>",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        elif action == "reset_pass":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            new_pass = "解析失败，请点击实时探测查看"
            for line in real_output.split("\n"):
                if line.startswith("NEW_PASS="):
                    new_pass = line.split("=", 1)[1].strip()
            
            keyboard = build_mgui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>面板登录密码已成功重置并生效！</b>\n\n"
                f"🖥 <b>实例ID</b>：<code>{instance_id}</code>\n"
                f"🔑 <b>全新登录密码</b>：<code>{new_pass}</code>\n\n"
                f"💡 <i>服务已自动完成热重启，请妥善保管新密码。</i>",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        elif action == "bind_bot":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            keyboard = build_mgui_keyboard(instance_id, is_running=True)
            
            if "BIND_SUCCESS" in real_output:
                await call.message.edit_text(
                    f"🤖 <b>流量监控 Bot 绑定成功！</b>\n\n"
                    f"🖥 <b>实例ID</b>：<code>{instance_id}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✅ 已成功将当前中控 Token 与 Admin ID 注入远程 SQLite 数据库，并拉起 <code>mg-bot.service</code> 后台守护进程。\n"
                    f"🛡️ <b>该节点现已具备到期提醒、超限阻断及定时巡检能力！</b>",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                await call.message.edit_text(
                    f"⚠️ <b>绑定指令已下发，但未捕获明确回显</b>\n\n"
                    f"可能是由于底层数据库未完成初始化，请稍候点击「🔍 实时探测」核对绑定状态。\n"
                    f"底层回显：<code>{real_output[:100]}</code>",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                
        elif action in ["start", "stop"]:
            # 启动或停止执行后，动态重载对应的相反操作键盘
            is_now_running = (action == "start")
            keyboard = build_mgui_keyboard(instance_id, is_running=is_now_running)
            status_word = "启动" if is_now_running else "停止"
            
            await call.message.edit_text(
                f"✅ <b>MG-UI 面板服务已成功{status_word}！</b>\n\n"
                f"🖥 <b>实例ID</b>：<code>{instance_id}</code>\n"
                f"⏳ <i>底层的 systemd 服务已完成变更，您可以通过底部按钮随时切换服务状态。</i>",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        else:
            # 针对 deploy / update / uninstall 等基础长任务指令的回显
            keyboard = build_mgui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>MG-UI {action} 指令下发成功！</b>\n\n"
                f"🖥 <b>实例ID</b>：<code>{instance_id}</code>\n"
                f"⏳ <b>任务类型</b>：底层一键自动化运维\n\n"
                f"系统已在服务器后台开始执行处理。该任务大约需要 30~60 秒完结，建议稍后点击最上方的「🔍 实时探测」验证最终运行结果。",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        # 降级容错：发生 API 异常或网络通信中断时，剥离标签解析纯文本化报错
        await call.message.edit_text(
            f"❌ 执行远程管理命令失败：\n{str(e)}",
            parse_mode=None
        )
    finally:
        await call.answer()
