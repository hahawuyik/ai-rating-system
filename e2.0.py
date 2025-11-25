import streamlit as st
import pandas as pd
import json
import os
from pathlib import Path
from PIL import Image
import sqlite3
from datetime import datetime
import cloudinary
import cloudinary.api
from cloudinary.utils import cloudinary_url

# 🔥 这一行必须放在所有 st. 命令的最前面！
st.set_page_config(
    page_title="AI游戏图像质量评价系统",
    page_icon="🎮",
    layout="wide"
)

# ===== Cloudinary 配置 =====
# 把下面的值换成你自己Dashboard里的信息
cloudinary.config(
    cloud_name="root",
    api_key="676912851999589",
    api_secret="YIY48Z9VOM1zHfPWZvFKlHpyXzk",
    secure=True
)

# 你在 Cloudinary 里存放图片的根文件夹名，例如 ai-rating-images
CLOUDINARY_ROOT_FOLDER = "ai-rating-images"

# ===== 路径配置（仅用于本地建库 & 保存评分用的SQLite文件）=====

if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    # 云环境：使用相对路径
    DATASET_ROOT = "./ai_dataset_project"
else:
    # 本地环境：使用原路径（仅用来扫描图片 & 读取meta）
    DATASET_ROOT = "D:/ai_dataset_project"

OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
EVALUATION_DIR = os.path.join(DATASET_ROOT, "evaluations")

DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(METADATA_DIR, exist_ok=True)
os.makedirs(EVALUATION_DIR, exist_ok=True)

# ===== 数据库初始化 =====

