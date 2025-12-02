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

# ===== Cloudinary 配置 (请保持你的 Key) =====
cloudinary.config(
    cloud_name="dwskobcad",
    api_key="676912851999589",
    api_secret="YIY48Z9VOM1zHfPWZvFKlHpyXzk",
    secure=True
)
CLOUDINARY_ROOT_FOLDER = "ai-rating-images"

# ===== 路径配置 =====
# 自动判断环境
if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    DATASET_ROOT = os.path.join(os.getcwd(), "ai_dataset_project")
else:
    DATASET_ROOT = "D:/ai_dataset_project" # 本地路径

OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

# 确保目录存在
for p in [OUTPUT_DIR, METADATA_DIR]:
    os.makedirs(p, exist_ok=True)

# ===== 🧠 核心功能：自动用户ID管理 =====
def get_user_id():
    """
    自动生成或获取用户ID。
    优先检查 URL 参数 (?user=xxx)，如果没有则生成随机 ID 并写入 URL。
    """
    query_params = st.query_params
    
    # 1. 检查 URL 中是否有 user 参数
    if "user" in query_params:
        return query_params["user"]
    
    # 2. 检查 Session State
    if "user_id" not in st.session_state:
        # 生成一个短 UUID (如 user_a1b2c3)
        new_id = f"user_{uuid.uuid4().hex[:6]}"
        st.session_state.user_id = new_id
        # 写入 URL，这样刷新页面 ID 不会丢
        st.query_params["user"] = new_id
        return new_id
    
    return st.session_state.user_id

