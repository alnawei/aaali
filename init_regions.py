import os
import time
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_vpc20160428.client import Client as VpcClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_vpc20160428 import models as vpc_models

# ================= 配置区 =================
ACCESS_KEY_ID = "替换为你的 AccessKey ID"
ACCESS_KEY_SECRET = "替换为你的 AccessKey Secret"

# 你想要一键开荒的地域列表 (可根据需要增删)
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

def create_client(client_class, region_id):
    """初始化阿里云 API 客户端"""
    config = open_api_models.Config(
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET,
        region_id=region_id
    )
    # 自动适配端点 (Endpoint)
    if client_class == EcsClient:
        config.endpoint = f"ecs.{region_id}.aliyuncs.com"
    else:
        config.endpoint = f"vpc.{region_id}.aliyuncs.com"
    return client_class(config)

def init_region(region_id):
    print(f"\n🚀 开始初始化地域: {region_id}")
    ecs_client = create_client(EcsClient, region_id)
    vpc_client = create_client(VpcClient, region_id)

    try:
        # 1. 创建 VPC (192.168.0.0/16)
        print("  ⏳ 正在创建 VPC...")
        vpc_request = vpc_models.CreateVpcRequest(
            cidr_block="192.168.0.0/16",
            vpc_name="Node-VPC-Auto"
        )
        vpc_response = vpc_client.create_vpc(vpc_request)
        vpc_id = vpc_response.body.vpc_id
        print(f"  ✅ VPC 创建成功: {vpc_id}")
        
        # 阿里云创建资源需要几秒钟生效，稍微等待
        time.sleep(3)

        # 2. 查询可用区并创建交换机 (192.168.0.0/24)
        print("  ⏳ 正在查询可用区并创建交换机...")
        zones_request = ecs_models.DescribeZonesRequest()
        zones_response = ecs_client.describe_zones(zones_request)
        zone_id = zones_response.body.zones.zone[0].zone_id # 默认取第一个可用区
        
        vswitch_request = vpc_models.CreateVSwitchRequest(
            vpc_id=vpc_id,
            zone_id=zone_id,
            cidr_block="192.168.0.0/24",
            v_switch_name=f"Node-VSwitch-{zone_id}"
        )
        vswitch_response = vpc_client.create_vswitch(vswitch_request)
        vswitch_id = vswitch_response.body.v_switch_id
        print(f"  ✅ 交换机创建成功: {vswitch_id} (可用区: {zone_id})")
        time.sleep(3)

        # 3. 创建安全组
        print("  ⏳ 正在创建安全组并配置全放行规则...")
        sg_request = ecs_models.CreateSecurityGroupRequest(
            vpc_id=vpc_id,
            security_group_name="Node-SG-AllOpen",
            description="Automated Security Group with all ports open"
        )
        sg_response = ecs_client.create_security_group(sg_request)
        sg_id = sg_response.body.security_group_id
        
        # 配置安全组规则 (所有协议，所有端口，0.0.0.0/0)
        sg_rule_request = ecs_models.AuthorizeSecurityGroupRequest(
            security_group_id=sg_id,
            ip_protocol="all",
            port_range="-1/-1",
            source_cidr_ip="0.0.0.0/0"
        )
        ecs_client.authorize_security_group(sg_rule_request)
        print(f"  ✅ 安全组创建并放行成功: {sg_id}")
        time.sleep(3)

        # 4. 创建启动模板 (组合上述资源)
        print("  ⏳ 正在生成 ECS 启动模板...")
        # 配置模板的网络接口属性
        network_interface = ecs_models.CreateLaunchTemplateRequestTemplateResourceNetworkInterfaces(
            v_switch_id=vswitch_id,
            security_group_id=sg_id
        )
        
        template_request = ecs_models.CreateLaunchTemplateRequest(
            launch_template_name="Node-Auto-Template",
            version_description="V1.0 - Auto Initialized",
            template_resource=ecs_models.CreateLaunchTemplateRequestTemplateResource(
                security_group_id=sg_id,
                v_switch_id=vswitch_id,
                network_interfaces=[network_interface],
                # 如果你有固定的镜像ID(如Ubuntu 22.04)或实例规格(如ecs.e-c1m1.large)，也可以在这里写死
                # image_id="ubuntu_22_04_x64_20G_alibase_20240926.vhd", 
                # instance_type="ecs.e-c1m1.large"
            )
        )
        template_response = ecs_client.create_launch_template(template_request)
        template_id = template_response.body.launch_template_id
        print(f"  🎉 启动模板创建完成: {template_id}")

    except Exception as e:
        print(f"  ❌ 地域 {region_id} 初始化失败: {str(e)}")

if __name__ == "__main__":
    print("开始全局自动化部署基建...")
    for region in TARGET_REGIONS:
        init_region(region)
        time.sleep(5) # 跨地域操作间隔，避免触发 API 限流
    print("\n✅ 所有指定地域的基础设施部署完毕！")
