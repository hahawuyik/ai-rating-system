import streamlit as st
import pandas as pd
import os
import sqlite3
import cloudinary
import cloudinary.api
from cloudinary.utils import cloudinary_url
from datetime import datetime
import time
import uuid
import json

# 🔥 1. 页面配置
st.set_page_config(
    page_title="AI游戏美术评分系统 Ultimate",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== Cloudinary 配置 =====
cloudinary.config(
    cloud_name="dwskobcad",
    api_key="676912851999589",
    api_secret="YIY48Z9VOM1zHfPWZvFKlHpyXzk",
    secure=True
)
CLOUDINARY_ROOT_FOLDER = "ai-rating-images"

# ===== 路径配置 (修正版) =====
# 检查是否在 Streamlit Cloud 云端运行
if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    # ☁️ 云端环境：直接存在当前目录下的 data 文件夹里
    DATASET_ROOT = "data_folder"
else:
    # 💻 本地环境：存到你的 D 盘
    DATASET_ROOT = "D:/ai_dataset_project"

# 自动创建路径
OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

# 📍 指定本地 Prompt 文件路径
LOCAL_PROMPT_JSON = os.path.join(METADATA_DIR, "final_prompts_translated.json")

for p in [OUTPUT_DIR, METADATA_DIR]:
    os.makedirs(p, exist_ok=True)

# ===== 🧠 用户ID管理 =====
def get_user_id():
    query_params = st.query_params
    if "user" in query_params:
        return query_params["user"]
    if "user_id" not in st.session_state:
        new_id = f"user_{uuid.uuid4().hex[:6]}"
        st.session_state.user_id = new_id
        st.query_params["user"] = new_id
        return new_id
    return st.session_state.user_id

# ===== 💾 数据库结构 =====
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT, model_id TEXT, image_number INTEGER, filepath TEXT UNIQUE,
            prompt_text TEXT, type TEXT, style TEXT, model_name TEXT, quality_tier TEXT, generation_time TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER, evaluator_id TEXT,
            clarity INTEGER, detail_richness INTEGER, color_harmony INTEGER, prompt_adherence INTEGER,
            perspective_check INTEGER, asset_cleanliness INTEGER, style_consistency INTEGER, structural_logic INTEGER,
            overall_quality INTEGER, is_usable TEXT, notes TEXT, evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== ⚡ 自动加载本地 Prompt (核心修改) =====
def auto_load_local_prompts():
    """
    启动时自动检查本地有没有JSON文件，如果有，且数据库里的Prompt是空的，就自动填进去。
    """
    if not os.path.exists(LOCAL_PROMPT_JSON):
        return # 文件不存在就不做操作

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查一下数据库里是否已经有Prompt了（避免每次刷新都重新写数据库，浪费性能）
    # 我们随机检查 10 条数据，如果它们都有 Prompt，就假设已经加载过了
    try:
        cursor.execute("SELECT COUNT(*) FROM images WHERE prompt_text IS NOT NULL AND prompt_text != ''")
        filled_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM images")
        total_count = cursor.fetchone()[0]
        
        # 如果填充率超过 95%，就不再加载了
        if total_count > 0 and (filled_count / total_count > 0.95):
            conn.close()
            return 
    except:
        pass

    # 开始加载
    try:
        with open(LOCAL_PROMPT_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, dict):
            # 开启事务加速
            cursor.execute("BEGIN TRANSACTION")
            for key, value in data.items():
                prompt_text = value if isinstance(value, str) else str(value)
                cursor.execute("UPDATE images SET prompt_text = ? WHERE filepath LIKE ?", 
                               (prompt_text, f"%{key}%"))
            cursor.execute("COMMIT")
            print(f"✅ [系统自动] 已从本地文件加载 Prompt 数据")
    except Exception as e:
        print(f"❌ 自动加载 Prompt 失败: {e}")
    
    conn.close()

# ===== Cloudinary 拉取 =====
# ===== 🛡️ 安全版：加载数据 (不删除旧ID) =====
def load_images_from_cloudinary_to_db(force_refresh=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 如果不是强制刷新，且数据库里有图，就直接跳过
    if not force_refresh:
        cursor.execute("SELECT COUNT(*) FROM images")
        if cursor.fetchone()[0] > 0:
            conn.close()
            # 顺便检查一下Prompt
            auto_load_local_prompts()
            return

    placeholder = st.empty()
    placeholder.info(f"🔍 正在同步 Cloudinary 数据...")
    
    # ❌ [删除这就话] 绝对不要再清空表了！
    # if force_refresh:
    #     cursor.execute("DELETE FROM images") 
    
    try:
        subfolders_result = cloudinary.api.subfolders(CLOUDINARY_ROOT_FOLDER)
        subfolders = subfolders_result.get('folders', [])
        total_added = 0
        total_skipped = 0
        
        progress_bar = st.progress(0)
        
        for idx, folder in enumerate(subfolders):
            folder_path = folder['path']
            model_id = folder_path.split('/')[-1]
            next_cursor = None
            
            while True:
                try:
                    time.sleep(0.1) #稍微防一下限流
                    resources = cloudinary.api.resources(
                        type="upload", folders=folder_path, max_results=100,
                        next_cursor=next_cursor, resource_type="image"
                    )
                    batch = resources.get("resources", [])
                    if not batch: break
                        
                    for res in batch:
                        full_public_id = res["public_id"]
                        
                        # 🛡️ 核心修改：使用 INSERT OR IGNORE
                        # 意思：如果这个 filepath 已经在数据库里了，就什么都不做（保留旧ID）
                        # 如果不在，才插入新的。
                        actual_filename = full_public_id.split('/')[-1]
                        prompt_id = actual_filename
                        image_number = 1
                        parts = actual_filename.split('_')
                        if len(parts) > 1 and parts[-1].isdigit():
                            image_number = int(parts[-1])
                            prompt_id = "_".join(parts[:-1])
                        
                        context = res.get("context", {}).get("custom", {})
                        
                        cursor.execute('''
                            INSERT OR IGNORE INTO images (
                                prompt_id, model_id, image_number, filepath,
                                prompt_text, type, style, model_name, quality_tier, generation_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            prompt_id, model_id, image_number, full_public_id,
                            context.get("prompt", ""), 
                            context.get("type", "unknown"),
                            context.get("style", "unknown"),
                            context.get("model_name", model_id),
                            context.get("quality_tier", "medium"),
                            res.get("created_at", datetime.now().isoformat())
                        ))
                        
                        if cursor.rowcount > 0:
                            total_added += 1
                        else:
                            total_skipped += 1
                            
                    conn.commit()
                    next_cursor = resources.get("next_cursor")
                    if not next_cursor: break
                    
                except Exception as e:
                    if "420" in str(e):
                        conn.close(); placeholder.empty(); return
                    break
            progress_bar.progress((idx + 1) / len(subfolders))
            
    except Exception as e:
        st.error(f"加载出错: {e}")
    
    conn.close()
    
    # 同步完图片后，再同步Prompt
    auto_load_local_prompts()
    
    placeholder.success(f"✅ 同步完成！新增 {total_added} 张，跳过 {total_skipped} 张。")
    time.sleep(2)
    placeholder.empty()
    st.rerun()

# ===== 辅助函数 =====
def get_cloud_image_url(filepath: str) -> str:
    try:
        url, _ = cloudinary_url(filepath, width=800, crop="limit", quality="auto", fetch_format="auto", secure=True)
        return url
    except: return "https://via.placeholder.com/800x800?text=URL+Error"

def save_evaluation(image_id, user_id, scores):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT id FROM evaluations WHERE image_id=? AND evaluator_id=?", (image_id, user_id))
    exists = cursor.fetchone()
    data = (
        user_id,
        scores['clarity'], scores['detail_richness'], scores['color_harmony'], scores['prompt_adherence'],
        scores['perspective_check'], scores['asset_cleanliness'], 
        scores['style_consistency'], scores['structural_logic'],
        scores['overall_quality'], scores['is_usable'], scores['notes'],
        now
    )
    try:
        if exists:
            sql = '''UPDATE evaluations SET 
                     evaluator_id=?, clarity=?, detail_richness=?, color_harmony=?, prompt_adherence=?,
                     perspective_check=?, asset_cleanliness=?, style_consistency=?, structural_logic=?,
                     overall_quality=?, is_usable=?, notes=?, evaluation_time=?
                     WHERE id=?'''
            cursor.execute(sql, data + (exists[0],))
            msg = "🔄 更新成功"
        else:
            sql = '''INSERT INTO evaluations (
                     evaluator_id, clarity, detail_richness, color_harmony, prompt_adherence,
                     perspective_check, asset_cleanliness, style_consistency, structural_logic,
                     overall_quality, is_usable, notes, evaluation_time, image_id
                     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
            cursor.execute(sql, data + (image_id,))
            msg = "✅ 保存成功"
        conn.commit()
        st.toast(msg)
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False
    finally: conn.close()

def get_existing_score(image_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM evaluations WHERE image_id=? AND evaluator_id=?", conn, params=(image_id, user_id))
        if not df.empty: return df.iloc[0].to_dict()
    except: pass
    finally: conn.close()
    return {}

# ===== 主程序 =====
def main():
    #     # ----------------- 🚨 调试代码开始 -----------------
    # st.markdown("### 🕵️‍♂️ 数据库侦探")
    
    # # 1. 打印当前绝对路径
    # abs_db_path = os.path.abspath(DB_PATH)
    # st.error(f"📍 程序正在读取的数据库路径是：\n\n`{abs_db_path}`")
    
    # # 2. 检查文件是否存在
    # if os.path.exists(abs_db_path):
    #     st.warning("⚠️ 发现数据库文件存在！(这就是导致报错的旧文件)")
        
    #     # 3. 提供核按钮
    #     if st.button("💣 点击这里：强制粉碎这个数据库文件！", type="primary"):
    #         try:
    #             # 强制断开所有连接
    #             sqlite3.connect(abs_db_path).close()
    #             # 删除文件
    #             os.remove(abs_db_path)
    #             st.success("✅ 删除成功！请立即刷新网页 (按 F5)")
    #             time.sleep(2)
    #             st.rerun()
    #         except Exception as e:
    #             st.error(f"删除失败，可能是文件被占用: {e}")
    # else:
    #     st.success("✅ 这里的数据库文件已被删除。程序正在准备重新创建...")
    
    # st.markdown("---")
    # # ----------------- 🚨 调试代码结束 -----------------
    load_images_from_cloudinary_to_db(force_refresh=False)
    
    current_user = get_user_id()

    with st.sidebar:
        st.title("👤 评分系统 Pro")
        st.info(f"ID: **{current_user}**")
        st.caption("保留浏览器地址栏链接以保存进度。")
        
        with st.expander("🔐 找回之前的进度"):
            input_id = st.text_input("输入旧ID", key="restore_id_input")
            if st.button("恢复"):
                if input_id: st.query_params["user"]=input_id.strip(); st.session_state.user_id=input_id.strip(); st.rerun()
        
        st.divider()
        admin_pwd = st.text_input("管理员密码", type="password", key="admin_pwd")
        if admin_pwd == "123456":
            if st.button("⚠️ 强制重置数据库结构"): init_database(); st.success("表结构已更新")
            # 这里我把手动上传的按钮注释掉了，因为已经自动化了，不需要了
            # st.file_uploader... 

        st.divider()
        st.subheader("📊 数据导出中心")
        st.caption("点击下方按钮下载云端保存的评分数据")

        # 添加一个刷新按钮，确保读取最新数据
        if st.button("🔄 刷新并准备下载"):
            # 连接数据库
            conn = sqlite3.connect(DB_PATH)
            
            # 编写 SQL 查询：把评分表和图片信息表连起来查
            # 这样导出的表格里既有分数，也有图片文件名和模型
            sql = '''
            SELECT 
                e.id as ID,
                e.evaluator_id as 评分员,
                i.model_id as 模型类型,
                i.filepath as 图片路径,
                i.prompt_text as Prompt提示词,
                e.prompt_adherence as Prompt匹配度,
                e.overall_quality as 整体评分,
                e.clarity as 清晰度,
                e.detail_richness as 细节,
                e.color_harmony as 色彩,
                e.perspective_check as 透视,
                e.asset_cleanliness as 资产干净度,
                e.style_consistency as 风格一致性,
                e.structural_logic as 结构,
                e.is_usable as 是否可用,
                e.notes as 备注,
                e.evaluation_time as 提交时间
            FROM evaluations e
            LEFT JOIN images i ON e.image_id = i.id
            ORDER BY e.evaluation_time DESC
            '''
            
            try:
                # 使用 pandas 读取数据
                df_export = pd.read_sql(sql, conn)
                conn.close()

                if not df_export.empty:
                    st.success(f"✅ 成功读取 {len(df_export)} 条记录")
                    
                    # 1. 简单预览前3条
                    with st.expander("👀 预览数据 (前3条)"):
                        st.dataframe(df_export.head(3))

                    # 2. 生成 CSV 文件
                    # ⚠️ 关键：使用 utf-8-sig 编码，否则 Excel 打开中文会乱码
                    csv_data = df_export.to_csv(index=False).encode('utf-8-sig')
                    
                    # 3. 显示下载按钮
                    st.download_button(
                        label="📥 点击下载 CSV 表格 (Excel可直接打开)",
                        data=csv_data,
                        file_name=f"Rating_Data_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv",
                        type="primary" # 让按钮显眼一点
                    )
                else:
                    st.warning("📭 数据库里暂时还没有评分数据。")
                    
            except Exception as e:
                st.error(f"读取数据失败: {e}")

        # st.divider()
        # st.subheader("🛠️ Prompt 修复工具")
        # st.caption("如果自动加载失败，请手动上传 JSON 文件：")
        
        # # 📂 手动上传入口
        # uploaded_prompt_file = st.file_uploader("上传 final_prompts_translated.json", type="json")
        
        # if uploaded_prompt_file is not None:
        #     if st.button("▶️ 开始匹配并导入 Prompt"):
        #         try:
        #             # 读取上传的 JSON
        #             data = json.load(uploaded_prompt_file)
        #             st.info(f"文件包含 {len(data)} 条数据，开始匹配数据库...")
                    
        #             conn = sqlite3.connect(DB_PATH)
        #             cursor = conn.cursor()
                    
        #             # 开启事务加速
        #             cursor.execute("BEGIN TRANSACTION")
        #             updated_count = 0
                    
        #             # 进度条
        #             prog = st.progress(0)
                    
        #             for i, (key, value) in enumerate(data.items()):
        #                 # 确保 value 是字符串
        #                 p_text = value if isinstance(value, str) else str(value)
                        
        #                 # 核心匹配逻辑：文件名包含 Key 就算匹配
        #                 # 例如 Key="char_anim_01", Filepath=".../char_anim_01_dalle3..." -> 匹配成功
        #                 cursor.execute("UPDATE images SET prompt_text = ? WHERE filepath LIKE ?", 
        #                                (p_text, f"%{key}%"))
        #                 updated_count += cursor.rowcount
                        
        #                 if i % 100 == 0:
        #                     prog.progress(min((i+1)/len(data), 1.0))
                            
        #             cursor.execute("COMMIT")
        #             conn.close()
                    
        #             if updated_count > 0:
        #                 st.success(f"🎉 成功！更新了 {updated_count} 张图片的 Prompt！")
        #                 time.sleep(1)
        #                 st.rerun()
        #             else:
        #                 st.error("❌ 匹配失败：更新了 0 条数据。")
        #                 st.warning("可能原因：JSON里的 Key 和数据库里的文件名对应不上。")
        #                 st.write("JSON Key 示例:", list(data.keys())[:3])
                        
        #         except Exception as e:
        #             st.error(f"导入出错: {e}")
    

    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        try: my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", conn, params=(current_user,)).iloc[0]['cnt']
        except: my_evals = 0
    except: images_df = pd.DataFrame(); my_evals = 0
    conn.close()

    if images_df.empty: st.warning("正在初始化..."); return

    col1, col2, col3 = st.columns(3)
    col1.metric("总图片", len(images_df))
    col2.metric("我的进度", f"{my_evals}")
    col3.metric("完成率", f"{my_evals/len(images_df)*100:.1f}%")
    st.progress(my_evals/len(images_df) if len(images_df)>0 else 0)

    if 'page_number' not in st.session_state: st.session_state.page_number = 1
    total_pages = len(images_df)
    
    col_prev, col_page, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("⬅️ 上一张") and st.session_state.page_number > 1: st.session_state.page_number -= 1; st.rerun()
    with col_page:
        st.session_state.page_number = st.number_input("页码", 1, total_pages, st.session_state.page_number, label_visibility="collapsed")
    with col_next:
        if st.button("下一张 ➡️") and st.session_state.page_number < total_pages: st.session_state.page_number += 1; st.rerun()

    idx = st.session_state.page_number - 1
    if idx < len(images_df):
        row = images_df.iloc[idx]
        existing = get_existing_score(row['id'], current_user)

        st.markdown("---")
        
        # 📝 Prompt 自动显示
        if row['prompt_text']:
            st.info(f"**📝 Prompt:**\n{row['prompt_text']}")
        else:
            # 如果本地文件里没有匹配到，才会显示警告
            st.warning("⚠️ 暂无 Prompt 数据 (正在检查本地文件...)")

        col_img, col_form = st.columns([1.2, 1])
        with col_img:
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"): st.code(f"File: {row['filepath']}\nPrompt ID: {row['prompt_id']}")
                
        with col_form:
            with st.form(key=f"form_{row['id']}"):
                st.markdown("#### 🎯 核心匹配度")
                prompt_adhere = st.slider("Prompt 匹配度", 1, 5, existing.get('prompt_adherence', 3))
                
                st.markdown("#### 🛠️ 游戏工业标准")
                c1, c2 = st.columns(2)
                with c1:
                    style_const = st.slider("风格一致性", 1, 5, existing.get('style_consistency', 3))
                    perspective = st.slider("透视准确性", 1, 5, existing.get('perspective_check', 3))
                with c2:
                    asset_clean = st.slider("资产干净度", 1, 5, existing.get('asset_cleanliness', 3))
                    struct_logic = st.slider("结构合理性", 1, 5, existing.get('structural_logic', 3))

                st.markdown("#### 🎨 基础美术质量")
                c3, c4 = st.columns(2)
                with c3:
                    clarity = st.slider("清晰度", 1, 5, existing.get('clarity', 3))
                    detail = st.slider("细节丰富度", 1, 5, existing.get('detail_richness', 3))
                with c4:
                    color = st.slider("色彩和谐度", 1, 5, existing.get('color_harmony', 3))

                st.markdown("---")
                overall = st.slider("⭐ 整体评分", 1, 5, existing.get('overall_quality', 3))
                is_usable = st.radio("🎮 是否可用？", ["是", "否", "需微调"], index=["是", "否", "需微调"].index(existing.get('is_usable', '否')), horizontal=True)
                notes = st.text_area("备注", existing.get('notes', ''))
                
                if st.form_submit_button("💾 保存并下一张", type="primary", use_container_width=True):
                    scores = {
                        "clarity": clarity, "detail_richness": detail, "color_harmony": color,
                        "prompt_adherence": prompt_adhere, 
                        "perspective_check": perspective, "asset_cleanliness": asset_clean,
                        "structural_logic": struct_logic, "style_consistency": style_const,
                        "overall_quality": overall, "is_usable": is_usable, "notes": notes
                    }
                    if save_evaluation(row['id'], current_user, scores):
                        if st.session_state.page_number < total_pages: st.session_state.page_number += 1; st.rerun()

if __name__ == "__main__":
    main()












