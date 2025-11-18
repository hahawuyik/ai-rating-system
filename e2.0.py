import streamlit as st
import pandas as pd
import json
import os
from pathlib import Path
from PIL import Image
import sqlite3
from datetime import datetime


# ===== 配置 =====

if 'STREAMLIT_SHARING' in os.environ or 'STREAMLIT_SERVER' in os.environ:
    # 云环境：使用相对路径
    DATASET_ROOT = "./ai_dataset_project"
else:
    # 本地环境：使用原路径
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
            filepath TEXT UNIQUE,
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


def load_images_to_db():
    """扫描硬盘图片并加载到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    models = ['dalle3', 'sd15', 'sdxl_turbo', 'dreamshaper']
    loaded_count = 0

    for model_id in models:
        model_dir = os.path.join(OUTPUT_DIR, model_id)
        if not os.path.exists(model_dir):
            continue

        for filename in os.listdir(model_dir):
            if not filename.endswith('.png'):
                continue

            filepath = os.path.join(model_dir, filename)

            # 检查是否已存在
            cursor.execute("SELECT id FROM images WHERE filepath = ?", (filepath,))
            if cursor.fetchone():
                continue

            # 解析文件名: char_real_01_dalle3_1.png
            parts = filename.replace('.png', '').split('_')
            if len(parts) < 3:
                continue

            image_number = int(parts[-1])
            model = parts[-2]
            prompt_id = '_'.join(parts[:-2])

            # 读取元数据
            meta_path = filepath.replace('.png', '_meta.json')
            metadata = {}
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)

            # 插入数据库
            cursor.execute('''
                INSERT INTO images (
                    prompt_id, model_id, image_number, filepath,
                    prompt_text, type, style, model_name, quality_tier, generation_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                prompt_id,
                model_id,
                image_number,
                filepath,
                metadata.get('prompt', ''),
                metadata.get('type', ''),
                metadata.get('style', ''),
                metadata.get('model_name', ''),
                metadata.get('quality_tier', ''),
                metadata.get('generation_time', '')
            ))

            loaded_count += 1

    conn.commit()
    conn.close()

    return loaded_count


def save_evaluation(image_id, evaluator_id, evaluator_name, scores):
    """保存评分"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 检查是否已评分
    cursor.execute('''
        SELECT id FROM evaluations 
        WHERE image_id = ? AND evaluator_id = ?
    ''', (image_id, evaluator_id))

    existing = cursor.fetchone()

    if existing:
        # 更新
        cursor.execute('''
            UPDATE evaluations SET
                clarity = ?, detail_richness = ?, color_accuracy = ?,
                lighting_quality = ?, composition = ?,
                prompt_match = ?, style_consistency = ?, subject_completeness = ?,
                game_usability = ?, needs_fix = ?, direct_use = ?,
                major_defects = ?, minor_issues = ?,
                overall_quality = ?, grade = ?, notes = ?,
                evaluation_time = ?
            WHERE id = ?
        ''', (
            scores['clarity'], scores['detail_richness'], scores['color_accuracy'],
            scores['lighting_quality'], scores['composition'],
            scores['prompt_match'], scores['style_consistency'], scores['subject_completeness'],
            scores['game_usability'], scores['needs_fix'], scores['direct_use'],
            scores['major_defects'], scores['minor_issues'],
            scores['overall_quality'], scores['grade'], scores['notes'],
            datetime.now().isoformat(),
            existing[0]
        ))
    else:
        # 插入
        cursor.execute('''
            INSERT INTO evaluations (
                image_id, evaluator_id, evaluator_name,
                clarity, detail_richness, color_accuracy,
                lighting_quality, composition,
                prompt_match, style_consistency, subject_completeness,
                game_usability, needs_fix, direct_use,
                major_defects, minor_issues,
                overall_quality, grade, notes,
                evaluation_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            image_id, evaluator_id, evaluator_name,
            scores['clarity'], scores['detail_richness'], scores['color_accuracy'],
            scores['lighting_quality'], scores['composition'],
            scores['prompt_match'], scores['style_consistency'], scores['subject_completeness'],
            scores['game_usability'], scores['needs_fix'], scores['direct_use'],
            scores['major_defects'], scores['minor_issues'],
            scores['overall_quality'], scores['grade'], scores['notes'],
            datetime.now().isoformat()
        ))

    conn.commit()
    conn.close()


def get_evaluation(image_id, evaluator_id):
    """获取已有评分"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM evaluations 
        WHERE image_id = ? AND evaluator_id = ?
    ''', (image_id, evaluator_id))

    result = cursor.fetchone()
    conn.close()

    if result:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, result))
    return None