# ===== 数据库结构升级 =====
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 图片表 (不变)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT, model_id TEXT, image_number INTEGER, filepath TEXT UNIQUE,
            prompt_text TEXT, type TEXT, style TEXT, model_name TEXT, quality_tier TEXT, generation_time TEXT
        )
    ''')

    # 评分表 (🏆 大幅升级：包含游戏专业指标)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER,
            evaluator_id TEXT,     -- 自动生成的 ID
            
            -- 🎨 基础美学 (1-5)
            clarity INTEGER,        -- 清晰度
            detail_richness INTEGER,-- 细节丰富度
            color_harmony INTEGER,  -- 色彩和谐度
            
            -- 🎮 游戏工业标准 (1-5)
            perspective_check INTEGER, -- 透视准确性 (ISO/Topdown是否标准)
            asset_cleanliness INTEGER, -- 资产干净度 (背景是否易抠图/无杂色)
            style_consistency INTEGER, -- 风格一致性 (能否放入同一游戏包)
            structural_logic INTEGER,  -- 结构合理性 (关节/机械结构是否正常)
            
            -- 📝 结论
            overall_quality INTEGER,   -- 整体评分
            is_usable TEXT,            -- 是否可用 (是/否/需修改)
            notes TEXT,                -- 备注
            
            evaluation_time TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    conn.commit()
    conn.close()

# ===== 纯本地生成 URL (无需 API 调用) =====
def get_cloud_image_url(filepath: str) -> str:
    try:
        url, _ = cloudinary_url(
            filepath,
            width=800,
            crop="limit",
            quality="auto",
            fetch_format="auto",
            secure=True
        )
        return url
    except:
        return "https://via.placeholder.com/800x800?text=URL+Error"

# ===== 保存评分 =====
def save_evaluation(image_id, user_id, scores):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    # 检查该用户是否已评过此图
    cursor.execute("SELECT id FROM evaluations WHERE image_id=? AND evaluator_id=?", (image_id, user_id))
    exists = cursor.fetchone()
    
    # 准备数据
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
            # 更新
            sql = '''UPDATE evaluations SET 
                     evaluator_id=?, clarity=?, detail_richness=?, color_harmony=?,
                     perspective_check=?, asset_cleanliness=?, style_consistency=?, structural_logic=?,
                     overall_quality=?, is_usable=?, notes=?, evaluation_time=?
                     WHERE id=?'''
            cursor.execute(sql, data + (exists[0],))
            msg = "🔄 评分已更新"
        else:
            # 插入
            sql = '''INSERT INTO evaluations (
                     evaluator_id, clarity, detail_richness, color_harmony,
                     perspective_check, asset_cleanliness, style_consistency, structural_logic,
                     overall_quality, is_usable, notes, evaluation_time, image_id
                     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)'''
            cursor.execute(sql, data + (image_id,))
            msg = "✅ 评分已保存"
            
        conn.commit()
        st.toast(msg) # 使用 Toast 提示更优雅
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False
    finally:
        conn.close()

# ===== 获取已有评分 =====
def get_existing_score(image_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM evaluations WHERE image_id=? AND evaluator_id=?", 
                     conn, params=(image_id, user_id))
    conn.close()
    if not df.empty:
        return df.iloc[0].to_dict()
    return {}

# ===== 辅助：获取本机 IP (方便手机访问) =====
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

# ===== 主程序 =====
def main():
    # 初始化数据库
    if not os.path.exists(DB_PATH):
        init_database()

    # 1. 获取当前用户 ID
    current_user = get_user_id()

    # 2. 侧边栏：用户信息与局域网连接
    with st.sidebar:
        st.title("👤 评分员信息")
        st.info(f"当前 ID: **{current_user}**")
        st.caption("系统自动分配，不同设备ID不同")
        
        st.divider()
        st.subheader("📱 手机/多人协作")
        local_ip = get_local_ip()
        st.write("在同一 WiFi 下，其他人可通过以下地址访问：")
        st.code(f"http://{local_ip}:8501")
        
        st.divider()
        if st.button("⚠️ 强制重置数据库结构"):
             # 仅用于增加新列，不建议频繁使用
            init_database()
            st.success("表结构已更新")

    # 3. 加载图片数据
    conn = sqlite3.connect(DB_PATH)
    try:
        images_df = pd.read_sql("SELECT * FROM images", conn)
        # 获取当前用户的已评分数量
        my_evals = pd.read_sql("SELECT COUNT(*) as cnt FROM evaluations WHERE evaluator_id=?", 
                               conn, params=(current_user,)).iloc[0]['cnt']
    except:
        images_df = pd.DataFrame()
        my_evals = 0
    conn.close()

    if images_df.empty:
        st.error("数据库为空。请运行之前的加载代码先获取图片数据。")
        return

    # 4. 顶部进度条
    col1, col2, col3 = st.columns(3)
    col1.metric("总图片数", len(images_df))
    col2.metric("我的进度", f"{my_evals} / {len(images_df)}")
    col3.metric("完成率", f"{my_evals/len(images_df)*100:.1f}%")
    st.progress(my_evals/len(images_df) if len(images_df)>0 else 0)

    # 5. 分页显示
    limit = 1
    total_pages = len(images_df)
    
    # 使用 Session State 保持页码
    if 'page_number' not in st.session_state:
        st.session_state.page_number = 1
        
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

    # 获取当前图片
    idx = st.session_state.page_number - 1
    row = images_df.iloc[idx]
    
    # 获取已有评分（回显）
    existing = get_existing_score(row['id'], current_user)

    st.markdown("---")
    
    # 6. 评分界面布局
    col_img, col_form = st.columns([1.2, 1])
    
    with col_img:
        st.subheader(f"🖼️ {row['model_id']} | 图片 {row['image_number']}")
        img_url = get_cloud_image_url(row['filepath'])
        st.image(img_url, use_container_width=True)
        with st.expander("调试信息"):
            st.code(row['filepath'])
            
    with col_form:
        st.subheader("📝 专业游戏资产评分")
        
        with st.form(key=f"form_{row['id']}"):
            # 第一部分：游戏工业标准 (最重要的放前面)
            st.markdown("#### 🛠️ 游戏工业标准 (核心指标)")
            
            c1, c2 = st.columns(2)
            with c1:
                perspective = st.slider("透视准确性（是否扭曲） (Perspective)", 1, 5, existing.get('perspective_check', 3), 
                                      help="透视是否扭曲？是否符合特定的游戏视角（如ISO/顶视图）？")
                asset_clean = st.slider("资产干净度（边缘是否清晰） (Cleanliness)", 1, 5, existing.get('asset_cleanliness', 3), 
                                      help="边缘是否清晰？背景是否容易去除（Matting）？有无伪影？")
            with c2:
                struct_logic = st.slider("结构合理性 (Structure)", 1, 5, existing.get('structural_logic', 3), 
                                       help="物体结构是否合理？例如人体关节、建筑支撑结构是否符合逻辑？")
                style_const = st.slider("风格一致性 (Consistency)", 1, 5, existing.get('style_consistency', 3), 
                                      help="是否具有明显的风格特征？能否直接放入统一风格的游戏包中？")

            st.markdown("---")
            
            # 第二部分：基础美学
            st.markdown("#### 🎨 基础美术质量")
            c3, c4 = st.columns(2)
            with c3:
                clarity = st.slider("清晰度 (Clarity)", 1, 5, existing.get('clarity', 3))
                detail = st.slider("细节丰富度 (Detail)", 1, 5, existing.get('detail_richness', 3))
            with c4:
                color = st.slider("色彩和谐度 (Color)", 1, 5, existing.get('color_harmony', 3))

            st.markdown("---")
            
            # 第三部分：结论
            overall = st.slider("⭐ 整体评分", 1, 5, existing.get('overall_quality', 3))
            is_usable = st.radio("🎮 是否可直接进游戏？", ["是", "否", "需微调"], 
                               index=["是", "否", "需微调"].index(existing.get('is_usable', '否')),
                               horizontal=True)
            
            notes = st.text_area("备注/缺陷描述", existing.get('notes', ''))
            
            # 提交按钮
            submit = st.form_submit_button("💾 保存评分", type="primary", use_container_width=True)
            
            if submit:
                scores = {
                    "clarity": clarity, "detail_richness": detail, "color_harmony": color,
                    "perspective_check": perspective, "asset_cleanliness": asset_clean,
                    "structural_logic": struct_logic, "style_consistency": style_const,
                    "overall_quality": overall, "is_usable": is_usable, "notes": notes
                }
                if save_evaluation(row['id'], current_user, scores):
                    # 自动跳转下一页逻辑（可选）
                    if st.session_state.page_number < total_pages:
                        st.session_state.page_number += 1
                        st.rerun()

if __name__ == "__main__":
    main()
