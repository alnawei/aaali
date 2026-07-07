import os
import asyncio
import smtplib
import random
import time
import json
from email.mime.text import MIMEText
from email.header import Header

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# 阿里云 SDK 依赖
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526 import models as ecs_models

# 引入全局配置
import config

# 实例化当前模块的路由器
router = Router()

# 定义 FSM 状态机
class ServerManagement(StatesGroup):
    waiting_for_code = State()
    waiting_for_region = State()

# ================= 1. 底层 API 与发信函数 =================

def get_template_id(region_id: str) -> str:
    """根据 region_id 动态读取环境变量中的启动模板 ID"""
    env_var_name = f"TPL_{region_id.replace('-', '_').upper()}"
    return os.getenv(env_var_name, "").strip()

def send_email_sync(code: str) -> bool:
    """同步的 SMTP 发信逻辑，将在后台线程中运行防阻塞"""
    try:
        msg = MIMEText(f"【MG 控制台】\n\n您的开服验证码是：{code}\n请在5分钟内返回 TG 进行验证。", 'plain', 'utf-8')
        msg['Subject'] = Header("MG 控制台 V2.0 - 开机验证码", 'utf-8')
        msg['From'] = config.SENDER_EMAIL
        msg['To'] = config.RECIPIENT

        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.SENDER_EMAIL, config.SENDER_PASSWORD)
            server.sendmail(config.SENDER_EMAIL, [config.RECIPIENT], msg.as_string())
        return True
    except Exception as e:
        print(f"SMTP 发信失败: {e}")
        return False

def create_ecs_instance_sync(region_id: str, template_id: str) -> dict:
    """同步调用阿里云 API 创建实例并轮询获取 IP (后台线程执行)"""
    try:
        # 初始化客户端
        ali_config = open_api_models.Config(
            access_key_id=config.ALIYUN_ACCESS_KEY_ID,
            access_key_secret=config.ALIYUN_ACCESS_KEY_SECRET,
            endpoint=f'ecs.{region_id}.aliyuncs.com'
        )
        client = EcsClient(ali_config)

        # 发起 RunInstances 创建请求
        run_request = ecs_models.RunInstancesRequest(
            region_id=region_id,
            launch_template_id=template_id,
            amount=1
        )
        run_response = client.run_instances(run_request)
        instance_id = run_response.body.instance_id_sets.instance_id_set[0]
        
        # 轮询 DescribeInstances 等待机器 Running 并获取公网 IP
        describe_request = ecs_models.DescribeInstancesRequest(
            region_id=region_id,
            instance_ids=json.dumps([instance_id])
        )
        
        # 最多轮询 15 次，每次间隔 5 秒 (约 75 秒超时)
        for _ in range(15):
            time.sleep(5)
            desc_resp = client.describe_instances(describe_request)
            instances = desc_resp.body.instances.instance
            if not instances:
                continue
                
            instance = instances[0]
            status = instance.status
            
            if status == "Running":
                # 提取公网 IP
                public_ip = "无公网IP"
                if instance.public_ip_address and instance.public_ip_address.ip_address:
                    public_ip = instance.public_ip_address.ip_address[0]
                
                return {"success": True, "instance_id": instance_id, "ip": public_ip}
            
            elif status in ["Stopped", "Deleted"]:
                return {"success": False, "error": f"实例状态异常: {status}"}
                
        return {"success": False, "error": "轮询超时，机器可能还在创建中，请稍后去控制台查看。"}

    except Exception as e:
        return {"success": False, "error": str(e)}

# ================= 2. 动态折叠菜单 UI 构建器 =================

def get_region_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🇭🇰 中国香港", callback_data="region_cn-hongkong"))
    builder.row(
        InlineKeyboardButton(text="🌏 亚洲地区", callback_data="menu_asia"),
        InlineKeyboardButton(text="🌍 欧美地区", callback_data="menu_eu_us")
    )
    builder.row(InlineKeyboardButton(text="🐪 中东及其他", callback_data="menu_others"))
    return builder.as_markup()

def get_region_asia_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇯🇵 日本(东京)", callback_data="region_ap-northeast-1"),
        InlineKeyboardButton(text="🇰🇷 韩国(首尔)", callback_data="region_ap-northeast-2")
    )
    builder.row(
        InlineKeyboardButton(text="🇸🇬 新加坡", callback_data="region_ap-southeast-1"),
        InlineKeyboardButton(text="🇲🇾 马来西亚(吉隆坡)", callback_data="region_ap-southeast-3")
    )
    builder.row(
        InlineKeyboardButton(text="🇮🇩 印尼(雅加达)", callback_data="region_ap-southeast-5"),
        InlineKeyboardButton(text="🇵🇭 菲律宾(马尼拉)", callback_data="region_ap-southeast-6")
    )
    builder.row(
        InlineKeyboardButton(text="🇹🇭 泰国(曼谷)", callback_data="region_ap-southeast-7"),
        InlineKeyboardButton(text="🇲🇾 马来西亚(柔佛)", callback_data="region_ap-southeast-8")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="menu_main"))
    return builder.as_markup()

def get_region_eu_us_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇺🇸 美国(弗吉尼亚)", callback_data="region_us-east-1"),
        InlineKeyboardButton(text="🇺🇸 美国(硅谷)", callback_data="region_us-west-1")
    )
    builder.row(
        InlineKeyboardButton(text="🇲🇽 墨西哥", callback_data="region_na-south-1"),
        InlineKeyboardButton(text="🇬🇧 英国(伦敦)", callback_data="region_eu-west-1")
    )
    builder.row(
        InlineKeyboardButton(text="🇫🇷 法国(巴黎)", callback_data="region_eu-west-2"),
        InlineKeyboardButton(text="🇩🇪 德国(法兰克福)", callback_data="region_eu-central-1")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="menu_main"))
    return builder.as_markup()

