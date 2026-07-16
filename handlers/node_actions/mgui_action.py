import base64
import asyncio
import time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models

import config
from db import get_active_servers

router = Router()

# ================= 🛠️ 独立绑定 Bot 凭证状态机 (备用) =================
class MguiBindBotFSM(StatesGroup):
    wait_for_token = State()
    wait_for_admin = State()

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
    try:
        servers = get_active_servers(user_id)
        for srv in servers:
            if srv["instance_id"] == instance_id:
                return srv["region"]
    except Exception:
        pass
    return "cn-hongkong" 

def fetch_command_output_sync(client: EcsClient, region_id: str, invoke_id: str) -> str:
    req = ecs_models.DescribeInvocationResultsRequest(region_id=region_id, invoke_id=invoke_id)
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
        except Exception:
            continue
    return "⏳ 查询超时：后台任务仍运行中，请稍候点击探测刷新。"

def build_mgui_keyboard(instance_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    if is_running:
        toggle_btn = InlineKeyboardButton(text="🛑 停止面板后台服务", callback_data=f"mgui_cmd:stop:{instance_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="🟢 启动 / 重启面板服务", callback_data=f"mgui_cmd:start:{instance_id}")

    builder = [
        [InlineKeyboardButton(text="🔍 实时探测状态 & 查看登录账号密码", callback_data=f"mgui_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 一键部署/重装 (含自动绑定Bot)", callback_data=f"mgui_cmd:install:{instance_id}"),
            InlineKeyboardButton(text="🔄 更新面板到最新版", callback_data=f"mgui_cmd:update:{instance_id}")
        ],
        [
            toggle_btn,
            InlineKeyboardButton(text="🤖 修改/重新绑定 Bot", callback_data=f"mgui_cmd:bind_bot:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔑 随机重置登录密码", callback_data=f"mgui_cmd:reset_pass:{instance_id}"),
            InlineKeyboardButton(text="🗑️ 彻底卸载 MG-UI 面板", callback_data=f"mgui_cmd:uninstall:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= 🚀 1. 渲染 MG-UI 初始化交付面板 =================
@router.callback_query(F.data.startswith("run_sh:mgui:"))
async def show_mgui_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    keyboard = build_mgui_keyboard(instance_id, is_running=True)
    
    text = (
        f"🔴 <b>MG 私有化面板 (MG-UI) 自动交付中心</b>\n\n"
        f"🖥 <b>操作物理机</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>默认监听端口</b>：<code>8888</code>\n"
        f"💡 <b>初始化交付说明</b>：\n"
        f"• <b>一键交付</b>：点击「🟢 一键部署/重装」，系统将自动安装面板并<b>无感注入当前设置好的 Bot 凭证</b>。\n"
        f"• <b>业务运维</b>：本控制台仅负责环境交付与启停控制；节点与端口配置请登录 Web 后台或专用 Bot 操作。\n\n"
        f"👇 <b>请选择要向该阿里云机器下发的初始化指令：</b>"
    )
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()


# ================= 🚀 2. 接收并自动化下发初始化脚本 =================
@router.callback_query(F.data.startswith("mgui_cmd:"))
async def execute_mgui_command(call: CallbackQuery, state: FSMContext):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据解密异常！", show_alert=True)
    
    if "testVirtualServer" in instance_id:
        return await call.answer(f"UI 测试模式：已模拟向 MG-UI 执行【{action}】！", show_alert=True)

    if action == "bind_bot":
        await state.update_data(bind_instance_id=instance_id)
        await state.set_state(MguiBindBotFSM.wait_for_token)
        await call.message.answer(
            f"🤖 <b>为该机器重新配置监控 Bot 凭证</b>\n\n"
            f"👉 请回复需要注入该机器底层数据库的 <b>Bot Token</b>：\n\n"
            f"<i>(回复 0 则直接使用当前中控默认配置；发送 /cancel 取消)</i>",
            parse_mode="HTML"
        )
        return await call.answer()

    await call.message.edit_text(f"⏳ 正在向实例 <code>{instance_id}</code> 下发 <code>{action}</code> 指令...", parse_mode="HTML")
    
    if action == "check":
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
        if c.fetchone()[0] == 2: bot_bound = '🟢 已绑定激活'
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
        # ⭐ 核心逻辑：执行 GitHub 安装脚本后，立刻无感把 BOT_TOKEN 和 ADMIN_ID 写入数据库并拉起服务！
        shell_script = f"""
bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/install.sh) &&
python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('/root/mg_core.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS mg_settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('bot_token', '{config.BOT_TOKEN}'))
    c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('admin_id', '{config.ADMIN_ID}'))
    conn.commit()
    conn.close()
except: pass" && systemctl restart mg-bot && echo "INSTALL_AND_BIND_SUCCESS"
        """
    elif action == "update":
        shell_script = "bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/update.sh)"
    elif action == "uninstall":
        shell_script = "bash <(curl -sL https://raw.githubusercontent.com/alnawei/sh/main/MG-UI/uninstall.sh)"
    elif action == "start":
        shell_script = "systemctl start mg-panel && systemctl restart mg-panel && echo 'SUCCESS'"
    elif action == "stop":
        shell_script = "systemctl stop mg-panel && echo 'SUCCESS'"
    elif action == "reset_pass":
        shell_script = """
NEW_PASS=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9!@#$' | fold -w 10 | head -n 1)
sed -i "s/PANEL_PASS = .*/PANEL_PASS = \"$NEW_PASS\"/g" /root/mg_panel.py
systemctl restart mg-panel
echo "NEW_PASS=$NEW_PASS"
"""
    else:
        shell_script = "echo 'Unknown command'"

    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        encoded_script = encode_command(shell_script)
        
        request = ecs_models.RunCommandRequest(
            region_id=region_id, type='RunShellScript', command_content=encoded_script,
            instance_id=[instance_id], name=f"MG_UI_{action}", timeout=180
        )
        response = await asyncio.to_thread(client.run_command, request)
        invoke_id = response.body.invoke_id
        
        if action == "check":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            data_map = {}
            for line in real_output.split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    data_map[k.strip()] = v.strip()
            
            is_running = (data_map.get("PANEL_STATUS") == "running")
            panel_status = "🟢 运行中 (Running)" if is_running else "🔴 已停止 (Stopped)"
            bot_status = "🟢 正常运行" if data_map.get("BOT_STATUS") == "running" else "🔴 停止/异常"
            
            keyboard = build_mgui_keyboard(instance_id, is_running=is_running)
            await call.message.edit_text(
                f"📡 <b>MG-UI 自动化交付与探针报告</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ <b>Web 面板状态</b>：{panel_status}\n"
                f"🤖 <b>监控 Bot 状态</b>：{bot_status} | {data_map.get('BOUND', '未绑定')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🌐 <b>默认访问端口</b>：<code>{data_map.get('PORT', '8888')}</code>\n"
                f"👤 <b>面板登录账号</b>：<code>{data_map.get('USER', '未知')}</code>\n"
                f"🔑 <b>面板登录密码</b>：<code>{data_map.get('PASS', '未知')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>提示：该服务器环境已交付，你可以直接在浏览器输入 http://实例公网IP:端口 登录后台操作！</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        elif action == "reset_pass":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            new_pass = "解析失败，请点击实时探测查看"
            for line in real_output.split("\n"):
                if line.startswith("NEW_PASS="):
                    new_pass = line.split("=", 1)[1].strip()
            
            keyboard = build_mgui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>密码已重置生效！</b>\n\n🖥 实例ID：<code>{instance_id}</code>\n🔑 <b>新密码</b>：<code>{new_pass}</code>\n\n💡 <i>请妥善保管新密码。</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        elif action in ["start", "stop"]:
            is_now_running = (action == "start")
            keyboard = build_mgui_keyboard(instance_id, is_running=is_now_running)
            status_word = "启动" if is_now_running else "停止"
            await call.message.edit_text(
                f"✅ <b>面板服务已成功{status_word}！</b>\n\n🖥 实例ID：<code>{instance_id}</code>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            keyboard = build_mgui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>指令下发成功！</b>\n\n🖥 实例ID：<code>{instance_id}</code>\n⏳ <b>执行动作</b>：初始化自动交付与部署 ({action})\n\n任务将在服务器后台自动执行完结（约需40秒），完成后将自动完成 Bot 凭证绑定！",
                reply_markup=keyboard, parse_mode="HTML"
            )
    except Exception as e:
        await call.message.edit_text(f"❌ 执行失败：\n{str(e)}", parse_mode=None)
    finally:
        await call.answer()


# ================= 🚀 3. 手动修改/绑定 Bot 凭证状态机 (备用) =================
@router.message(MguiBindBotFSM.wait_for_token)
async def mgui_bind_token(message: Message, state: FSMContext):
    token = message.text.strip()
    if token == '0': token = config.BOT_TOKEN
    await state.update_data(bot_token=token)
    await state.set_state(MguiBindBotFSM.wait_for_admin)
    await message.answer("👤 <b>请输入接收该节点报警的 Admin ID：</b>\n<i>(回复 0 使用当前默认 Admin ID)</i>", parse_mode="HTML")

@router.message(MguiBindBotFSM.wait_for_admin)
async def mgui_bind_admin(message: Message, state: FSMContext):
    admin_id = message.text.strip()
    if admin_id == '0': admin_id = config.ADMIN_ID
    
    data = await state.get_data()
    token = data.get('bot_token')
    instance_id = data.get('bind_instance_id')
    await state.clear()
    
    wait_msg = await message.answer("⏳ 正在向该机器底层注入 Bot 凭证并启动守护进程...")
    shell_script = f"""python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('/root/mg_core.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS mg_settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('bot_token', '{token}'))
    c.execute('REPLACE INTO mg_settings (key, value) VALUES (?, ?)', ('admin_id', '{admin_id}'))
    conn.commit(); conn.close()
except: pass" && systemctl restart mg-bot && echo "BIND_SUCCESS" """
    
    try:
        region_id = get_region_by_instance(message.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=60)
        resp = await asyncio.to_thread(client.run_command, req)
        out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
        keyboard = build_mgui_keyboard(instance_id, is_running=True)
        
        if "BIND_SUCCESS" in out:
            await wait_msg.edit_text(
                f"🤖 <b>Bot 凭证绑定成功！</b>\n\n"
                f"🖥 <b>机器实例</b>：<code>{instance_id}</code>\n"
                f"🔑 <b>绑定Token</b>：<code>{token[:10]}...</code>\n"
                f"✅ 机器已自动拉起后台进程，监控已就绪！",
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            await wait_msg.edit_text(f"⚠️ 绑定下发完成，请点击实时探测核对状态。\n回显：{out[:50]}", reply_markup=keyboard)
    except Exception as e:
        await wait_msg.edit_text(f"❌ 注入失败：\n{str(e)}", parse_mode=None)
