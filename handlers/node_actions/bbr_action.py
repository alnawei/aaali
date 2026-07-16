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

def build_bbr_keyboard(instance_id: str) -> InlineKeyboardMarkup:
    builder = [
        [InlineKeyboardButton(text="🔍 实时探测当前 Linux 内核 BBR 状态", callback_data=f"bbr_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 开启 BBR+FQ (推荐)", callback_data=f"bbr_cmd:bbr_fq:{instance_id}"),
            InlineKeyboardButton(text="🛑 停用加速 (恢复Cubic)", callback_data=f"bbr_cmd:stop:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🚀 BBR+FQ_PIE (新内核)", callback_data=f"bbr_cmd:bbr_fq_pie:{instance_id}"),
            InlineKeyboardButton(text="🚀 BBR+CAKE (抗丢包)", callback_data=f"bbr_cmd:bbr_cake:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔥 BBRplus 魔改参数", callback_data=f"bbr_cmd:bbr_plus:{instance_id}"),
            InlineKeyboardButton(text="🔄 重启服务器 (配置生效)", callback_data=f"bbr_cmd:reboot:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=builder)

# ================= 1. 渲染 BBR 控制面板 =================
@router.callback_query(F.data.startswith("run_sh:bbr:"))
async def show_bbr_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数异常！", show_alert=True)
    
    keyboard = build_bbr_keyboard(instance_id)
    text = (
        f"⚡️ <b>BBR 网络吞吐与拥塞控制中心</b>\n\n"
        f"🖥 <b>当前操作实例</b>：<code>{instance_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>参数说明与运维指南</b>：\n"
        f"• <b>标准 BBR+FQ</b>：适合 90% 的跨境 Linux 场景，能极大压榨带宽、平稳抗丢包。\n"
        f"• <b>FQ_PIE / CAKE</b>：针对高并发及严重丢包链路优化的现代 AQM 队列算法。\n"
        f"• <b>配置生效</b>：内核参数修改后，建议点击底部 <b>[🔄 重启服务器]</b> 彻底释放队列。\n\n"
        f"👇 <b>请点击最上方 [🔍 实时探测] 检查当前拥塞控制算法，或下发加速配置：</b>"
    )
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await call.answer()

# ================= 2. 执行 BBR 底层指令 =================
@router.callback_query(F.data.startswith("bbr_cmd:"))
async def execute_bbr_command(call: CallbackQuery):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("解密异常！", show_alert=True)
        
    if "testVirtualServer" in instance_id:
        return await call.answer(f"测试模式：模拟执行【{action}】！", show_alert=True)

    await call.message.edit_text(f"⏳ 正在向实例 <code>{instance_id}</code> 下发 BBR <code>{action}</code> 指令...", parse_mode="HTML")
    
    if action == "check":
        shell_script = "sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc && uname -r"
    elif action == "bbr_fq":
        shell_script = """
sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf
sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf
echo "net.core.default_qdisc = fq" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control = bbr" >> /etc/sysctl.conf
sysctl -p
echo "BBR_FQ_SUCCESS"
"""
    elif action == "bbr_fq_pie":
        shell_script = """
sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf
sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf
echo "net.core.default_qdisc = fq_pie" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control = bbr" >> /etc/sysctl.conf
sysctl -p
echo "BBR_FQ_PIE_SUCCESS"
"""
    elif action == "bbr_cake":
        shell_script = """
sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf
sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf
echo "net.core.default_qdisc = cake" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control = bbr" >> /etc/sysctl.conf
sysctl -p
echo "BBR_CAKE_SUCCESS"
"""
    elif action == "stop":
        shell_script = """
sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf
sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf
echo "net.core.default_qdisc = pfifo_fast" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control = cubic" >> /etc/sysctl.conf
sysctl -p
echo "STOP_SUCCESS"
"""
    elif action == "reboot":
        shell_script = "reboot && echo 'REBOOTING'"
    else:
        shell_script = "echo 'Unknown Command'"

    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        req = ecs_models.RunCommandRequest(
            region_id=region_id, type='RunShellScript', command_content=encode_command(shell_script),
            instance_id=[instance_id], name=f"MG_BBR_{action}", timeout=60
        )
        resp = await asyncio.to_thread(client.run_command, req)
        
        if action == "reboot":
            await call.message.edit_text(f"🔄 <b>服务器正在重启！</b>\n\n实例 <code>{instance_id}</code> 将在 30 秒后重载内核并重新连接公网。", parse_mode="HTML")
            return

        out = await asyncio.to_thread(fetch_command_output_sync, client, region_id, resp.body.invoke_id)
        keyboard = build_bbr_keyboard(instance_id)
        
        if action == "check":
            await call.message.edit_text(
                f"📡 <b>Linux BBR 内核拥塞状态探测报告</b>\n\n"
                f"🖥 <b>物理机实例</b>：<code>{instance_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"终端内核参数回显：\n<code>{out}</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>提示：如果看到 <code>tcp_congestion_control = bbr</code> 即代表加速已完全启动！</i>",
                reply_markup=keyboard, parse_mode="HTML"
            )
        else:
            await call.message.edit_text(f"✅ <b>BBR 加速策略配置成功！</b>\n\n🖥 实例ID：<code>{instance_id}</code>\n内核参数已实时载入生效。你可以点击最上方「🔍 实时探测」验证当前算法！", reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        await call.message.edit_text(f"❌ 指令执行失败：\n{str(e)}", parse_mode=None)
    finally:
        await call.answer()
