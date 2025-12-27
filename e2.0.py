import streamlit as st
import pandas as pd
import json
import os
import time
from datetime import datetime
from streamlit_gsheets import GSheetsConnection
import cloudinary
from cloudinary.utils import cloudinary_url

# 🔥 1. 页面配置
st.set_page_config(
    page_title="AI评分系统 (Google Sheets版)",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== Cloudinary 配置 (只用于生成图片链接，不调用管理API) =====
cloudinary.config(
    cloud_name="dwskobcad",
    api_key="676912851999589",
    api_secret="YIY48Z9VOM1zHfPWZvFKlHpyXzk",
    secure=True
)

# ===== 路径配置 =====
BASE_DIR = os.getcwd()
METADATA_DIR = os.path.join(BASE_DIR, "data_storage", "metadata")
# 本地 Prompt 文件路径 (作为图片源)
LOCAL_PROMPT_JSON = os.path.join(BASE_DIR, "cloudinary_image_map.json")

# 确保目录存在
os.makedirs(METADATA_DIR, exist_ok=True)

# ===== 🧠 用户ID管理 =====
def get_user_id():
    query_params = st.query_params
    if "user" in query_params:
        return query_params["user"]
    if "user_id" not in st.session_state:
        import uuid
        new_id = f"user_{uuid.uuid4().hex[:6]}"
        st.session_state.user_id = new_id
        st.query_params["user"] = new_id
        return new_id
    return st.session_state.user_id

# ===== ☁️ Google Sheets 连接核心 =====
def get_db_connection():
    # 使用 st.connection 连接 Google Sheets
    return st.connection("gsheets", type=GSheetsConnection)

def fetch_evaluations():
    """从 Google Sheets 读取所有评分"""
    conn = get_db_connection()
    try:
        # ttl=0 确保每次都从云端拉取最新数据，不缓存
        df = conn.read(worksheet="Evaluations", ttl=0)
        return df
    except Exception:
        # 如果表格是空的或者不存在，返回一个空的 DataFrame 结构
        return pd.DataFrame(columns=[
            "filepath", "user_id", "model_id", 
            "technical_quality", "intent_alignment", "game_usability", 
            "notes", "timestamp"
        ])

def save_to_gsheets(new_data_dict):
    """保存单条评分到 Google Sheets"""
    conn = get_db_connection()
    
    try:
        # 1. 读取现有数据
        existing_data = fetch_evaluations()
        
        # 2. 转换新数据为 DataFrame
        new_row = pd.DataFrame([new_data_dict])
        
        # 3. 检查是否已经评过 (覆盖逻辑)
        # 根据 filepath 和 user_id 判断
        mask = (existing_data["filepath"] == new_data_dict["filepath"]) & \
               (existing_data["user_id"] == new_data_dict["user_id"])
        
        if mask.any():
            # 更新现有行
            existing_data.update(new_row)
            updated_df = existing_data
            msg = "🔄 评分已更新 (云端)"
        else:
            # 追加新行 (使用 concat 替代 append)
            updated_df = pd.concat([existing_data, new_row], ignore_index=True)
            msg = "✅ 评分已保存 (云端)"
            
        # 4. 写回 Google Sheets
        conn.update(worksheet="Evaluations", data=updated_df)
        st.toast(msg)
        return True
        
    except Exception as e:
        st.error(f"云端保存失败: {e}")
        st.warning("可能是 API 配额限制或网络问题，请稍后重试。")
        return False

# ===== 🖼️ 加载图片列表 (从本地 JSON) =====
@st.cache_data
def load_images_from_json():
    """
    不再调用 Cloudinary API，直接读取本地 JSON 文件作为图片列表。
    这是最稳定、最快、最省额度的方法。
    """
    if not os.path.exists(LOCAL_PROMPT_JSON):
        return []
    
    try:
        with open(LOCAL_PROMPT_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        image_list = []
        # 将字典转换为列表格式
        for filename, prompt in data.items():
            # 简单的模型解析逻辑 (基于文件名规则)
            model = "unknown"
            if "dalle3" in filename: model = "dalle3"
            elif "sdxl" in filename: model = "sdxl_turbo"
            elif "dreamshaper" in filename: model = "dreamshaper"
            elif "sd15" in filename: model = "sd15"
            
            # 构建完整的 Cloudinary public_id
            # 假设结构是: ai-rating-images/{model}/{filename}
            # 如果之前的 filename 已经是完整路径则不需要拼
            full_path = f"ai-rating-images/{model}/{filename}" if "/" not in filename else filename
            
            image_list.append({
                "filepath": full_path,
                "filename": filename,
                "prompt": prompt,
                "model": model
            })
            
        return image_list
    except Exception as e:
        st.error(f"读取本地 JSON 失败: {e}")
        return []

# ===== 🌐 生成图片链接 =====
def get_image_url(filepath):
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
        return "https://via.placeholder.com/800x400?text=Image+Error"

# ===== 主程序 =====
def main():
    current_user = get_user_id()
    
    # 1. 加载图片源 (本地 JSON)
    all_images = load_images_from_json()
    
    # 2. 加载云端评分数据 (Google Sheets)
    # 为了性能，我们在 session_state 里缓存一下，保存时再强制刷新
    if "gsheet_data" not in st.session_state:
        with st.spinner("正在连接 Google Cloud 读取数据..."):
            st.session_state.gsheet_data = fetch_evaluations()
    
    # 计算进度
    total_images = len(all_images)
    try:
        # 筛选当前用户的评分
        my_evals_count = len(st.session_state.gsheet_data[st.session_state.gsheet_data["user_id"] == current_user])
    except:
        my_evals_count = 0

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("☁️ 评分系统 (Google Cloud)")
        st.info(f"用户 ID: **{current_user}**")
        st.success("✅ 数据实时同步至 Google Sheets")
        
        st.divider()
        st.metric("总图片数", total_images)
        st.metric("已完成", my_evals_count)
        st.progress(my_evals_count / total_images if total_images > 0 else 0)
        
        st.divider()
        with st.expander("🛠️ 调试工具"):
            if st.button("🔄 强制从云端重新拉取数据"):
                st.session_state.gsheet_data = fetch_evaluations()
                st.rerun()
            
            # 手动上传 JSON (如果本地没有)
            uploaded_json = st.file_uploader("更新 prompts.json", type="json")
            if uploaded_json:
                with open(LOCAL_PROMPT_JSON, "wb") as f:
                    f.write(uploaded_json.getbuffer())
                st.success("文件已更新，请刷新页面")

    if total_images == 0:
        st.warning("⚠️ 未找到图片列表。请在侧边栏上传 `final_prompts_translated.json` 文件。")
        return

    # --- 分页逻辑 ---
    if 'page_number' not in st.session_state: st.session_state.page_number = 1
    
    col_prev, col_page, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("⬅️ 上一张") and st.session_state.page_number > 1: 
            st.session_state.page_number -= 1; st.rerun()
    with col_page:
        st.session_state.page_number = st.number_input("页码", 1, total_images, st.session_state.page_number, label_visibility="collapsed")
    with col_next:
        if st.button("下一张 ➡️") and st.session_state.page_number < total_images: 
            st.session_state.page_number += 1; st.rerun()

    # --- 当前图片数据 ---
    idx = st.session_state.page_number - 1
    img_data = all_images[idx]
    
    # 查找该图是否已评过
    existing_score = {}
    if not st.session_state.gsheet_data.empty:
        # 在 DataFrame 中查找
        record = st.session_state.gsheet_data[
            (st.session_state.gsheet_data["filepath"] == img_data["filepath"]) & 
            (st.session_state.gsheet_data["user_id"] == current_user)
        ]
        if not record.empty:
            existing_score = record.iloc[0].to_dict()

    # --- 主界面 ---
    st.markdown("---")
    
    # 显示 Prompt
    prompt_display = img_data['prompt'] if img_data['prompt'] else "暂无 Prompt"
    st.info(f"**📝 Prompt:**\n{prompt_display}")

    col_img, col_form = st.columns([1.2, 1])
    
    with col_img:
        st.image(get_image_url(img_data['filepath']), use_container_width=True)
        with st.expander("详细信息"):
            st.code(f"File: {img_data['filename']}\nModel: {img_data['model']}")

    with col_form:
        with st.form(key=f"form_{idx}"):
            st.subheader("评分维度")
            
            # 安全获取分数的辅助函数
            def get_val(key, default=3):
                try: return int(existing_score.get(key, default))
                except: return default

            t_q = st.slider("维度1：技术质量", 1, 5, get_val('technical_quality'))
            i_a = st.slider("维度2：意图对齐", 1, 5, get_val('intent_alignment'))
            g_u = st.slider("维度3：开发可用性", 1, 5, get_val('game_usability'))
            
            # 处理备注（处理 NaN 也就是空值的情况）
            note_val = existing_score.get('notes', '')
            if pd.isna(note_val): note_val = ""
            notes = st.text_area("备注", str(note_val))
            
            if st.form_submit_button("💾 保存并同步到 Google Cloud", type="primary", use_container_width=True):
                # 构造要保存的数据字典
                data_to_save = {
                    "filepath": img_data["filepath"],
                    "user_id": current_user,
                    "model_id": img_data["model"],
                    "technical_quality": t_q,
                    "intent_alignment": i_a,
                    "game_usability": g_u,
                    "notes": notes,
                    "timestamp": datetime.now().isoformat()
                }
                
                # 执行保存
                if save_to_gsheets(data_to_save):
                    # 更新本地缓存，这样不用重新拉取就能看到进度更新
                    st.session_state.gsheet_data = fetch_evaluations()
                    time.sleep(0.5) # 给一点点缓冲
                    
                    # 自动翻页
                    if st.session_state.page_number < total_images:
                        st.session_state.page_number += 1
                        st.rerun()

if __name__ == "__main__":
    main()



