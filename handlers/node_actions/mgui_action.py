import base64
import asyncio
import time
import json
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

# ================= 🛠️ FSM 状态机 =================
class MguiBindBotFSM(StatesGroup):
    wait_for_token = State()
    wait_for_admin = State()

class MguiAddPortFSM(StatesGroup):
    wait_for_port = State()
    wait_for_limit = State()

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
    for _ in range(6):
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
    return "⏳ 查询超时：后台任务仍运行中，请稍候刷新。"

def build_mgui_keyboard(instance_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    if is_running:
        toggle_btn = InlineKeyboardButton(text="🛑 停止面板后台服务", callback_data=f"mgui_cmd:stop:{instance_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="🟢 启动 / 重启面板服务", callback_data=f"mgui_cmd:start:{instance_id}")

    builder = [
        # ⭐ 独家升级：在此处进入底层端口管控中心
        [InlineKeyboardButton(text="📋 节点端口管控 (查看 / 修改 / 新增端口)", callback_data=f"mgui_port:list:{instance_id}")],
        [InlineKeyboardButton(text="🔍 实时探测面板状态 & 查看访问凭证", callback_data=f"mgui_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 一键部署/重装 MG-UI", callback_data=f"mgui_cmd:install:{instance_id}"),
            InlineKeyboardButton(text="🔄 更新面板到最新版", callback_data=f"mgui_cmd:update:{instance_id}")
        ],
        [
            toggle_btn,
            InlineKeyboardButton(text="🤖 绑定专属监控 Bot", callback_data=f"mgui_cmd:bind_bot:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔑 随机重置面板登录密码", callback_data=f"mgui_cmd:reset_pass:{instance_id}"),
            InlineKeyboardButton(text="🗑️ 彻底卸载 MG-UI 面板", callback_data=f"mgui_cmd:uninstall:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= 🚀 1. 渲染 MG-UI 主控制面板 =================
@router.callback_query(F.data.startswith("run_sh:mgui:"))
async def show_mgui_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    keyboard = build_mgui_keyboard(instance_id, is_running=True)
    
    text = (
        f"🔴 <b>MG 私有化管理面板 (MG-UI) 控制中心</b>\n\n"
        f"🖥 <b>当前操作实例</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>默认监听端口</b>：<code>8888</code>\n"
        f"💡 <b>运维指南与功能介绍</b>：\n"
        f"• <b>端口管控</b>：点击最上方<b>「📋 节点端口管控」</b>可直接对本物理机的多节点进行续费/清零/添删。\n"
        f"• <b>一键部署</b>：自动配置面板，并默认注入当前主控 Bot 防冲突雷达。\n"
        f"• <b>访问凭证</b>：点击「🔍 实时探测」，即可同步最新端口与登录账号密码。\n\n"
        f"👇 <b>请选择要向该远程服务器下发的运维管理指令：</b>"
    )
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()


# ================= 🚀 2. 接收 MG-UI 基础面板指令 =================
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
            f"🤖 <b>为该节点配置专属独立 Bot</b>\n\n"
            f"如果想给不同的节点配置专属通知 Bot，请回复其 <b>Bot Token</b>：\n\n"
            f"<i>(如需直接使用主控默认配置，请回复 0；发送 /cancel 取消)</i>",
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
        if c.fetchone()[0] == 2: bot_bound = '🟢 已绑定静默雷达'
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
except: pass" && systemctl restart mg-bot
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
            panel_status_text = "🟢 运行中 (Running)" if is_running else "🔴 已停止 (Stopped)"
            bot_status_text = "🟢 正常运行" if data_map.get("BOT_STATUS") == "running" else "🔴 停止/异常"
            
            keyboard = build_mgui_keyboard(instance_id, is_running=is_running)
            await call.message.edit_text(
                f"📡 <b>MG-UI 实时深度探针报告</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ <b>Web 面板状态</b>：{panel_status_text}\n"
                f"🤖 <b>预警雷达状态</b>：{bot_status_text} | {data_map.get('BOUND', '未绑定')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🌐 <b>默认访问端口</b>：<code>{data_map.get('PORT', '8888')}</code>\n"
                f"👤 <b>面板登录账号</b>：<code>{data_map.get('USER', '未知')}</code>\n"
                f"🔑 <b>面板登录密码</b>：<code>{data_map.get('PASS', '未知')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>提示：复制上方凭证，在浏览器访问 http://公网IP:端口 即可进入 Web 面板。</i>",
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
                f"✅ <b>指令下发成功！</b>\n\n🖥 实例ID：<code>{instance_id}</code>\n⏳ <b>执行动作</b>：一键自动化运维 ({action})\n\n任务大约需要 30~60 秒完结，稍后可点击实时探测验证。",
                reply_markup=keyboard, parse_mode="HTML"
            )
    except Exception as e:
        await call.message.edit_text(f"❌ 执行失败：\n{str(e)}", parse_mode=None)
    finally:
        await call.answer()


# ================= 🚀 3. 【核心升维】全套两级端口管理中心 =================
@router.callback_query(F.data.startswith("mgui_port:"))
async def mgui_port_manager(call: CallbackQuery, state: FSMContext):
    try:
        parts = call.data.split(":")
        action = parts[1]
        instance_id = parts[2]
        port_num = parts[3] if len(parts) > 3 else None
    except ValueError:
        return await call.answer("端口回调数据格式异常！", show_alert=True)
        
    if "testVirtualServer" in instance_id:
        return await call.answer("测试节点无法拉取底层端口！", show_alert=True)
        
    region_id = get_region_by_instance(call.from_user.id, instance_id)
    client = get_ecs_client(region_id)

    # (A) 渲染服务器上的端口列表
    if action == "list":
        await call.message.edit_text(f"📡 正在拉取物理实例 <code>{instance_id}</code> 的各个节点端口，请稍候...", parse_mode="HTML")
        shell_script = """python3 -c "
import sqlite3, json, os
if not os.path.exists('/root/mg_core.db'):
    print('JSON_RES:[]')
else:
    try:
        conn = sqlite3.connect('/root/mg_core.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT port, status, used_bytes, limit_gb, expiry_date FROM mg_nodes')
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        print('JSON_RES:' + json.dumps(rows))
    except:
        print('JSON_RES:[]')
" """
        try:
            req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=30)
            resp = await asyncio.to_thread(client.run_command, req)
            out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
            
            nodes = []
            for line in out.split("\n"):
                if line.startswith("JSON_RES:"):
                    try: nodes = json.loads(line.split(":", 1)[1])
                    except: pass
            
            buttons = []
            for n in nodes:
                p = n['port']
                used_gb = round((n.get('used_bytes') or 0) / (1024**3), 1)
                limit_gb = int(n.get('limit_gb') or 0)
                status_icon = "🟢" if n.get('status') == 'running' else ("🔴" if n.get('status') == 'blocked' else "⚪")
                btn_text = f"{status_icon} 端口 {p} | 流量 {used_gb}/{limit_gb}G"
                buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"mgui_port:detail:{instance_id}:{p}")])
                
            buttons.append([InlineKeyboardButton(text="➕ 在该物理机上添加新端口", callback_data=f"mgui_port:add_start:{instance_id}")])
            buttons.append([InlineKeyboardButton(text="🔙 返回 MG-UI 控制面板", callback_data=f"run_sh:mgui:{instance_id}")])
            
            text = (
                f"📋 <b>物理实例节点列表</b>\n\n"
                f"🖥 <b>当前服务器</b>：<code>{instance_id}</code>\n"
                f"📊 <b>运行节点</b>：共 {len(nodes)} 个端口处于托管状态\n\n"
                f"👇 <b>请点击具体端口进入详情页与运维管理：</b>"
            )
            await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
        except Exception as e:
            await call.message.edit_text(f"❌ 读取端口列表失败：\n{str(e)}", parse_mode=None)

    # (B) 渲染具体端口的详情页面
    elif action == "detail" and port_num:
        await call.message.edit_text(f"📡 正在拉取端口 <code>{port_num}</code> 的详细配置与密钥...", parse_mode="HTML")
        shell_script = f"""python3 -c "
import sqlite3, json, os
try:
    conn = sqlite3.connect('/root/mg_core.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM mg_nodes WHERE port={port_num}')
    row = c.fetchone()
    conn.close()
    if row: print('NODE_RES:' + json.dumps(dict(row)))
    else: print('NODE_RES:NONE')
except: print('NODE_RES:NONE')
" """
        try:
            req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=30)
            resp = await asyncio.to_thread(client.run_command, req)
            out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
            
            node = None
            for line in out.split("\n"):
                if line.startswith("NODE_RES:") and "NONE" not in line:
                    try: node = json.loads(line.split(":", 1)[1])
                    except: pass
            
            if not node:
                return await call.answer("❌ 该端口在远程数据库中不存在或已删除！", show_alert=True)
                
            used_gb = round((node.get('used_bytes') or 0) / (1024**3), 2)
            limit_gb = node.get('limit_gb') or 0
            expiry = node.get('expiry_date') or '长期有效'
            secret = node.get('secret') or '无'
            status_map = {'running': '🟢 运行中', 'stopped': '⚪ 已停止', 'expired': '⏳ 已到期', 'blocked': '🔴 超限阻断'}
            
            # 获取公网 IP 用于拼接通用链接
            try: ip = client.describe_instances(ecs_models.DescribeInstancesRequest(region_id=region_id, instance_ids=json.dumps([instance_id]))).body.instances.instance[0].public_ip_address.ip_address[0]
            except: ip = "服务器公网IP"
            link = f"tg://proxy?server={ip}&port={port_num}&secret={secret}"
            
            text = (
                f"📄 <b>端口节点详细信息</b>\n━━━━━━━━━━━━━━━\n"
                f"🖥 <b>物理实例：</b><code>{instance_id}</code>\n"
                f"🔌 <b>端口编号：</b><code>{port_num}</code>\n"
                f"🕒 <b>到期时间：</b>{expiry}\n"
                f"📊 <b>已用流量：</b>{used_gb} / {limit_gb} GB\n"
                f"📈 <b>当前状态：</b>{status_map.get(node.get('status'), '未知')}\n\n"
                f"🔑 <b>直连密钥：</b>\n<code>{secret}</code>\n\n"
                f"🔗 <b>一键直连链接：</b>\n<code>{link}</code>"
            )
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 一键延期 (续费 1 个月)", callback_data=f"mgui_port:renew:{instance_id}:{port_num}")],
                [InlineKeyboardButton(text="🔄 流量清零", callback_data=f"mgui_port:reset:{instance_id}:{port_num}"),
                 InlineKeyboardButton(text="❌ 彻底删除端口", callback_data=f"mgui_port:delete:{instance_id}:{port_num}")],
                [InlineKeyboardButton(text="🔙 返回端口列表", callback_data=f"mgui_port:list:{instance_id}")]
            ])
            await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            await call.message.edit_text(f"❌ 拉取详情失败：\n{str(e)}", parse_mode=None)

    # (C) 端口一键续费 1 个月
    elif action == "renew" and port_num:
        await call.message.edit_text(f"⏳ 正在为实例 <code>{instance_id}</code> 的 <code>{port_num}</code> 端口延期 1 个自然月...", parse_mode="HTML")
        shell_script = f"""python3 -c "
import sqlite3, datetime, calendar, subprocess
def add_months(d, m):
    month = d.month - 1 + m
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)
try:
    conn = sqlite3.connect('/root/mg_core.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT expiry_date, status, secret FROM mg_nodes WHERE port={port_num}')
    row = c.fetchone()
    if row:
        base = datetime.datetime.now()
        if row['expiry_date']:
            try:
                curr = datetime.datetime.strptime(row['expiry_date'], '%Y-%m-%d %H:%M:%S')
                if curr > base: base = curr
            except: pass
        new_exp = add_months(base, 1).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('UPDATE mg_nodes SET expiry_date=?, status=\'running\' WHERE port={port_num}', (new_exp,))
        conn.commit()
        if row['status'] in ['expired', 'blocked', 'stopped']:
            subprocess.run(['iptables', '-D', 'INPUT', '-p', 'tcp', '--dport', '{port_num}', '-j', 'DROP'], stderr=subprocess.DEVNULL)
            subprocess.run(['bash', '/root/mg_executor.sh', 'start', '{port_num}', row['secret']], stderr=subprocess.DEVNULL)
    conn.close()
    print('ACTION_SUCCESS')
except Exception as e: print(f'ERR:{{e}}')
" """
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=30)
        await asyncio.to_thread(client.run_command, req)
        await asyncio.sleep(2)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 刷新查看端口详情", callback_data=f"mgui_port:detail:{instance_id}:{port_num}")]])
        await call.message.edit_text(f"✅ <b>续费成功！</b>\n\n端口 <code>{port_num}</code> 已顺延 1 个自然月，到期和阻断状态已重置为正常运行。", reply_markup=kb, parse_mode="HTML")

    # (D) 端口流量清零
    elif action == "reset" and port_num:
        shell_script = f"""
bash /root/mg_executor.sh reset {port_num} &&
python3 -c "import sqlite3; conn=sqlite3.connect('/root/mg_core.db'); c=conn.cursor(); c.execute('UPDATE mg_nodes SET used_bytes=0 WHERE port={port_num}'); conn.commit(); conn.close()" &&
echo "ACTION_SUCCESS"
        """
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=30)
        await asyncio.to_thread(client.run_command, req)
        await asyncio.sleep(1)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 刷新查看端口详情", callback_data=f"mgui_port:detail:{instance_id}:{port_num}")]])
        await call.message.edit_text(f"✅ <b>流量清零成功！</b>\n\n端口 <code>{port_num}</code> 的计数已清空。", reply_markup=kb, parse_mode="HTML")

    # (E) 彻底删除端口
    elif action == "delete" and port_num:
        shell_script = f"""
bash /root/mg_executor.sh delete {port_num} &&
python3 -c "import sqlite3; conn=sqlite3.connect('/root/mg_core.db'); c=conn.cursor(); c.execute('DELETE FROM mg_nodes WHERE port={port_num}'); conn.commit(); conn.close()" &&
echo "ACTION_SUCCESS"
        """
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=30)
        await asyncio.to_thread(client.run_command, req)
        await asyncio.sleep(1)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回端口列表", callback_data=f"mgui_port:list:{instance_id}")]])
        await call.message.edit_text(f"🗑️ <b>端口已彻底卸载！</b>\n\n编号 <code>{port_num}</code> 已从底层防火墙与数据库中移除。", reply_markup=kb, parse_mode="HTML")

    # (F) 触发新增端口流程
    elif action == "add_start":
        await state.update_data(add_instance_id=instance_id)
        await state.set_state(MguiAddPortFSM.wait_for_port)
        await call.message.answer(
            f"➕ <b>向物理机 <code>{instance_id}</code> 添加新端口</b>\n\n"
            f"👉 请回复想要开放的 <b>纯数字端口号</b> (如 58899)：\n\n"
            f"<i>(回复 0 将由系统自动随机分配；发送 /cancel 取消)</i>",
            parse_mode="HTML"
        )
    await call.answer()


