import base64
import asyncio  # 🛠️ 修正：导入异步框架核心工具库
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models

import config
from db import get_active_servers

router = Router()

# ================= 🛠️ 工具函数 =================
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
    """辅助函数：根据 instance_id 查出所属地域"""
    try:
        servers = get_active_servers(user_id)
        for srv in servers:
            if srv["instance_id"] == instance_id:
                return srv["region"]
    except Exception:
        pass
    return "cn-hongkong"  # 默认 fallback


# ================= 🚀 1. 渲染 BBR 专属控制面板 =================
@router.callback_query(F.data.startswith("run_sh:bbr:"))
async def show_bbr_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    # 构建 BBR 的专属悬浮键盘
    builder = [
        [
            InlineKeyboardButton(text="🛠️ 安装/开启 BBR", callback_data=f"bbr_cmd:install:{instance_id}"),
            InlineKeyboardButton(text="🛑 停用 BBR", callback_data=f"bbr_cmd:disable:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="BBR+FQ加速", callback_data=f"bbr_cmd:fq:{instance_id}"),
            InlineKeyboardButton(text="BBR+FQ_PIE加速", callback_data=f"bbr_cmd:fq_pie:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="BBR+CAKE加速", callback_data=f"bbr_cmd:cake:{instance_id}"),
            InlineKeyboardButton(text="BBRplus+FQ版加速", callback_data=f"bbr_cmd:bbrplus:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回上一级", callback_data=f"srv_sel:{instance_id}")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=builder)
    
    text = (
        f"⚡️ **BBR 加速配置中心**\n\n"
        f"当前操作实例：`{instance_id}`\n\n"
        f"**配置状态面板：**\n"
        f"BBR：🔴 没启用\n"
        f"BBR：🟢 BBR+FQ加速\n"
        f"🔴 BBR+FQ_PIE加速\n"
        f"🔴 BBR+CAKE加速\n"
        f"🔴 BBRplus+FQ版加速\n\n"
        f"👇 请点击下方按钮下发相应的加速指令："
    )
    
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()


# ================= 🚀 2. 接收具体 BBR 指令并调用阿里云 API =================
@router.callback_query(F.data.startswith("bbr_cmd:"))
async def execute_bbr_command(call: CallbackQuery):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据包解密异常！", show_alert=True)
    
    # UI 测试模式拦截
    if "testVirtualServer" in instance_id:
        await call.answer(f"UI 测试模式：已模拟下发【{action}】指令！", show_alert=True)
        return

    await call.message.edit_text(f"⏳ 正在向实例 `{instance_id}` 下发 BBR `{action}` 指令，请稍候...", parse_mode="Markdown")
    
    if action == "install" or action == "fq":
        shell_script = (
            "echo 'net.core.default_qdisc=fq' >> /etc/sysctl.conf && "
            "echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && "
            "sysctl -p"
        )
    elif action == "disable":
        shell_script = (
            "sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf && "
            "sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf && "
            "echo 'net.core.default_qdisc=pfifo_fast' >> /etc/sysctl.conf && "
            "echo 'net.ipv4.tcp_congestion_control=cubic' >> /etc/sysctl.conf && "
            "sysctl -p"
        )
    elif action == "fq_pie":
        shell_script = "echo 'net.core.default_qdisc=fq_pie' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && sysctl -p"
    elif action == "cake":
        shell_script = "echo 'net.core.default_qdisc=cake' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && sysctl -p"
    elif action == "bbrplus":
        shell_script = "echo 'BBRplus 部署脚本待接入...'"
    else:
        shell_script = "echo 'Unknown BBR command'"

    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        encoded_script = encode_command(shell_script)
        client = get_ecs_client(region_id)
        
        request = ecs_models.RunCommandRequest(
            region_id=region_id,
            type='RunShellScript',
            command_content=encoded_script,
            instance_id=[instance_id],
            name=f"Bot_BBR_{action}",
            timeout=120
        )
        
        # 🛠️ 修正核心：强制使用 asyncio.to_thread 调用同步 SDK 的 run_command，避免原框架_async引发的协程死锁！
        response = await asyncio.to_thread(client.run_command, request)
        invoke_id = response.body.invoke_id
        
        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 返回上一级", callback_data=f"srv_sel:{instance_id}")]]
        )
        
        await call.message.edit_text(
            f"✅ **BBR 指令下发成功！**\n\n"
            f"🖥 实例ID: `{instance_id}`\n"
            f"⚡️ 动作项: `{action}`\n\n"
            f"系统正在后台修改内核参数，加速即将生效。",
            reply_markup=back_keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        # 🛠️ 修正：当发生网络或阿里云API报错时，去掉 Markdown 解析模式，直接以普通文本输出错误堆栈，防止因带特殊字符引发 TG BadRequest
        await call.message.edit_text(
            f"❌ 指令下发失败\n\n错误原因：\n{str(e)}",
            parse_mode=None
        )
    finally:
        await call.answer()
