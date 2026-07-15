from aiogram import Router, F
from aiogram.types import CallbackQuery

router = Router()

# 这里先放一个空的监听器占位，防止 main.py 导入报错
# 注意把下面拦截的字符串换成对应的 (比如 bbr_action 里写 run_sh:bbr:)
@router.callback_query(F.data.startswith("run_sh:填入对应的脚本id:"))
async def handle_install(call: CallbackQuery):
    await call.message.edit_text("🚧 模块正在开发中...")
    await call.answer()
