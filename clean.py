import sqlite3
import db  # 确保能读到你配置的 db.DB_PATH

print("开始清理幽灵账单...")
conn = sqlite3.connect(db.DB_PATH)
cursor = conn.cursor()

# 直接清空业务表里所有的机器记录（反正你现在阿里云里也是 0 台）
cursor.execute("DELETE FROM ecs_business")
conn.commit()
conn.close()

print("✅ 幽灵数据清理完成！")
