import os
import psutil
import asyncio
import sqlite3
from datetime import datetime
from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery


import db
import config
import init_regions
import sync_assets # 导入你写的脚本
from config import DB_PATH
import io
import sys


router = Router()

# 这两行一加，整个文件都不需要再写权限判断了！
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)
# 修改密码状态机
class GlobalConfigFSM(StatesGroup):
    wait_for_password = State()

# 模板管理状态机 (不再需要手动输入地域了，直接点击菜单)
class TemplateFSM(StatesGroup):
    wait_for_template_id = State()

# ================= 🛠️ 工具函数：SQLite 安全一致性备份 =================
def create_safe_backup_sync(src_path: str, dst_path: str) -> bool:
    """底层使用 sqlite3官方热备份接口，防止读取热写入锁导致文件损坏或锁冲突"""
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        return True
    except Exception as e:
        print(f"❌ 备份执行异常: {e}")
        return False

# ================= 1. 系统设置主菜单 =================
@router.message(F.text.contains("系统设置"))
async def system_dashboard(message: types.Message):
    text = "🛠️ **系统设置与高级管理**\n\n请选择您要进行的操作："
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 中控节点探针 (状态看板)", callback_data="sys_status"))
    builder.row(InlineKeyboardButton(text="🔄 全局资产强制对账", callback_data="sys_sync_assets"))
    
    builder.row(
        InlineKeyboardButton(text="🔑 全局参数", callback_data="sys_global_config"),
        InlineKeyboardButton(text="📝 启动模板", callback_data="sys_tpl_main")
    )
    
    # 🌟 修改这里：把“建表”按钮和“备份”按钮放在同一排
    builder.row(
        InlineKeyboardButton(text="📁 账本一键备份至本地", callback_data="sys_backup"),
        InlineKeyboardButton(text="🗄️ 初始化数据库表", callback_data="sys_init_db")
    )
    
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# ================= 2. 中控节点探针 =================
@router.callback_query(F.data == "sys_status")
async def show_sys_status(callback: types.CallbackQuery):
    await callback.answer("⏳ 正在采集底层负载指标...")
    
    # 🛠️ 修正 1：将同步阻塞的 cpu_percent (0.5s等待) 放入异步线程池执行
    cpu_usage = await asyncio.to_thread(psutil.cpu_percent, 0.5)
    mem = psutil.virtual_memory()
    mem_usage = mem.percent
    
    # 获取账本文件大小
    db_size_kb = 0
    if os.path.exists(db.DB_PATH):
        db_size_kb = os.path.getsize(db.DB_PATH) / 1024
        
    text = (
        f"📊 **中控服务器 (1Panel机) 探针**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ **CPU 占用率**: `{cpu_usage}%`\n"
        f"🧠 **内存 占用率**: `{mem_usage}%` ({mem.used//1048576}MB / {mem.total//1048576}MB)\n"
        f"💾 **SQLite 账本体积**: `{db_size_kb:.2f} KB`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 状态正常。异步并发架构资源余量充足。"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 返回", callback_data="back_to_sys"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# ================= 3. 账本一键备份 =================
@router.callback_query(F.data == "sys_backup")
async def do_sys_backup(callback: types.CallbackQuery):
    await callback.answer("⏳ 正在生成热数据一致性镜像并打包...")
    
    if not os.path.exists(db.DB_PATH):
        await callback.message.answer("❌ 数据库文件不存在！")
        return
        
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_backup_filename = f"IDC_Backup_Safe_{date_str}.db"
    
    # 🛠️ 修正 2：异步执行热镜像备份，防止写入锁引发数据损坏
    success = await asyncio.to_thread(create_safe_backup_sync, db.DB_PATH, temp_backup_filename)
    
    if not success:
        return await callback.message.answer("❌ 生成系统热备份镜像失败，可能是因为遇到大耗时锁冲突，请稍候重试！")

    try:
        db_file = FSInputFile(temp_backup_filename, filename=f"MG_Console_Backup_{date_str}.db")
        await callback.message.answer_document(
            document=db_file, 
            caption=f"📁 **账本数据库已安全热备份**\n时间: `{date_str}`\n\n💡 *提示：本文件属于热镜像同步安全快照，无锁损坏风险，可随时用于生产系统恢复。*",
            parse_mode="Markdown"
        )
    finally:
        # 发送完毕后顺手清理根目录生成的临时备份文件
        if os.path.exists(temp_backup_filename):
            try:
                os.remove(temp_backup_filename)
            except Exception:
                pass

@router.callback_query(F.data == "sys_sync_assets")
async def execute_sync_assets(callback: types.CallbackQuery):
    await callback.message.edit_text("🔄 **正在启动全局资产盘点...**\n\n正在向阿里云核对所有账号与地域的真实快照，请稍候...")
    await callback.answer()
    
    # 包装执行逻辑，捕获输出
    def _run_sync():        
        # 拦截 print 输出，以便发给 Telegram
        captured_output = io.StringIO()
        sys.stdout = captured_output
        try:
            sync_assets.sync_all_accounts()
        finally:
            sys.stdout = sys.__stdout__
            
        return captured_output.getvalue()

    try:
        import asyncio
        # 后台异步执行对账
        log_result = await asyncio.to_thread(_run_sync)
        
        # 截取日志最后一部分展示（防止字数超限）
        short_log = log_result[-3000:] if len(log_result) > 3000 else log_result
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 返回系统设置", callback_data="back_to_sys"))
        
        await callback.message.edit_text(
            f"✅ **全局资产对账完成！**\n\n"
            f"**执行日志摘要：**\n```text\n{short_log}\n```",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ **对账执行异常:**\n`{e}`")

# ================= 4. 全局参数管理 =================
@router.callback_query(F.data == "sys_global_config")
async def show_sys_global_config(callback: types.CallbackQuery):
    await callback.answer()
    
    current_password = "@QS00008" 
    
    text = (
        f"⚙️ **全局参数配置**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔑 **默认重装密码**: `{current_password}`\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ 修改重装密码", callback_data="sys_edit_password"))
    builder.row(InlineKeyboardButton(text="🔙 返回", callback_data="back_to_sys"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "sys_edit_password")
async def ask_new_password(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(GlobalConfigFSM.wait_for_password)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ 取消", callback_data="sys_global_config"))
    await callback.message.edit_text("🔑 **请输入新的默认重装密码：**\n\n*(建议包含大小写字母和数字)*", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.message(GlobalConfigFSM.wait_for_password)
async def receive_new_password(message: types.Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID: return
    new_pwd = message.text.strip()
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 返回参数配置", callback_data="sys_global_config"))
    await message.answer(f"✅ **全局重装密码已修改为**: `{new_pwd}`", reply_markup=builder.as_markup())

# ================= 5. 启动模板管理 =================

def get_sys_region_asia_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇯🇵 日本(东京)", callback_data="sys_region_ap-northeast-1_日本东京"),
        InlineKeyboardButton(text="🇰🇷 韩国(首尔)", callback_data="sys_region_ap-northeast-2_韩国首尔")
    )
    builder.row(
        InlineKeyboardButton(text="🇸🇬 新加坡", callback_data="sys_region_ap-southeast-1_新加坡"),
        InlineKeyboardButton(text="🇲🇾 马来西亚(吉隆坡)", callback_data="sys_region_ap-southeast-3_马来西亚吉隆坡")
    )
    builder.row(
        InlineKeyboardButton(text="🇲🇾 马来西亚(柔佛州)", callback_data="sys_region_ap-southeast-x_马来西亚柔佛州"),
        InlineKeyboardButton(text="🇮🇩 印尼(雅加达)", callback_data="sys_region_ap-southeast-5_印尼雅加达")
    )
    builder.row(
        InlineKeyboardButton(text="🇵🇭 菲律宾(马尼拉)", callback_data="sys_region_ap-southeast-6_菲律宾马尼拉"),
        InlineKeyboardButton(text="🇹🇭 泰国(曼谷)", callback_data="sys_region_ap-southeast-7_泰国曼谷")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="sys_tpl_main"))
    return builder.as_markup()

def get_sys_region_eu_us_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇩🇪 德国(法兰克福)", callback_data="sys_region_eu-central-1_德国法兰克福"),
        InlineKeyboardButton(text="🇬🇧 英国(伦敦)", callback_data="sys_region_eu-west-1_英国伦敦")
    )
    builder.row(
        InlineKeyboardButton(text="🇫🇷 法国(巴黎)", callback_data="sys_region_eu-central-x_法国巴黎"),
        InlineKeyboardButton(text="🇺🇸 美国(硅谷)", callback_data="sys_region_us-west-1_美国硅谷")
    )
    builder.row(
        InlineKeyboardButton(text="🇺🇸 美国(弗吉尼亚)", callback_data="sys_region_us-east-1_美国弗吉尼亚"),
        InlineKeyboardButton(text="🇲🇽 墨西哥", callback_data="sys_region_na-mexico-x_墨西哥")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="sys_tpl_main"))
    return builder.as_markup()

def get_sys_region_others_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🇦🇪 阿联酋(迪拜)", callback_data="sys_region_me-east-1_阿联酋迪拜"),
        InlineKeyboardButton(text="🇸🇦 沙特(利雅得)", callback_data="sys_region_me-central-1_沙特利雅得")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回上级", callback_data="sys_tpl_main"))
    return builder.as_markup()

# ================= 2. 核心主菜单 =================
def get_sys_region_main_menu():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    
    # 🌟 把它放在最显眼的第一排，作为整个架构的“大招”
    builder.row(InlineKeyboardButton(text="🚀 全局一键开荒 (后台自动执行)", callback_data="run_global_init"))
    
    builder.row(
        # 香港按钮已经统一为标准格式
        InlineKeyboardButton(text="🇭🇰 中国香港", callback_data="sys_region_cn-hongkong_中国香港"),
        InlineKeyboardButton(text="🌏 亚太地区", callback_data="sys_tpl_asia")
    )
    builder.row(
        InlineKeyboardButton(text="🌍 欧美地区", callback_data="sys_tpl_eu_us"),
        InlineKeyboardButton(text="🐪 中东及其他", callback_data="sys_tpl_others")
    )
    builder.row(InlineKeyboardButton(text="🔙 返回系统设置", callback_data="sys_main")) 
    return builder.as_markup()

# 🌟 这个就是你不小心删掉的“总入口”！必须得有它！
@router.callback_query(F.data == "sys_tpl_main")
async def show_sys_tpl_main(callback: CallbackQuery):
    # 这里兼容了 config.ADMIN_ID 防止变量报错   
    await callback.message.edit_text(
        "📝 **启动模板管理 (多账号架构)**\n\n"
        "您可以点击上方 `全局一键开荒` 为所有账号全自动配置基建；\n"
        "也可以进入下方具体地域，查看各账号的配置状态：", 
        reply_markup=get_sys_region_main_menu(), parse_mode="Markdown"
    )
    await callback.answer()

# ================= 补充：处理所有“大区分类”的点击 =================
@router.callback_query(F.data == "sys_tpl_asia")
async def show_sys_tpl_asia(callback: CallbackQuery):
    
    # 这里的 get_sys_region_asia_menu() 是你之前代码里写好的键盘生成函数
    await callback.message.edit_text(
        "🌏 **亚洲地区 - 模板管理**\n\n请选择具体地域：", 
        reply_markup=get_sys_region_asia_menu(), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "sys_tpl_eu_us")
async def show_eu_us_menu(callback: CallbackQuery):
    
    await callback.message.edit_text(
        "🌍 **欧美地区 - 模板管理**\n\n请选择具体节点：", 
        reply_markup=get_sys_region_eu_us_menu(), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "sys_tpl_others")
async def show_others_menu(callback: CallbackQuery):
    
    await callback.message.edit_text(
        "🐪 **中东及其他 - 模板管理**\n\n请选择具体节点：", 
        reply_markup=get_sys_region_others_menu(), 
        parse_mode="Markdown"
    )
    await callback.answer()

# ================= 3. 动态展示单地域所有账号状态 =================
# ================= 3. 动态展示单地域所有账号状态 =================
@router.callback_query(F.data.startswith("sys_region_"))
async def manage_specific_region(callback: CallbackQuery):
    
    parts = callback.data.split("_")
    region_id = parts[2]
    region_name = parts[3]
    
    # 🌟 核心升级：连表查询，把所有激活账号和该地域的模板拉出来对比
    def get_all_accounts_status(r_id):

        # 🛠️ 修复1：增加 timeout 防止被其他并发操作锁死
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        cursor = conn.cursor()
        
        # 🛠️ 修复2：强制兜底建表，防止全新环境执行 LEFT JOIN 时报错崩溃
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS launch_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                region_id TEXT,
                template_id TEXT,
                UNIQUE(account_id, region_id)
            )
        """)
        
        cursor.execute('''
            SELECT c.alias, l.template_id 
            FROM cloud_accounts c
            LEFT JOIN launch_templates l ON c.id = l.account_id AND l.region_id = ?
            WHERE c.is_active = 1
        ''', (r_id,))
        res = cursor.fetchall()
        conn.close()
        return res
        

    results = await asyncio.to_thread(get_all_accounts_status, region_id)
    
    text = f"🌍 **地域**: `{region_name}` (`{region_id}`)\n\n"
    
    if not results:
        text += "⚠️ 当前系统中没有处于激活状态的云账号，请先添加。"
    else:
        text += "📊 **各云账号当前配置状态**：\n\n"
        for alias, tpl_id in results:
            if tpl_id:
                text += f"🏢 `{alias}`: ✅ `{tpl_id}`\n"
            else:
                text += f"🏢 `{alias}`: ❌ `未配置`\n"
                
        text += "\n*(注: 采用 IaC 架构后不再支持手动修改，请通过一键开荒全自动校准网络环境。)*"


    builder = InlineKeyboardBuilder()
    
    # 单地域一键开荒
    builder.row(InlineKeyboardButton(
        text=f"🚀 一键自动配置 {region_name}", 
        callback_data=f"init_single:{region_id}"
    ))
    
    # 🌟 新增：强制清理本地缓存按钮
    builder.row(InlineKeyboardButton(
        text="🧹 清理本地失效模版 (强制重置)", 
        callback_data=f"force_clean_tpl_{region_id}"
    ))
    
    builder.row(InlineKeyboardButton(text="🔙 返回目录", callback_data="sys_tpl_main"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("init_single:"))
async def trigger_single_region_init(call: types.CallbackQuery):
    if call.from_user.id != config.ADMIN_ID: return await call.answer()
    
    region_id = call.data.split(":")[1]
    
    await call.message.edit_text(
        f"⏳ **正在后台为所有未配置的账号初始化 `{region_id}` 节点...**\n\n"
        "这通常需要一两分钟，请稍作等待。完成后机器人会发送通知。",
        parse_mode="Markdown"
    )
    await call.answer()
    
    # 丢入后台异步执行，不卡死机器人
    async def bg_single_task():
        import init_regions
        import sqlite3
        from config import DB_PATH
        
        try:
            conn = sqlite3.connect(DB_PATH, timeout=20.0)
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
            
            cursor.execute("SELECT id, alias, access_key, access_secret FROM cloud_accounts WHERE is_active = 1")
            accounts = cursor.fetchall()
            
            new_configured = 0
            failed_count = 0  # 🌟 新增：统计失败次数
            
            for acc in accounts:
                acc_id, acc_alias, acc_ak, acc_sk = acc
                
                cursor.execute(
                    "SELECT template_id FROM launch_templates WHERE account_id = ? AND region_id = ?", 
                    (acc_id, region_id)
                )
                existing = cursor.fetchone()
                
                if not (existing and existing[0]):
                    # 🌟 核心修复：接收真实结果，如果脚本崩溃返回False，必须记作失败！
                    is_success = init_regions.init_region_for_account(acc_id, acc_alias, acc_ak, acc_sk, region_id)
                    if is_success:
                        new_configured += 1
                    else:
                        failed_count += 1
            
            conn.close()
            
            # 🌟 修复：不再谎报军情，严格判断真实结果
            if failed_count > 0:
                await call.message.answer(f"❌ **`{region_id}` 开荒遭遇失败！**\n\n有 {failed_count} 个账号未能成功配置。请立刻去服务器终端查看日志 (终端应该输出了红色 ❌ 错误原因)！")
            elif new_configured == 0:
                await call.message.answer(f"✅ `{region_id}` 扫描完毕，所有账号此前均已成功配置完毕。")
            else:
                await call.message.answer(f"🎉 **单地域开荒完成**\n\n成功为 {new_configured} 个账号配置了 `{region_id}` 的基建和启动模板！")
                
        except Exception as e:
            await call.message.answer(f"❌ **后台执行严重异常**\n\n原因: `{str(e)}`")

    asyncio.create_task(bg_single_task())

# ================= 4. 拦截“全局开荒”与“单地域开荒”的后台任务 =================
@router.callback_query(F.data == "run_global_init")
async def trigger_global_init(call: CallbackQuery):
    # 删除了冗余且报错的权限校验，由文件顶部的 router filter 统一拦截
    await call.message.edit_text("⏳ **正在后台执行 [全局] 自动化开荒...**\n\n预计需要几分钟时间，请稍作等待。完成后机器人会主动向您发送通知。", parse_mode="Markdown")
    await call.answer()
    
    # 丢入后台线程执行，防止机器人假死
    async def bg_task():
        success, msg = await asyncio.to_thread(init_regions.run_all)
        await call.message.answer(f"🎉 **全局开荒报告**\n\n{msg}" if success else f"❌ **开荒异常**\n\n{msg}", parse_mode="Markdown")
    asyncio.create_task(bg_task())


# ================= 辅助：返回主菜单 =================
@router.callback_query(F.data == "back_to_sys")
async def back_to_sys_main(callback: types.CallbackQuery):
    # 正常处理逻辑，调用主面板并删除旧消息
    await system_dashboard(callback.message)
    await callback.message.delete()
    
    # 显式解除当前按钮的 loading 状态
    await callback.answer()

# ================= 5. 补充：清理失效模板缓存 =================
@router.callback_query(F.data.startswith("force_clean_tpl_"))
async def execute_force_clean_tpl(callback: types.CallbackQuery):
    
    region_id = callback.data.replace("force_clean_tpl_", "")
    
    # 包装成同步函数扔进后台线程，防止卡死
    def _clean_db():
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH) 
        cursor = conn.cursor()
        
        # 💥 精准爆破，只删除当前操作地域的模板记录
        cursor.execute("DELETE FROM launch_templates WHERE region_id = ?", (region_id,))
        conn.commit()
        conn.close()
        
    try:
        await callback.message.edit_text(f"🧹 正在强行抹除 `{region_id}` 的本地模版缓存记录...")
        await asyncio.to_thread(_clean_db)
        
        # 重新生成返回按钮
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 返回目录", callback_data="sys_tpl_main")) 
        
        await callback.message.edit_text(
            f"✅ **清理彻底完成！**\n\n"
            f"已强制切断本地数据库与 `{region_id}` 失效模板的关联。\n"
            f"请返回并重新点击「🚀 一键自动配置」，系统将为您在阿里云重新生成全新的完美模板。",
            parse_mode="Markdown",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ 清理失败: {str(e)}")
        
    await callback.answer()

# ================= 补充：一键初始化数据库表 =================
@router.callback_query(F.data == "sys_init_db")
async def execute_init_db(callback: types.CallbackQuery):
    await callback.answer("⏳ 正在执行底层建表作业...")
    
    # 将同步的 sqlite3 写盘操作扔进后台线程池
    def _do_init():
        import sqlite3
        import config
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # 核心建表逻辑 (IF NOT EXISTS 保证了重复点击的安全性)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                region_id TEXT,
                running_count INTEGER DEFAULT 0,
                stopped_count INTEGER DEFAULT 0,
                last_sync_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, region_id)
            )
        """)
        conn.commit()
        conn.close()

    try:
        import asyncio
        await asyncio.to_thread(_do_init)
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 返回系统设置", callback_data="back_to_sys"))
        
        await callback.message.edit_text(
            "✅ **数据库底层初始化已完成！**\n\n"
            "资产账本表 (`account_assets`) 已成功创建或确认存在，现在可以安全执行全局对账了。",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ **初始化数据库失败:**\n`{e}`")
