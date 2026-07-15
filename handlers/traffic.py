import asyncio
import sqlite3
import db
from datetime import datetime
from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config

router = Router()

# ================= 🛠️ 工具函数：异步隔离拉取财务数据 =================
def fetch_global_billing_data_sync():
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT instance_id, traffic_limit_gb, expire_time FROM ecs_business")
    rows = cursor.fetchall()
    conn.close()
    return rows

async def generate_billing_text_and_keyboard():
    # 放入异步线程池执行拉取操作
    rows = await asyncio.to_thread(fetch_global_billing_data_sync)
    
    if not rows:
        return "📝 **全局账单总览**\n\n目前数据库中没有任何实例的计费记录。请先在 [💻 服务器管理] 中操作机器。", None

    total_instances = len(rows)
    total_allocated_traffic = 0
    expiring_soon_count = 0
    expiring_details = ""
    
    now = datetime.now()
    
    for row in rows:
        instance_id = row[0]
        limit_gb = row[1]
        expire_time_str = row[2]
        
        total_allocated_traffic += limit_gb
        
        if expire_time_str:
            try:
                expire_date = datetime.strptime(expire_time_str, "%Y-%m-%d")
                days_left = (expire_date - now).days
                short_id = instance_id[-6:] 
                
                if 0 <= days_left <= 7:
                    expiring_soon_count += 1
                    expiring_details += f"• `...{short_id}` 剩余 **{days_left}** 天 ({expire_time_str})\n"
                elif days_left < 0:
                    expiring_soon_count += 1
                    expiring_details += f"• `...{short_id}` ❌ **已过期 {-days_left} 天**\n"
            except ValueError:
                pass

    text = (
        "📊 **全局财务与计费总览**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ **在管实例总数**: `{total_instances}` 台\n"
        f"📶 **本月已分配流量总额**: `{total_allocated_traffic} GB`\n\n"
        f"⚠️ **近期到期/催费预警 (7天内)**: `{expiring_soon_count}` 台\n"
    )
    
    if expiring_soon_count > 0:
        text += f"━━━━━━━━━━━━━━━━━━\n{expiring_details}"
    else:
        text += "━━━━━━━━━━━━━━━━━━\n✅ 所有实例状态良好，近期无催费任务。"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 刷新全局报表", callback_data="refresh_global_billing"))
    
    return text, builder.as_markup()

@router.message(F.text == "📊 流量与计费")
async def global_billing_dashboard(message: types.Message):
    if message.from_user.id != config.ADMIN_ID: return
    wait_msg = await message.answer("🔄 正在生成全局财务与流量报表，请稍候...")
    
    text, reply_markup = await generate_billing_text_and_keyboard()
    
    await wait_msg.delete()
    await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")

# 🛠️ 修正 3：优化按钮更新体验，实现面板内容即时替换与动态无感刷新
@router.callback_query(F.data == "refresh_global_billing")
async def refresh_billing(callback: types.CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID: return await callback.answer()
    await callback.answer("⏳ 正在重新核算最精准账单数据...")
    
    text, reply_markup = await generate_billing_text_and_keyboard()
    
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        # 当数据没有实际任何变动触发此异常，安全放过
        await callback.answer("📊 当前已是最新数据！")
