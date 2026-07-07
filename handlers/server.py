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

from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526 import models as ecs_models

import config

router = Router()

class ServerManagement(StatesGroup):
    waiting_for_code = State()
    waiting_for_region = State()

# === 阿里云与发信底层函数 ===
def get_template_id(region_id: str) -> str:
    env_var_name = f"TPL_{region_id.replace('-', '_').upper()}"
    return os.getenv(env_var_name, "").strip()

def send_email_sync(code: str) -> bool:
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
    except: return False

def create_ecs_instance_sync(region_id: str, template_id: str) -> dict:
    # (此处省略阿里云开机与轮询代码的内部实现，与上一版完全一致)
    pass # 替换为上一版的 create_ecs_instance_sync 函数内容

# === 菜单生成器 ===
# (此处省略四个 get_region_xxxx_menu 菜单生成函数，与上一版完全一致)

# === 业务路由拦截 ===
@router.message(F.text == "💻 服务器管理")
async def show_server_management(message: types.Message):
    if message.from_user.id != config.ADMIN_ID: return
    
    text = "📊 **当前 ECS 服务器概览**\n\n🟢 运行中: 1 台\n🔴 已停用: 0 台\n🔵 部署中: 0 台"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ 新增服务器", callback_data="action_add_server"))
    builder.row(InlineKeyboardButton(text="🟢 IP: 47.100.22.33", callback_data="ignore"))
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "action_add_server")
async def trigger_add_server(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID: return await callback.answer()
    verify_code = f"{random.randint(0, 999999):06d}"
    await callback.message.answer("⏳ 正在向绑定邮箱发送验证码，请稍候...")
    send_success = await asyncio.to_thread(send_email_sync, verify_code)
    if send_success:
        await state.update_data(code=verify_code, timestamp=time.time())
        await state.set_state(ServerManagement.waiting_for_code)
        await callback.message.answer("✅ 验证码已发送至邮箱，请直接在此回复。")
    else:
        await callback.message.answer("❌ 验证码发送失败。")
    await callback.answer()

# (此处补全上一版的 verify_code_input, navigate_menus, execute_run_instances 路由逻辑)
# 记得将里面的 ADMIN_ID 替换为 config.ADMIN_ID，@dp 替换为 @router 即可
