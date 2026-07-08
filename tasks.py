import asyncio
import db
import config
from aiogram import Bot
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models
# 引入我们刚才在 server.py 写的查流量黑科技
from handlers.server import get_real_traffic_gb

# 设置一个简单的内存缓存，防止 80% 预警信息每半小时就给你发一次“轰炸”你
alerted_80_instances = set()

async def traffic_monitor_job(bot: Bot, admin_id: int):
    """后台静默巡查任务：计算流量并执行风控"""
    # 1. 从本地账本拉取所有登记在册的机器
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT instance_id, traffic_limit_gb, traffic_start_time FROM ecs_business")
    rows = cursor.fetchall()
    conn.close()

    # 初始化阿里云 ECS 客户端（用于发送关机指令）
    region_id = "cn-hongkong"
    ali_config = open_api_models.Config(
        access_key_id=config.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=config.ALIYUN_ACCESS_KEY_SECRET,
        endpoint=f'ecs.{region_id}.aliyuncs.com'
    )
    ecs_client = EcsClient(ali_config)

    for row in rows:
        instance_id = row[0]
        limit_gb = row[1]
        start_time_str = row[2]

        if not start_time_str:
            continue  # 如果这台机器连计费起点都没设置，直接跳过

        # 2. 【核心优化】先看机器是不是开着的，关机状态就不需要查流量了，节省 API 次数
        try:
            req_status = ecs_models.DescribeInstancesRequest(region_id=region_id, instance_ids=f'["{instance_id}"]')
            resp_status = ecs_client.describe_instances(req_status)
            if not resp_status.body.instances.instance:
                continue
            status = resp_status.body.instances.instance[0].status
            if status != "Running":
                continue # 只要不是 Running，就放过它
        except Exception:
            continue

        # 3. 去查真实的流量 (加入 0.5 秒睡眠，防止并发请求太多被阿里云 API 封禁)
        await asyncio.sleep(0.5) 
        current_traffic = await asyncio.to_thread(get_real_traffic_gb, instance_id, start_time_str)
        
        # 4. 计算消耗比例，执行风控判断
        usage_percent = current_traffic / limit_gb

        # 🛑 【最高警报】：触发 95% 熔断关机
        if usage_percent >= 0.95:
            try:
                # 下发强制关机指令！
                req_stop = ecs_models.StopInstanceRequest(instance_id=instance_id)
                await asyncio.to_thread(ecs_client.stop_instance, req_stop)
                
                # 给老板（你）的 Telegram 发送拔管通知
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"🚨 **【系统熔断断网警报】**\n\n"
                         f"🆔 实例: `{instance_id}`\n"
                         f"📶 当前流量: `{current_traffic} GB` / `{limit_gb} GB`\n"
                         f"⚠️ 消耗已达 **{usage_percent*100:.1f}%**\n"
                         f"🛑 系统已自动执行**物理关机**，防止产生天价流量账单！"
                )
                # 既然已经关机了，把它的预警记录清掉，等客户交钱重置后再重新监控
                alerted_80_instances.discard(instance_id)
            except Exception as e:
                await bot.send_message(admin_id, f"❌ **警告：对实例 {instance_id} 执行熔断关机失败**，请立刻手动处理！\n报错: {e}")

        # 📢 【中级警报】：触发 80% 预警
        elif usage_percent >= 0.80 and usage_percent < 0.95:
            # 只有还没被警告过的，才发消息，防骚扰
            if instance_id not in alerted_80_instances:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ **【流量消耗极速预警】**\n\n"
                         f"🆔 实例: `{instance_id}`\n"
                         f"📶 当前流量: `{current_traffic} GB` / `{limit_gb} GB`\n"
                         f"📢 消耗已达 **{usage_percent*100:.1f}%**，即将面临熔断，请注意客户续费动态。"
                )
                alerted_80_instances.add(instance_id) # 标记为“已警告”
        
        # 🟢 【绿灯恢复】：如果有人调高了流量包或重置了流量，比例降下来了
        elif usage_percent < 0.80:
            alerted_80_instances.discard(instance_id) # 解除它的警告标记
