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

# ===== 📥 本地 Prompt 导入逻辑 (补全这个函数) =====
def import_prompts_from_json(uploaded_file):
    """从本地JSON更新数据库的prompt字段"""
    try:
        # 读取上传的文件
        data = json.load(uploaded_file)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        updated_count = 0
        
        # 进度条
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        if isinstance(data, dict):
            total_items = len(data)
            # 使用事务处理加速
            cursor.execute("BEGIN TRANSACTION")
            
            for i, (key, value) in enumerate(data.items()):
                # key 是文件名核心部分 (例如 char_anim_01_dreamshaper_1)
                # value 是 prompt 文本
                prompt_text = value if isinstance(value, str) else str(value)
                
                # 模糊匹配：只要 filepath 包含 key 就算匹配
                cursor.execute("UPDATE images SET prompt_text = ? WHERE filepath LIKE ?", 
                               (prompt_text, f"%{key}%"))
                
                updated_count += cursor.rowcount
                
                # 每100条更新一次进度条
                if i % 100 == 0:
                    progress_bar.progress(min((i + 1) / total_items, 1.0))
                    status_text.text(f"正在匹配... {i+1}/{total_items}")

            cursor.execute("COMMIT")
            
        progress_bar.empty()
        status_text.empty()
        conn.close()
        return updated_count
        
    except Exception as e:
        st.error(f"解析失败: {e}")
        return 0
# ===== 主程序 =====
def main():
    # ------------------------------------------------------------------
    # 1. 🔥 第一步：无论如何，先确保数据库表结构存在！
    # (init_database 里面有 "IF NOT EXISTS"，所以重复运行也没事，很安全)
    # ------------------------------------------------------------------
    init_database()
    
    # 2. ⚡ 自动加载数据 (如果数据库是新的，这里会自动拉取)
    load_images_from_cloudinary_to_db(force_refresh=False)
    
    # 3. 获取当前用户
    current_user = get_user_id()

    # 4. 侧边栏
    with st.sidebar:
        st.title("👤 评分系统 Pro")
        st.info(f"ID: **{current_user}**")
        st.caption("请保留地址栏链接以保存进度。")
        
        # 找回进度功能
        with st.expander("🔐 找回之前的进度"):
            input_id = st.text_input("输入旧ID", key="restore_id_input")
            if st.button("恢复"):
                if input_id: 
                    st.query_params["user"] = input_id.strip()
                    st.session_state.user_id = input_id.strip()
                    st.rerun()
        
        st.divider()
        # Prompt 手动修复工具
        with st.expander("🛠️ Prompt 修复工具"):
            uploaded_prompt_file = st.file_uploader("上传 final_prompts_translated.json", type="json")
            if uploaded_prompt_file and st.button("开始导入"):
                import_prompts_from_json(uploaded_prompt_file)
                st.success("导入完成！")
                time.sleep(1)
                st.rerun()

        # 数据下载功能
        st.divider()
        st.subheader("📊 数据导出")
        if st.button("🔄 刷新并准备下载"):
            conn = sqlite3.connect(DB_PATH)
            sql = '''
            SELECT 
                e.id as ID, e.evaluator_id as 评分员, i.model_id as 模型,
                i.filepath as 路径, i.prompt_text as Prompt,
                e.prompt_adherence as Prompt匹配度, e.overall_quality as 整体评分,
                e.clarity as 清晰度, e.detail_richness as 细节, e.color_harmony as 色彩,
                e.perspective_check as 透视, e.asset_cleanliness as 资产干净度,
                e.style_consistency as 风格一致性, e.structural_logic as 结构,
                e.is_usable as 是否可用, e.notes as 备注, e.evaluation_time as 时间
            FROM evaluations e
            LEFT JOIN images i ON e.image_id = i.id
            ORDER BY e.id DESC
            '''
            try:
                df = pd.read_sql(sql, conn)
                conn.close()
                st.dataframe(df.head(3), height=100)
                st.download_button(
                    "📥 下载 CSV",
                    df.to_csv(index=False).encode('utf-8-sig'),
                    f"data_{datetime.now().strftime('%H%M')}.csv",
                    "text/csv",
                    type="primary"
                )
            except Exception as e:
                st.error(f"读取失败: {e}")

    # 5. 读取主数据
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        # 兼容性处理：防止 evaluations 表还没生成时报错
        try:
            my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", 
                               conn, params=(current_user,)).iloc[0]['cnt']
        except:
            my_evals = 0
    except:
        images_df = pd.DataFrame()
        my_evals = 0
    conn.close()

    if images_df.empty:
        st.warning("⏳ 正在初始化数据库并拉取图片，请稍候... (这可能需要1-2分钟)")
        # 这里不需要手动 return，让它自然刷新即可
        return

    # 6. 顶部进度条
    col1, col2, col3 = st.columns(3)
    col1.metric("总图片", len(images_df))
    col2.metric("我的进度", f"{my_evals}")
    col3.metric("完成率", f"{my_evals/len(images_df)*100:.1f}%")
    st.progress(my_evals/len(images_df) if len(images_df)>0 else 0)

    # 7. 分页逻辑
    if 'page_number' not in st.session_state: st.session_state.page_number = 1
    total_pages = len(images_df)
    
    col_prev, col_page, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("⬅️ 上一张") and st.session_state.page_number > 1:
            st.session_state.page_number -= 1; st.rerun()
    with col_page:
        st.session_state.page_number = st.number_input("页码", 1, total_pages, st.session_state.page_number, label_visibility="collapsed")
    with col_next:
        if st.button("下一张 ➡️") and st.session_state.page_number < total_pages:
            st.session_state.page_number += 1; st.rerun()

    # 8. 图片展示与评分表单
    idx = st.session_state.page_number - 1
    if idx < len(images_df):
        row = images_df.iloc[idx]
        existing = get_existing_score(row['id'], current_user)

        st.markdown("---")
        
        # Prompt 显示
        if row['prompt_text']:
            st.info(f"**📝 Prompt:**\n{row['prompt_text']}")
        else:
            st.warning("⚠️ 暂无 Prompt (请在侧边栏手动导入 JSON)")

        col_img, col_form = st.columns([1.2, 1])
        with col_img:
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"):
                st.code(f"File: {row['filepath']}\nID: {row['id']}")
                
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
                        if st.session_state.page_number < total_pages: 
                            st.session_state.page_number += 1
                            st.rerun()

if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()