def get_region_others_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇦🇪 阿联酋(迪拜)", callback_data="region_me-east-1"),
        InlineKeyboardButton(text="🇸🇦 沙特(利雅得)", callback_data="region_me-central-1")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="menu_main"))
    return builder.as_markup()

# ================= 3. 核心业务路由拦截 =================

@router.message(F.text == "💻 服务器管理")
async def show_server_management(message: types.Message):
    """响应主菜单点击，展示概览面板"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    # 模拟数据，后续可接入 API 获取真实状态
    running_count, stopped_count, pending_count = 1, 0, 0 
    
    text = (
        f"📊 **当前 ECS 服务器概览**\n\n"
        f"🟢 运行中: {running_count} 台\n"
        f"🔴 已停用: {stopped_count} 台\n"
        f"🔵 部署中: {pending_count} 台"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ 新增服务器", callback_data="action_add_server"))
    builder.row(InlineKeyboardButton(text="🟢 IP: 47.100.22.33", callback_data="ignore"))
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "action_add_server")
async def trigger_add_server(callback: types.CallbackQuery, state: FSMContext):
    """处理【新增服务器】点击事件"""
    if callback.from_user.id != config.ADMIN_ID: return await callback.answer()

    verify_code = f"{random.randint(0, 999999):06d}"
    await callback.message.answer("⏳ 正在向绑定邮箱发送验证码，请稍候...")
    send_success = await asyncio.to_thread(send_email_sync, verify_code)

    if send_success:
        await state.update_data(code=verify_code, timestamp=time.time())
        await state.set_state(ServerManagement.waiting_for_code)
        await callback.message.answer("✅ 验证码已发送至绑定邮箱！\n请直接在此回复 `6位数字验证码`。")
    else:
        await callback.message.answer("❌ 验证码发送失败，请检查 SMTP 或网络配置。")
    await callback.answer()

@router.message(ServerManagement.waiting_for_code)
async def verify_code_input(message: types.Message, state: FSMContext):
    """处理用户输入的验证码"""
    if message.from_user.id != config.ADMIN_ID: return
    
    user_input_code = message.text.strip()
    user_data = await state.get_data()
    
    if time.time() - user_data.get("timestamp", 0) > 300:
        await state.clear()
        return await message.answer("⚠️ 验证码已过期，请重新进入【💻 服务器管理】点击新增。")

    if user_input_code == user_data.get("code"):
        await message.answer("✅ 验证通过！请选择开服地区：", reply_markup=get_region_main_menu())
        await state.set_state(ServerManagement.waiting_for_region)
    else:
        await message.answer("❌ 验证码错误，请重试。")

@router.callback_query(ServerManagement.waiting_for_region, F.data.startswith("menu_"))
async def navigate_menus(callback: types.CallbackQuery):
    """处理二级菜单的动态折叠与展开"""
    if callback.from_user.id != config.ADMIN_ID: return await callback.answer()
    
    target_menu = callback.data
    
    if target_menu == "menu_main":
        await callback.message.edit_reply_markup(reply_markup=get_region_main_menu())
    elif target_menu == "menu_asia":
        await callback.message.edit_reply_markup(reply_markup=get_region_asia_menu())
    elif target_menu == "menu_eu_us":
        await callback.message.edit_reply_markup(reply_markup=get_region_eu_us_menu())
    elif target_menu == "menu_others":
        await callback.message.edit_reply_markup(reply_markup=get_region_others_menu())
        
    await callback.answer()

@router.callback_query(ServerManagement.waiting_for_region, F.data.startswith("region_"))
async def execute_run_instances(callback: types.CallbackQuery, state: FSMContext):
    """接收最终的地区选择，调用阿里云 API 自动化开机"""
    if callback.from_user.id != config.ADMIN_ID: return await callback.answer()
    
    region_id = callback.data.replace("region_", "")
    template_id = get_template_id(region_id)
    
    if not template_id:
        return await callback.message.edit_text(f"⚠️ 暂未在 `.env` 中配置 `{region_id}` 对应的启动模板 (TPL_xxx)，请配置后重试。")
    
    # 验证通过并找到模板，清理状态机释放锁
    await state.clear()
    
    # 发送过渡动画/提示
    progress_msg = await callback.message.edit_text(f"🚀 已拦截指令。正在向阿里云 `{region_id}` 下发创建任务，请耐心等待 (约需20-40秒)...")
    
    # 异步抛出阿里云 SDK 调用，防止阻塞事件循环
    result = await asyncio.to_thread(create_ecs_instance_sync, region_id, template_id)
    
    if result["success"]:
        text = (
            f"🎉 **MG 控制台扩容成功！**\n\n"
            f"🌍 **地域**: `{region_id}`\n"
            f"🆔 **实例 ID**: `{result['instance_id']}`\n"
            f"🌐 **公网 IP**: `{result['ip']}`\n"
            f"✅ **状态**: 运行中\n\n"
            f"安全组与计费模式已按模板自动下发。"
        )
        await progress_msg.edit_text(text, parse_mode="Markdown")
    else:
        await progress_msg.edit_text(f"❌ **创建失败**\n\n原因: {result.get('error')}", parse_mode="Markdown")

    await callback.answer()