# ===== Streamlit 界面 =====

def main():
    st.set_page_config(
        page_title="AI游戏图像质量评价系统",
        page_icon="🎮",
        layout="wide"
    )

    # 初始化session_state
    if 'page' not in st.session_state:
        st.session_state.page = 1

    # 初始化
    if not os.path.exists(METADATA_DIR):
        st.error(f"❌ 数据集目录不存在: {DATASET_ROOT}")
        st.info("请先运行图片生成脚本")
        return

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

    # 筛选选项
    st.sidebar.subheader("📊 筛选条件")

    conn = sqlite3.connect(DB_PATH)

    # 获取筛选选项
    models = pd.read_sql("SELECT DISTINCT model_id FROM images", conn)['model_id'].tolist()
    types = pd.read_sql("SELECT DISTINCT type FROM images", conn)['type'].tolist()
    styles = pd.read_sql("SELECT DISTINCT style FROM images", conn)['style'].tolist()

    selected_model = st.sidebar.selectbox("模型", ['全部'] + models)
    selected_type = st.sidebar.selectbox("类型", ['全部'] + types)
    selected_style = st.sidebar.selectbox("风格", ['全部'] + styles)

    show_evaluated = st.sidebar.checkbox("显示已评分", value=True)
    show_unevaluated = st.sidebar.checkbox("显示未评分", value=True)

    st.sidebar.markdown("---")

    # 构建查询
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

    # 筛选已评分/未评分
    if not show_evaluated or not show_unevaluated:
        evaluated_ids = pd.read_sql(
            f"SELECT DISTINCT image_id FROM evaluations WHERE evaluator_id = '{evaluator_id}'",
            conn
        )['image_id'].tolist()

        if not show_evaluated:
            images_df = images_df[~images_df['id'].isin(evaluated_ids)]
        if not show_unevaluated:
            images_df = images_df[images_df['id'].isin(evaluated_ids)]

    conn.close()

    # 主界面
    st.title("🎮 AI游戏图像质量评价系统")

    # 统计信息
    col1, col2, col3, col4 = st.columns(4)

    conn = sqlite3.connect(DB_PATH)
    total_images = pd.read_sql("SELECT COUNT(*) as count FROM images", conn)['count'][0]
    evaluated_count = pd.read_sql(
        f"SELECT COUNT(DISTINCT image_id) as count FROM evaluations WHERE evaluator_id = '{evaluator_id}'",
        conn
    )['count'][0]
    conn.close()

    col1.metric("总图片数", total_images)
    col2.metric("已评分", evaluated_count)
    col3.metric("未评分", total_images - evaluated_count)
    col4.metric("完成度", f"{evaluated_count / total_images * 100:.1f}%")

    st.markdown("---")

    # 图片列表
    if len(images_df) == 0:
        st.warning("⚠️ 没有符合条件的图片")
        return

    # 分页
    items_per_page = 10
    total_pages = (len(images_df) - 1) // items_per_page + 1

    # 使用session_state管理页面状态
    current_page = st.session_state.page

    # 确保页面在有效范围内
    if current_page < 1:
        current_page = 1
    if current_page > total_pages:
        current_page = total_pages

    # 简洁的页面导航 - 只使用数字输入框
    col_nav = st.columns([1, 2, 1])
    with col_nav[1]:
        st.markdown(
            f"<div style='text-align: center; margin-bottom: 10px;'>第 <b>{current_page}</b> 页 / 共 <b>{total_pages}</b> 页</div>",
            unsafe_allow_html=True)

        # 使用数字输入框实现翻页（通过+/-按钮）
        new_page = st.number_input(
            "页码",
            min_value=1,
            max_value=total_pages,
            value=current_page,
            key="page_input",
            label_visibility="collapsed"
        )

        # 如果页码发生变化，更新session_state并刷新页面
        if new_page != current_page:
            st.session_state.page = new_page
            st.rerun()

    st.info(
        f"📄 显示 {len(images_df)} 张图片中的第 {(current_page - 1) * items_per_page + 1} - {min(current_page * items_per_page, len(images_df))} 张")

    # 计算当前页的数据
    start_idx = (current_page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, len(images_df))
    page_images = images_df.iloc[start_idx:end_idx]

    # 显示图片并评分
    for idx, row in page_images.iterrows():
        with st.expander(f"🖼️ {row['prompt_id']} - {row['model_name']} - 图片{row['image_number']}", expanded=False):
            col_img, col_form = st.columns([1, 2])

            # 左侧：图片
            with col_img:
                if os.path.exists(row['filepath']):
                    image = Image.open(row['filepath'])
                    st.image(image, use_container_width=True)
                else:
                    st.error("图片文件不存在")

                st.caption(f"**Prompt:** {row['prompt_text']}")
                st.caption(f"**类型:** {row['type']} | **风格:** {row['style']}")
                st.caption(f"**模型:** {row['model_name']} ({row['quality_tier']})")

            # 右侧：评分表单
            with col_form:
                # 检查是否已评分
                existing_eval = get_evaluation(row['id'], evaluator_id)

                if existing_eval:
                    st.success("✅ 已评分")

                with st.form(f"eval_form_{row['id']}"):
                    st.subheader("📊 技术质量 (1-5分)")

                    col1, col2 = st.columns(2)
                    with col1:
                        clarity = st.slider("清晰度", 1, 5, existing_eval['clarity'] if existing_eval else 3,
                                            key=f"clarity_{row['id']}")
                        detail = st.slider("细节丰富度", 1, 5, existing_eval['detail_richness'] if existing_eval else 3,
                                           key=f"detail_{row['id']}")
                        color = st.slider("色彩准确性", 1, 5, existing_eval['color_accuracy'] if existing_eval else 3,
                                          key=f"color_{row['id']}")

                    with col2:
                        lighting = st.slider("光影合理性", 1, 5,
                                             existing_eval['lighting_quality'] if existing_eval else 3,
                                             key=f"lighting_{row['id']}")
                        composition = st.slider("构图美感", 1, 5, existing_eval['composition'] if existing_eval else 3,
                                                key=f"compo_{row['id']}")

                    st.subheader("🎯 内容准确性 (1-5分)")

                    col3, col4 = st.columns(2)
                    with col3:
                        prompt_match = st.slider("符合prompt", 1, 5,
                                                 existing_eval['prompt_match'] if existing_eval else 3,
                                                 key=f"prompt_{row['id']}")
                        style_cons = st.slider("风格一致性", 1, 5,
                                               existing_eval['style_consistency'] if existing_eval else 3,
                                               key=f"style_{row['id']}")

                    with col4:
                        subject_comp = st.slider("主体完整性", 1, 5,
                                                 existing_eval['subject_completeness'] if existing_eval else 3,
                                                 key=f"subject_{row['id']}")

                    st.subheader("🎮 游戏适用性")

                    col5, col6 = st.columns(2)
                    with col5:
                        game_use = st.slider("游戏资产价值", 1, 5,
                                             existing_eval['game_usability'] if existing_eval else 3,
                                             key=f"game_{row['id']}")
                        direct_use = st.radio("可直接用于游戏", ["是", "否"],
                                              index=0 if existing_eval and existing_eval['direct_use'] == '是' else 1,
                                              key=f"direct_{row['id']}")

                    with col6:
                        needs_fix = st.radio("需要后期修复", ["是", "否"],
                                             index=0 if existing_eval and existing_eval['needs_fix'] == '是' else 1,
                                             key=f"fix_{row['id']}")

                    st.subheader("⚠️ 缺陷记录")

                    major_defects = st.text_area(
                        "明显缺陷（手部畸形、比例失调等）",
                        value=existing_eval['major_defects'] if existing_eval else "",
                        key=f"major_{row['id']}"
                    )

                    minor_issues = st.text_area(
                        "次要问题（轻微模糊、色彩偏差等）",
                        value=existing_eval['minor_issues'] if existing_eval else "",
                        key=f"minor_{row['id']}"
                    )

                    st.subheader("⭐ 整体评价")

                    col7, col8 = st.columns(2)
                    with col7:
                        overall = st.slider("整体质量", 1, 5, existing_eval['overall_quality'] if existing_eval else 3,
                                            key=f"overall_{row['id']}")

                    with col8:
                        grade_options = ['A', 'B', 'C', 'D', 'F']
                        grade_index = grade_options.index(existing_eval['grade']) if existing_eval and existing_eval[
                            'grade'] in grade_options else 2
                        grade = st.selectbox("推荐等级", grade_options, index=grade_index, key=f"grade_{row['id']}")

                    notes = st.text_area(
                        "备注",
                        value=existing_eval['notes'] if existing_eval else "",
                        key=f"notes_{row['id']}"
                    )

                    # 提交按钮
                    submitted = st.form_submit_button("💾 保存评分", use_container_width=True, key=f"submit_{row['id']}")

                    if submitted:
                        scores = {
                            'clarity': clarity,
                            'detail_richness': detail,
                            'color_accuracy': color,
                            'lighting_quality': lighting,
                            'composition': composition,
                            'prompt_match': prompt_match,
                            'style_consistency': style_cons,
                            'subject_completeness': subject_comp,
                            'game_usability': game_use,
                            'needs_fix': needs_fix,
                            'direct_use': direct_use,
                            'major_defects': major_defects,
                            'minor_issues': minor_issues,
                            'overall_quality': overall,
                            'grade': grade,
                            'notes': notes
                        }

                        save_evaluation(row['id'], evaluator_id, evaluator_name, scores)
                        st.success("✅ 评分已保存！")
                        st.rerun()


