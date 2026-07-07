import os
import asyncio
import smtplib
import random
import time
from email.mime.text import MIMEText
from email.header import Header
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================= 1. 环境与基础设置 =================
# 加载 .env 文件
load_dotenv()

# 读取基础配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
# 注意：环境变量读取的都是字符串，ID需要转为整数
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECIPIENT = os.getenv("RECIPIENT")

# 初始化 Bot 和 Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 定义 FSM 状态机
class ServerManagement(StatesGroup):
    waiting_for_code = State()
    waiting_for_region = State()

# ================= 2. 底层发信模块 =================
def send_email_sync(code: str) -> bool:
    """同步的 SMTP 发信逻辑，将在后台线程中运行防阻塞"""
    try:
        msg = MIMEText(f"【MG 控制台】\n\n您的开服验证码是：{code}\n请在5分钟内返回 TG 进行验证。", 'plain', 'utf-8')
        msg['Subject'] = Header("MG 控制台 V2.0 - 开机验证码", 'utf-8')
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECIPIENT], msg.as_string())
        return True
    except Exception as e:
        print(f"SMTP 发信失败: {e}")
        return False

# ================= 3. 交互 UI 界面 =================
def get_main_keyboard():
    """生成底部主菜单"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="💻 服务器管理")]],
        resize_keyboard=True,
        is_persistent=True
    )

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """响应 /start 指令"""
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("欢迎进入 MG 终极私有控制台 V2.0", reply_markup=get_main_keyboard())

@dp.message(F.text == "💻 服务器管理")
async def show_server_management(message: types.Message):
    """响应主菜单点击，展示概览面板"""
    if message.from_user.id != ADMIN_ID:
        return
    
    # 模拟从数据库或阿里云 API 获取的节点数量
    running_count, stopped_count, pending_count = 1, 0, 0 
    
    text = (
        f"📊 **当前 ECS 服务器概览**\n\n"
        f"🟢 运行中: {running_count} 台\n"
        f"🔴 已停用: {stopped_count} 台\n"
        f"🔵 部署中: {pending_count} 台"
    )
    
    # 构建悬浮按钮
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ 新增服务器", callback_data="action_add_server"))
    
    # 模拟渲染现有的机器 IP (仅做展示)
    builder.row(InlineKeyboardButton(text="🟢 IP: 47.100.22.33", callback_data="ignore"))
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# ================= 4. 邮箱验证流 =================
@dp.callback_query(F.data == "action_add_server")
async def trigger_add_server(callback: types.CallbackQuery, state: FSMContext):
    """处理【新增服务器】点击事件"""
    if callback.from_user.id != ADMIN_ID: 
        return await callback.answer()

    verify_code = f"{random.randint(0, 999999):06d}"
    await callback.message.answer("⏳ 正在向绑定邮箱发送验证码，请稍候...")
    
    # 异步抛出邮件发送任务
    send_success = await asyncio.to_thread(send_email_sync, verify_code)

    if send_success:
        # 记录验证码和当前时间戳到 FSM 内存中
        await state.update_data(code=verify_code, timestamp=time.time())
        await state.set_state(ServerManagement.waiting_for_code)
        await callback.message.answer("✅ 验证码已发送至绑定邮箱！\n请直接在此回复 `6位数字验证码`。")
    else:
        await callback.message.answer("❌ 验证码发送失败，请检查 SMTP 或网络配置。")
    
    await callback.answer()

@dp.message(ServerManagement.waiting_for_code)
async def verify_code_input(message: types.Message, state: FSMContext):
    """处理用户输入的验证码"""
    if message.from_user.id != ADMIN_ID:
        return
    
    user_input_code = message.text.strip()
    user_data = await state.get_data()
    
    # 校验：是否超时 (300秒)
    if time.time() - user_data.get("timestamp", 0) > 300:
        await state.clear()
        return await message.answer("⚠️ 验证码已过期，请重新进入【💻 服务器管理】点击新增。")

    # 校验：验证码是否匹配
    if user_input_code == user_data.get("code"):
        # 验证成功，渲染地域选择一级菜单
        builder = InlineKeyboardBuilder()
        
        # 第一行：直达香港
        builder.row(InlineKeyboardButton(text="🇭🇰 中国香港", callback_data="region_cn-hongkong"))
        # 第二行：大区折叠菜单
        builder.row(
            InlineKeyboardButton(text="🌏 亚洲地区", callback_data="menu_asia"),
            InlineKeyboardButton(text="🌍 欧美地区", callback_data="menu_eu_us"),
            InlineKeyboardButton(text="🐪 中东及其他", callback_data="menu_others")
        )
        
        await message.answer("✅ 安全验证通过！\n请选择你要开通的服务器地区：", reply_markup=builder.as_markup())
        await state.set_state(ServerManagement.waiting_for_region)
    else:
        await message.answer("❌ 验证码错误，请检查后重新输入。")

# 启动入口
async def main():
    # 兼容 aiogram 3.x 特性
    from aiogram.filters import CommandStart
    # 注册 CommandStart 到全局以供使用
    global CommandStart
    
    print("MG 控制台 V2.0 机器人已启动...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    from aiogram.filters import CommandStart
    asyncio.run(main())