# ================= 🚀 4. FSM 交互收集参数并新增端口 =================
@router.message(MguiAddPortFSM.wait_for_port)
async def mgui_add_port_step1(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == '0':
        import random
        port = str(random.randint(10000, 60000))
    else:
        if not text.isdigit():
            return await message.answer("❌ 格式错误，请输入纯数字端口 (如 58899)：")
        port = text
    await state.update_data(new_port=port)
    await state.set_state(MguiAddPortFSM.wait_for_limit)
    await message.answer(f"✅ 已选择端口：<b>{port}</b>\n\n👉 请继续输入该端口的<b>每月流量限额 (GB)</b>\n<i>(直接回复数字即可，例如 500 或 1000)</i>", parse_mode="HTML")

@router.message(MguiAddPortFSM.wait_for_limit)
async def mgui_add_port_step2(message: Message, state: FSMContext):
    try: limit = float(message.text.strip())
    except ValueError: return await message.answer("❌ 请输入有效数字 (如 500)：")
    
    data = await state.get_data()
    port = data.get('new_port')
    instance_id = data.get('add_instance_id')
    await state.clear()
    
    wait_msg = await message.answer(f"⏳ 正在向远程节点 <code>{instance_id}</code> 底层配置开放 <code>{port}</code> 端口并分配独立密钥...", parse_mode="HTML")
    
    shell_script = f"""python3 -c "
import sqlite3, datetime, subprocess
port = {port}
limit = {limit}
try: secret = subprocess.check_output('/usr/local/bin/mg generate-secret --hex icloud.com', shell=True).decode('utf-8').strip()
except: secret = 'ee1234567890abcdef1234567890abcdef69636c6f75642e636f6d'
expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
conn = sqlite3.connect('/root/mg_core.db')
c = conn.cursor()
c.execute('INSERT OR REPLACE INTO mg_nodes (port, secret, limit_gb, status, reset_cycle, expiry_date) VALUES (?, ?, ?, \'running\', \'monthly\', ?)', (port, secret, limit, expiry))
conn.commit(); conn.close()
print(f'GEN_SECRET={{secret}}')
" && bash /root/mg_executor.sh start {port} $(python3 -c "import sqlite3; conn=sqlite3.connect('/root/mg_core.db'); c=conn.cursor(); c.execute('SELECT secret FROM mg_nodes WHERE port={port}'); print(c.fetchone()[0]); conn.close()") && echo "ADD_SUCCESS" """
    
    try:
        region_id = get_region_by_instance(message.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=45)
        resp = await asyncio.to_thread(client.run_command, req)
        out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
        
        secret = "未知密钥"
        for line in out.split("\n"):
            if line.startswith("GEN_SECRET="):
                secret = line.split("=", 1)[1].strip()
                
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 查看该端口详情", callback_data=f"mgui_port:detail:{instance_id}:{port}")],
            [InlineKeyboardButton(text="📋 返回端口列表", callback_data=f"mgui_port:list:{instance_id}")]
        ])
        
        if "ADD_SUCCESS" in out or "GEN_SECRET" in out:
            await wait_msg.edit_text(
                f"🎉 <b>新端口节点部署成功！</b>\n\n"
                f"🖥 <b>物理实例</b>：<code>{instance_id}</code>\n"
                f"🔌 <b>开放端口</b>：<code>{port}</code>\n"
                f"📊 <b>流量限额</b>：{limit} GB\n\n"
                f"🔑 <b>专属直连密钥</b>：\n<code>{secret}</code>\n\n"
                f"💡 <i>底层代理进程及防火墙规则已自动放行就绪！</i>",
                reply_markup=kb, parse_mode="HTML"
            )
        else:
            await wait_msg.edit_text(f"⚠️ 端口添加指令已下发，但终端回显异常，请前往列表核实。\n回显：{out[:60]}", reply_markup=kb)
    except Exception as e:
        await wait_msg.edit_text(f"❌ 部署失败：\n{str(e)}", parse_mode=None)


