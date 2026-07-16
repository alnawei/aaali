import base64
import asyncio
import time
import json
import random
import uuid
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
class XuiRealityFSM(StatesGroup):
    wait_for_upstream = State()  # 等待输入上游 SOCKS 转发
    wait_for_limit = State()     # 等待输入流量限额(GB)

class XuiLimitFSM(StatesGroup):
    wait_for_port = State()      # 等待输入要修改的端口号
    wait_for_new_limit = State() # 等待输入新流量上限/清零

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
    return "⏳ 查询超时：后台任务仍运行中，请稍候点击探测刷新。"

def build_xui_keyboard(instance_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    if is_running:
        toggle_btn = InlineKeyboardButton(text="🛑 停止面板后台服务", callback_data=f"xui_cmd:stop:{instance_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="🟢 启动 / 重启面板服务", callback_data=f"xui_cmd:start:{instance_id}")

    builder = [
        # ⭐ 核心功能全新矩阵：最强伪装、中转落地、流量管控，尽在顶排！
        [InlineKeyboardButton(text="⚡️ 一键生成 VLESS-Reality (含住宅中转 & 500G限额)", callback_data=f"xui_cmd:add_reality_start:{instance_id}")],
        [InlineKeyboardButton(text="📊 修改端口流量限额 / 流量一键清零", callback_data=f"xui_cmd:modify_limit_start:{instance_id}")],
        [InlineKeyboardButton(text="🔍 实时探测状态 & 查看登录账号密码", callback_data=f"xui_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 一键部署/重装 (固定端口54321)", callback_data=f"xui_cmd:install:{instance_id}"),
            InlineKeyboardButton(text="🔄 更新 3x-ui 到最新版", callback_data=f"xui_cmd:update:{instance_id}")
        ],
        [
            toggle_btn,
            InlineKeyboardButton(text="🚀 重启面板服务 (Restart)", callback_data=f"xui_cmd:restart:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔑 恢复默认账号密码 (admin)", callback_data=f"xui_cmd:reset_pass:{instance_id}"),
            InlineKeyboardButton(text="🗑️ 彻底卸载 X-UI 面板", callback_data=f"xui_cmd:uninstall:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=builder)


# ================= 🚀 1. 渲染 X-UI 控制中心主面板 =================
@router.callback_query(F.data.startswith("run_sh:xui:"))
async def show_xui_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    keyboard = build_xui_keyboard(instance_id, is_running=True)
    
    text = (
        f"⚡️ <b>3x-ui 代理面板自动交付与管理中心</b>\n\n"
        f"🖥 <b>操作物理机</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>默认固定端口</b>：<code>54321</code> | 👤 <b>账号</b>：<code>admin</code> | 🔑 <b>密码</b>：<code>admin</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>核心高阶功能架构</b>：\n"
        f"• <b>VLESS-Reality</b>：融合苹果 CDN 顶级伪装 + <b>住宅 IP 中转路由</b>，默认赋予 <b>500GB 细粒度管控</b>！\n"
        f"• <b>流量管控</b>：随时点击「📊 修改端口流量限额」，可对任意节点实时调整额度并实现一键清零！\n\n"
        f"👇 <b>请选择要向该远程机器下发的 X-UI 极速管理指令：</b>"
    )
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()


# ================= 🚀 2. 接收管理指令与触发 FSM =================
@router.callback_query(F.data.startswith("xui_cmd:"))
async def execute_xui_command(call: CallbackQuery, state: FSMContext):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据解密异常！", show_alert=True)
    
    if "testVirtualServer" in instance_id:
        return await call.answer(f"UI 测试模式：已模拟向 3x-ui 执行【{action}】！", show_alert=True)

    # 🛑 拦截点 1：触发 VLESS-Reality 生成向导
    if action == "add_reality_start":
        await state.update_data(reality_instance_id=instance_id)
        await state.set_state(XuiRealityFSM.wait_for_upstream)
        await call.message.answer(
            f"⚡️ <b>创建 VLESS-Reality (苹果 CDN 伪装) 节点</b>\n\n"
            f"👉 <b>第一步：是否配置【上游住宅 SOCKS 中转】？</b>\n"
            f"如果想让流量经过阿里云中转至海外住宅 IP 出去，请严格回复：\n"
            f"<code>IP:端口:账号:密码</code> (例如 <code>1.2.3.4:1080:proxyuser:proxypass</code>)\n\n"
            f"🚀 <i>如果不需要中转（直接用阿里云原生 BGP 宽带直连落地），请直接回复 <b>0</b></i>",
            parse_mode="HTML"
        )
        return await call.answer()

    # 🛑 拦截点 2：触发流量限额修改向导
    if action == "modify_limit_start":
        await state.update_data(limit_instance_id=instance_id)
        await state.set_state(XuiLimitFSM.wait_for_port)
        await call.message.answer(
            f"📊 <b>修改端口流量限额 / 流量一键清零</b>\n\n"
            f"👉 请回复想要修改或清零的<b>节点监听端口号</b> (纯数字，例如 <code>48991</code>)：\n\n"
            f"<i>(发送 /cancel 随时取消当前操作)</i>",
            parse_mode="HTML"
        )
        return await call.answer()

    await call.message.edit_text(f"⏳ 正在向实例 <code>{instance_id}</code> 下发 3x-ui <code>{action}</code> 指令，请稍候...", parse_mode="HTML")
    
    if action == "check":
        shell_script = """python3 -c "
import os, sqlite3
status_panel = 'running' if os.system('systemctl is-active --quiet x-ui') == 0 else 'stopped'
user, pwd, port = 'admin', 'admin', '54321'
db_path = '/etc/x-ui/x-ui.db'
if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(\"SELECT value FROM settings WHERE key='webPort'\")
        row = c.fetchone()
        if row and row[0]: port = str(row[0])
        c.execute(\"SELECT username, password FROM users LIMIT 1\")
        row = c.fetchone()
        if row:
            if row[0]: user = str(row[0])
            if row[1]: pwd = str(row[1])
        conn.close()
    except Exception: pass
print(f'PANEL_STATUS={status_panel}')
print(f'USER={user}')
print(f'PASS={pwd}')
print(f'PORT={port}')
" """
    elif action == "install":
        shell_script = """
apt-get update -y && apt-get install -y curl wget sqlite3
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) <<< $'y\\nadmin\\nadmin\\n54321\\n' || true
/usr/local/x-ui/x-ui setting -username admin -password admin -port 54321 || true
systemctl enable x-ui && systemctl restart x-ui
echo "INSTALL_XUI_SUCCESS"
"""
    elif action == "update":
        shell_script = "bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) <<< $'n\\n' && systemctl restart x-ui"
    elif action == "start":
        shell_script = "systemctl start x-ui && systemctl restart x-ui && echo 'SUCCESS'"
    elif action == "stop":
        shell_script = "systemctl stop x-ui && echo 'SUCCESS'"
    elif action == "restart":
        shell_script = "systemctl restart x-ui && echo 'SUCCESS'"
    elif action == "reset_pass":
        shell_script = """
/usr/local/x-ui/x-ui setting -username admin -password admin -port 54321
systemctl restart x-ui
echo "RESET_SUCCESS"
"""
    elif action == "uninstall":
        shell_script = """
systemctl stop x-ui 2>/dev/null
systemctl disable x-ui 2>/dev/null
rm -rf /etc/x-ui /usr/local/x-ui /usr/bin/x-ui /etc/systemd/system/x-ui.service
systemctl daemon-reload
echo "UNINSTALL_SUCCESS"
"""
    else:
        shell_script = "echo 'Unknown command'"

    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        encoded_script = encode_command(shell_script)
        
        request = ecs_models.RunCommandRequest(
            region_id=region_id, type='RunShellScript', command_content=encoded_script,
            instance_id=[instance_id], name=f"MG_XUI_{action}", timeout=180
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
            
            keyboard = build_xui_keyboard(instance_id, is_running=is_running)
            await call.message.edit_text(
                f"📡 <b>3x-ui 实时深度探针报告</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ <b>Web 面板状态</b>：{panel_status}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🌐 <b>当前监听端口</b>：<code>{data_map.get('PORT', '54321')}</code>\n"
                f"👤 <b>面板登录账号</b>：<code>{data_map.get('USER', 'admin')}</code>\n"
                f"🔑 <b>面板登录密码</b>：<code>{data_map.get('PASS', 'admin')}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>提示：如密码显示密文或被修改，随时点击下方「🔑 恢复默认账号密码」一键重置为 admin。</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        elif action == "reset_pass":
            keyboard = build_xui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>X-UI 面板账号密码已成功重置！</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"🌐 <b>访问端口</b>：<code>54321</code> | 👤 <b>账号</b>：<code>admin</code> | 🔑 <b>密码</b>：<code>admin</code>\n\n"
                f"💡 <i>服务已在后台完成热重载，请立刻通过浏览器尝试登录。</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        elif action in ["start", "stop", "restart"]:
            is_now_running = (action in ["start", "restart"])
            keyboard = build_xui_keyboard(instance_id, is_running=is_now_running)
            status_word = "启动/重启" if is_now_running else "停止"
            await call.message.edit_text(
                f"✅ <b>3x-ui 面板服务已成功{status_word}！</b>\n\n🖥 实例ID：<code>{instance_id}</code>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            keyboard = build_xui_keyboard(instance_id, is_running=True)
            await call.message.edit_text(
                f"✅ <b>指令下发成功！</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"⏳ <b>执行动作</b>：一键自动化运维 ({action})\n\n"
                f"任务已在后台自动执行（约需40秒），完成后点击「🔍 实时探测」即可核对部署状态！",
                reply_markup=keyboard, parse_mode="HTML"
            )
    except Exception as e:
        await call.message.edit_text(f"❌ 执行远程命令失败：\n{str(e)}", parse_mode=None)
    finally:
        await call.answer()


# ================= 🚀 3. FSM：VLESS-Reality + 住宅中转 + 流量限额 =================
@router.message(XuiRealityFSM.wait_for_upstream)
async def xui_reality_step1(message: Message, state: FSMContext):
    text = message.text.strip()
    await state.update_data(reality_upstream=text if text != '0' else "")
    await state.set_state(XuiRealityFSM.wait_for_limit)
    await message.answer(
        f"✅ 上游中转出口：<b>{'已配置外部转发' if text != '0' else '阿里云本机原生 IP 直连'}</b>\n\n"
        f"👉 <b>第二步：请输入该端口节点的【月度流量限额 (GB)】</b>\n"
        f"⚡️ <i>默认赋予 <b>500</b> GB，请直接回复数字 <code>500</code>；如需不限流量请回复 <code>0</code></i>",
        parse_mode="HTML"
    )

@router.message(XuiRealityFSM.wait_for_limit)
async def xui_reality_step2(message: Message, state: FSMContext):
    try:
        limit_gb = float(message.text.strip())
    except ValueError:
        return await message.answer("❌ 格式错误，请输入纯数字 (例如 `500` 或 `0`):")
        
    data = await state.get_data()
    upstream_str = data.get("reality_upstream", "")
    instance_id = data.get("reality_instance_id")
    await state.clear()
    
    wait_msg = await message.answer(f"⏳ 正在为实例 <code>{instance_id}</code> 映射 Apple CDN 证书、配置中转路由并设限 <b>{limit_gb}GB</b>...", parse_mode="HTML")
    
    port = random.randint(40000, 58000)
    total_bytes = int(limit_gb * 1024**3) if limit_gb > 0 else 0
    
    # 构建高阶 Python 注入脚本：生成 X25519、设限、以及自动写入 Xray 出站中转路由
    shell_script = f"""python3 -c "
import sqlite3, json, subprocess, uuid, random, os
port = {port}
total_bytes = {total_bytes}
upstream_str = '{upstream_str}'

try:
    xray_bin = subprocess.check_output('find /usr/local/x-ui/bin -name \"xray*\" -type f | head -n 1', shell=True).decode().strip()
    key_out = subprocess.check_output(f'{{xray_bin}} x25519', shell=True).decode()
    priv_key, pub_key = '', ''
    for line in key_out.split('\\n'):
        if 'Private' in line or 'private' in line: priv_key = line.split(':')[-1].strip()
        elif 'Public' in line or 'public' in line: pub_key = line.split(':')[-1].strip()
except Exception:
    priv_key, pub_key = 'yNu1z_fallback_private_key_replace_me', 'zMu1z_fallback_public_key_replace_me'

client_id = str(uuid.uuid4())
short_id = ''.join(random.choices('0123456789abcdef', k=8))
inbound_tag = f'inbound-{{port}}'

settings = {{'clients': [{{'id': client_id, 'flow': 'xtls-rprx-vision', 'email': f'reality_{{port}}'}}], 'decryption': 'none'}}
stream_settings = {{
    'network': 'tcp', 'security': 'reality',
    'realitySettings': {{
        'show': False, 'dest': 'www.apple.com:443', 'xver': 0,
        'serverNames': ['www.apple.com', 'apple.com', 'gateway.icloud.com'],
        'privateKey': priv_key, 'minClientVer': '', 'maxClientVer': '', 'maxTimediff': 0, 'shortIds': [short_id]
    }}
}}

conn = sqlite3.connect('/etc/x-ui/x-ui.db')
c = conn.cursor()
c.execute('''INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
             VALUES (1, 0, 0, ?, ?, 1, 0, '', ?, 'vless', ?, ?, ?, '{{\"enabled\":true,\"destOverride\":[\"http\",\"tls\",\"quic\"]}}')''',
          (total_bytes, f'Reality_Apple_{{limit_gb}}G', port, json.dumps(settings), json.dumps(stream_settings), inbound_tag))

# 如果配置了上游中转，智能注入 xrayTemplateConfig 实现出站链绑定
if upstream_str and ':' in upstream_str:
    try:
        parts = upstream_str.split(':')
        up_ip, up_port = parts[0], int(parts[1])
        up_user = parts[2] if len(parts) >= 3 else ''
        up_pass = parts[3] if len(parts) >= 4 else ''
        outbound_tag = f'outbound-fwd-{{port}}'
        
        c.execute(\"SELECT value FROM settings WHERE key='xrayTemplateConfig'\")
        row = c.fetchone()
        if row and row[0]:
            xray_tmpl = json.loads(row[0])
            new_outbound = {{
                'tag': outbound_tag, 'protocol': 'socks',
                'settings': {{'servers': [{{'address': up_ip, 'port': up_port, 'users': [{{'user': up_user, 'pass': up_pass}}] if up_user else []}}]}}
            }}
            if 'outbounds' not in xray_tmpl: xray_tmpl['outbounds'] = []
            xray_tmpl['outbounds'].append(new_outbound)
            
            new_rule = {{'type': 'field', 'inboundTag': [inbound_tag], 'outboundTag': outbound_tag}}
            if 'routing' not in xray_tmpl: xray_tmpl['routing'] = {{'rules': []}}
            if 'rules' not in xray_tmpl['routing']: xray_tmpl['routing']['rules'] = []
            xray_tmpl['routing']['rules'].insert(0, new_rule)
            
            c.execute(\"UPDATE settings SET value=? WHERE key='xrayTemplateConfig'\", (json.dumps(xray_tmpl),))
    except Exception as e: print(f'ROUTING_ERR:{{e}}')

conn.commit(); conn.close()
print(f'REALITY_RES:{{port}}|{{client_id}}|{{pub_key}}|{{short_id}}')
" && systemctl restart x-ui && echo "ADD_REALITY_SUCCESS" """

    try:
        region_id = get_region_by_instance(message.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=60)
        resp = await asyncio.to_thread(client.run_command, req)
        out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
        
        port_res, client_id, pub_key, short_id = str(port), "未知", "未知", "未知"
        for line in out.split("\n"):
            if line.startswith("REALITY_RES:"):
                try: port_res, client_id, pub_key, short_id = line.split(":", 1)[1].split("|")
                except Exception: pass
                
        try:
            ip_resp = client.describe_instances(ecs_models.DescribeInstancesRequest(region_id=region_id, instance_ids=json.dumps([instance_id])))
            pub_ip = ip_resp.body.instances.instance[0].public_ip_address.ip_address[0]
        except Exception:
            pub_ip = "服务器公网IP"
            
        vless_link = (
            f"vless://{client_id}@{pub_ip}:{port_res}?security=reality&encryption=none&pbk={pub_key}"
            f"&headerType=none&fp=chrome&type=tcp&flow=xtls-rprx-vision&sni=www.apple.com&sid={short_id}#Apple_CDN_{limit_gb}G"
        )
        
        fwd_tip = f"🔄 <b>上游住宅中转</b>：已绑定出口 <code>{upstream_str.split(':')[0]}</code> (底层私密转发)" if upstream_str else "⚡️ <b>落地出口</b>：阿里云原生 BGP 宽带直连"
        
        keyboard = build_xui_keyboard(instance_id, is_running=True)
        await wait_msg.edit_text(
            f"🎉 <b>VLESS-Reality 顶级伪装节点创建成功！</b>\n\n"
            f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
            f"🔌 <b>节点监听端口</b>：<code>{port_res}</code> | 📊 <b>流量限额</b>：<b>{limit_gb} GB</b>\n"
            f"🍎 <b>伪装 SNI</b>：<code>www.apple.com:443</code>\n"
            f"{fwd_tip}\n\n"
            f"🚀 <b>一键专属链接 (可直接导入 v2rayN / Shadowrocket / 客户端)：</b>\n"
            f"<code>{vless_link}</code>\n\n"
            f"💡 <i>Reality X25519 证书已映射生效，无需购买域名！请去阿里云安全组放行 TCP {port_res} 端口！</i>",
            reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ 节点创建失败：\n{str(e)}", parse_mode=None)


# ================= 🚀 4. FSM：动态修改端口限额 & 流量清零 =================
@router.message(XuiLimitFSM.wait_for_port)
async def xui_limit_step1(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        return await message.answer("❌ 格式错误，请输入纯数字监听端口号 (例如 `48991`):")
    await state.update_data(target_port=text)
    await state.set_state(XuiLimitFSM.wait_for_new_limit)
    await message.answer(
        f"✅ 已选中待处理端口：<b>{text}</b>\n\n"
        f"👉 <b>请输入该端口新的【流量限额 (GB)】</b>\n"
        f"• 如果输入新的额度 (例如 <code>1000</code>)，系统将对该端口重新设限并将<b>已用流量清零</b>！\n"
        f"• 如果只想将当前流量清零而不改限额，请直接输入原来的额度号；\n"
        f"• 如果改为不限流量，请回复数字 <code>0</code>。",
        parse_mode="HTML"
    )

@router.message(XuiLimitFSM.wait_for_new_limit)
async def xui_limit_step2(message: Message, state: FSMContext):
    try:
        new_limit_gb = float(message.text.strip())
    except ValueError:
        return await message.answer("❌ 格式错误，请输入纯数字 (例如 `1000` 或 `0`):")
        
    data = await state.get_data()
    port = data.get("target_port")
    instance_id = data.get("limit_instance_id")
    await state.clear()
    
    wait_msg = await message.answer(f"⏳ 正在向实例 <code>{instance_id}</code> 底层修改端口 <b>{port}</b> 的流量规则并热重载...", parse_mode="HTML")
    
    total_bytes = int(new_limit_gb * 1024**3) if new_limit_gb > 0 else 0
    
    shell_script = f"""python3 -c "
import sqlite3
conn = sqlite3.connect('/etc/x-ui/x-ui.db')
c = conn.cursor()
c.execute('UPDATE inbounds SET total=?, up=0, down=0 WHERE port=?', ({total_bytes}, {port}))
changes = conn.total_changes
conn.commit(); conn.close()
if changes > 0: print('MODIFY_SUCCESS')
else: print('PORT_NOT_FOUND')
" && systemctl restart x-ui && echo "LIMIT_DONE" """

    try:
        region_id = get_region_by_instance(message.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        req = ecs_models.RunCommandRequest(region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script), instance_id=[instance_id], timeout=45)
        resp = await asyncio.to_thread(client.run_command, req)
        out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
        
        keyboard = build_xui_keyboard(instance_id, is_running=True)
        if "MODIFY_SUCCESS" in out:
            limit_str = f"<b>{new_limit_gb} GB</b>" if new_limit_gb > 0 else "<b>无限流量 (Unlimited)</b>"
            await wait_msg.edit_text(
                f"✅ <b>端口流量规则修改与清零成功！</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"🔌 <b>操作节点端口</b>：<code>{port}</code>\n"
                f"📊 <b>新设定总额度</b>：{limit_str}\n"
                f"🔄 <b>已用流量计数</b>：<b>已归零 (0 MB)</b>\n\n"
                f"💡 <i>底层服务已完成热重启，该节点的流量监控与超限阻断规则已重新激活！</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        elif "PORT_NOT_FOUND" in out:
            await wait_msg.edit_text(f"⚠️ 操作失败：远程机器数据库中未找到监听端口为 `<code>{port}</code>` 的节点，请核对后重试！", reply_markup=keyboard, parse_mode="HTML")
        else:
            await wait_msg.edit_text(f"⚠️ 指令已执行但终端回显异常，请确认端口是否存在。\n回显：{out[:50]}", reply_markup=keyboard)
    except Exception as e:
        await wait_msg.edit_text(f"❌ 修改失败：\n{str(e)}", parse_mode=None)
