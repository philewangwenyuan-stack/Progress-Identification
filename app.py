import streamlit as st
import sqlite3
import os
import cv2
import pandas as pd
from PIL import Image
import json
from datetime import datetime
import threading
import time
import numpy as np

# 引入核心组件
from config import settings
from core.llm_parser import ConstructionLLMParser
from core.spatial_engine import ProjectProgressManager

# ================= 配置页面与全局设置 =================
st.set_page_config(page_title="CSCEC | 施工现场智能进度监控", page_icon="🏢", layout="wide")

# 统一配置和截图保存路径
CONFIG_PATH = os.path.join(settings.DATA_DIR, "project_config.json")
SNAPSHOT_PATH = os.path.join(settings.DATA_DIR, "latest_snapshot.jpg")

def load_config():
    """读取全局系统配置"""
    default_config = {
        "floors": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "rtsp_url": "rtsp://14.18.91.10:10554/rtp/34020000001110100700_34020000001311001700",
        "auto_interval_minutes": 0,  # 默认0代表不自动抓拍
        "current_zone": "塔楼A区"
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception:
            pass
    return default_config

def save_config(config_data):
    """保存配置"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=4)

def get_floor_sequence():
    return load_config().get("floors", [str(i) for i in range(1, 21)])

def capture_rtsp_frame(rtsp_url, save_path):
    """核心引擎：通过 OpenCV 接入 RTSP 并截取一帧保存为图片"""
    try:
        # 1. 强制确保保存图片的目录一定存在
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        # 设置超时，防止视频流断开时卡死
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;5000"
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            return False, "无法连接到该 RTSP 地址，请检查网络或流地址。"
        
        ret, frame = False, None
        
        # 2. 针对 HEVC(H.265) 编码的摄像头，连续读10帧"热身"
        for _ in range(10):
            ret, frame = cap.read()
            
        cap.release()
        
        if ret and frame is not None:
            # 3. 🚨 核心修复：解决 OpenCV 不支持中文路径保存的 Bug！
            # 先将图片编码为 .jpg 格式的内存数据
            is_success, im_buf_arr = cv2.imencode(".jpg", frame)
            if is_success:
                # 使用 numpy 的 tofile 方法写入硬盘（完美支持中文路径）
                im_buf_arr.tofile(save_path)
                return True, save_path
            else:
                return False, "成功获取画面，但内部编码图片失败。"
        else:
            return False, "成功连接视频流，但等待10帧后画面依然为空或解码失败。"
    except Exception as e:
        return False, f"截图发生异常: {str(e)}"

# ================= 后台自动化抓拍与AI解析线程 =================
@st.cache_resource
def start_auto_capture_thread():
    """
    此后台线程会在 Streamlit 启动时运行一次。
    它会持续轮询，判断是否到了设定的抓拍时间。
    """
    def task():
        # 在子线程独立实例化，防止多线程操作引起冲突
        parser = ConstructionLLMParser(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model_name=settings.LLM_MODEL_NAME,
            log_file_path=settings.LOG_FILE_PATH
        )
        manager = ProjectProgressManager(db_path=settings.DB_FILE_PATH)
        
        while True:
            try:
                config = load_config()
                interval = config.get("auto_interval_minutes", 0)
                rtsp_url = config.get("rtsp_url", "")
                zone_name = config.get("current_zone", "")
                
                # 如果开启了定时器（大于0分钟），且配了RTSP
                if interval > 0 and rtsp_url:
                    should_capture = False
                    if not os.path.exists(SNAPSHOT_PATH):
                        should_capture = True
                    else:
                        mtime = os.path.getmtime(SNAPSHOT_PATH)
                        # 如果当前时间与最后一次截图时间差 大于 设定的分钟数，则触发
                        if (time.time() - mtime) >= (interval * 60):
                            should_capture = True
                            
                    if should_capture:
                        success, path = capture_rtsp_frame(rtsp_url, SNAPSHOT_PATH)
                        if success:
                            # 截图成功，抛给大模型分析
                            result = parser.parse_instruction_with_image("请识别当前施工工序", path)
                            result["位置"] = zone_name
                            manager.parse_json_log(result)
            except Exception as e:
                print(f"[后台定时抓拍异常] {e}")
            
            # 每隔 30 秒检查一次是否到达了设定的时间边界
            time.sleep(30)
            
    # 设为 Daemon 线程，随主进程退出
    t = threading.Thread(target=task, daemon=True)
    t.start()
    return t

# 启动后台守护线程
start_auto_capture_thread()

# ================= UI 样式与初始化 =================
st.markdown("""
<style>
    :root {
        --cscec-blue: #004b87;
        --cscec-light: #e6f0fa;
        --bg-color: #f4f6f9;
        --card-white: #ffffff;
        --text-main: #333333;
    }
    .stApp { background-color: var(--bg-color); color: var(--text-main); }
    h1, h2, h3, h4, h5, h6 { color: var(--cscec-blue) !important; font-weight: 600 !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; background-color: transparent; }
    .stTabs [data-baseweb="tab"] { background-color: var(--card-white); border-radius: 8px 8px 0 0; padding: 10px 24px; border: 1px solid #e0e0e0; border-bottom: none; }
    .stTabs [aria-selected="true"] { background-color: var(--cscec-blue) !important; color: white !important; }
    .stButton>button[kind="primary"] { background-color: var(--cscec-blue); color: white; border-radius: 6px; padding: 0.5rem 1rem; font-weight: bold; transition: all 0.3s ease; }
    .stButton>button[kind="primary"]:hover { background-color: #003366; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)

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

def fetch_progress_data():
    if not os.path.exists(settings.DB_FILE_PATH): return pd.DataFrame()
    with sqlite3.connect(settings.DB_FILE_PATH) as conn:
        try:
            df = pd.read_sql_query("SELECT zone_name as 区域, floor as 当前楼层, stage as 当前工序, is_poured as 是否已浇筑 FROM zone_states_v2", conn)
            df['是否已浇筑'] = df['是否已浇筑'].apply(lambda x: "🟢 已完成" if x else "🟡 待浇筑")
            return df
        except: return pd.DataFrame()

def fetch_timeline_data(zone_name):
    if not os.path.exists(settings.DB_FILE_PATH): return pd.DataFrame()
    with sqlite3.connect(settings.DB_FILE_PATH) as conn:
        try:
            df = pd.read_sql_query("SELECT floor as 楼层, stage as 施工阶段, start_time as 开始时间, end_time as 结束时间 FROM stage_timeline WHERE zone_name = ? ORDER BY id DESC", conn, params=(zone_name,))
            df['结束时间'] = df['结束时间'].fillna('⏳ 进行中...')
            return df
        except: return pd.DataFrame()


# ================= 页面主体结构 =================
st.markdown("### 🏢 中国建筑第四工程局有限公司 | 进度自动识别")
st.markdown("---")

tab1, tab2 = st.tabs(["🖥️ 监控与进度看板", "⚙️ 系统配置中心"])
app_config = load_config()

# ----------------- Tab 1: 监控与进度看板 -----------------
with tab1:
    col_video, col_data = st.columns([1.2, 1], gap="large")
    
    with col_video:
        st.markdown("#### 📷 现场监控画面 (最近一帧)")
        rtsp_url = app_config.get("rtsp_url", "")
        zone_name = app_config.get("current_zone", "")
        
        st.info(f"📍 **当前监控区域**: {zone_name} &nbsp;&nbsp;|&nbsp;&nbsp; 🔗 **视频源**: `{rtsp_url}`")
        
        # 加载展示最近保存的截图
        if os.path.exists(SNAPSHOT_PATH):
            mtime = os.path.getmtime(SNAPSHOT_PATH)
            last_time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            try:
                image = Image.open(SNAPSHOT_PATH)
                st.image(image, caption=f"📸 画面定格时间 - {last_time_str} (每{app_config.get('auto_interval_minutes', 0)}分钟自动更新)", use_column_width=True)
            except Exception:
                st.error("⚠️ 无法加载最新的抓拍图片。")
        else:
            st.warning("⚠️ 暂无抓拍画面，请点击下方「手动抓拍」按钮进行首次抓拍提取。")

        st.markdown("<br>", unsafe_allow_html=True)
        
        # 手动抓拍按钮逻辑
        if st.button("📸 手动抓拍并进行大模型 AI 分析", type="primary", use_container_width=True):
            if not rtsp_url:
                st.error("❌ 请先在「系统配置中心」设置 RTSP 视频流地址！")
            else:
                with st.spinner("⏳ 正在实时接入 RTSP 视频流并抽取关键帧..."):
                    success, msg = capture_rtsp_frame(rtsp_url, SNAPSHOT_PATH)
                    
                if success:
                    st.success("✅ 画面抓拍成功！正在调用大模型...")
                    with st.spinner("🧠 视觉引擎解析中，请稍候..."):
                        result = parser.parse_instruction_with_image("请识别当前施工工序", SNAPSHOT_PATH)
                        result["位置"] = zone_name 
                        manager.parse_json_log(result)
                        
                        st.success("✅ 智能识别完成！现场语义数据已更新。")
                        time.sleep(1) # 短暂休眠让文件系统释放
                        st.rerun() # 重新加载页面刷新右侧台账和左侧图片时间
                else:
                    st.error(f"❌ 抓拍失败: {msg}")
                    
    with col_data:
        st.markdown("#### 📊 项目整体进度看板")
        df_progress = fetch_progress_data()
        
        if not df_progress.empty:
            st.markdown("##### 📍 各责任区最新进度")
            metric_cols = st.columns(2)
            for idx, row in df_progress.iterrows():
                with metric_cols[idx % 2]:
                    st.metric(label=f"🏢 {row['区域']}", value=f"{row['当前楼层']} 层", delta=f"工序: {row['当前工序']}")
            
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### 🔍 各阶段明细时间轴 (下钻查看)")
            selected_zone = st.selectbox("👉 选择施工区域查看工序起止时间：", df_progress['区域'].unique())
            if selected_zone:
                df_timeline = fetch_timeline_data(selected_zone)
                if not df_timeline.empty:
                    # 使用 HTML 渲染合并后的表格单元格 (Rowspan 方案)
                    rowspans = []
                    current_floor, count = None, 0
                    for floor in df_timeline['楼层']:
                        if floor == current_floor: count += 1
                        else:
                            if current_floor is not None: rowspans.append(count)
                            current_floor = floor
                            count = 1
                    if count > 0: rowspans.append(count)
                        
                    html_table = '<table style="width: 100%; border-collapse: collapse; font-size: 14px; text-align: left; background-color: #ffffff; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-radius: 8px; overflow: hidden;">'
                    html_table += '<thead><tr style="background-color: var(--cscec-blue); color: #ffffff;"><th style="padding: 12px; border: 1px solid #e0e0e0;">楼层</th><th style="padding: 12px; border: 1px solid #e0e0e0;">施工阶段</th><th style="padding: 12px; border: 1px solid #e0e0e0;">开始时间</th><th style="padding: 12px; border: 1px solid #e0e0e0;">结束时间</th></tr></thead><tbody>'
                    
                    row_idx = 0
                    for span in rowspans:
                        for i in range(span):
                            html_table += '<tr style="border-bottom: 1px solid #f0f0f0;">'
                            if i == 0: 
                                html_table += f'<td rowspan="{span}" style="padding: 12px; border: 1px solid #e0e0e0; vertical-align: middle; text-align: center; font-weight: bold; background-color: var(--cscec-light); color: var(--cscec-blue); font-size: 16px;">{df_timeline.iloc[row_idx]["楼层"]}</td>'
                            html_table += f'<td style="padding: 12px; border: 1px solid #e0e0e0;">{df_timeline.iloc[row_idx]["施工阶段"]}</td>'
                            html_table += f'<td style="padding: 12px; border: 1px solid #e0e0e0; color: #666;">{df_timeline.iloc[row_idx]["开始时间"]}</td>'
                            end_time = df_timeline.iloc[row_idx]["结束时间"]
                            html_table += f'<td style="padding: 12px; border: 1px solid #e0e0e0; font-weight: bold; color: #d48806;">{end_time}</td>' if "进行中" in end_time else f'<td style="padding: 12px; border: 1px solid #e0e0e0; color: #666;">{end_time}</td>'
                            html_table += '</tr>'
                            row_idx += 1
                    html_table += '</tbody></table><br>'
                    st.markdown(html_table, unsafe_allow_html=True)
                else:
                    st.info("尚无历史工序时间流转记录。")

            floor_seq = get_floor_sequence()
            TOTAL_FLOORS = len(floor_seq)
            progress_sum = sum((floor_seq.index(str(val)) + 1) for val in df_progress['当前楼层'] if str(val) in floor_seq)
            progress_pct = min(progress_sum / (TOTAL_FLOORS * len(df_progress)), 1.0) if len(df_progress) > 0 else 0.0
            st.markdown(f"##### 📈 主体结构总工期进度 (共配置 {TOTAL_FLOORS} 个施工段/层)")
            st.progress(progress_pct, text=f"当前综合完成度: {progress_pct*100:.1f}%")
        else:
            st.info("💡 暂无进度数据。请在左侧点击「手动抓拍」或在系统配置中心初始化。")

# ----------------- Tab 2: 系统配置中心 -----------------
with tab2:
    st.markdown("#### ⚙️ 系统集成与设备绑定")
    st.markdown("<hr/>", unsafe_allow_html=True)
    
    col_cam, col_floor = st.columns(2, gap="large")
    
    with col_cam:
        st.markdown("##### 📹 RTSP 视频流与自动化配置")
        st.caption("填写真实的流媒体地址，系统将基于 OpenCV 自动提取关键帧画面。")
        
        new_rtsp = st.text_input("RTSP 源地址", value=app_config.get("rtsp_url", ""))
        new_zone = st.text_input("绑定工地空间语义名称", value=app_config.get("current_zone", ""))
        
        st.markdown("##### ⏱️ AI 自动巡检间隔时间配置")
        new_interval = st.number_input("巡检间隔 (分钟)。设置为 0 表示关闭自动抓拍，仅支持手动。", 
                                       min_value=0, max_value=1440, 
                                       value=app_config.get("auto_interval_minutes", 0))
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 保存流媒体与任务配置", type="primary"):
            app_config["rtsp_url"] = new_rtsp
            app_config["current_zone"] = new_zone
            app_config["auto_interval_minutes"] = new_interval
            save_config(app_config)
            st.success("✅ 配置已写入 `project_config.json`。下一次检测周期会自动生效。")

    with col_floor:
        st.markdown("##### 🏢 主体结构楼层序列配置")
        st.caption("按自下而上的施工顺序填写，支持文字，使用英文逗号分隔。")
        current_seq_str = ",".join(get_floor_sequence())
        new_seq_str = st.text_area("楼层/标段顺序定义", value=current_seq_str, height=100)
        
        if st.button("💾 保存楼层序列"):
            seq_list = [x.strip() for x in new_seq_str.split(",") if x.strip()]
            app_config["floors"] = seq_list
            save_config(app_config)
            st.success(f"楼层顺序更新为: {seq_list}")
            
        st.markdown("<hr/>", unsafe_allow_html=True)
        st.markdown("##### ⚠️ 引擎基准强制修正")
        init_zone = st.text_input("重置目标区域", value=app_config.get("current_zone", ""))
        col_f1, col_f2 = st.columns(2)
        with col_f1: init_floor = st.text_input("物理基准楼层", value="1")
        with col_f2: init_stage = st.selectbox("当前实际工序", ["未开始", "模板阶段", "钢筋阶段", "混凝土浇筑阶段"])
        
        if st.button("🚨 确认覆写空间台账"):
            manager.manual_fix_zone(init_zone, init_floor, init_stage)
            st.success(f"已强制覆盖引擎数据：{init_zone} -> {init_floor} 层 ({init_stage})")