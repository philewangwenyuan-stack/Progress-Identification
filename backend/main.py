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
import asyncio
from fastapi import WebSocket, WebSocketDisconnect

# 引入现有的核心组件 
from config import settings
from core.llm_parser import ConstructionLLMParser
from core.spatial_engine import ProjectProgressManager

app = FastAPI(title="CSCEC 智能进度监控 API", version="1.0.0")
# --- WebSocket 连接管理器 ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

ws_manager = ConnectionManager()
main_loop = None

def notify_frontend():
    """通知前端数据已更新"""
    if main_loop:
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast(json.dumps({"event": "update"})), 
            main_loop
        )

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
                        notify_frontend()
        except Exception as e:
            print(f"[后台任务异常] {e}")
        time.sleep(30)

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()  # 捕获主事件循环，方便子线程调用
    # 启动后台守护线程
    t = threading.Thread(target=auto_capture_task, daemon=True)
    t.start()

# --- 新增的 WebSocket 监听接口 ---
@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # 保持连接心跳
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

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
        notify_frontend()
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
        notify_frontend()  # <--- 新增：手工修正数据后主动推送更新通知给前端
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
    """点击时间轴获取该时间段内所有抓拍数据（合并同名区域，一条记录=一张照片）"""
    import re
    from datetime import datetime
    import json
    import os

    logs = []
    total_workers = 0  # 记录所有照片中出现的人数总和
    count = 0          # 记录一共抓拍了多少张有效照片
    
    if os.path.exists(settings.LOG_FILE_PATH):
        with open(settings.LOG_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    
                    # 【核心修复 1】：把所有同名区域合并起来（模糊匹配）
                    saved_zone = data.get('位置', '')
                    clean_saved = saved_zone.strip()
                    clean_target = zone_name.strip()
                    
                    # 如果日志里的位置不是空的，也不是“识别失败”
                    # 那么只要目标区域名称包含在日志名称里，或者日志名称包含在目标名称里，都算匹配！
                    # （例如："澳门银行" 和 "澳门银行一区" 会被合并算在一起）
                    if clean_saved and clean_saved != "识别失败":
                        if clean_target not in clean_saved and clean_saved not in clean_target:
                            continue # 名字完全不沾边，跳过

                    # 解析时间
                    log_time_str = data.get('识别时间')
                    if not log_time_str: continue
                    
                    log_time = datetime.strptime(log_time_str, "%Y-%m-%d %H:%M:%S")
                    start_t = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    
                    if end_time:
                        end_t = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                        in_range = (start_t <= log_time < end_t) 
                    else:
                        end_t = datetime.now()
                        in_range = (start_t <= log_time <= end_t)
                    
                    if in_range:
                        # 过滤无效记录：如果视觉描述包含“无法识别”或“无有效信息”，则不计入统计
                        desc = data.get("视觉确认描述", "")
                        if "无法识别" in desc or "无有效" in desc or "图像内容为空" in desc:
                            continue
                            
                        logs.append(data)
                        
                        # 【核心修复 2】：增强人数解析逻辑
                        workers = data.get("人数", 0)
                        workers_cnt = 0
                        if isinstance(workers, int):
                            workers_cnt = workers
                        elif isinstance(workers, str):
                            nums = re.findall(r'\d+', workers)
                            if nums:
                                workers_cnt = int(nums[0])
                            elif "多" in workers or "若干" in workers:
                                workers_cnt = 5 # 预估值
                            else:
                                workers_cnt = 0
                            
                        total_workers += workers_cnt
                        count += 1
                except Exception:
                    continue
                    
    # 计算该阶段内，每次抓拍画面的平均作业人数
    avg_workers = round(total_workers / count, 1) if count > 0 else 0

    # 【修复新增逻辑】：计算实际经历的天数，以得出投入人天
    start_t_obj = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    if end_time:
        end_t_obj = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    else:
        end_t_obj = datetime.now()
        
    # 计算总秒数换算为天数（不足1天按1天算，避免除0或数值太小）
    duration_days = max((end_t_obj - start_t_obj).total_seconds() / 86400, 1.0)
    total_man_days = round(avg_workers * duration_days, 1)

    logs.reverse() # 倒序，最新的记录在最上面
    
    # 将 total_man_days 一并返回给前端
    return {"logs": logs, "avg_workers": avg_workers, "count": count, "total_man_days": total_man_days}