# ================= 🚀 5. 绑定专属 Bot 的参数收集 =================
@router.message(MguiBindBotFSM.wait_for_token)
async def mgui_bind_token(message: Message, state: FSMContext):
    token = message.text.strip()
    if token == '0': token = config.BOT_TOKEN
    await state.update_data(bot_token=token)
    await state.set_state(MguiBindBotFSM.wait_for_admin)
    await message.answer("👤 <b>请输入接收该节点报警的 Admin ID：</b>\n<i>(如需使用主控默认 Admin ID，请回复 0)</i>", parse_mode="HTML")

@router.message(MguiBindBotFSM.wait_for_admin)
async def mgui_bind_admin(message: Message, state: FSMContext):
    admin_id = message.text.strip()
    if admin_id == '0': admin_id = config.ADMIN_ID
    
    data = await state.get_data()
    token = data.get('bot_token')
    instance_id = data.get('bind_instance_id')
    await state.clear()
    
    wait_msg = await message.answer("⏳ 正在向远程服务器底层注入 Bot 专属配置并启停守护进程...")
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
                f"🤖 <b>专属静默雷达绑定成功！</b>\n\n"
                f"🖥 <b>节点实例</b>：<code>{instance_id}</code>\n"
                f"🔑 <b>专属Token</b>：<code>{token[:10]}...</code>\n"
                f"✅ 守护进程已重启，该节点现以静默推送模式独立告警！",
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            await wait_msg.edit_text(f"⚠️ 绑定下发完成，请核素探测状态。\n回显：{out[:50]}", reply_markup=keyboard)
    except Exception as e:
        await wait_msg.edit_text(f"❌ 注入失败：\n{str(e)}", parse_mode=None)