def init_database():
    """初始化SQLite数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 图片索引表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT,
            model_id TEXT,
            image_number INTEGER,
            filepath TEXT UNIQUE,   -- 这里的 filepath 将存 Cloudinary 资源路径
            prompt_text TEXT,
            type TEXT,
            style TEXT,
            model_name TEXT,
            quality_tier TEXT,
            generation_time TEXT
        )
    ''')

    # 评分表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            evaluator_id TEXT,
            evaluator_name TEXT,

            -- 技术质量
            clarity INTEGER,
            detail_richness INTEGER,
            color_accuracy INTEGER,
            lighting_quality INTEGER,
            composition INTEGER,

            -- 内容准确性
            prompt_match INTEGER,
            style_consistency INTEGER,
            subject_completeness INTEGER,

            -- 游戏适用性
            game_usability INTEGER,
            needs_fix TEXT,
            direct_use TEXT,

            -- 缺陷
            major_defects TEXT,
            minor_issues TEXT,

            -- 整体
            overall_quality INTEGER,
            grade TEXT,
            notes TEXT,

            evaluation_time TEXT,

            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    conn.commit()
    conn.close()


# ===== 从本地扫描图片，但在库里保存 Cloudinary 资源路径 =====

def load_images_to_db():
    """
    从本地 D:/ai_dataset_project/images/... 扫描图片，
    但写入数据库时，filepath 字段保存 Cloudinary 中的资源路径：
    例如：ai-rating-images/dalle3/char_anim_01_dalle3_1.png
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    loaded_count = 0
    models = ['dalle3', 'sd15', 'sdxl_turbo', 'dreamshaper']
    
    for model_id in models:
        # 本地图片目录（只在你本地运行建库脚本时有效，云端不会执行到）
        model_dir = os.path.join("D:/ai_dataset_project/images", model_id)
        
        if not os.path.exists(model_dir):
            st.warning(f"⚠️ 模型目录不存在: {model_dir}")
            continue
            
        st.info(f"📁 扫描 {model_id} 模型的图片...")
        
        try:
            png_files = [f for f in os.listdir(model_dir) if f.endswith('.png')]
            st.write(f"找到 {len(png_files)} 张PNG图片")
            
            for filename in png_files:
                local_filepath = os.path.join(model_dir, filename)
                
                # ✅ 关键：生成 Cloudinary 资源路径（用来存库 & 之后生成URL）
                # 你的 Cloudinary 目录结构应该和本地类似：
                # CLOUDINARY_ROOT_FOLDER / model_id / filename
                # 例如 ai-rating-images/dalle3/char_anim_01_dalle3_1.png
                resource_path = f"{CLOUDINARY_ROOT_FOLDER}/{model_id}/{filename}"
                
                # 检查是否已存在
                cursor.execute("SELECT id FROM images WHERE filepath = ?", (resource_path,))
                if cursor.fetchone():
                    continue
                
                try:
                    base_name = filename.replace('.png', '')
                    parts = base_name.split('_')
                    
                    if len(parts) >= 3:
                        image_number = int(parts[-1])
                        file_model = parts[-2]
                        prompt_id = '_'.join(parts[:-2])
                        
                        # 仍然从本地读取 meta.json
                        meta_path = local_filepath.replace('.png', '_meta.json')
                        metadata = {}
                        if os.path.exists(meta_path):
                            try:
                                with open(meta_path, 'r', encoding='utf-8') as f:
                                    metadata = json.load(f)
                            except Exception as e:
                                st.warning(f"读取元数据文件失败 {meta_path}: {e}")
                        
                        cursor.execute('''
                            INSERT INTO images (
                                prompt_id, model_id, image_number, filepath,
                                prompt_text, type, style, model_name, quality_tier, generation_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            prompt_id,
                            model_id,
                            image_number,
                            resource_path,  # ✅ 存 Cloudinary 资源路径
                            metadata.get('prompt', f'Prompt: {prompt_id}'),
                            metadata.get('type', 'unknown'),
                            metadata.get('style', 'unknown'),
                            metadata.get('model_name', model_id),
                            metadata.get('quality_tier', 'medium'),
                            metadata.get('generation_time', datetime.now().isoformat())
                        ))
                        
                        loaded_count += 1
                        
                        if loaded_count % 100 == 0:
                            conn.commit()
                            st.success(f"✅ 已加载 {loaded_count} 张图片...")
                            
                except Exception as e:
                    st.error(f"❌ 处理文件 {filename} 时出错: {e}")
                    continue
        
        except Exception as e:
            st.error(f"❌ 扫描目录 {model_dir} 时出错: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    if loaded_count > 0:
        st.success(f"🎉 成功加载 {loaded_count} 张图片到数据库！")
    else:
        st.info("📊 数据库已包含所有图片记录")
    
    return loaded_count


# ===== 工具函数：把数据库里的 filepath 转成 Cloudinary URL =====

def get_cloud_image_url(resource_path: str) -> str:
    """
    根据数据库中的 filepath（例如 'ai-rating-images/dalle3/xxx.png'）
    生成可在浏览器显示的 Cloudinary URL
    """
    url, _ = cloudinary_url(
        resource_path,
        width=800,
        height=800,
        crop="limit",
        quality="auto"
    )
    return url


# ===== 评分保存、获取函数（你原来的逻辑，不动）=====

def save_evaluation(image_id, evaluator_id, evaluator_name, scores):
    """保存评分"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # ... 原实现不变 ...
    # 省略：这里保持你之前的代码

def get_evaluation(image_id, evaluator_id):
    """获取已有评分"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM evaluations 
        WHERE image_id = ? AND evaluator_id = ?
    ''', (image_id, evaluator_id))
    result = cursor.fetchone()
    cols = [d[0] for d in cursor.description] if result else []
    conn.close()
    if result:
        return dict(zip(cols, result))
    return None


# ===== Streamlit 界面 =====

