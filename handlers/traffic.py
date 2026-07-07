from aiogram import Router, types, F
import config

router = Router()

@router.message(F.text == "📊 流量与计费")
async def show_traffic(message: types.Message):
    if message.from_user.id != config.ADMIN_ID: return
    await message.answer("🚧 **流量与计费模块**正在开发中...\n敬请期待下一阶段更新！", parse_mode="Markdown")
