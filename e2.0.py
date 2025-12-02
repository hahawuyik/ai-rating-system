import streamlit as st
import pandas as pd
import json
import os
from pathlib import Path
from datetime import datetime
import sqlite3
import cloudinary
import cloudinary.api
from cloudinary.utils import cloudinary_url
from cloudinary.exceptions import NotFound
import time

# 🔥 这一行必须放在所有 st. 命令的最前面！
st.set_page_config(
    page_title="AI游戏图像质量评价系统",
    page_icon="🎮",
    layout="wide"
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
def ensure_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        st.error(f"❌ 目录不可写: {path} | 错误: {str(e)}")
        return False

# 适配本地/云端环境路径
if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    DATASET_ROOT = os.path.join(os.getcwd(), "ai_dataset_project")
else:
    # ⚠️ 注意：这里硬编码了 D 盘路径，请确认你是否真的想用这个路径
    DATASET_ROOT = "D:/ai_dataset_project"

OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
EVALUATION_DIR = os.path.join(DATASET_ROOT, "evaluations")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

for dir_path in [OUTPUT_DIR, METADATA_DIR, EVALUATION_DIR]:
    ensure_writable_dir(dir_path)

# ===== 数据库操作 =====
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT,
            model_id TEXT,
            image_number INTEGER,
            filepath TEXT UNIQUE,
            prompt_text TEXT,
            type TEXT,
            style TEXT,
            model_name TEXT,
            quality_tier TEXT,
            generation_time TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            evaluator_id TEXT,
            evaluator_name TEXT,
            clarity INTEGER, detail_richness INTEGER, color_accuracy INTEGER, lighting_quality INTEGER, composition INTEGER,
            prompt_match INTEGER, style_consistency INTEGER, subject_completeness INTEGER,
            game_usability INTEGER, needs_fix TEXT, direct_use TEXT,
            major_defects TEXT, minor_issues TEXT,
            overall_quality INTEGER, grade TEXT, notes TEXT,
            evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

def load_images_from_cloudinary_to_db(force_refresh=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    st.info(f"🔍 开始从 Cloudinary 拉取资源...")

    if force_refresh:
        cursor.execute("DELETE FROM images")
        conn.commit()

    try:
        # 获取子文件夹
        subfolders_result = cloudinary.api.subfolders(CLOUDINARY_ROOT_FOLDER)
        subfolders = subfolders_result.get('folders', [])
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        loaded_count = 0
        
        for folder_idx, folder in enumerate(subfolders):
            folder_path = folder['path'] # 例如: ai-rating-images/dalle3
            model_id = folder_path.split('/')[-1]
            
            # 检查该文件夹是否已有数据（避免重复加载消耗额度）
            cursor.execute("SELECT COUNT(*) FROM images WHERE model_id = ?", (model_id,))
            existing_count = cursor.fetchone()[0]
            if existing_count > 0 and not force_refresh:
                st.info(f"⏭️ 跳过 {model_id} (数据库已有 {existing_count} 张)")
                continue

            status_text.text(f"📁 正在处理: {folder_path}...")
            
            next_cursor = None
            
            while True:
                try:
                    time.sleep(0.5) # 限速保护
                    
                    resources = cloudinary.api.resources(
                        type="upload",
                        folders=folder_path, # 指定文件夹
                        max_results=100,
                        next_cursor=next_cursor,
                        resource_type="image"
                    )
                    
                    batch_resources = resources.get("resources", [])
                    if not batch_resources: break
                        
                    for res in batch_resources:
                        # ✅ 修正的核心：直接使用 res['public_id']，它已经包含了完整路径
                        full_public_id = res["public_id"] 
                        
                        # 解析文件名逻辑
                        actual_filename = full_public_id.split('/')[-1]
                        parts = actual_filename.split('_')
                        
                        # 简单的解析逻辑
                        prompt_id = actual_filename
                        image_number = 1
                        if len(parts) > 1 and parts[-1].isdigit():
                            image_number = int(parts[-1])
                            prompt_id = "_".join(parts[:-1])

                        context = res.get("context", {}).get("custom", {})
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO images (
                                prompt_id, model_id, image_number, filepath,
                                prompt_text, type, style, model_name, quality_tier, generation_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            prompt_id, model_id, image_number, full_public_id,
                            context.get("prompt", f"Prompt: {prompt_id}"),
                            context.get("type", "unknown"),
                            context.get("style", "unknown"),
                            context.get("model_name", model_id),
                            context.get("quality_tier", "medium"),
                            res.get("created_at", datetime.now().isoformat())
                        ))
                        loaded_count += 1
                    
                    conn.commit()
                    next_cursor = resources.get("next_cursor")
                    if not next_cursor: break
                    
                except Exception as e:
                    if "420" in str(e):
                        st.warning("⚠️ API速率限制已达上限。已保存当前进度。")
                        conn.close()
                        return loaded_count
                    st.error(f"❌ 错误: {str(e)}")
                    break
            
            progress_bar.progress((folder_idx + 1) / len(subfolders))
            
    except Exception as e:
        st.error(f"❌ 严重错误: {str(e)}")
    
    conn.close()
    return loaded_count

def get_cloud_image_url(filepath: str) -> str:
    """生成图片URL"""
    try:
        url, _ = cloudinary_url(
            filepath,
            width=800,
            crop="limit",
            quality="auto",
            fetch_format="auto", # 自动适配格式
            secure=True
        )
        return url
    except Exception:
        return "https://via.placeholder.com/800x800?text=Error"

# ===== 评分保存逻辑 =====
def save_evaluation_db(image_id, evaluator_id, evaluator_name, scores):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # ... (简化的保存逻辑，与原版相同) ...
    # 这里为了代码简洁省略了具体的 INSERT/UPDATE 语句，请使用你原来的 save_evaluation 函数内容
    # 但请确保表名和字段名一致
    try:
        # 简单实现，确保演示代码可运行
        current_time = datetime.now().isoformat()
        cursor.execute("SELECT id FROM evaluations WHERE image_id=? AND evaluator_id=?", (image_id, evaluator_id))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("UPDATE evaluations SET overall_quality=?, notes=?, evaluation_time=? WHERE id=?", 
                          (scores['overall_quality'], scores['notes'], current_time, exists[0]))
        else:
            cursor.execute("INSERT INTO evaluations (image_id, evaluator_id, overall_quality, notes, evaluation_time) VALUES (?, ?, ?, ?, ?)",
                          (image_id, evaluator_id, scores['overall_quality'], scores['notes'], current_time))
        conn.commit()
        st.success("已保存")
    except Exception as e:
        st.error(f"保存失败: {e}")
    conn.close()

# ===== 主界面 =====
def main():
    # 1. 显示当前数据库路径，防止找错文件
    st.sidebar.warning(f"📂 当前数据库路径:\n{DB_PATH}")
    
    # 2. 强制重置按钮
    if st.sidebar.button("⚠️ 强制清空并重新获取数据", type="primary"):
        init_database()
        count = load_images_from_cloudinary_to_db(force_refresh=True)
        st.sidebar.success(f"已重新加载 {count} 条数据！")
        time.sleep(1)
        st.rerun()

    # 初始化
    if not os.path.exists(DB_PATH):
        init_database()
        load_images_from_cloudinary_to_db()

    # 3. 读取数据
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
    except:
        init_database()
        images_df = pd.DataFrame()
    conn.close()

    st.title(f"🎮 AI游戏图像评价 (共 {len(images_df)} 张)")

    if len(images_df) == 0:
        st.warning("暂无数据，请点击左侧'强制清空并重新获取数据'按钮")
        return

    # 分页
    limit = 5
    total_pages = max(1, (len(images_df)-1)//limit + 1)
    page = st.number_input("页码", 1, total_pages, 1)
    
    start = (page-1) * limit
    current_images = images_df.iloc[start : start+limit]

    for _, row in current_images.iterrows():
        with st.expander(f"🖼️ {row['filepath']} ({row['model_id']})", expanded=True):
            col1, col2 = st.columns([1, 1])
            with col1:
                # 显示图片
                url = get_cloud_image_url(row['filepath'])
                st.image(url)
                
                # 调试信息
                if st.checkbox("Debug URL", key=f"d_{row['id']}"):
                    st.code(url)
            
            with col2:
                st.write(f"**Model Folder:** {row['model_id']}")
                st.write(f"**Filename:** {row['filepath'].split('/')[-1]}")
                st.info("如果这里显示的 Filename 包含 'sdxl' 但 Model Folder 是 'dalle3'，说明文件被传到了 dalle3 文件夹中。")
                
                rating = st.slider("评分", 1, 5, 3, key=f"r_{row['id']}")
                notes = st.text_input("备注", key=f"n_{row['id']}")
                if st.button("保存", key=f"s_{row['id']}"):
                    save_evaluation_db(row['id'], "eval_001", "User", {"overall_quality": rating, "notes": notes})

if __name__ == "__main__":
    main()
