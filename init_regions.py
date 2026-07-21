import os
import time
import base64
import sqlite3
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models

# ================= 配置区 =================
# 数据库路径 (对应你 config.DB_PATH)
DB_PATH = "bot_data.db" 

# 想要一键开荒的地域列表 (可随意增删)
TARGET_REGIONS = [
    "cn-hongkong",      # 中国香港
    # "ap-northeast-1", # 日本东京
    # "ap-southeast-1", # 新加坡
]

# 🎯 终极基座配置 (完美兼容你的“无定时维护”架构)
INSTANCE_TYPE = "ecs.e-c1m1.large"
IMAGE_ID = "debian_12_14_x64_20G_alibase_20260609.vhd"
ROOT_PASSWORD = "@QS00008" # ⚠️ 生产环境记得修改
# ==========================================

# 🛠️ 客户端工厂
def create_client(region_id, ak, sk) -> EcsClient:
    """初始化动态阿里云 API 客户端"""
    config = open_api_models.Config(
        access_key_id=ak,
        access_key_secret=sk,
        region_id=region_id
    )
    # 统一使用 ECS Endpoint
    config.endpoint = f"ecs.{region_id}.aliyuncs.com"
    return EcsClient(config)

# 🛠️ 核心开荒逻辑 (融合了 test_init_hk.py 的精华)
def init_region_for_account(account_id, alias, ak, sk, region_id):
    print(f"\n🚀 [账号: {alias}] 开始初始化地域: {region_id}")
    client = create_client(region_id, ak, sk)

    try:
        # ---------------- 1. 动态获取可用区 (Zone) ----------------
        zones_req = ecs_models.DescribeZonesRequest(region_id=region_id)
        zones_resp = client.describe_zones(zones_req)
        zone_list = getattr(getattr(zones_resp.body, 'zones', None), 'zone', [])
        if not zone_list:
            raise Exception("该地域未返回任何可用区！")
        zone_id = zone_list[0].zone_id # 取第一个可用区
        print(f"   📍 锁定可用区: {zone_id}")

        # ---------------- 2. 初始化 VPC (先查后建) ----------------
        vpc_id = None
        req_vpc = ecs_models.DescribeVpcsRequest(region_id=region_id)
        resp_vpc = client.describe_vpcs(req_vpc)

        vpcs_obj = getattr(resp_vpc.body, 'vpcs', None)
        vpc_list = getattr(vpcs_obj, 'vpc', []) if vpcs_obj else []
        for v in vpc_list:
            if getattr(v, 'vpc_name', '') == "Node-VPC-Auto":
                vpc_id = v.vpc_id
                break

        if vpc_id:
            print(f"   ✅ [VPC] 已存在: {vpc_id}")
        else:
            print("   ⏳ [VPC] 未找到，正在创建...")
            create_vpc_req = ecs_models.CreateVpcRequest(region_id=region_id, vpc_name="Node-VPC-Auto", cidr_block="192.168.0.0/16")
            create_vpc_resp = client.create_vpc(create_vpc_req)
            vpc_id = create_vpc_resp.body.vpc_id
            print(f"   🎉 [VPC] 创建成功: {vpc_id}")
            time.sleep(3) # 等待网络生效

        # ---------------- 3. 初始化 VSwitch (先查后建) ----------------
        vswitch_id = None
        vsw_name = f"Node-VSwitch-{zone_id}"
        req_vsw = ecs_models.DescribeVSwitchesRequest(region_id=region_id, vpc_id=vpc_id)
        resp_vsw = client.describe_vswitches(req_vsw)

        vsws_obj = getattr(resp_vsw.body, 'v_switches', getattr(resp_vsw.body, 'vswitches', None))
        vsw_list = getattr(vsws_obj, 'v_switch', getattr(vsws_obj, 'vswitch', [])) if vsws_obj else []
        for v in vsw_list:
            name = getattr(v, 'v_switch_name', getattr(v, 'vswitch_name', ''))
            if name == vsw_name:
                vswitch_id = getattr(v, 'v_switch_id', getattr(v, 'vswitch_id', None))
                break

        if vswitch_id:
            print(f"   ✅ [VSwitch] 已存在: {vswitch_id}")
        else:
            print(f"   ⏳ [VSwitch] 未找到，正在 {zone_id} 创建...")
            create_vsw_req = ecs_models.CreateVSwitchRequest(region_id=region_id, vpc_id=vpc_id, zone_id=zone_id, v_switch_name=vsw_name, cidr_block="192.168.1.0/24")
            create_vsw_resp = client.create_vswitch(create_vsw_req)
            vswitch_id = create_vsw_resp.body.v_switch_id
            print(f"   🎉 [VSwitch] 创建成功: {vswitch_id}")

        # ---------------- 4. 初始化安全组 (先查后建) ----------------
        sg_id = None
        sg_name = "Node-SG-Auto"
        req_sg = ecs_models.DescribeSecurityGroupsRequest(region_id=region_id, vpc_id=vpc_id)
        resp_sg = client.describe_security_groups(req_sg)

        sgs_obj = getattr(resp_sg.body, 'security_groups', None)
        sg_list = getattr(sgs_obj, 'security_group', []) if sgs_obj else []
        for sg in sg_list:
            name = getattr(sg, 'security_group_name', getattr(sg, 'security_groupname', ''))
            if name == sg_name:
                sg_id = sg.security_group_id
                break

        if sg_id:
            print(f"   ✅ [安全组] 已存在: {sg_id}")
        else:
            print("   ⏳ [安全组] 未找到，正在创建...")
            create_sg_req = ecs_models.CreateSecurityGroupRequest(region_id=region_id, vpc_id=vpc_id, security_group_name=sg_name)
            create_sg_resp = client.create_security_group(create_sg_req)
            sg_id = create_sg_resp.body.security_group_id
            
            # 极简通用规则
            rules = [
                {"protocol": "icmp", "port": "-1/-1"},
                {"protocol": "tcp", "port": "22/22"},
                {"protocol": "tcp", "port": "443/443"},
                {"protocol": "tcp", "port": "80/80"},
                {"protocol": "all", "port": "-1/-1"}
            ]
            for rule in rules:
                auth_sg_req = ecs_models.AuthorizeSecurityGroupRequest(
                    region_id=region_id, security_group_id=sg_id, ip_protocol=rule["protocol"], port_range=rule["port"], source_cidr_ip="0.0.0.0/0"
                )
                client.authorize_security_group(auth_sg_req)
            print(f"   🎉 [安全组] 创建并配置成功: {sg_id}")

        # ---------------- 5. 组装 LaunchTemplate ----------------
        template_name = f"Node-Template-Acc{account_id}-{region_id}"
        
        # 定义基础环境脚本 (包含密码注入)
        boot_script = f"""#!/bin/bash
        echo "root:{ROOT_PASSWORD}" | chpasswd
        sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/g' /etc/ssh/sshd_config
        sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/g' /etc/ssh/sshd_config
        systemctl restart sshd
        apt-get update -y
        apt-get install -y curl wget git
        echo "Node {region_id} initialized successfully!" > /root/init.log
        """
        user_data_b64 = base64.b64encode(boot_script.encode('utf-8')).decode('utf-8')

        print("   ⏳ [启动模板] 正在拼装完美图纸...")
        system_disk_config = ecs_models.CreateLaunchTemplateRequestSystemDisk(category='cloud_essd', size=20, delete_with_instance=True)
        
        # 💡 使用最稳妥的扁平结构，摒弃 TemplateResource 字典
        create_lt_req = ecs_models.CreateLaunchTemplateRequest(
            region_id=region_id,
            launch_template_name=template_name,
            image_id=IMAGE_ID,
            instance_type=INSTANCE_TYPE,
            instance_name=f"Node-Acc{account_id}", 
            internet_max_bandwidth_out=5,
            internet_charge_type="PayByTraffic",
            instance_charge_type="PostPaid",
            user_data=user_data_b64,
            system_disk=system_disk_config,
            security_group_id=sg_id,
            v_switch_id=vswitch_id
        )
        
        create_lt_resp = client.create_launch_template(create_lt_req)
        template_id = create_lt_resp.body.launch_template_id
        print(f"   🎉 [启动模板] 成功: {template_id}")
        
        # 存入数据库
        save_template_to_db(account_id, region_id, template_id)
        return True

    except Exception as e:
        print(f"   ❌ [账号: {alias}] 地域 {region_id} 初始化失败: {str(e)}")
        return False

