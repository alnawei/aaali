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
    """⭐ 核心探针函数：同步轮询阿里云助手执行结果，解码获取远程机器真实终端输出"""
    req = ecs_models.DescribeInvocationResultsRequest(
        region_id=region_id,
        invoke_id=invoke_id
    )
    # 最多轮询 5 次，每次间隔 2 秒 (共等待 10 秒)
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
    return "⏳ 查询超时：脚本可能仍在远程后台运行，请稍候点击探测按钮查看。"

def reboot_instance_sync(client: EcsClient, instance_id: str):
    """同步下发阿里云 ECS 物理重启指令"""
    req = ecs_models.RebootInstanceRequest(instance_id=instance_id, force_stop=False)
    return client.reboot_instance(req)


# ================= 🚀 1. 渲染 BBR 专属控制面板 =================
@router.callback_query(F.data.startswith("run_sh:bbr:"))
async def show_bbr_panel(call: CallbackQuery):
    try:
        _, script_id, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("回调参数格式异常！", show_alert=True)
    
    # 重新构建层次分明、极具操作感的 BBR 控制中心键盘
    builder = [
        # ⭐ 独家杀手锏：一键探测真实状态
        [InlineKeyboardButton(text="🔍 实时探测当前 Linux 内核 BBR 状态", callback_data=f"bbr_cmd:check:{instance_id}")],
        [
            InlineKeyboardButton(text="🟢 开启 BBR+FQ (推荐)", callback_data=f"bbr_cmd:fq:{instance_id}"),
            InlineKeyboardButton(text="🛑 停用加速 (恢复Cubic)", callback_data=f"bbr_cmd:disable:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🚀 BBR+FQ_PIE (新内核)", callback_data=f"bbr_cmd:fq_pie:{instance_id}"),
            InlineKeyboardButton(text="🚀 BBR+CAKE (抗丢包)", callback_data=f"bbr_cmd:cake:{instance_id}")
        ],
        [
            InlineKeyboardButton(text="🔥 BBRplus 魔改参数", callback_data=f"bbr_cmd:bbrplus:{instance_id}"),
            InlineKeyboardButton(text="🔄 重启服务器 (配置生效)", callback_data=f"bbr_cmd:reboot:{instance_id}")
        ],
        [InlineKeyboardButton(text="🔙 返回服务器列表", callback_data=f"srv_sel:{instance_id}")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=builder)
    
    text = (
        f"⚡️ **BBR 网络吞吐与拥塞控制中心**\n\n"
        f"🖥 **当前操作实例**：`{instance_id}`\n\n"
        f"💡 **参数说明与运维指南**：\n"
        f"• **标准 BBR+FQ**：Debian10+/Ubuntu20+ 默认已集成，适合 90% 的跨境场景，平稳抗丢包。\n"
        f"• **FQ_PIE / CAKE**：针对高并发及严重丢包链路优化的现代 AQM 队列。\n"
        f"• **魔改与生效**：修改内核参数或升级 BBRplus 后，建议点击底部 **[🔄 重启服务器]** 释放队列配置。\n\n"
        f"👇 **请点击最上方 [🔍 实时探测] 获取物理机当前运行状态，或直接下发加速配置：**"
    )
    
    await call.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await call.answer()


# ================= 🚀 2. 接收 BBR 指令、探测探针与物理机联动 =================
@router.callback_query(F.data.startswith("bbr_cmd:"))
async def execute_bbr_command(call: CallbackQuery):
    try:
        _, action, instance_id = call.data.split(":")
    except ValueError:
        return await call.answer("底层数据包解密异常！", show_alert=True)
    
    # UI 测试模式安全拦截
    if "testVirtualServer" in instance_id:
        await call.answer(f"UI 测试模式：已模拟执行【{action}】操作！", show_alert=True)
        return

    # ---------------- 附加分支 A：一键极速重启 ECS ----------------
    if action == "reboot":
        await call.message.edit_text(f"⏳ 正在下发指令，准备将实例 `{instance_id}` 执行物理重启...", parse_mode="Markdown")
        try:
            region_id = get_region_by_instance(call.from_user.id, instance_id)
            client = get_ecs_client(region_id)
            await asyncio.to_thread(reboot_instance_sync, client, instance_id)
            
            back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回 BBR 面板", callback_data=f"run_sh:bbr:{instance_id}")]])
            await call.message.edit_text(f"🔄 **系统重启指令已下发！**\n\n🖥 实例: `{instance_id}`\n⏳ 服务器预计需要在 30~60 秒后恢复 SSH 连接，请稍后再次探测状态。", reply_markup=back_kb, parse_mode="Markdown")
        except Exception as e:
            await call.message.edit_text(f"❌ 重启请求失败：\n{str(e)}", parse_mode=None)
        finally:
            return await call.answer()

    # ---------------- 附加分支 B：实时探测物理机状态 (探针模式) ----------------
    if action == "check":
        await call.message.edit_text(f"📡 正在建立阿里云助手内网特权管道，实时探测 `{instance_id}` 的底层内核拥塞控制状态，请稍候 (约需4秒)...", parse_mode="Markdown")
        # 探测脚本：同时查询拥塞控制算法和默认排队规则
        check_script = "echo \"===ALGO===\" && sysctl net.ipv4.tcp_congestion_control && echo \"===QDISC===\" && sysctl net.core.default_qdisc"
    else:
        await call.message.edit_text(f"⏳ 正在向实例 `{instance_id}` 下发 BBR `{action}` 配置指令，请稍候...", parse_mode="Markdown")
        
        # ⭐ 核心升级：强幂等性脚本（Idempotent Clean Up）
        # 在追加任何新参数前，先用 sed 删掉系统配置文件里历史所有的队列和拥塞算法语句，防止反复点击造成上百行配置重复配置！
        clean_old_sysctl = (
            "sed -i '/net.core.default_qdisc/d' /etc/sysctl.conf && "
            "sed -i '/net.ipv4.tcp_congestion_control/d' /etc/sysctl.conf"
        )
        
        if action == "install" or action == "fq":
            shell_script = f"{clean_old_sysctl} && echo 'net.core.default_qdisc=fq' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && sysctl -p"
        elif action == "disable":
            shell_script = f"{clean_old_sysctl} && echo 'net.core.default_qdisc=pfifo_fast' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=cubic' >> /etc/sysctl.conf && sysctl -p"
        elif action == "fq_pie":
            shell_script = f"{clean_old_sysctl} && echo 'net.core.default_qdisc=fq_pie' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && sysctl -p"
        elif action == "cake":
            shell_script = f"{clean_old_sysctl} && echo 'net.core.default_qdisc=cake' >> /etc/sysctl.conf && echo 'net.ipv4.tcp_congestion_control=bbr' >> /etc/sysctl.conf && sysctl -p"
        elif action == "bbrplus":
            # ⭐ 补全 BBRplus 真实逻辑：如果已安装 bbrplus 内核则立即生效；同时附上社区一键装内核备用下载指令
            shell_script = (
                f"{clean_old_sysctl} && echo 'net.core.default_qdisc=fq' >> /etc/sysctl.conf && "
                f"echo 'net.ipv4.tcp_congestion_control=bbrplus' >> /etc/sysctl.conf && sysctl -p || "
                f"echo '提示：当前内核不支持 bbrplus，请先通过 SSH 运行社区一键脚本安装自定义内核：wget -N --no-check-certificate https://raw.githubusercontent.com/ylx2016/Linux-NetSpeed/master/tcp.sh && bash tcp.sh'"
            )
        else:
            shell_script = "echo 'Unknown BBR command'"

    # ---------------- 执行阿里云异步 RunCommand 与结果捕获 ----------------
    try:
        region_id = get_region_by_instance(call.from_user.id, instance_id)
        client = get_ecs_client(region_id)
        
        # 决定这次发送的是探测指令还是修改指令
        final_script = check_script if action == "check" else shell_script
        encoded_script = encode_command(final_script)
        
        request = ecs_models.RunCommandRequest(
            region_id=region_id,
            type='RunShellScript',
            command_content=encoded_script,
            instance_id=[instance_id],
            name=f"MG_Bot_BBR_{action}",
            timeout=120
        )
        
        # 1. 在异步线程池里下发执行指令
        response = await asyncio.to_thread(client.run_command, request)
        invoke_id = response.body.invoke_id
        
        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 返回 BBR 控制面板", callback_data=f"run_sh:bbr:{instance_id}")]]
        )
        
        # 2. 如果是【实时探测】操作，我们在后台轮询回显，给管理员展示真正的内核物理数据！
        if action == "check":
            real_output = await asyncio.to_thread(fetch_command_output_sync, client, region_id, invoke_id)
            
            # 美化解析终端输出
            algo_res = "未知"
            qdisc_res = "未知"
            for line in real_output.split("\n"):
                if "tcp_congestion_control" in line:
                    algo_res = line.split("=")[-1].strip()
                elif "default_qdisc" in line:
                    qdisc_res = line.split("=")[-1].strip()
                    
            status_emoji = "🟢" if "bbr" in algo_res.lower() else "🔴"
            
            await call.message.edit_text(
                f"📡 **服务器内网探针报告**\n\n"
                f"🖥 **物理机实例**：`{instance_id}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡️ **拥塞控制算法**：{status_emoji} `{algo_res}`\n"
                f"🚦 **默认队列规则**：`{qdisc_res}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 *数据来源：通过云助手实时执行 `sysctl` 从底层 Linux 内核读取，真实准确。*",
                reply_markup=back_keyboard,
                parse_mode="Markdown"
            )
        else:
            # 修改参数指令下发完成后的反馈
            await call.message.edit_text(
                f"✅ **BBR 加速参数下发成功！**\n\n"
                f"🖥 **实例ID**: `{instance_id}`\n"
                f"⚡️ **动作项**: `{action}`\n\n"
                f"系统已在后台自动清洗旧参数并重载 `/etc/sysctl.conf`。\n"
                f"👉 *建议立刻点击下方【返回 BBR 控制面板】，使用 [🔍 实时探测] 验证新参数是否已成功生效。*",
                reply_markup=back_keyboard,
                parse_mode="Markdown"
            )
    except Exception as e:
        # 防红屏保护：发生异常时去除 Markdown 解析，直接抛出堆栈文本
        await call.message.edit_text(
            f"❌ 执行失败或网络通信发生错误：\n{str(e)}",
            parse_mode=None
        )
    finally:
        await call.answer()
