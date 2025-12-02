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
import socket
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

# ===== 路径配置 =====
if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    DATASET_ROOT = os.path.join(os.getcwd(), "ai_dataset_project")
else:
    DATASET_ROOT = "D:/ai_dataset_project"

OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

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

# ===== 💾 数据库结构 (新增 prompt_adherence) =====
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 图片表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT, model_id TEXT, image_number INTEGER, filepath TEXT UNIQUE,
            prompt_text TEXT, type TEXT, style TEXT, model_name TEXT, quality_tier TEXT, generation_time TEXT
        )
    ''')

    # 评分表 (新增 prompt_adherence)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            evaluator_id TEXT,
            
            -- 🎨 基础与内容
            clarity INTEGER, 
            detail_richness INTEGER, 
            color_harmony INTEGER,
            prompt_adherence INTEGER,  -- ✅ 新增：Prompt 匹配度
            
            -- 🎮 游戏工业标准
            perspective_check INTEGER, 
            asset_cleanliness INTEGER, 
            style_consistency INTEGER, 
            structural_logic INTEGER,
            
            -- 📝 结论
            overall_quality INTEGER, 
            is_usable TEXT, 
            notes TEXT,
            evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== ☁️ Cloudinary 拉取 (保持不变) =====
def load_images_from_cloudinary_to_db(force_refresh=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if not force_refresh:
        cursor.execute("SELECT COUNT(*) FROM images")
        if cursor.fetchone()[0] > 0:
            conn.close()
            return

    placeholder = st.empty()
    placeholder.info(f"🔍 正在从 Cloudinary 恢复数据列表...")
    
    if force_refresh:
        cursor.execute("DELETE FROM images")
        conn.commit()

    try:
        subfolders_result = cloudinary.api.subfolders(CLOUDINARY_ROOT_FOLDER)
        subfolders = subfolders_result.get('folders', [])
        total_loaded = 0
        progress_bar = st.progress(0)
        
        for idx, folder in enumerate(subfolders):
            folder_path = folder['path']
            model_id = folder_path.split('/')[-1]
            next_cursor = None
            while True:
                try:
                    time.sleep(0.2)
                    resources = cloudinary.api.resources(
                        type="upload", folders=folder_path, max_results=100,
                        next_cursor=next_cursor, resource_type="image"
                    )
                    batch = resources.get("resources", [])
                    if not batch: break
                    for res in batch:
                        full_public_id = res["public_id"]
                        actual_filename = full_public_id.split('/')[-1]
                        prompt_id = actual_filename
                        image_number = 1
                        parts = actual_filename.split('_')
                        if len(parts) > 1 and parts[-1].isdigit():
                            image_number = int(parts[-1])
                            prompt_id = "_".join(parts[:-1])
                        
                        # 尝试从Cloudinary Metadata获取prompt，如果没有则留空，等待本地导入
                        context = res.get("context", {}).get("custom", {})
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO images (
                                prompt_id, model_id, image_number, filepath,
                                prompt_text, type, style, model_name, quality_tier, generation_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            prompt_id, model_id, image_number, full_public_id,
                            context.get("prompt", ""), # 默认为空
                            context.get("type", "unknown"),
                            context.get("style", "unknown"),
                            context.get("model_name", model_id),
                            context.get("quality_tier", "medium"),
                            res.get("created_at", datetime.now().isoformat())
                        ))
                        total_loaded += 1
                    conn.commit()
                    next_cursor = resources.get("next_cursor")
                    if not next_cursor: break
                except Exception as e:
                    if "420" in str(e):
                        st.warning("⚠️ API速率限制，已保存现有进度。")
                        conn.close()
                        placeholder.empty()
                        return
                    break
            progress_bar.progress((idx + 1) / len(subfolders))
    except Exception as e:
        st.error(f"加载出错: {e}")
    conn.close()
    placeholder.success(f"✅ 恢复完成！共加载 {total_loaded} 张")
    time.sleep(1)
    placeholder.empty()
    st.rerun()

# ===== 📥 本地 Prompt 导入逻辑 (优化版：带进度条) =====
def import_prompts_from_json(uploaded_file):
    """从本地JSON更新数据库的prompt字段"""
    try:
        data = json.load(uploaded_file)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        updated_count = 0
        
        # 创建进度条
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_items = len(data)
        
        if isinstance(data, dict):
            # 开始批量更新
            # 使用事务处理加速
            cursor.execute("BEGIN TRANSACTION")
            
            for i, (key, value) in enumerate(data.items()):
                # key 是文件名核心部分 (例如 char_anim_01_dreamshaper_1)
                # value 是 prompt 文本
                prompt_text = value if isinstance(value, str) else str(value)
                
                # 模糊匹配：只要 filepath 包含 key 就算匹配
                # 这样 char_anim_01_dreamshaper_1 能匹配到 char_anim_01_dreamshaper_1_randomstr
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

# ===== 生成图片URL =====
def get_cloud_image_url(filepath: str) -> str:
    try:
        url, _ = cloudinary_url(
            filepath, width=800, crop="limit", quality="auto",
            fetch_format="auto", secure=True
        )
        return url
    except:
        return "https://via.placeholder.com/800x800?text=URL+Error"

# ===== 保存评分 (新增 prompt_adherence) =====
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
    finally:
        conn.close()

# ===== 获取已有评分 =====
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
    if not os.path.exists(DB_PATH): init_database()
    load_images_from_cloudinary_to_db(force_refresh=False)
    current_user = get_user_id()

    with st.sidebar:
        st.title("👤 评分系统 Pro")
        st.info(f"ID: **{current_user}**")
        
        # --- Prompt 导入功能 ---
        with st.expander("📂 导入 Prompt 文件 (JSON)"):
            st.caption("上传 JSON 文件以填充 Prompt，避免调用 Cloudinary API")
            uploaded_file = st.file_uploader("选择 JSON", type="json")
            if uploaded_file and st.button("开始匹配导入"):
                cnt = import_prompts_from_json(uploaded_file)
                st.success(f"成功更新 {cnt} 条 Prompt 数据！")
                time.sleep(1)
                st.rerun()

        st.divider()
        # 管理员密码区域
        admin_pwd = st.text_input("管理员密码", type="password", key="admin_pwd")
        if admin_pwd == "123456":
            if st.button("⚠️ 强制重置数据库表结构"):
                init_database()
                st.success("表结构已更新 (新增 Prompt 字段)")

    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        try:
            my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", conn, params=(current_user,)).iloc[0]['cnt']
        except: my_evals = 0
    except:
        images_df = pd.DataFrame(); my_evals = 0
    conn.close()

    if images_df.empty:
        st.warning("正在初始化..."); return

    col1, col2, col3 = st.columns(3)
    col1.metric("总图片", len(images_df))
    col2.metric("我的进度", f"{my_evals}")
    col3.metric("完成率", f"{my_evals/len(images_df)*100:.1f}%")
    st.progress(my_evals/len(images_df) if len(images_df)>0 else 0)

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

    idx = st.session_state.page_number - 1
    if idx < len(images_df):
        row = images_df.iloc[idx]
        existing = get_existing_score(row['id'], current_user)

        st.markdown("---")
        
        # 🔥 Prompt 展示区
        prompt_text = row['prompt_text']
        if not prompt_text:
            st.warning("⚠️ 此图片暂无 Prompt 数据。请在侧边栏上传 JSON 导入。")
        else:
            st.info(f"**📝 Prompt:** {prompt_text}")

        col_img, col_form = st.columns([1.2, 1])
        with col_img:
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"):
                st.code(f"File: {row['filepath']}\nPrompt ID: {row['prompt_id']}")
                
        with col_form:
            with st.form(key=f"form_{row['id']}"):
                # 1. 匹配度 (最重要)
                st.markdown("#### 🎯 核心匹配度")
                prompt_adhere = st.slider("Prompt 匹配度 (Text-to-Image Accuracy)", 1, 5, existing.get('prompt_adherence', 3), help="生成的图像是否忠实反映了上方的 Prompt 描述？")
                
                # 2. 游戏标准
                st.markdown("#### 🛠️ 游戏工业标准")
                c1, c2 = st.columns(2)
                with c1:
                    style_const = st.slider("风格一致性", 1, 5, existing.get('style_consistency', 3), help="画风是否统一？")
                    perspective = st.slider("透视准确性", 1, 5, existing.get('perspective_check', 3))
                with c2:
                    asset_clean = st.slider("资产干净度", 1, 5, existing.get('asset_cleanliness', 3))
                    struct_logic = st.slider("结构合理性", 1, 5, existing.get('structural_logic', 3))

                # 3. 基础质量
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
                        "prompt_adherence": prompt_adhere, # 新字段
                        "perspective_check": perspective, "asset_cleanliness": asset_clean,
                        "structural_logic": struct_logic, "style_consistency": style_const,
                        "overall_quality": overall, "is_usable": is_usable, "notes": notes
                    }
                    if save_evaluation(row['id'], current_user, scores):
                        if st.session_state.page_number < total_pages:
                            st.session_state.page_number += 1; st.rerun()

if __name__ == "__main__":
    main()
