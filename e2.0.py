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

# 🔥 这一行必须放在所有 st. 命令的最前面！
st.set_page_config(
    page_title="AI游戏图像质量评价系统",
    page_icon="🎮",
    layout="wide"
)

# ===== Cloudinary 配置 =====
# 替换为你自己的Cloudinary Dashboard信息
cloudinary.config(
    cloud_name="dwskobcad",
    api_key="676912851999589",
    api_secret="YIY48Z9VOM1zHfPWZvFKlHpyXzk",
    secure=True
)

# 你在Cloudinary中存放图片的根文件夹名（必须和实际上传的一致）
CLOUDINARY_ROOT_FOLDER = "ai-rating-images"

# ===== 路径配置 & 可写性校验 =====
def ensure_writable_dir(path):
    """确保目录存在且可写"""
    try:
        os.makedirs(path, exist_ok=True)
        # 测试写入权限
        test_file = os.path.join(path, ".test_write_perm")
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
        return True
    except Exception as e:
        st.error(f"❌ 目录不可写: {path} | 错误: {str(e)}")
        return False

# 适配本地/云端环境路径
if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    # 云端环境：使用当前工作目录（Streamlit Cloud可写）
    DATASET_ROOT = os.path.join(os.getcwd(), "ai_dataset_project")
else:
    # 本地环境：保留原路径
    DATASET_ROOT = "D:/ai_dataset_project"

OUTPUT_DIR = os.path.join(DATASET_ROOT, "images")
METADATA_DIR = os.path.join(DATASET_ROOT, "metadata")
EVALUATION_DIR = os.path.join(DATASET_ROOT, "evaluations")
DB_PATH = os.path.join(METADATA_DIR, "image_index.db")

# 确保所有目录可写
for dir_path in [OUTPUT_DIR, METADATA_DIR, EVALUATION_DIR]:
    ensure_writable_dir(dir_path)

