#python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 启动
import os
import json
import time
import threading
import cv2
import pandas as pd
import sqlite3
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import re


# 引入现有的核心组件 
from config import settings
from core.llm_parser import ConstructionLLMParser
from core.spatial_engine import ProjectProgressManager

app = FastAPI(title="CSCEC 智能进度监控 API", version="1.0.0")

# 允许跨域，方便前后端分离本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_PATH = os.path.join(settings.DATA_DIR, "project_config.json")
SNAPSHOT_PATH = os.path.join(settings.DATA_DIR, "latest_snapshot.jpg")

# 全局引擎实例
parser = ConstructionLLMParser(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    model_name=settings.LLM_MODEL_NAME,
    log_file_path=settings.LOG_FILE_PATH
)
manager = ProjectProgressManager(db_path=settings.DB_FILE_PATH)

# --- 辅助函数 (复用您的逻辑) ---
def load_config():
    default_config = {
        "floors": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
        "rtsp_url": "rtsp://14.18.91.10:10554/rtp/...",
        "auto_interval_minutes": 0,
        "current_zone": "塔楼A区"
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                default_config.update(json.load(f))
        except Exception: pass
    return default_config

def capture_rtsp_frame(rtsp_url, save_path):
    # (完全复用您 app.py 中的 capture_rtsp_frame 代码)
    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;5000"
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened(): return False, "无法连接 RTSP"
        ret, frame = False, None
        for _ in range(10): ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            is_success, im_buf_arr = cv2.imencode(".jpg", frame)
            if is_success:
                im_buf_arr.tofile(save_path)
                return True, save_path
        return False, "解码失败"
    except Exception as e: return False, str(e)

# --- 后台自动任务 ---
def auto_capture_task():
    while True:
        try:
            config = load_config()
            interval = config.get("auto_interval_minutes", 0)
            rtsp_url = config.get("rtsp_url", "")
            zone_name = config.get("current_zone", "")
            
            if interval > 0 and rtsp_url:
                should_capture = False
                if not os.path.exists(SNAPSHOT_PATH): should_capture = True
                else:
                    mtime = os.path.getmtime(SNAPSHOT_PATH)
                    if (time.time() - mtime) >= (interval * 60): should_capture = True
                        
                if should_capture:
                    success, path = capture_rtsp_frame(rtsp_url, SNAPSHOT_PATH)
                    if success:
                        result = parser.parse_instruction_with_image("请识别当前施工工序", path, zone_name)
                        result["位置"] = zone_name
                        manager.parse_json_log(result)
        except Exception as e:
            print(f"[后台任务异常] {e}")
        time.sleep(30)

@app.on_event("startup")
def startup_event():
    # 启动后台守护线程
    t = threading.Thread(target=auto_capture_task, daemon=True)
    t.start()

# ================= API 路由 =================

@app.get("/api/progress")
def get_progress():
    """获取整体进度看板数据"""
    if not os.path.exists(settings.DB_FILE_PATH): return {"data": []}
    with sqlite3.connect(settings.DB_FILE_PATH) as conn:
        df = pd.read_sql_query("SELECT zone_name, floor, stage, is_poured FROM zone_states_v2", conn)
        return {"data": df.to_dict(orient="records")}

@app.get("/api/timeline/{zone_name}")
def get_timeline(zone_name: str):
    """获取单个区域的时间轴"""
    if not os.path.exists(settings.DB_FILE_PATH): return {"data": []}
    with sqlite3.connect(settings.DB_FILE_PATH) as conn:
        df = pd.read_sql_query("SELECT floor, stage, start_time, end_time FROM stage_timeline WHERE zone_name = ? ORDER BY id DESC", conn, params=(zone_name,))
        return {"data": df.to_dict(orient="records")}

@app.get("/api/snapshot/latest")
def get_latest_snapshot():
    """获取最新抓拍的图片文件"""
    if os.path.exists(SNAPSHOT_PATH):
        return FileResponse(SNAPSHOT_PATH, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="尚无抓拍图片")

@app.post("/api/capture/manual")
def manual_capture():
    """手动触发抓拍与大模型解析"""
    config = load_config()
    rtsp_url = config.get("rtsp_url", "")
    zone_name = config.get("current_zone", "塔楼A区")
        
    success, msg = capture_rtsp_frame(rtsp_url, SNAPSHOT_PATH)
    if success:
        result = parser.parse_instruction_with_image("请识别当前施工工序", SNAPSHOT_PATH, zone_name)
        result["位置"] = zone_name 
        manager.parse_json_log(result)
        
        if result.get("当前作业工序", "识别失败") == "识别失败":
            # 关键修改：加入了 llm_result 字段返回给前端
            return {"status": "warning", "message": "抓拍完成，但 AI 识别异常", "llm_result": result}
            
        # 关键修改：加入了 llm_result 字段返回给前端
        return {"status": "success", "message": "智能识别完成，进度已更新！", "llm_result": result}
    else:
        raise HTTPException(status_code=500, detail=msg)

# ================= 手动强制修改楼层接口 =================
class ManualFixRequest(BaseModel):
    zone_name: str
    target_floor: str
    target_stage: str

@app.post("/api/progress/manual")
def manual_fix_progress(req: ManualFixRequest):
    """人工介入：强制修改某个区域的楼层和状态"""
    try:
        # 调用你在 spatial_engine.py 中写好的 manual_fix_zone 函数
        manager.manual_fix_zone(req.zone_name, req.target_floor, req.target_stage)
        
        # 记录一条特殊的人工干预日志到底层表里，保持时间轴连续性
        record_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(settings.DB_FILE_PATH) as conn:
            cursor = conn.cursor()
            # 结束上一条状态
            cursor.execute("""UPDATE stage_timeline SET end_time = ? 
                              WHERE zone_name = ? AND end_time IS NULL""", 
                           (record_time, req.zone_name))
            # 插入强制修改的新状态
            cursor.execute("""INSERT INTO stage_timeline (zone_name, floor, stage, start_time) 
                              VALUES (?, ?, ?, ?)""", 
                           (req.zone_name, req.target_floor, req.target_stage, record_time))
            conn.commit()

        return {"status": "success", "message": f"{req.zone_name} 已由人工指令强制变更为：{req.target_floor}层 - {req.target_stage}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"手动修改失败: {str(e)}")
# ==============================================================
# 配置相关的 Pydantic 模型
class ConfigUpdate(BaseModel):
    rtsp_url: str
    current_zone: str
    auto_interval_minutes: int
    floors: list[str]

@app.get("/api/config")
def get_config():
    return load_config()

@app.post("/api/config")
def update_config(config_data: ConfigUpdate):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data.dict(), f, ensure_ascii=False, indent=4)
    return {"status": "success", "message": "配置已更新"}

@app.post("/api/project/reset")
def reset_project_api():
    """提供给前端的系统全盘重置接口"""
    try:
        manager.reset_project()
        return {"status": "success", "message": "系统数据已全部清空，项目已重置归零！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@app.get("/api/logs/latest")
def get_latest_log():
    """解决 Bug 3: 获取最近一次的 AI 识别记录，用于页面刷新后回显"""
    if not os.path.exists(settings.LOG_FILE_PATH):
        return {"data": None}
    try:
        with open(settings.LOG_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if lines:
                return {"data": json.loads(lines[-1])}
    except Exception:
        pass
    return {"data": None}


@app.get("/api/timeline/details")
def get_timeline_details(zone_name: str, start_time: str, end_time: str = None):
    """点击时间轴获取该时间段内所有抓拍数据与平均人数 (彻底修复次数计算Bug)"""
    logs = []
    total_workers = 0
    count = 0
    if os.path.exists(settings.LOG_FILE_PATH):
        with open(settings.LOG_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    
                    # 兼容处理历史脏数据：只对明确标记了其他具体区域的进行拦截
                    saved_zone = data.get('位置', '')
                    if saved_zone and saved_zone != zone_name and saved_zone != "识别失败":
                        # 假如之前没传zone，大模型胡编了"施工现场"或"工地"，予以放行统计
                        if "区" in saved_zone or "楼" in saved_zone:
                            if saved_zone != zone_name: continue

                    log_time_str = data.get('识别时间')
                    if not log_time_str: continue
                    
                    log_time = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S")
                    start_t = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    
                    # 【修复核心 2】：时间范围改为左闭右开 ( < end_t )，防止跨阶段重复统计同一条抓拍
                    if end_time:
                        end_t = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                        in_range = (start_t <= log_time < end_t) 
                    else:
                        end_t = datetime.now()
                        in_range = (start_t <= log_time <= end_t)
                    
                    if in_range:
                        logs.append(data)
                        workers = data.get("人数", 0)
                        if isinstance(workers, str):
                            nums = re.findall(r'\d+', workers)
                            workers_cnt = int(nums[0]) if nums else 0
                        elif isinstance(workers, int):
                            workers_cnt = workers
                        else:
                            workers_cnt = 0
                        
                        total_workers += workers_cnt
                        count += 1
                except Exception:
                    continue
                    
    avg = round(total_workers / count, 1) if count > 0 else 0
    logs.reverse() # 倒序，最新的记录在最上面
    
    return {"logs": logs, "avg_workers": avg, "count": count}
                    
