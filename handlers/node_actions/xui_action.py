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

def build_xui_keyboard(instance_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    """⭐ 支持根据底层服务运行状态，动态切换「启动/停止」按键样式"""
    if is_running:
        toggle_btn = InlineKeyboardButton(text="🛑 停止面板后台服务", callback_data=f"xui_cmd:stop:{instance_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="🟢 启动 / 重启面板服务", callback_data=f"xui_cmd:start:{instance_id}")

    builder = [
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
        f"⚡️ <b>3x-ui 代理面板自动交付中心</b>\n\n"
        f"🖥 <b>操作物理机</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>默认固定端口</b>：<code>54321</code>\n"
        f"👤 <b>默认登录账号</b>：<code>admin</code>\n"
        f"🔑 <b>默认登录密码</b>：<code>admin</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>自动化交付说明</b>：\n"
        f"• <b>一键部署</b>：自动执行 3x-ui 官方脚本，并<b>全自动绕过互动问答</b>，直接将端口及账号密码强制锁定为上述默认参数！\n"
        f"• <b>运维控制</b>：支持随时在 Telegram 中控内实现面板服务热重启、密码一键重置及完全卸载。\n\n"
        f"👇 <b>请选择要向该远程机器下发的 X-UI 管理指令：</b>"
    )
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()


# ================= 🚀 2. 接收 X-UI 管理指令并执行远程调度 =================
@router.callback_query(F.data.startswith("xui_cmd:"))
async def execute_xui_command(call: CallbackQuery):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据解密异常！", show_alert=True)
    
    if "testVirtualServer" in instance_id:
        return await call.answer(f"UI 测试模式：已模拟向 3x-ui 执行【{action}】！", show_alert=True)

    await call.message.edit_text(f"⏳ 正在向实例 <code>{instance_id}</code> 下发 3x-ui <code>{action}</code> 指令，请稍候...", parse_mode="HTML")
    
    if action == "check":
        # ⭐ 深度探针：读取 systemd 状态 + 查询 sqlite 数据库中的端口与用户凭证
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
        # ⭐ 核心杀手锏：利用管道重定向回答互动提问，再追加 CLI 命令强制覆写锁定 admin/admin/54321
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
        # 使用 3x-ui 官方 CLI 指令瞬间重置账号密码为默认，并重启面板生效
        shell_script = """
/usr/local/x-ui/x-ui setting -username admin -password admin -port 54321
systemctl restart x-ui
echo "RESET_SUCCESS"
"""
    elif action == "uninstall":
        # 暴力安全清理，避免官方卸载脚本再次等待用户确认
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
            instance_id=[instance_id], name=f"MG_UI_XUI_{action}", timeout=180
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
                f"🌐 <b>访问端口</b>：<code>54321</code>\n"
                f"👤 <b>登录账号</b>：<code>admin</code>\n"
                f"🔑 <b>登录密码</b>：<code>admin</code>\n\n"
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
                f"系统已在服务器后台开始执行部署。任务大约需要 30~50 秒完结，稍后点击最上方的「🔍 实时探测」即可获取部署结果！",
                reply_markup=keyboard, parse_mode="HTML"
            )
    except Exception as e:
        await call.message.edit_text(f"❌ 执行远程管理命令失败：\n{str(e)}", parse_mode=None)
    finally:
        await call.answer()
