import asyncio
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # 💡 别忘了导包
import tasks  # 💡 导入刚才新建的 tasks 文件
import config
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

    # 填入位置就在这里：👇 【启动 Bot 轮询的前面】 👇
    # ==================== 流量监控定时任务 ====================
    scheduler = AsyncIOScheduler()
    
    # ⚠️ 必须把 123456789 改成你真正的 Telegram 数字 ID，否则报错信息发给别人了
    admin_chat_id = 123456789  
    
    # 设置每 30 分钟执行一次全网巡查
    scheduler.add_job(
        tasks.traffic_monitor_job, 
        'interval', 
        minutes=30, 
        args=[bot, admin_chat_id]
    )
    scheduler.start()
    print("✅ APScheduler: 流量监控风控系统已启动，每 30 分钟巡查一次。")
    # ==========================================================

    # 这行就是真正的“启动 Bot 轮询”
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
