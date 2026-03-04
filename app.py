import streamlit as st
import sqlite3
import os
import cv2
import pandas as pd
from PIL import Image
import json
from datetime import datetime

# 引入核心组件
from config import settings
from core.llm_parser import ConstructionLLMParser
from core.spatial_engine import ProjectProgressManager

# ================= 配置页面与初始化 =================
# 将页面设置为宽屏，并加上合适的图标
st.set_page_config(page_title="CSCEC | 施工现场智能进度监控", page_icon="🏢", layout="wide")

# 注入中建风格的自定义 CSS
st.markdown("""
<style>
    /* 定义中建蓝白主题变量 */
    :root {
        --cscec-blue: #004b87;         /* 中建主色调深蓝 */
        --cscec-light: #e6f0fa;        /* 悬浮/浅色背景 */
        --bg-color: #f4f6f9;           /* 整体页面浅灰背景 */
        --card-white: #ffffff;         /* 卡片纯白背景 */
        --text-main: #333333;          /* 主文本颜色 */
    }

    /* 全局背景与字体颜色 */
    .stApp {
        background-color: var(--bg-color);
        color: var(--text-main);
    }
    
    /* 所有的标题使用中建蓝 */
    h1, h2, h3, h4, h5, h6 {
        color: var(--cscec-blue) !important;
        font-weight: 600 !important;
    }

    /* 选项卡 Tabs 美化 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: transparent;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: var(--card-white);
        border-radius: 8px 8px 0 0;
        padding: 10px 24px;
        border: 1px solid #e0e0e0;
        border-bottom: none;
        box-shadow: 0 -2px 5px rgba(0,0,0,0.02);
    }
    .stTabs [aria-selected="true"] {
        background-color: var(--cscec-blue) !important;
        color: white !important;
    }

    /* 按钮美化 (Primary Button) */
    .stButton>button[kind="primary"] {
        background-color: var(--cscec-blue);
        color: white;
        border: none;
        border-radius: 6px;
        padding: 0.5rem 1rem;
        font-weight: bold;
        box-shadow: 0 4px 6px rgba(0, 75, 135, 0.2);
        transition: all 0.3s ease;
    }
    .stButton>button[kind="primary"]:hover {
        background-color: #003366;
        box-shadow: 0 6px 8px rgba(0, 75, 135, 0.3);
        transform: translateY(-1px);
    }

    /* Metric 数据卡片美化 */
    [data-testid="stMetric"] {
        background-color: var(--card-white);
        border-left: 6px solid var(--cscec-blue);
        padding: 15px 20px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    [data-testid="stMetricValue"] {
        color: var(--cscec-blue);
        font-size: 1.8rem !important;
        font-weight: bold;
    }

    /* DataFrame 表格美化 */
    [data-testid="stDataFrame"] {
        background-color: var(--card-white);
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }

    /* 进度条美化 */
    .stProgress > div > div > div > div {
        background-color: var(--cscec-blue);
    }
    
    /* 图片容器阴影 */
    [data-testid="stImage"] {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

# 初始化核心引擎
@st.cache_resource
def get_engines():
    parser = ConstructionLLMParser(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model_name=settings.LLM_MODEL_NAME,
        log_file_path=settings.LOG_FILE_PATH
    )
    manager = ProjectProgressManager(db_path=settings.DB_FILE_PATH)
    return parser, manager

parser, manager = get_engines()

# 数据库读取辅助函数
def fetch_progress_data():
    if not os.path.exists(settings.DB_FILE_PATH):
        return pd.DataFrame(columns=["区域", "当前楼层", "当前工序", "是否已浇筑"])
    
    with sqlite3.connect(settings.DB_FILE_PATH) as conn:
        df = pd.read_sql_query("SELECT zone_name as 区域, floor as 当前楼层, stage as 当前工序, is_poured as 是否已浇筑 FROM zone_states", conn)
        # 将布尔值转换为更友好的显示，增加状态指示灯
        df['是否已浇筑'] = df['是否已浇筑'].apply(lambda x: "🟢 已完成" if x else "🟡 待浇筑")
        return df

# ================= 页面主体结构 =================

# 顶部企业 Logo/Header 区域替代
st.markdown("###  中国建筑第四工程局有限公司 | 进度自动识别")
st.markdown("---")

tab1, tab2 = st.tabs(["🖥️ 监控与进度看板", "⚙️ 系统配置中心"])

# ----------------- Tab 1: 监控与进度看板 -----------------
with tab1:
    
    col_video, col_data = st.columns([1.2, 1], gap="large")
    
    with col_video:
        st.markdown("#### 📷 现场监控画面实时接入")
        camera_source = st.session_state.get("camera_source", "test_materials/混凝土浇筑阶段.jpg")
        zone_name = st.session_state.get("current_zone", "云端工厂东北角")
        
        st.info(f"📍 **当前监控区域**: {zone_name} &nbsp;&nbsp;|&nbsp;&nbsp; 🔗 **视频源**: `{camera_source}`")
        
        # 显示当前画面
        try:
            image = Image.open(camera_source)
            st.image(image, caption=f"🟢 摄像头画面在线 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", use_column_width=True)
        except Exception as e:
            st.error("⚠️ 无法加载摄像头画面，请检查配置源路径。")
            image = None

        st.markdown("<br>", unsafe_allow_html=True) # 增加间距
        if st.button("📸 立即抓拍并进行大模型 AI 分析", type="primary", use_container_width=True):
            if image:
                with st.spinner("🧠 正在调用视觉大模型解析现场语义，请稍候..."):
                    result = parser.parse_instruction_with_image("请识别当前施工工序", camera_source)
                    result["位置"] = zone_name 
                    manager.parse_json_log(result)
                    
                    st.success("✅ 智能识别完成！现场语义数据已更新。")
                    with st.expander("查看底层 JSON 解析结果"):
                        st.json(result)
                    
    with col_data:
        st.markdown("#### 📊 项目整体进度看板")
        df_progress = fetch_progress_data()
        
        if not df_progress.empty:
            # 顶部核心指标 (单列两个指标，更加大气)
            st.markdown("##### 📍 各责任区最新进度")
            metric_cols = st.columns(2)
            for idx, row in df_progress.iterrows():
                # 均分在两列中显示
                col = metric_cols[idx % 2]
                with col:
                    st.metric(
                        label=f"🏢 {row['区域']}", 
                        value=f"第 {row['当前楼层']} 层", 
                        delta=f"工序: {row['当前工序']}"
                    )
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 详细状态台账
            st.markdown("##### 📝 进度台账明细")
            # 使用 styled dataframe 提升表格颜值
            st.dataframe(
                df_progress.style.applymap(
                    lambda x: 'color: green; font-weight: bold' if '已完成' in str(x) else 'color: #d48806',
                    subset=['是否已浇筑']
                ), 
                use_container_width=True, 
                hide_index=True
            )
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # 总体进度计算 (假设总楼层为20层)
            TOTAL_FLOORS = 20
            avg_floor = df_progress['当前楼层'].mean()
            progress_pct = min(avg_floor / TOTAL_FLOORS, 1.0)
            
            st.markdown("##### 📈 主体结构总工期进度 (按 20 层封顶计算)")
            st.progress(progress_pct, text=f"当前综合完成度: {progress_pct*100:.1f}%")
        else:
            st.info("💡 暂无进度数据。请在左侧点击「抓拍分析」或在配置中心初始化系统。")

# ----------------- Tab 2: 系统配置中心 -----------------
with tab2:
    st.markdown("#### ⚙️ 系统集成与设备绑定")
    st.markdown("配置摄像头抓拍流，或处理大模型极端情况下的容错纠偏。")
    st.markdown("<hr/>", unsafe_allow_html=True)
    
    col_cam, col_floor = st.columns(2, gap="large")
    
    with col_cam:
        st.markdown("##### 📹 视频流挂接配置")
        test_images = [os.path.join(settings.TEST_MATERIALS_DIR, f) for f in os.listdir(settings.TEST_MATERIALS_DIR) if f.endswith(('.jpg', '.png'))]
        
        selected_cam = st.selectbox("流媒体源地址 (RTSP / 本地测试图)", test_images + ["RTSP://192.168.1.100:554/stream1 (不可用)"])
        bind_zone = st.text_input("绑定工地空间语义名称", value="塔楼")
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 保存并应用节点配置", type="primary"):
            st.session_state["camera_source"] = selected_cam
            st.session_state["current_zone"] = bind_zone
            st.success("配置已生效，视频流已重定向。")
            
    with col_floor:
        st.markdown("##### ⚠️ 空间引擎基准强制修正")
        st.caption("工程管理人员专用：当由于遮挡导致 AI 持续跳层误判时，可强行重置空间台账。")
        
        init_zone = st.text_input("重置目标区域", value="云端工厂西南角")
        
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            init_floor = st.number_input("物理基准楼层", min_value=1, max_value=100, value=1)
        with col_f2:
            init_stage = st.selectbox("当前实际工序", ["未开始", "模板阶段", "钢筋阶段", "混凝土浇筑阶段"])
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚨 确认覆写空间台账进度"):
            manager.manual_fix_zone(init_zone, init_floor, init_stage)
            st.success(f"已强制覆盖引擎数据：{init_zone} -> 第 {init_floor} 层 ({init_stage})")