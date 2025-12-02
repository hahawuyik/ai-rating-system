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

# 🔥 1. 页面配置
st.set_page_config(
    page_title="AI游戏美术评分系统 Pro",
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

# ===== 💾 数据库结构 =====
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

    # 评分表 (包含游戏专业指标)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            evaluator_id TEXT,
            clarity INTEGER, detail_richness INTEGER, color_harmony INTEGER,
            perspective_check INTEGER, asset_cleanliness INTEGER, 
            style_consistency INTEGER, structural_logic INTEGER,
            overall_quality INTEGER, is_usable TEXT, notes TEXT,
            evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== ☁️ 自动从 Cloudinary 拉取数据 (复活的函数) =====
def load_images_from_cloudinary_to_db(force_refresh=False):
    """当数据库为空时，自动从Cloudinary重新拉取列表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查是否真的需要加载
    if not force_refresh:
        cursor.execute("SELECT COUNT(*) FROM images")
        if cursor.fetchone()[0] > 0:
            conn.close()
            return

    placeholder = st.empty()
    placeholder.info(f"🔍 数据库为空，正在从 Cloudinary 恢复数据，请稍候...")
    
    if force_refresh:
        cursor.execute("DELETE FROM images")
        conn.commit()

    try:
        subfolders_result = cloudinary.api.subfolders(CLOUDINARY_ROOT_FOLDER)
        subfolders = subfolders_result.get('folders', [])
        
        total_loaded = 0
        
        # 创建进度条
        progress_bar = st.progress(0)
        
        for idx, folder in enumerate(subfolders):
            folder_path = folder['path']
            model_id = folder_path.split('/')[-1]
            next_cursor = None
            
            while True:
                try:
                    time.sleep(0.2) #稍微防一下限流
                    resources = cloudinary.api.resources(
                        type="upload", folders=folder_path, max_results=100,
                        next_cursor=next_cursor, resource_type="image"
                    )
                    batch = resources.get("resources", [])
                    if not batch: break
                        
                    for res in batch:
                        full_public_id = res["public_id"]
                        actual_filename = full_public_id.split('/')[-1]
                        
                        # 简单的ID解析
                        prompt_id = actual_filename
                        image_number = 1
                        parts = actual_filename.split('_')
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
    placeholder.success(f"✅ 数据恢复完成！共加载 {total_loaded} 张图片")
    time.sleep(1)
    placeholder.empty()
    st.rerun() # 加载完自动刷新页面

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

# ===== 保存评分 =====
def save_evaluation(image_id, user_id, scores):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    cursor.execute("SELECT id FROM evaluations WHERE image_id=? AND evaluator_id=?", (image_id, user_id))
    exists = cursor.fetchone()
    
    data = (
        user_id,
        scores['clarity'], scores['detail_richness'], scores['color_harmony'],
        scores['perspective_check'], scores['asset_cleanliness'], 
        scores['style_consistency'], scores['structural_logic'],
        scores['overall_quality'], scores['is_usable'], scores['notes'],
        now
    )
    
    try:
        if exists:
            sql = '''UPDATE evaluations SET 
                     evaluator_id=?, clarity=?, detail_richness=?, color_harmony=?,
                     perspective_check=?, asset_cleanliness=?, style_consistency=?, structural_logic=?,
                     overall_quality=?, is_usable=?, notes=?, evaluation_time=?
                     WHERE id=?'''
            cursor.execute(sql, data + (exists[0],))
            msg = "🔄 评分已更新"
        else:
            sql = '''INSERT INTO evaluations (
                     evaluator_id, clarity, detail_richness, color_harmony,
                     perspective_check, asset_cleanliness, style_consistency, structural_logic,
                     overall_quality, is_usable, notes, evaluation_time, image_id
                     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)'''
            cursor.execute(sql, data + (image_id,))
            msg = "✅ 评分已保存"
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
        df = pd.read_sql("SELECT * FROM evaluations WHERE image_id=? AND evaluator_id=?", 
                         conn, params=(image_id, user_id))
        if not df.empty:
            return df.iloc[0].to_dict()
    except:
        pass
    finally:
        conn.close()
    return {}

# ===== 主程序 =====
def main():
    # 1. 确保数据库存在
    if not os.path.exists(DB_PATH):
        init_database()
        
    # 2. 🔥 关键：每次运行都检查数据库是否为空，空则自动拉取
    load_images_from_cloudinary_to_db(force_refresh=False)

    # 3. 获取用户 ID
    current_user = get_user_id()

    # 4. 侧边栏 (SideBar) - 经过安全改造
    with st.sidebar:
        st.title("👤 评分系统 Pro")
        
        # --- 用户身份区域 ---
        st.info(f"当前 ID: **{current_user}**")
        st.caption("⚠️ 注意：请保留当前浏览器地址栏的链接！如果关闭网页，下次需通过下方输入框找回此ID，否则进度会丢失。")
        
        # --- 找回旧ID的功能 ---
        with st.expander("🔐 找回之前的进度"):
            input_id = st.text_input("输入旧的 ID (例如 user_xxx)", key="restore_id_input")
            if st.button("恢复身份"):
                if input_id:
                    st.query_params["user"] = input_id.strip()
                    st.session_state.user_id = input_id.strip()
                    st.rerun()

        st.divider()
        
        # --- 危险操作区域 (加密码锁) ---
        # 只有输入正确密码，才能看到刷新按钮
        admin_pwd = st.text_input("管理员密码 (非管理员勿动)", type="password", key="admin_pwd")
        if admin_pwd == "123456":  # 👈 你可以在这里修改密码
            st.error("⚠️ 危险区域")
            if st.button("🔥 强制清空并重拉数据"):
                init_database()
                load_images_from_cloudinary_to_db(force_refresh=True)
        else:
            # 普通用户只能看到这个
            st.caption("管理员功能已隐藏")

    # 5. 读取数据
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        # 兼容性处理：如果 evaluations 表不存在，不报错
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
        st.warning("正在初始化数据，请稍候...")
        return

    # 6. 顶部统计
    col1, col2, col3 = st.columns(3)
    col1.metric("总图片", len(images_df))
    col2.metric("我的进度", f"{my_evals}")
    col3.metric("完成率", f"{my_evals/len(images_df)*100:.1f}%")
    st.progress(my_evals/len(images_df) if len(images_df)>0 else 0)

    # 7. 分页控制
    if 'page_number' not in st.session_state:
        st.session_state.page_number = 1
        
    total_pages = len(images_df)
    col_prev, col_page, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("⬅️ 上一张") and st.session_state.page_number > 1:
            st.session_state.page_number -= 1
            st.rerun()
    with col_page:
        st.session_state.page_number = st.number_input("页码", 1, total_pages, st.session_state.page_number, label_visibility="collapsed")
    with col_next:
        if st.button("下一张 ➡️") and st.session_state.page_number < total_pages:
            st.session_state.page_number += 1
            st.rerun()

    # 8. 内容展示区
    idx = st.session_state.page_number - 1
    if idx < len(images_df):
        row = images_df.iloc[idx]
        existing = get_existing_score(row['id'], current_user)

        st.markdown("---")
        col_img, col_form = st.columns([1.2, 1])
        
        with col_img:
            st.subheader(f"🖼️ {row['model_id']} ({row['image_number']})")
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"):
                st.code(row['filepath'])
                
        with col_form:
            with st.form(key=f"form_{row['id']}"):
                st.markdown("#### 🛠️ 游戏工业标准")
                c1, c2 = st.columns(2)
                with c1:
                    perspective = st.slider("透视准确性", 1, 5, existing.get('perspective_check', 3))
                    asset_clean = st.slider("资产干净度", 1, 5, existing.get('asset_cleanliness', 3))
                with c2:
                    struct_logic = st.slider("结构合理性", 1, 5, existing.get('structural_logic', 3))
                    style_const = st.slider("风格一致性", 1, 5, existing.get('style_consistency', 3))

                st.markdown("#### 🎨 基础美术质量")
                c3, c4 = st.columns(2)
                with c3:
                    clarity = st.slider("清晰度", 1, 5, existing.get('clarity', 3))
                    detail = st.slider("细节丰富度", 1, 5, existing.get('detail_richness', 3))
                with c4:
                    color = st.slider("色彩和谐度", 1, 5, existing.get('color_harmony', 3))

                st.markdown("---")
                overall = st.slider("⭐ 整体评分", 1, 5, existing.get('overall_quality', 3))
                is_usable = st.radio("🎮 是否可用？", ["是", "否", "需微调"], 
                                   index=["是", "否", "需微调"].index(existing.get('is_usable', '否')), horizontal=True)
                notes = st.text_area("备注", existing.get('notes', ''))
                
                if st.form_submit_button("💾 保存并下一张", type="primary", use_container_width=True):
                    scores = {
                        "clarity": clarity, "detail_richness": detail, "color_harmony": color,
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

