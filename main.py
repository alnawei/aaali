import asyncio
from aiogram import Bot, Dispatcher
import config

# 引入我们拆分出去的模块
from handlers import common, server, traffic, node, system

async def main():
    print("🚀 MG 控制台 V2.0 机器人已启动 (多路由架构)...")
    
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # 将各个子模块的 Router 拼装到主 Dispatcher 上
    dp.include_router(common.router)
    dp.include_router(server.router)
    dp.include_router(traffic.router)
    dp.include_router(node.router)
    dp.include_router(system.router)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
