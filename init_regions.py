import os
import time
import sqlite3
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_vpc20160428.client import Client as VpcClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_vpc20160428 import models as vpc_models

# ================= 配置区 =================
# 你数据库的真实路径，请根据实际情况修改 (对应你 config.DB_PATH)
DB_PATH = "bot_data.db" 

# 你想要一键开荒的地域列表
TARGET_REGIONS = [
    "ap-northeast-1", # 日本(东京)
    "ap-northeast-2", # 韩国(首尔)
    "ap-southeast-1", # 新加坡
    "ap-southeast-3", # 马来西亚(吉隆坡)
    "eu-central-1",   # 德国(法兰克福)
    "us-west-1",      # 美国(硅谷)
    "me-east-1"       # 阿联酋(迪拜)
]
# ==========================================

# 🛠️ 改造 1：客户端工厂现在接收动态的 ak 和 sk
def create_client(client_class, region_id, ak, sk):
    """初始化动态阿里云 API 客户端"""
    config = open_api_models.Config(
        access_key_id=ak,
        access_key_secret=sk,
        region_id=region_id
    )
    if client_class == EcsClient:
        config.endpoint = f"ecs.{region_id}.aliyuncs.com"
    else:
        config.endpoint = f"vpc.{region_id}.aliyuncs.com"
    return client_class(config)

# 🛠️ 改造 2：初始化函数现在接收账号信息
def init_region_for_account(account_id, alias, ak, sk, region_id):
    print(f"\n🚀 [账号: {alias}] 开始初始化地域: {region_id}")
    ecs_client = create_client(EcsClient, region_id, ak, sk)
    vpc_client = create_client(VpcClient, region_id, ak, sk)

    try:
        # 1. 创建 VPC
        print("   ⏳ 正在创建 VPC...")
        vpc_request = vpc_models.CreateVpcRequest(cidr_block="192.168.0.0/16", vpc_name="Node-VPC-Auto")
        vpc_response = vpc_client.create_vpc(vpc_request)
        vpc_id = vpc_response.body.vpc_id
        time.sleep(3)

        # 2. 查询可用区并创建交换机
        print("   ⏳ 正在查询可用区并创建交换机...")
        zones_request = ecs_models.DescribeZonesRequest()
        zones_response = ecs_client.describe_zones(zones_request)
        zone_id = zones_response.body.zones.zone[0].zone_id 
        
        vswitch_request = vpc_models.CreateVSwitchRequest(
            vpc_id=vpc_id, zone_id=zone_id, cidr_block="192.168.0.0/24", v_switch_name=f"Node-VSwitch-{zone_id}"
        )
        vswitch_response = vpc_client.create_vswitch(vswitch_request)
        vswitch_id = vswitch_response.body.v_switch_id
        time.sleep(3)

        # 3. 创建安全组并放行全端口 (契合节点自动化需求)
        print("   ⏳ 正在创建安全组并配置全放行规则...")
        sg_request = ecs_models.CreateSecurityGroupRequest(
            vpc_id=vpc_id, security_group_name="Node-SG-AllOpen"
        )
        sg_response = ecs_client.create_security_group(sg_request)
        sg_id = sg_response.body.security_group_id
        
        sg_rule_request = ecs_models.AuthorizeSecurityGroupRequest(
            security_group_id=sg_id, ip_protocol="all", port_range="-1/-1", source_cidr_ip="0.0.0.0/0"
        )
        ecs_client.authorize_security_group(sg_rule_request)
        time.sleep(3)

        # 4. 创建启动模板
        print("   ⏳ 正在生成 ECS 启动模板...")
        network_interface = ecs_models.CreateLaunchTemplateRequestTemplateResourceNetworkInterfaces(
            v_switch_id=vswitch_id, security_group_id=sg_id
        )
        template_request = ecs_models.CreateLaunchTemplateRequest(
            launch_template_name="Node-Auto-Template",
            version_description="V1.0 - Auto Initialized",
            template_resource=ecs_models.CreateLaunchTemplateRequestTemplateResource(
                security_group_id=sg_id,
                v_switch_id=vswitch_id,
                network_interfaces=[network_interface],
                # image_id="ubuntu_22_04_x64_20G_alibase_20240926.vhd", 
                # instance_type="ecs.e-c1m1.large"
            )
        )
        template_response = ecs_client.create_launch_template(template_request)
        template_id = template_response.body.launch_template_id
        print(f"   🎉 启动模板创建完成: {template_id}")

        # 🛠️ 改造 3：将成功的模板 ID 自动写入数据库，供机器人读取
        save_template_to_db(account_id, region_id, template_id)

    except Exception as e:
        print(f"   ❌ [账号: {alias}] 地域 {region_id} 初始化失败: {str(e)}")

# 🛠️ 改造 4：数据库持久化逻辑
def save_template_to_db(account_id, region_id, template_id):
    """将模板 ID 保存到数据库中"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 确保存储模板的表存在
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS launch_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            region_id TEXT,
            template_id TEXT,
            UNIQUE(account_id, region_id)
        )
    """)
    # 插入或更新模板信息
    cursor.execute("""
        INSERT INTO launch_templates (account_id, region_id, template_id)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id, region_id) DO UPDATE SET template_id=excluded.template_id
    """, (account_id, region_id, template_id))
    conn.commit()
    conn.close()
    print(f"   💾 模板 ID {template_id} 已成功存入数据库！")

if __name__ == "__main__":
    print("====== 开始多账号全局自动化部署基建 ======")
    
    # 从数据库读取所有处于激活状态的云账号
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, alias, access_key, access_secret FROM cloud_accounts WHERE is_active = 1")
        accounts = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ 无法读取账号数据库: {e}")
        accounts = []

    if not accounts:
        print("⚠️ 未找到任何激活的云账号，请先通过 Telegram 机器人添加账号。")
    
    # 双重循环：遍历所有账号 -> 遍历所有地域
    for account in accounts:
        acc_id, acc_alias, acc_ak, acc_sk = account
        print(f"\n\n==================================================")
        print(f" 🏢 正在处理账号: {acc_alias} (ID: {acc_id})")
        print(f"==================================================")
        
        for region in TARGET_REGIONS:
            init_region_for_account(acc_id, acc_alias, acc_ak, acc_sk, region)
            time.sleep(5) # 跨地域操作间隔，避免触发 API 限流
            
    print("\n✅ 所有账号的基础设施部署与模板入库完毕！")