# ===== 统计分析页面 =====

def show_statistics():
    st.title("📊 评分统计分析")

    conn = sqlite3.connect(DB_PATH)

    # 总体统计
    st.subheader("📈 总体统计")

    total_images = pd.read_sql("SELECT COUNT(*) as count FROM images", conn)['count'][0]
    total_evaluations = pd.read_sql("SELECT COUNT(*) as count FROM evaluations", conn)['count'][0]
    evaluators = \
        pd.read_sql("SELECT COUNT(DISTINCT evaluator_id) as count FROM evaluations", conn)['count'][0]

    col1, col2, col3 = st.columns(3)
    col1.metric("总图片数", total_images)
    col2.metric("总评分数", total_evaluations)
    col3.metric("评分员数", evaluators)

    st.markdown("---")

    # 模型对比
    st.subheader("🔍 模型质量对比")

    model_stats = pd.read_sql('''
                    SELECT 
                        i.model_name,
                        i.quality_tier,
                        COUNT(e.id) as eval_count,
                        AVG(e.overall_quality) as avg_quality,
                        AVG(e.clarity) as avg_clarity,
                        AVG(e.detail_richness) as avg_detail,
                        AVG(e.prompt_match) as avg_prompt_match,
                        AVG(e.game_usability) as avg_game_use
                    FROM images i
                    LEFT JOIN evaluations e ON i.id = e.image_id
                    GROUP BY i.model_id, i.model_name, i.quality_tier
                ''', conn)

    if len(model_stats) > 0:
        # 雷达图
        fig = go.Figure()

        for idx, row in model_stats.iterrows():
            fig.add_trace(go.Scatterpolar(
                r=[
                    row['avg_clarity'] or 0,
                    row['avg_detail'] or 0,
                    row['avg_prompt_match'] or 0,
                    row['avg_game_use'] or 0,
                    row['avg_quality'] or 0
                ],
                theta=['清晰度', '细节', 'Prompt匹配', '游戏适用', '整体质量'],
                fill='toself',
                name=row['model_name']
            ))

        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 5])),
            showlegend=True,
            title="模型质量雷达图"
        )

        st.plotly_chart(fig, use_container_width=True)

        # 数据表
        st.dataframe(model_stats, use_container_width=True)

    st.markdown("---")

    # 等级分布
    st.subheader("📊 等级分布")

    grade_dist = pd.read_sql('''
                    SELECT 
                        i.model_name,
                        e.grade,
                        COUNT(*) as count
                    FROM evaluations e
                    JOIN images i ON e.image_id = i.id
                    GROUP BY i.model_name, e.grade
                ''', conn)

    if len(grade_dist) > 0:
        fig = px.bar(
            grade_dist,
            x='model_name',
            y='count',
            color='grade',
            title="各模型等级分布",
            labels={'model_name': '模型', 'count': '数量', 'grade': '等级'}
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # 缺陷统计
    st.subheader("⚠️ 常见缺陷统计")

    defects = pd.read_sql('''
                    SELECT 
                        i.model_name,
                        e.major_defects,
                        COUNT(*) as count
                    FROM evaluations e
                    JOIN images i ON e.image_id = i.id
                    WHERE e.major_defects != ''
                    GROUP BY i.model_name, e.major_defects
                    ORDER BY count DESC
                    LIMIT 20
                ''', conn)

    if len(defects) > 0:
        st.dataframe(defects, use_container_width=True)

    st.markdown("---")

    # 导出功能
    st.subheader("💾 导出数据")

    if st.button("导出完整评分数据 (Excel)", use_container_width=True):
        export_df = pd.read_sql('''
                        SELECT 
                            i.prompt_id,
                            i.prompt_text,
                            i.type,
                            i.style,
                            i.model_name,
                            i.quality_tier,
                            i.image_number,
                            e.evaluator_name,
                            e.clarity,
                            e.detail_richness,
                            e.color_accuracy,
                            e.lighting_quality,
                            e.composition,
                            e.prompt_match,
                            e.style_consistency,
                            e.subject_completeness,
                            e.game_usability,
                            e.direct_use,
                            e.needs_fix,
                            e.major_defects,
                            e.minor_issues,
                            e.overall_quality,
                            e.grade,
                            e.notes,
                            e.evaluation_time
                        FROM evaluations e
                        JOIN images i ON e.image_id = i.id
                        ORDER BY i.prompt_id, i.model_id, i.image_number
                    ''', conn)

        export_path = os.path.join(EVALUATION_DIR,
                                   f"evaluation_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        os.makedirs(EVALUATION_DIR, exist_ok=True)
        export_df.to_excel(export_path, index=False)

        st.success(f"✅ 已导出到: {export_path}")

    conn.close()


# ===== 主入口 =====

if __name__ == "__main__":
    # 创建必要目录
    os.makedirs(EVALUATION_DIR, exist_ok=True)

    # 侧边栏导航
    page = st.sidebar.radio("导航", ["📝 评分", "📊 统计分析"])

    if page == "📝 评分":
        main()
    else:

        show_statistics()