# 🛠️ 数据库持久化逻辑
def save_template_to_db(account_id, region_id, template_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS launch_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            region_id TEXT,
            template_id TEXT,
            UNIQUE(account_id, region_id)
        )
    """)
    cursor.execute("""
        INSERT INTO launch_templates (account_id, region_id, template_id)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id, region_id) DO UPDATE SET template_id=excluded.template_id
    """, (account_id, region_id, template_id))
    conn.commit()
    conn.close()
    print(f"   💾 模板 ID [{template_id}] 已安全入库！")

# 🛠️ 批量执行框架
def run_all():
    print("====== 开始多账号全局自动化部署基建 ======")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 确保基础表存在
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                region_id TEXT,
                template_id TEXT,
                UNIQUE(account_id, region_id)
            )
        """)
        cursor.execute("SELECT id, alias, access_key, access_secret FROM cloud_accounts WHERE is_active = 1")
        accounts = cursor.fetchall()
    except Exception as e:
        print(f"❌ 无法读取账号数据库: {e}")
        return False, f"数据库读取失败: {e}"
    
    if not accounts:
        return False, "未找到任何激活的云账号，请先在 Telegram 里添加账号。"
    
    success_logs = []
    failed_logs = []
    skipped_count = 0
    
    for account in accounts:
        acc_id, acc_alias, acc_ak, acc_sk = account
        acc_ak = str(acc_ak).strip()
        acc_sk = str(acc_sk).strip()
        
        print(f"\n==================================================")
        print(f" 🏢 正在扫描账号: {acc_alias} (ID: {acc_id})")
        
        for region in TARGET_REGIONS:
            cursor.execute("SELECT template_id FROM launch_templates WHERE account_id = ? AND region_id = ?", (acc_id, region))
            existing = cursor.fetchone()
            
            if existing and existing[0]:
                print(f"   ⏭️ 地域 {region} 已存在模板 [{existing[0]}]，自动跳过。")
                skipped_count += 1
                continue
            
            is_success = init_region_for_account(acc_id, acc_alias, acc_ak, acc_sk, region)
            if is_success:
                success_logs.append(f"✅ [{acc_alias}] - {region}")
            else:
                failed_logs.append(f"❌ [{acc_alias}] - {region}")
                
            time.sleep(5) # 避开阿里云风控流控
            
    conn.close()
    
    if not success_logs and not failed_logs:
        return True, "✅ 扫描完毕。所有账号和地域均已存在完美配置，无需重复开荒！"
    
    report = ""
    if success_logs:
        report += f"【🎉 成功开荒】({len(success_logs)}个)\n" + "\n".join(success_logs) + "\n\n"
    if failed_logs:
        report += f"【⚠️ 遭遇失败】({len(failed_logs)}个 - 请看服务器后台日志)\n" + "\n".join(failed_logs)
        
    return True, f"🚀 基建巡检完毕！\n\n{report}"

if __name__ == "__main__":
    run_all()