# ===== 数据库初始化 =====
def init_database():
    """初始化SQLite数据库表结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 图片索引表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_id TEXT,
            model_id TEXT,
            image_number INTEGER,
            filepath TEXT UNIQUE,   -- 存储Cloudinary的public_id（不含扩展名）
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

# 在代码开头附近添加这个辅助函数
def is_image_file(public_id: str, format: str) -> bool:
    """判断是否为图片文件"""
    # 排除JSON文件
    if (".info.json" in public_id or 
        public_id.endswith(".json") or
        format == "json" or
        "thumb" in public_id.lower() or
        "_thumb" in public_id.lower()):
        return False
    
    # 只接受常见的图片格式
    image_formats = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"]
    return format.lower() in image_formats

# ===== 从Cloudinary API拉取图片资源并初始化数据库 =====
# 修改 load_images_from_cloudinary_to_db 函数
def load_images_from_cloudinary_to_db(force_refresh=False):
    """
    从Cloudinary拉取资源并初始化数据库
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    loaded_count = 0
    
    st.info(f"🔍 从Cloudinary拉取资源：{CLOUDINARY_ROOT_FOLDER}/*")

    if force_refresh:
        st.warning("⚠️ 强制刷新模式：清空现有图片记录")
        cursor.execute("DELETE FROM images")
        conn.commit()

    try:
        cloudinary.api.ping()
        st.success(f"✅ Cloudinary API连通正常！")
        
        # 修改：直接使用resources_by_tag或其他方法获取所有图片
        # Cloudinary API的folders参数可能有问题，我们尝试不同的方法
        
        st.info("🔄 尝试不同的Cloudinary API调用方法...")
        
        # 方法1: 使用resources_by_tag（如果所有图片都打了标签）
        try:
            # 获取所有资源，不分文件夹
            all_resources = cloudinary.api.resources(
                type="upload",
                max_results=500,
                resource_type="image"
            )
            
            st.info(f"📊 Cloudinary账户中共有 {all_resources.get('total_count', 0)} 个资源")
            
            # 过滤出我们目标文件夹的资源
            target_resources = []
            for res in all_resources.get('resources', []):
                public_id = res.get('public_id', '')
                if public_id.startswith(f"{CLOUDINARY_ROOT_FOLDER}/"):
                    target_resources.append(res)
            
            st.info(f"✅ 找到 {len(target_resources)} 个目标文件夹中的资源")
            
            # 如果没有找到资源，尝试其他方法
            if len(target_resources) == 0:
                st.warning("⚠️ 未在指定文件夹中找到资源，尝试直接搜索...")
                
                # 获取所有资源，然后手动过滤
                all_resources = cloudinary.api.resources(
                    type="upload",
                    max_results=1000,
                    resource_type="image"
                )
                
                target_resources = []
                for res in all_resources.get('resources', []):
                    public_id = res.get('public_id', '')
                    # 检查是否包含目标文件夹
                    if f"/{CLOUDINARY_ROOT_FOLDER}/" in public_id or public_id.startswith(f"{CLOUDINARY_ROOT_FOLDER}/"):
                        target_resources.append(res)
                
                st.info(f"📊 通过直接搜索找到 {len(target_resources)} 个资源")
            
        except Exception as e:
            st.error(f"❌ 获取资源列表失败: {str(e)}")
            return 0
        
        if len(target_resources) == 0:
            st.error("❌ 未找到任何资源，请检查Cloudinary配置和文件夹名称")
            return 0
        
        # 进度条
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 按模型分组处理
        model_groups = {}
        for res in target_resources:
            public_id = res.get('public_id', '')
            # 提取模型ID
            parts = public_id.split('/')
            if len(parts) >= 3:
                model_id = parts[-2]  # 倒数第二部分是模型文件夹
                if model_id not in model_groups:
                    model_groups[model_id] = []
                model_groups[model_id].append(res)
        
        st.success(f"✅ 找到 {len(model_groups)} 个模型的图片")
        
        for idx, (model_id, resources) in enumerate(model_groups.items()):
            status_text.text(f"🔄 处理模型: {model_id} ({idx+1}/{len(model_groups)})")
            
            for res in resources:
                public_id = res.get('public_id', '')
                format = res.get('format', '').lower()
                
                # 跳过非图片文件
                if not is_image_file(public_id, format):
                    continue
                
                # 提取文件名（不含扩展名和路径）
                filename_with_ext = os.path.basename(public_id)
                filename = os.path.splitext(filename_with_ext)[0]
                
                # 解析文件名
                parts = filename.split('_')
                
                # 确定prompt_id和image_number
                prompt_id = filename
                image_number = 1
                
                # 尝试从文件名中提取信息
                if len(parts) >= 5:
                    # 查找模型名在文件名中的位置
                    for i, part in enumerate(parts):
                        if part == model_id or part in model_id:
                            # 模型名之前的部分作为prompt_id
                            prompt_id = '_'.join(parts[:i]) if i > 0 else filename
                            # 模型名之后的数字作为image_number
                            if i+1 < len(parts) and parts[i+1].isdigit():
                                image_number = int(parts[i+1])
                            break
                
                # 读取自定义元数据
                context = res.get("context", {}).get("custom", {})
                
                # 尝试从同名的JSON文件读取更多元数据
                json_public_id = public_id + ".info.json"
                try:
                    json_resource = cloudinary.api.resource(json_public_id, resource_type="raw")
                    if json_resource.get("context", {}).get("custom"):
                        context.update(json_resource["context"]["custom"])
                except:
                    pass
                
                metadata = {
                    "prompt": context.get("prompt", f"Prompt: {prompt_id}"),
                    "type": context.get("type", "unknown"),
                    "style": context.get("style", "unknown"),
                    "model_name": context.get("model_name", model_id),
                    "quality_tier": context.get("quality_tier", "medium"),
                    "generation_time": res.get("created_at", datetime.now().isoformat())
                }
                
                # 插入或更新数据库记录
                cursor.execute('''
                    INSERT OR REPLACE INTO images (
                        prompt_id, model_id, image_number, filepath,
                        prompt_text, type, style, model_name, quality_tier, generation_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    prompt_id,
                    model_id,
                    image_number,
                    public_id,  # 使用完整的public_id
                    metadata["prompt"],
                    metadata["type"],
                    metadata["style"],
                    metadata["model_name"],
                    metadata["quality_tier"],
                    metadata["generation_time"]
                ))
                
                loaded_count += 1
                
                if loaded_count % 50 == 0:
                    conn.commit()
                    status_text.text(f"✅ 已加载 {loaded_count} 张图片...")
            
            # 更新进度条
            progress_bar.progress(min((idx + 1) / len(model_groups), 1.0))
        
        conn.commit()
        
        # 统计实际加载的图片数量
        cursor.execute("SELECT COUNT(*) FROM images")
        total_loaded = cursor.fetchone()[0]
        
        if total_loaded > 0:
            st.success(f"🎉 成功从Cloudinary加载 {total_loaded} 张图片到数据库！")
            
            # 显示各模型数量统计
            cursor.execute("SELECT model_id, COUNT(*) as count FROM images GROUP BY model_id")
            model_stats = cursor.fetchall()
            
            st.subheader("📊 各模型图片数量统计")
            for model_id, count in model_stats:
                st.write(f"- **{model_id}**: {count} 张图片")
            
            # 显示总体统计
            st.subheader("📈 总体统计")
            st.write(f"**实际加载**: {total_loaded} 张")
            
            if total_loaded < 100:  # 如果加载的图片很少，给出警告
                st.warning(f"⚠️ 只加载了 {total_loaded} 张图片，可能没有正确获取所有资源")
        else:
            st.error("❌ 没有加载任何图片到数据库")
        
    except Exception as e:
        st.error(f"❌ 拉取Cloudinary资源失败: {str(e)}")
        import traceback
        st.error(f"🔍 错误栈详情: {traceback.format_exc()}")
        conn.close()
        return 0
    
    conn.close()
    return loaded_count

# ===== 生成Cloudinary图片可访问URL =====
def get_cloud_image_url(public_id: str) -> str:
    """
    根据public_id生成正确的Cloudinary URL
    """
    try:
        # 先校验资源是否存在
        try:
            resource = cloudinary.api.resource(public_id, resource_type="image")
            # 确保是图片
            if not is_image_file(public_id, resource.get('format', '')):
                st.warning(f"⚠️ {public_id} 不是图片文件")
                return "https://via.placeholder.com/800x800?text=Not+Image"
        except NotFound:
            st.warning(f"⚠️ 图片不存在: {public_id}")
            return "https://via.placeholder.com/800x800?text=Image+Not+Found"
        
        # 生成优化后的URL：限制尺寸、自动质量压缩
        url, _ = cloudinary_url(
            public_id,
            resource_type="image",
            width=800,
            height=800,
            crop="limit",
            quality="auto:good",
            format="auto",
            secure=True
        )
        return url
        
    except Exception as e:
        st.error(f"❌ 加载图片失败: {str(e)}")
        return "https://via.placeholder.com/800x800?text=Error+Loading+Image"

# ===== 评分操作函数 =====
def get_evaluation(image_id, evaluator_id):
    """获取指定图片和评分员的已有评分"""
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

def save_evaluation(image_id, evaluator_id, evaluator_name, scores):
    """完整实现：保存/更新评分到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        existing_eval = get_evaluation(image_id, evaluator_id)
        current_time = datetime.now().isoformat()

        if existing_eval:
            # 更新已有评分
            cursor.execute('''
                UPDATE evaluations SET
                    evaluator_name = ?,
                    clarity = ?, detail_richness = ?, color_accuracy = ?, lighting_quality = ?, composition = ?,
                    prompt_match = ?, style_consistency = ?, subject_completeness = ?,
                    game_usability = ?, needs_fix = ?, direct_use = ?,
                    major_defects = ?, minor_issues = ?,
                    overall_quality = ?, grade = ?, notes = ?,
                    evaluation_time = ?
                WHERE id = ?
            ''', (
                evaluator_name,
                scores.get('clarity', 3), scores.get('detail_richness', 3), scores.get('color_accuracy', 3),
                scores.get('lighting_quality', 3), scores.get('composition', 3),
                scores.get('prompt_match', 3), scores.get('style_consistency', 3), scores.get('subject_completeness', 3),
                scores.get('game_usability', 3), scores.get('needs_fix', '否'), scores.get('direct_use', '否'),
                scores.get('major_defects', ''), scores.get('minor_issues', ''),
                scores.get('overall_quality', 3), scores.get('grade', 'B'), scores.get('notes', ''),
                current_time,
                existing_eval['id']
            ))
            st.success("✅ 评分已更新")
        else:
            # 插入新评分
            cursor.execute('''
                INSERT INTO evaluations (
                    image_id, evaluator_id, evaluator_name,
                    clarity, detail_richness, color_accuracy, lighting_quality, composition,
                    prompt_match, style_consistency, subject_completeness,
                    game_usability, needs_fix, direct_use,
                    major_defects, minor_issues,
                    overall_quality, grade, notes,
                    evaluation_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                image_id, evaluator_id, evaluator_name,
                scores.get('clarity', 3), scores.get('detail_richness', 3), scores.get('color_accuracy', 3),
                scores.get('lighting_quality', 3), scores.get('composition', 3),
                scores.get('prompt_match', 3), scores.get('style_consistency', 3), scores.get('subject_completeness', 3),
                scores.get('game_usability', 3), scores.get('needs_fix', '否'), scores.get('direct_use', '否'),
                scores.get('major_defects', ''), scores.get('minor_issues', ''),
                scores.get('overall_quality', 3), scores.get('grade', 'B'), scores.get('notes', ''),
                current_time
            ))
            st.success("✅ 评分已保存")
        
        conn.commit()
    except Exception as e:
        st.error(f"❌ 保存评分失败: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

# ===== 统计分析页面 =====
def show_statistics():
    st.title("📊 评分统计分析")
    
    conn = sqlite3.connect(DB_PATH)
    
    # 总评分统计
    total_eval = pd.read_sql("SELECT COUNT(*) as count FROM evaluations", conn)['count'][0]
    total_images = pd.read_sql("SELECT COUNT(*) as count FROM images", conn)['count'][0]
    
    # 按模型的评分分布
    model_eval_stats = pd.read_sql('''
        SELECT 
            i.model_name,
            COUNT(e.id) as eval_count,
            AVG(e.overall_quality) as avg_overall
        FROM evaluations e
        JOIN images i ON e.image_id = i.id
        GROUP BY i.model_name
        ORDER BY avg_overall DESC
    ''', conn)
    
    # 按评分员的完成度
    evaluator_stats = pd.read_sql('''
        SELECT 
            evaluator_id,
            evaluator_name,
            COUNT(DISTINCT image_id) as eval_count
        FROM evaluations
        GROUP BY evaluator_id, evaluator_name
        ORDER BY eval_count DESC
    ''', conn)
    
    conn.close()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("总评分记录数", total_eval)
        st.metric("已评分图片占比", f"{(total_eval / total_images * 100) if total_images >0 else 0:.1f}%")
    
    with col2:
        st.subheader("按模型平均质量评分")
        st.dataframe(model_eval_stats.style.format({"avg_overall": "{:.2f}"}))
    
    st.subheader("评分员完成度统计")
    st.dataframe(evaluator_stats)

# ===== 快速诊断函数 =====
def quick_diagnostic():
    """快速诊断函数"""
    st.title("🚨 快速诊断")
    
    st.info("正在测试Cloudinary连接...")
    try:
        result = cloudinary.api.ping()
        st.success("✅ Cloudinary API连接成功")
    except Exception as e:
        st.error(f"❌ API连接失败: {str(e)}")
        return
    
    # 检查目标文件夹
    st.info(f"检查文件夹: {CLOUDINARY_ROOT_FOLDER}")
    try:
        # 首先尝试获取所有资源
        all_resources = cloudinary.api.resources(
            type="upload",
            max_results=100,
            resource_type="image"
        )
        
        total = all_resources.get('total_count', 0)
        actual_resources = all_resources.get('resources', [])
        
        if actual_resources:
            st.success(f"✅ Cloudinary账户中共有 {total} 个资源")
            
            # 查找目标文件夹中的资源
            target_resources = []
            for res in actual_resources:
                public_id = res.get('public_id', '')
                if f"{CLOUDINARY_ROOT_FOLDER}/" in public_id:
                    target_resources.append(res)
            
            if target_resources:
                st.success(f"✅ 在 {CLOUDINARY_ROOT_FOLDER} 中找到 {len(target_resources)} 个资源")
                
                # 显示前几个资源
                for i, res in enumerate(target_resources[:5]):
                    st.write(f"{i+1}. `{res['public_id']}` - 格式: {res.get('format', 'unknown')}")
                    
                    # 尝试显示图片预览
                    try:
                        thumb_url, _ = cloudinary_url(
                            res['public_id'],
                            width=200,
                            height=200,
                            crop="fill",
                            quality="auto:low"
                        )
                        st.image(thumb_url, caption=res['public_id'], width=200)
                    except:
                        st.write("无法生成预览")
            else:
                st.warning(f"⚠️ 在 {CLOUDINARY_ROOT_FOLDER} 中没有找到资源")
                
                # 列出所有资源的前缀
                st.write("📋 所有资源的前缀:")
                prefixes = set()
                for res in actual_resources[:20]:
                    public_id = res.get('public_id', '')
                    if '/' in public_id:
                        prefix = public_id.split('/')[0]
                        prefixes.add(prefix)
                
                st.write("检测到的文件夹前缀:")
                for prefix in sorted(prefixes):
                    st.write(f"- `{prefix}`")
                
        else:
            st.error("❌ Cloudinary账户中没有资源")
    except Exception as e:
        st.error(f"检查失败: {str(e)}")

# ===== 核心评分页面 =====
def main_rating_page():
    # 初始化Session State
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 1

    # 初始化数据库 & 拉取Cloudinary资源
    if not os.path.exists(DB_PATH):
        with st.spinner("初始化数据库并从Cloudinary拉取图片资源..."):
            init_database()
            load_images_from_cloudinary_to_db()
    else:
        # 检查数据库是否为空，如果是空的则重新拉取
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM images")
        img_count = cursor.fetchone()[0]
        conn.close()
        
        if img_count == 0:
            with st.spinner("数据库为空，从Cloudinary拉取图片资源..."):
                load_images_from_cloudinary_to_db()

    # 侧边栏：评分员信息 & 筛选条件
    with st.sidebar:
        st.title("🎮 评分系统")
        evaluator_id = st.text_input("评分员ID", value="eval_001", key="eval_id")
        evaluator_name = st.text_input("评分员姓名", value="张三", key="eval_name")
        st.markdown("---")
        
        st.subheader("📊 筛选条件")
        conn = sqlite3.connect(DB_PATH)
        models = pd.read_sql("SELECT DISTINCT model_id FROM images", conn)['model_id'].tolist()
        types = pd.read_sql("SELECT DISTINCT type FROM images", conn)['type'].tolist()
        styles = pd.read_sql("SELECT DISTINCT style FROM images", conn)['style'].tolist()
        conn.close()
        
        selected_model = st.selectbox("模型", ['全部'] + models, key="sel_model")
        selected_type = st.selectbox("类型", ['全部'] + types, key="sel_type")
        selected_style = st.selectbox("风格", ['全部'] + styles, key="sel_style")
        
        show_evaluated = st.checkbox("显示已评分", value=True, key="show_eval")
        show_unevaluated = st.checkbox("显示未评分", value=True, key="show_uneval")
        st.markdown("---")
        
        page_nav = st.radio("导航", ["📝 评分", "📊 统计分析", "🚨 快速诊断"], key="page_nav")
        
        st.markdown("---")
        if st.button("🔄 重新从Cloudinary加载数据"):
            with st.spinner("重新加载数据中..."):
                load_images_from_cloudinary_to_db(force_refresh=True)
                st.rerun()

    # 如果切换到统计页面，直接跳转
    if page_nav == "📊 统计分析":
        show_statistics()
        return
    elif page_nav == "🚨 快速诊断":
        quick_diagnostic()
        return

    # 主页面：数据筛选 & 展示
    conn = sqlite3.connect(DB_PATH)
    
    # 构建筛选查询
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

    # 页面头部统计
    st.title("🎮 AI游戏图像质量评价系统")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总图片数", total_images)
    col2.metric("已评分", evaluated_count)
    col3.metric("未评分", total_images - evaluated_count)
    col4.metric("完成度", f"{(evaluated_count / total_images * 100) if total_images else 0:.1f}%")
    st.markdown("---")

    # 无数据提示
    if len(images_df) == 0:
        st.warning("⚠️ 没有符合条件的图片")
        if st.button("🔄 从Cloudinary加载图片"):
            with st.spinner("加载图片中..."):
                load_images_from_cloudinary_to_db()
                st.rerun()
        return

    # 分页逻辑
    items_per_page = 5  # 每页显示5张，避免页面过长
    total_pages = (len(images_df) - 1) // items_per_page + 1
    current_page = st.session_state.current_page
    current_page = max(1, min(current_page, total_pages))

    # 分页控件
    st.markdown(f"""
        <div style='text-align: center; margin-bottom: 1rem;'>
            第 <b>{current_page}</b> 页 / 共 <b>{total_pages}</b> 页
        </div>
    """, unsafe_allow_html=True)
    
    col_nav_left, col_nav_mid, col_nav_right = st.columns([1,2,1])
    with col_nav_mid:
        new_page = st.number_input(
            "跳转页码",
            min_value=1,
            max_value=total_pages,
            value=current_page,
            key="page_input"
        )
        if new_page != current_page:
            st.session_state.current_page = new_page
            st.rerun()

    # 分页数据切片
    start_idx = (current_page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(images_df))
    page_images = images_df.iloc[start_idx:end_idx]

    # 图片 & 评分表单展示
    for idx, row in page_images.iterrows():
        with st.expander(f"🖼️ {row['prompt_id']} | {row['model_name']} | 图片{row['image_number']}", expanded=True):
            col_img, col_form = st.columns([1, 2])

            # 左侧：图片展示
            with col_img:
                # 使用修正后的函数
                img_url = get_cloud_image_url(row['filepath'])
                
                # 显示图片
                st.image(img_url, use_container_width=True)
                
                # 添加调试信息
                with st.expander("🔍 调试信息", expanded=False):
                    st.write(f"**数据库中的filepath:** `{row['filepath']}`")
                    st.write(f"**模型ID:** `{row['model_id']}`")
                    st.write(f"**图片URL:** `{img_url}`")
                
                st.caption(f"**Prompt:** {row['prompt_text'][:100]}..." if len(row['prompt_text'])>100 else f"**Prompt:** {row['prompt_text']}")
                st.caption(f"**类型:** {row['type']} | **风格:** {row['style']}")
                st.caption(f"**模型:** {row['model_name']} ({row['quality_tier']})")
                st.caption(f"**上传时间:** {row['generation_time'][:19]}")

            # 右侧：评分表单
            with col_form:
                st.subheader("📝 图像质量评分")
                existing_eval = get_evaluation(row['id'], evaluator_id)

                # 辅助函数：获取已有评分值
                def get_existing_val(key, default=3):
                    return existing_eval.get(key, default) if existing_eval else default

                # 技术质量评分
                st.markdown("### 🛠️ 技术质量")
                clarity = st.slider("清晰度 (1-5)", 1, 5, get_existing_val('clarity'), key=f"clarity_{row['id']}")
                detail_richness = st.slider("细节丰富度 (1-5)", 1, 5, get_existing_val('detail_richness'), key=f"detail_{row['id']}")
                color_accuracy = st.slider("色彩准确性 (1-5)", 1, 5, get_existing_val('color_accuracy'), key=f"color_{row['id']}")
                lighting_quality = st.slider("光影质量 (1-5)", 1, 5, get_existing_val('lighting_quality'), key=f"lighting_{row['id']}")
                composition = st.slider("构图合理性 (1-5)", 1, 5, get_existing_val('composition'), key=f"comp_{row['id']}")
                
                # 内容准确性
                st.markdown("### 🎯 内容准确性")
                prompt_match = st.slider("与Prompt匹配度 (1-5)", 1, 5, get_existing_val('prompt_match'), key=f"prompt_{row['id']}")
                style_consistency = st.slider("风格一致性 (1-5)", 1, 5, get_existing_val('style_consistency'), key=f"style_{row['id']}")
                subject_completeness = st.slider("主体完整性 (1-5)", 1, 5, get_existing_val('subject_completeness'), key=f"subject_{row['id']}")
                
                # 游戏适用性
                st.markdown("### 🎮 游戏适用性")
                game_usability = st.slider("游戏场景可用性 (1-5)", 1, 5, get_existing_val('game_usability'), key=f"game_{row['id']}")
                needs_fix = st.selectbox(
                    "是否需要修改才能使用", 
                    ["是", "否", "不确定"], 
                    index=["是", "否", "不确定"].index(get_existing_val('needs_fix', '否')), 
                    key=f"fix_{row['id']}"
                )
                direct_use = st.selectbox(
                    "是否可直接用于游戏", 
                    ["是", "否", "不确定"], 
                    index=["是", "否", "不确定"].index(get_existing_val('direct_use', '否')), 
                    key=f"use_{row['id']}"
                )
                
                # 缺陷评估
                st.markdown("### 🚫 缺陷评估")
                major_defects = st.text_area(
                    "主要缺陷（如人体畸变、纹理错误）", 
                    value=get_existing_val('major_defects', ''), 
                    key=f"major_{row['id']}"
                )
                minor_issues = st.text_area(
                    "次要问题（如轻微模糊、色彩偏差）", 
                    value=get_existing_val('minor_issues', ''), 
                    key=f"minor_{row['id']}"
                )
                
                # 整体评价
                st.markdown("### ⭐ 整体评价")
                overall_quality = st.slider("整体质量评分 (1-5)", 1, 5, get_existing_val('overall_quality'), key=f"overall_{row['id']}")
                grade = st.selectbox(
                    "评级", 
                    ["S", "A", "B", "C", "D"], 
                    index=["S", "A", "B", "C", "D"].index(get_existing_val('grade', 'B')), 
                    key=f"grade_{row['id']}"
                )
                notes = st.text_area(
                    "备注", 
                    value=get_existing_val('notes', ''), 
                    key=f"notes_{row['id']}"
                )
                
                # 提交按钮
                if st.button("💾 保存/更新评分", key=f"submit_{row['id']}", type="primary"):
                    scores = {
                        'clarity': clarity,
                        'detail_richness': detail_richness,
                        'color_accuracy': color_accuracy,
                        'lighting_quality': lighting_quality,
                        'composition': composition,
                        'prompt_match': prompt_match,
                        'style_consistency': style_consistency,
                        'subject_completeness': subject_completeness,
                        'game_usability': game_usability,
                        'needs_fix': needs_fix,
                        'direct_use': direct_use,
                        'major_defects': major_defects,
                        'minor_issues': minor_issues,
                        'overall_quality': overall_quality,
                        'grade': grade,
                        'notes': notes
                    }
                    save_evaluation(row['id'], evaluator_id, evaluator_name, scores)
                    # 刷新页面以显示最新评分
                    st.rerun()

# ===== 主入口 =====
if __name__ == "__main__":
    main_rating_page()
