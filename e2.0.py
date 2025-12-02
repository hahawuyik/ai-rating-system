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

# ===== 路径配置 (智能适配云端/本地) =====
# 统一使用相对路径 'data' 文件夹，避免 D: 盘路径在云端失效的问题
BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, "data_storage")
METADATA_DIR = os.path.join(DATA_DIR, "metadata")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

# 确保文件夹存在
for p in [DATA_DIR, METADATA_DIR]:
    os.makedirs(p, exist_ok=True)

# 本地 Prompt 文件路径
LOCAL_PROMPT_JSON = os.path.join(METADATA_DIR, "final_prompts_translated.json")

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
    
    # 评分表
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
    
    # 自动升级检查 (防止旧数据库报错)
    try:
        cursor.execute("SELECT prompt_adherence FROM evaluations LIMIT 1")
    except:
        try:
            cursor.execute("ALTER TABLE evaluations ADD COLUMN prompt_adherence INTEGER")
        except: pass

    conn.commit()
    conn.close()

# ===== 🧹 工厂重置 (修复坏数据) =====
def factory_reset():
    """彻底清空数据库，解决 ID 不匹配问题"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS images")
    cursor.execute("DROP TABLE IF EXISTS evaluations")
    conn.commit()
    conn.close()
    # 重新初始化
    init_database()

# ===== 📥 本地 Prompt 导入逻辑 (补全的函数) =====
def import_prompts_from_json(uploaded_file):
    try:
        data = json.load(uploaded_file)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        updated_count = 0
        progress_bar = st.progress(0)
        
        if isinstance(data, dict):
            cursor.execute("BEGIN TRANSACTION")
            total = len(data)
            for i, (key, value) in enumerate(data.items()):
                p_text = value if isinstance(value, str) else str(value)
                cursor.execute("UPDATE images SET prompt_text = ? WHERE filepath LIKE ?", (p_text, f"%{key}%"))
                updated_count += cursor.rowcount
                if i % 100 == 0: progress_bar.progress(min((i+1)/total, 1.0))
            cursor.execute("COMMIT")
            
        progress_bar.empty()
        conn.close()
        return updated_count
    except Exception as e:
        st.error(f"解析失败: {e}")
        return 0

# ===== ⚡ 自动加载本地 Prompt =====
def auto_load_local_prompts():
    if not os.path.exists(LOCAL_PROMPT_JSON): return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM images WHERE prompt_text IS NOT NULL AND prompt_text != ''")
        if cursor.fetchone()[0] > 100: # 只要有超过100条prompt，就认为加载过了
            conn.close(); return
        
        with open(LOCAL_PROMPT_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cursor.execute("BEGIN TRANSACTION")
            for key, value in data.items():
                p_text = value if isinstance(value, str) else str(value)
                cursor.execute("UPDATE images SET prompt_text = ? WHERE filepath LIKE ?", (p_text, f"%{key}%"))
            cursor.execute("COMMIT")
    except: pass
    conn.close()

# ===== ☁️ Cloudinary 拉取 (安全版) =====
def load_images_from_cloudinary_to_db(force_refresh=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if not force_refresh:
        cursor.execute("SELECT COUNT(*) FROM images")
        if cursor.fetchone()[0] > 0:
            conn.close(); auto_load_local_prompts(); return

    placeholder = st.empty()
    placeholder.info(f"🔍 正在同步 Cloudinary 数据...")
    
    try:
        subfolders_result = cloudinary.api.subfolders(CLOUDINARY_ROOT_FOLDER)
        subfolders = subfolders_result.get('folders', [])
        total_added = 0
        progress_bar = st.progress(0)
        
        for idx, folder in enumerate(subfolders):
            folder_path = folder['path']
            model_id = folder_path.split('/')[-1]
            next_cursor = None
            while True:
                try:
                    time.sleep(0.1)
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
                        
                        context = res.get("context", {}).get("custom", {})
                        
                        # INSERT OR IGNORE 保证旧ID不乱
                        cursor.execute('''
                            INSERT OR IGNORE INTO images (
                                prompt_id, model_id, image_number, filepath,
                                prompt_text, type, style, model_name, quality_tier, generation_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            prompt_id, model_id, image_number, full_public_id,
                            context.get("prompt", ""), context.get("type", "unknown"),
                            context.get("style", "unknown"), context.get("model_name", model_id),
                            context.get("quality_tier", "medium"), res.get("created_at", datetime.now().isoformat())
                        ))
                        if cursor.rowcount > 0: total_added += 1
                        
                    conn.commit()
                    next_cursor = resources.get("next_cursor")
                    if not next_cursor: break
                except Exception as e:
                    if "420" in str(e): conn.close(); placeholder.empty(); return
                    break
            progress_bar.progress((idx + 1) / len(subfolders))
    except Exception as e:
        st.error(f"加载出错: {e}")
    
    conn.close()
    auto_load_local_prompts()
    placeholder.success(f"✅ 同步完成！新增 {total_added} 张。")
    time.sleep(1)
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
                     overall_quality=?, is_usable=?, notes=?, evaluation_time=? WHERE id=?'''
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
    # 0. 启动检查
    if not os.path.exists(DB_PATH): init_database()
    load_images_from_cloudinary_to_db(force_refresh=False)
    
    current_user = get_user_id()

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("👤 评分系统 Pro")
        st.info(f"ID: **{current_user}**")
        st.caption("保留地址栏链接以保存进度。")
        
        # 找回进度
        with st.expander("🔐 找回之前的进度"):
            input_id = st.text_input("输入旧ID", key="restore_id_input")
            if st.button("恢复"):
                if input_id: 
                    st.query_params["user"] = input_id.strip()
                    st.session_state.user_id = input_id.strip()
                    st.rerun()

        # Prompt 工具
        st.divider()
        with st.expander("🛠️ Prompt 修复工具"):
            uploaded_prompt_file = st.file_uploader("上传 final_prompts_translated.json", type="json")
            if uploaded_prompt_file and st.button("手动导入"):
                cnt = import_prompts_from_json(uploaded_prompt_file)
                st.success(f"成功更新 {cnt} 条")
                time.sleep(1)
                st.rerun()

        # 数据下载
        st.divider()
        st.subheader("📊 数据导出")
        if st.button("🔄 刷新并查看表格"):
            conn = sqlite3.connect(DB_PATH)
            sql = '''
            SELECT 
                e.id as 评分ID,
                e.image_id as [关键_评分表里的图片ID],
                i.id as [对照_图片表里的图片ID],
                e.evaluator_id as 评分员,
                i.model_id as 模型,
                i.filepath as 路径,
                i.prompt_text as Prompt,
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
                e.evaluation_time as 时间
            FROM evaluations e
            LEFT JOIN images i ON e.image_id = i.id
            ORDER BY e.evaluation_time DESC
            '''
            try:
                df = pd.read_sql(sql, conn)
                conn.close()
                st.dataframe(df.head(3), height=100)
                st.download_button("📥 下载 CSV", df.to_csv(index=False).encode('utf-8-sig'), f"data_{datetime.now().strftime('%H%M')}.csv", "text/csv", type="primary")
            except Exception as e: st.error(f"读取失败: {e}")
            
        # 管理员区域
        st.divider()
        admin_pwd = st.text_input("管理员密码", type="password", key="admin_pwd")
        if admin_pwd == "123456":
            st.error("⚠️ 危险区域")
            if st.button("🧨 工厂级重置 (清空所有数据)"):
                factory_reset()
                st.success("已重置！正在重新拉取数据...")
                load_images_from_cloudinary_to_db(force_refresh=True)
    
    # --- 主数据加载 ---
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        try: my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", conn, params=(current_user,)).iloc[0]['cnt']
        except: my_evals = 0
    except: images_df = pd.DataFrame(); my_evals = 0
    conn.close()

    if images_df.empty: st.warning("⏳ 正在加载数据，请稍候..."); return

    # --- 界面显示 ---
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
        if row['prompt_text']: st.info(f"**📝 Prompt:**\n{row['prompt_text']}")
        else: st.warning("⚠️ 暂无 Prompt")

        col_img, col_form = st.columns([1.2, 1])
        with col_img:
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"): st.code(f"File: {row['filepath']}\nID: {row['id']}")
                
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


