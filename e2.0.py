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
    page_title="AI游戏美术评分系统",
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
BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, "data_storage")
METADATA_DIR = os.path.join(DATA_DIR, "metadata")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")
LOCAL_PROMPT_JSON = os.path.join(METADATA_DIR, "final_prompts_translated.json")

for p in [DATA_DIR, METADATA_DIR]:
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

# ===== 💾 数据库结构 (已修改为3个维度) =====
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
    
    # 评分表 (3个核心维度)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER, 
            evaluator_id TEXT,
            
            technical_quality INTEGER,  -- 维度1：技术质量
            intent_alignment INTEGER,   -- 维度2：意图对齐
            game_usability INTEGER,     -- 维度3：开发可用性
            
            notes TEXT,                 -- 备注
            evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== 🧹 工厂重置 =====
def factory_reset():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS images")
    cursor.execute("DROP TABLE IF EXISTS evaluations")
    conn.commit()
    conn.close()
    init_database()

# ===== 📥 Prompt 导入 =====
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
        if cursor.fetchone()[0] > 100: 
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
    except Exception as e: st.error(f"加载出错: {e}")
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

# ===== 保存评分 (3维度版) =====
def save_evaluation(image_id, user_id, scores):
    image_id = int(image_id) # 强制转int
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT id FROM evaluations WHERE image_id=? AND evaluator_id=?", (image_id, user_id))
    exists = cursor.fetchone()
    
    data = (
        user_id,
        scores['technical_quality'], 
        scores['intent_alignment'], 
        scores['game_usability'], 
        scores['notes'],
        now
    )
    try:
        if exists:
            sql = '''UPDATE evaluations SET 
                     evaluator_id=?, technical_quality=?, intent_alignment=?, game_usability=?,
                     notes=?, evaluation_time=? WHERE id=?'''
            cursor.execute(sql, data + (exists[0],))
            msg = "🔄 更新成功"
        else:
            sql = '''INSERT INTO evaluations (
                     evaluator_id, technical_quality, intent_alignment, game_usability,
                     notes, evaluation_time, image_id
                     ) VALUES (?,?,?,?,?,?,?)'''
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
    if not os.path.exists(DB_PATH): init_database()
    load_images_from_cloudinary_to_db(force_refresh=False)
    current_user = get_user_id()

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("👤 评分系统 ")
        st.info(f"ID: **{current_user}**")
        st.caption("保留地址栏链接以保存进度。")
        
        with st.expander("🔐 找回之前的进度"):
            input_id = st.text_input("输入旧ID", key="restore_id_input")
            if st.button("恢复"):
                if input_id: st.query_params["user"]=input_id.strip(); st.session_state.user_id=input_id.strip(); st.rerun()

        st.divider()
        with st.expander("🛠️ Prompt 修复工具"):
            uploaded_prompt_file = st.file_uploader("上传 final_prompts_translated.json", type="json")
            if uploaded_prompt_file and st.button("手动导入"):
                cnt = import_prompts_from_json(uploaded_prompt_file)
                st.success(f"成功更新 {cnt} 条")
                time.sleep(1)
                st.rerun()

        st.divider()
        st.subheader("📊 数据导出")
        if st.button("🔄 刷新并查看表格"):
            conn = sqlite3.connect(DB_PATH)
            sql = '''
            SELECT 
                e.id as 评分ID, e.image_id as [关键_图片ID], 
                e.evaluator_id as 评分员, i.model_id as 模型,
                i.filepath as 路径, i.prompt_text as Prompt,
                e.technical_quality as [D1_技术质量], 
                e.intent_alignment as [D2_意图对齐],
                e.game_usability as [D3_开发可用性],
                e.notes as 备注, e.evaluation_time as 时间
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
            
        st.divider()
        admin_pwd = st.text_input("管理员密码", type="password", key="admin_pwd")
        if admin_pwd == "123456":
            st.error("⚠️ 危险区域")
            st.warning("更换了评分维度，旧数据不兼容，请务必先点击下方按钮！")
            if st.button("🧨 工厂级重置 (新评分标准专用)"):
                factory_reset()
                st.success("已重置！正在初始化新表...")
                load_images_from_cloudinary_to_db(force_refresh=True)

    # --- 主数据加载 ---
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        try: my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", conn, params=(current_user,)).iloc[0]['cnt']
        except: my_evals = 0
    except: images_df = pd.DataFrame(); my_evals = 0
    conn.close()

    if images_df.empty: st.warning("⏳ 正在初始化，请稍候..."); return

    # --- 界面 ---
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
        
        # 评分标准参考 (折叠起来，需要时看)
        with st.expander("📖 查看评分标准指南 (技术/意图/可用性)", expanded=False):
            st.markdown("""
            | 分数 | **技术质量 (清晰/色彩/构图)** | **意图对齐 (Prompt匹配度)** | **开发可用性 (进引擎)** |
            | :--- | :--- | :--- | :--- |
            | **5** | **优秀**：清晰锐利，无瑕疵 | **完美**：所有元素/风格完全一致 | **直接用**：无需修改 |
            | **4** | **良好**：轻微模糊/偏差 | **高度**：核心正确，次要偏差 | **微调用**：简单调色/裁剪 |
            | **3** | **一般**：明显噪点/模糊 | **大致**：风格或关键属性有误 | **中修**：需美术师重绘/修复 |
            | **2** | **较差**：严重扭曲/失真 | **部分**：关键元素缺失/错误 | **大修**：仅作参考/素材 |
            | **1** | **极差**：无法辨认/伪影 | **无关**：完全不匹配 | **废弃**：完全不可用 |
            """)

        if row['prompt_text']: st.info(f"**📝 Prompt:**\n{row['prompt_text']}")
        else: st.warning("⚠️ 暂无 Prompt")

        col_img, col_form = st.columns([1.2, 1])
        with col_img:
            st.image(get_cloud_image_url(row['filepath']), use_container_width=True)
            with st.expander("调试信息"): st.code(f"File: {row['filepath']}\nID: {row['id']}")
                
        with col_form:
            with st.form(key=f"form_{row['id']}"):
                st.subheader("📝 评分")
                
                # 维度 1
                tech_q = st.slider(
                    "维度1：技术质量 (Technical Quality)", 1, 5, existing.get('technical_quality', 3),
                    help="5分：清晰锐利无瑕疵 | 3分：明显噪点模糊 | 1分：无法辨认"
                )
                
                # 维度 2
                intent_a = st.slider(
                    "维度2：意图对齐 (Intent Alignment)", 1, 5, existing.get('intent_alignment', 3),
                    help="5分：完美符合提示词 | 3分：风格/关键属性有误 | 1分：完全无关"
                )
                
                # 维度 3
                game_u = st.slider(
                    "维度3：开发可用性 (Game Usability)", 1, 5, existing.get('game_usability', 3),
                    help="5分：直接进引擎 | 3分：需美术师重绘 | 1分：完全不可用"
                )
                
                notes = st.text_area("备注", existing.get('notes', ''))
                
                if st.form_submit_button("💾 保存并下一张", type="primary", use_container_width=True):
                    scores = {
                        "technical_quality": tech_q,
                        "intent_alignment": intent_a,
                        "game_usability": game_u,
                        "notes": notes
                    }
                    if save_evaluation(row['id'], current_user, scores):
                        if st.session_state.page_number < total_pages: st.session_state.page_number += 1; st.rerun()

if __name__ == "__main__":
    main()

