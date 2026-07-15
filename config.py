import os
import logging
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ==========================================
# 日志配置 (全局统一 Logging)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MG_Bot")

# ==========================================
# 读取基础配置 (包含容错处理)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 安全读取 ADMIN_ID，避免 .env 中留空导致项目启动崩溃
_admin_id = os.getenv("ADMIN_ID", "0")
ADMIN_ID = int(_admin_id) if _admin_id.strip().lstrip('-').isdigit() else 0

if ADMIN_ID == 0:
    logger.warning("未检测到有效的 ADMIN_ID 配置，请确认 .env 文件设置。")

# ==========================================
# 邮件服务配置
# ==========================================
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RECIPIENT = os.getenv("RECIPIENT")

# ==========================================
# 阿里云 ECS 核心凭证
# ==========================================
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET")

# 以后这里还可以加上数据库路径等其他配置...
# DB_PATH = os.getenv("DB_PATH", "data/mg_bot.db")
