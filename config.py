import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 读取基础配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RECIPIENT = os.getenv("RECIPIENT")

ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")