def main():
    # 初始化session_state
    if 'page' not in st.session_state:
        st.session_state.page = 1

    # 初始化数据库（云端只要有db文件，就不会再跑load_images_to_db）
    if not os.path.exists(DB_PATH):
        with st.spinner("初始化数据库..."):
            init_database()
            loaded = load_images_to_db()
            st.success(f"✅ 已加载 {loaded} 张图片到数据库")
    
    # 侧边栏：评分员信息
    st.sidebar.title("🎮 评分系统")
    evaluator_id = st.sidebar.text_input("评分员ID", value="eval_001")
    evaluator_name = st.sidebar.text_input("评分员姓名", value="张三")
    st.sidebar.markdown("---")

    # ===== 下面评分与筛选逻辑保持你原来的，不改，直到显示图片那一段 =====
    conn = sqlite3.connect(DB_PATH)

    st.sidebar.subheader("📊 筛选条件")
    models = pd.read_sql("SELECT DISTINCT model_id FROM images", conn)['model_id'].tolist()
    types = pd.read_sql("SELECT DISTINCT type FROM images", conn)['type'].tolist()
    styles = pd.read_sql("SELECT DISTINCT style FROM images", conn)['style'].tolist()

    selected_model = st.sidebar.selectbox("模型", ['全部'] + models)
    selected_type = st.sidebar.selectbox("类型", ['全部'] + types)
    selected_style = st.sidebar.selectbox("风格", ['全部'] + styles)

    show_evaluated = st.sidebar.checkbox("显示已评分", value=True)
    show_unevaluated = st.sidebar.checkbox("显示未评分", value=True)
    st.sidebar.markdown("---")

    query = "SELECT * FROM images WHERE 1=1"
    params = []

    if selected_model != '全部':
        query += " AND model_id = ?"
        params.append(selected_model)
    if selected_type != '全部':
        query += " AND type = ?"
        params.append(selected_type)
    if selected_style != '全部':
        query += " AND style = ?"
        params.append(selected_style)

    images_df = pd.read_sql(query, conn, params=params)

    # 筛选已/未评分
    if not show_evaluated or not show_unevaluated:
        evaluated_ids = pd.read_sql(
            "SELECT DISTINCT image_id FROM evaluations WHERE evaluator_id = ?",
            conn, params=(evaluator_id,)
        )['image_id'].tolist()
        if not show_evaluated:
            images_df = images_df[~images_df['id'].isin(evaluated_ids)]
        if not show_unevaluated:
            images_df = images_df[images_df['id'].isin(evaluated_ids)]

    # 统计信息
    total_images = pd.read_sql("SELECT COUNT(*) as count FROM images", conn)['count'][0]
    evaluated_count = pd.read_sql(
        "SELECT COUNT(DISTINCT image_id) as count FROM evaluations WHERE evaluator_id = ?",
        conn, params=(evaluator_id,)
    )['count'][0]
    conn.close()

    st.title("🎮 AI游戏图像质量评价系统")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总图片数", total_images)
    col2.metric("已评分", evaluated_count)
    col3.metric("未评分", total_images - evaluated_count)
    col4.metric("完成度", f"{(evaluated_count / total_images * 100) if total_images else 0:.1f}%")
    st.markdown("---")

    if len(images_df) == 0:
        st.warning("⚠️ 没有符合条件的图片")
        return

    # 分页
    items_per_page = 10
    total_pages = (len(images_df) - 1) // items_per_page + 1
    current_page = st.session_state.page
    current_page = max(1, min(current_page, total_pages))

    col_nav = st.columns([1, 2, 1])
    with col_nav[1]:
        st.markdown(
            f"<div style='text-align: center; margin-bottom: 10px;'>第 <b>{current_page}</b> 页 / 共 <b>{total_pages}</b> 页</div>",
            unsafe_allow_html=True)
        new_page = st.number_input(
            "页码",
            min_value=1,
            max_value=total_pages,
            value=current_page,
            key="page_input",
            label_visibility="collapsed"
        )
        if new_page != current_page:
            st.session_state.page = new_page
            st.rerun()

    st.info(
        f"📄 显示 {len(images_df)} 张图片中的第 {(current_page - 1) * items_per_page + 1} - {min(current_page * items_per_page, len(images_df))} 张"
    )

    start_idx = (current_page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(images_df))
    page_images = images_df.iloc[start_idx:end_idx]

    # ===== 关键：这里把本地 Image.open 改为 Cloudinary URL =====
    for idx, row in page_images.iterrows():
        with st.expander(f"🖼️ {row['prompt_id']} - {row['model_name']} - 图片{row['image_number']}", expanded=False):
            col_img, col_form = st.columns([1, 2])

            # 左侧：图片
            with col_img:
                # row['filepath'] 现在是 cloud 资源路径，如 ai-rating-images/dalle3/xxx.png
                img_url = get_cloud_image_url(row['filepath'])
                st.image(img_url, use_container_width=True)

                st.caption(f"**Prompt:** {row['prompt_text']}")
                st.caption(f"**类型:** {row['type']} | **风格:** {row['style']}")
                st.caption(f"**模型:** {row['model_name']} ({row['quality_tier']})")

            # 右侧：评分表单
            # ……（这里保持你原来的评分表单逻辑，不再重复粘贴）……



# ===== 统计分析页面（保留原逻辑即可）=====

def show_statistics():
    # 你原来的统计逻辑即可，和图片加载无关
    pass


# ===== 主入口 =====

if __name__ == "__main__":
    os.makedirs(EVALUATION_DIR, exist_ok=True)

    page = st.sidebar.radio("导航", ["📝 评分", "📊 统计分析"])

    if page == "📝 评分":
        main()
    else:
        show_statistics()
