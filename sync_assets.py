import sqlite3
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models

# ================= 配置区 =================
DB_PATH = "bot_data.db"
# 这里写上你目前所有可能开过机器的地域
TARGET_REGIONS = ["cn-hongkong", "ap-northeast-1"] 
# ==========================================

def create_client(region_id, ak, sk):
    """初始化 API 客户端"""
    config = open_api_models.Config(access_key_id=ak, access_key_secret=sk, region_id=region_id)
    config.endpoint = f"ecs.{region_id}.aliyuncs.com"
    return EcsClient(config)

def sync_all_accounts():
    """遍历数据库中所有账号，向阿里云核对服务器真实数量"""
    print("====== 开始全局资产盘点与对账 ======")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 提取所有激活状态的账号
        cursor.execute("SELECT id, alias, access_key, access_secret FROM cloud_accounts WHERE is_active = 1")
        accounts = cursor.fetchall()
    except Exception as e:
        print(f"❌ 无法读取账号表，请检查数据库: {e}")
        return

    for account in accounts:
        acc_id, acc_alias, acc_ak, acc_sk = account
        acc_ak = str(acc_ak).strip()
        acc_sk = str(acc_sk).strip()
        
        print(f"\n🏢 正在盘点账号: {acc_alias} (ID: {acc_id})")
        
        for region in TARGET_REGIONS:
            print(f"   ⏳ 正在向阿里云请求 {region} 的机器快照...")
            try:
                client = create_client(region, acc_ak, acc_sk)
                # 请求获取实例列表 (一次最多拉 100 台，绝对够基建用了)
                req = ecs_models.DescribeInstancesRequest(region_id=region, max_results=100)
                resp = client.describe_instances(req)
                
                # 安全解析嵌套的 JSON 数据
                instances_obj = getattr(resp.body, 'instances', None)
                instance_list = getattr(instances_obj, 'instance', []) if instances_obj else []
                
                running = 0
                stopped = 0
                
                for inst in instance_list:
                    if inst.status == 'Running':
                        running += 1
                    elif inst.status == 'Stopped':
                        stopped += 1
                
                # 将真实的数量覆盖写入刚才建立的本地账本
                cursor.execute("""
                    INSERT INTO account_assets (account_id, region_id, running_count, stopped_count, last_sync_time)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(account_id, region_id) 
                    DO UPDATE SET 
                        running_count=excluded.running_count, 
                        stopped_count=excluded.stopped_count,
                        last_sync_time=CURRENT_TIMESTAMP
                """, (acc_id, region, running, stopped))
                conn.commit()
                
                print(f"   ✅ [对账成功] {region} -> 运行中: {running} 台 | 已停用: {stopped} 台")
                
            except Exception as e:
                print(f"   ❌ [对账失败] {region}: {str(e)}")
                
    conn.close()
    print("\n🎉 全局资产同步完成！本地账本已是最新状态。")
