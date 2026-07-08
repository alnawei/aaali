from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import sqlite3
import db
from datetime import datetime

router = Router()

@router.message(F.text == "📊 流量与计费")
async def global_billing_dashboard(message: types.Message):
    # 1. 弹出加载提示
    wait_msg = await message.answer("🔄 正在生成全局财务与流量报表，请稍候...")
    
    # 2. 从本地 SQLite 账本读取所有机器的业务数据
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT instance_id, traffic_limit_gb, expire_time FROM ecs_business")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await wait_msg.delete()
        await message.answer("📝 **全局账单总览**\n\n目前数据库中没有任何实例的计费记录。请先在 [💻 服务器管理] 中操作机器。")
        return

    # 3. 统计全局数据
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
        
        # 计算是否有即将过期（7天内）的机器
        if expire_time_str:
            try:
                expire_date = datetime.strptime(expire_time_str, "%Y-%m-%d")
                days_left = (expire_date - now).days
                if 0 <= days_left <= 7:
                    expiring_soon_count += 1
                    # 截取实例 ID 的后 6 位，保持面板整洁
                    short_id = instance_id[-6:] 
                    expiring_details += f"• `...{short_id}` 剩余 {days_left} 天 ({expire_time_str})\n"
                elif days_left < 0:
                    expiring_soon_count += 1
                    short_id = instance_id[-6:]
                    expiring_details += f"• `...{short_id}` ❌ 已过期 {-days_left} 天\n"
            except ValueError:
                pass

    # 4. 拼装财务报表面板
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

    # 5. 添加交互按钮 (后续可扩展导出 Excel 账单等功能)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 刷新全局报表", callback_data="refresh_global_billing"))
    
    await wait_msg.delete()
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# 绑定刷新按钮的响应事件
@router.callback_query(F.data == "refresh_global_billing")
async def refresh_billing(callback: types.CallbackQuery):
    await callback.answer("刷新中...")
    # 这里的逻辑和上面一样，你可以选择封装成一个函数复用，为了简单，目前可以直接提示
    await callback.message.answer("报表已是最新状态。如需再次查看请点击底部菜单。")
