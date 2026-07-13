import asyncio
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import tasks
import config
from handlers import common, server, traffic, node, system

# 👇 1. 导入 init_db 函数
from db import init_db 

async def main():
    # 👇 2. 启动 Bot 之前，第一件事就是强制初始化数据库建表！
    init_db()
    
    print("🚀 MG 控制台 V2.0 机器人已启动 (多路由架构)...")
    
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # 将各个子模块的 Router 拼装到主 Dispatcher 上
    dp.include_router(common.router)
    dp.include_router(server.router)
    dp.include_router(traffic.router)
    dp.include_router(node.router)
    dp.include_router(system.router)

    # ==================== 流量监控定时任务 ====================
    scheduler = AsyncIOScheduler()
    
    admin_chat_id = config.ADMIN_ID
    
    scheduler.add_job(
        tasks.traffic_monitor_job, 
        'interval', 
        minutes=30, 
        args=[bot, admin_chat_id]
    )

    scheduler.add_job(
        tasks.daily_billing_check_job,
        'cron',
        hour=10,
        minute=0,
        args=[bot, admin_chat_id]
    )
    # ==========================================================
    
    scheduler.start()
    print("✅ APScheduler: 流量监控(30分钟) & 催费预警(每日10点) 已启动。")

    # ==========================================================

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
