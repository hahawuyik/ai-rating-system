import streamlit as st
import cloudinary
import cloudinary.api
from cloudinary.utils import cloudinary_url

st.set_page_config(page_title="Cloudinary深度调试", layout="wide")

st.title("🔍 Cloudinary深度调试工具")

# 配置输入
col1, col2 = st.columns(2)
with col1:
    cloud_name = st.text_input("Cloud Name", "dwskobcad")
    api_key = st.text_input("API Key", "676912851999589")
with col2:
    api_secret = st.text_input("API Secret", "YIY48Z9VOM1zHfPWZvFKlHpyXzk", type="password")
    root_folder = st.text_input("根文件夹", "ai-rating-images")

if st.button("运行完整诊断"):
    # 配置Cloudinary
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True
    )
    
    # 测试1: 基础连接
    st.header("1. 基础连接测试")
    try:
        ping_result = cloudinary.api.ping()
        st.success(f"✅ Cloudinary API连接成功")
        st.json(ping_result)
    except Exception as e:
        st.error(f"❌ API连接失败: {str(e)}")
        st.stop()
    
    # 测试2: 账户信息
    st.header("2. 账户信息")
    try:
        usage = cloudinary.api.usage()
        st.write(f"**计划类型:** {usage.get('plan', '未知')}")
        st.write(f"**存储使用量:** {usage.get('storage', {}).get('usage', 0) / 1024 / 1024:.2f} MB")
        st.write(f"**带宽使用量:** {usage.get('bandwidth', {}).get('usage', 0) / 1024 / 1024:.2f} MB")
        st.write(f"**转换次数:** {usage.get('transformations', {}).get('usage', 0)}")
    except Exception as e:
        st.warning(f"⚠️ 获取使用信息失败: {str(e)}")
    
    # 测试3: 列出所有资源类型
    st.header("3. 检查所有资源类型")
    
    resource_types = ["image", "raw", "video", "auto"]
    
    for rt in resource_types:
        st.subheader(f"资源类型: {rt}")
        try:
            resources = cloudinary.api.resources(
                type="upload",
                resource_type=rt,
                max_results=10
            )
            total = resources.get('total_count', 0)
            actual_resources = resources.get('resources', [])
            
            if total > 0:
                st.success(f"✅ 找到 {total} 个资源")
                for res in actual_resources[:5]:
                    st.write(f"- `{res['public_id']}` ({res.get('format', 'unknown')})")
            else:
                st.info(f"ℹ️ 没有找到资源 (资源类型: {rt})")
                
        except Exception as e:
            st.error(f"❌ 查询失败: {str(e)}")
    
    # 测试4: 特定API调用测试
    st.header("4. 特定API调用测试")
    
    test_cases = [
        ("获取根文件夹", lambda: cloudinary.api.root_folders()),
        ("获取子文件夹", lambda: cloudinary.api.subfolders(root_folder)),
        ("使用folders参数", lambda: cloudinary.api.resources(
            type="upload", 
            folders=root_folder,
            max_results=10
        )),
        ("使用prefix参数", lambda: cloudinary.api.resources(
            type="upload", 
            prefix=f"{root_folder}/",
            max_results=10
        )),
    ]
    
    for name, func in test_cases:
        st.subheader(f"测试: {name}")
        try:
            result = func()
            st.success(f"✅ 调用成功")
            
            # 如果是列表类结果
            if isinstance(result, dict) and 'resources' in result:
                total = result.get('total_count', 0)
                resources_list = result.get('resources', [])
                st.write(f"返回 {len(resources_list)}/{total} 个资源")
                
                if resources_list:
                    st.write("前5个资源:")
                    for res in resources_list[:5]:
                        st.write(f"- `{res['public_id']}`")
            
            # 如果是文件夹类结果
            elif isinstance(result, dict) and 'folders' in result:
                folders = result.get('folders', [])
                st.write(f"找到 {len(folders)} 个文件夹:")
                for folder in folders:
                    st.write(f"- `{folder['path']}`")
            
            # 显示完整响应（调试用）
            with st.expander("查看完整响应"):
                st.json(result)
                
        except Exception as e:
            st.error(f"❌ 调用失败: {str(e)}")
    
    # 测试5: 直接访问已知文件
    st.header("5. 直接文件访问测试")
    
    # 尝试不同的文件路径模式
    test_paths = [
        f"{root_folder}/dalle3/char_fant_01_dalle3_1",
        f"{root_folder}/dalle3/char_fant_01_dalle3_1.png",
        f"{root_folder}/dalle3/char_fant_01_dalle3_1.jpg",
        f"{root_folder}/dreamshaper/char_fant_01_dreamshaper_1",
    ]
    
    for test_path in test_paths:
        st.write(f"测试路径: `{test_path}`")
        col_a, col_b = st.columns(2)
        
        with col_a:
            if st.button(f"测试 {test_path.split('/')[-1]}", key=f"btn_{test_path}"):
                try:
                    # 尝试获取资源信息
                    resource = cloudinary.api.resource(test_path)
                    st.success(f"✅ 资源存在")
                    st.write(f"**Public ID:** {resource['public_id']}")
                    st.write(f"**格式:** {resource.get('format', 'unknown')}")
                    st.write(f"**大小:** {resource.get('bytes', 0) / 1024:.1f} KB")
                    
                    # 尝试生成URL并显示图片
                    try:
                        url, _ = cloudinary_url(
                            test_path,
                            width=300,
                            height=300,
                            crop="limit",
                            quality="auto:good"
                        )
                        st.image(url, caption=test_path)
                    except:
                        st.warning("无法生成图片URL")
                        
                except Exception as e:
                    st.error(f"❌ 资源不存在或访问失败: {str(e)}")
