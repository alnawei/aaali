#(基础指令与全局菜单)
import sqlite3
from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526.client import Client as EcsClient
import config

# 实例化当前模块的路由器
router = Router()

def get_main_keyboard():
    """生成底部的四大功能导航栏"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💻 服务器管理"), KeyboardButton(text="📊 流量与计费")],
            [KeyboardButton(text="⚙️ 节点配置"), KeyboardButton(text="🛠 系统设置")]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="请选择控制台功能..."
    )

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.from_user.id != config.ADMIN_ID: return
    await message.answer("欢迎进入 MG 终极私有控制台 V2.0", reply_markup=get_main_keyboard())

# ================= 动态 API 客户端工厂 =================
def get_dynamic_ecs_client(account_id: int, region_id: str) -> EcsClient:
    """
    动态 Client 工厂：
    根据传入的 account_id 从 SQLite 实时拉取凭证，并实例化对应地域的 ECS Client。
    """
    # 1. 实时连接数据库，查询活跃凭证
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT access_key, access_secret FROM cloud_accounts WHERE id = ? AND is_active = 1", 
        (account_id,)
    )
    result = cursor.fetchone()
    conn.close()

    # 2. 安全拦截
    if not result:
        raise ValueError(f"❌ 实例化失败: 找不到 ID 为 {account_id} 的有效云账号凭证！")

    access_key, access_secret = result

    # 3. 组装阿里云 Config
    api_config = open_api_models.Config(
        access_key_id=access_key,
        access_key_secret=access_secret,
        region_id=region_id
    )
    
    # 4. 动态配置 Endpoint，精准指向目标机房
    api_config.endpoint = f"ecs.{region_id}.aliyuncs.com"
    
    # 5. 返回轻量级的 Client 实例
    return EcsClient(api_config)
