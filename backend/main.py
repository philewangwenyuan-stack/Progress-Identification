# python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 启动
import os
import json
import time
import threading
import cv2
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import re
import asyncio
import pdfplumber
import io
import pymysql
from typing import Optional

# 引入现有的核心组件 
from config import settings
from core.llm_parser import ConstructionLLMParser
from core.spatial_engine import ProjectProgressManager, get_db_connection

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

# 允许跨域，注意 allow_credentials 必须是 False 才能配合 "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SNAPSHOT_PATH = os.path.join(settings.DATA_DIR, "latest_snapshot.jpg")

# 全局引擎实例
parser = ConstructionLLMParser(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    model_name=settings.LLM_MODEL_NAME,
    log_file_path=settings.LOG_FILE_PATH
)
manager = ProjectProgressManager()

# ================= 数据库初始化 =================
def init_db_tables():
    """系统启动时自动初始化 MySQL 表结构"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 创建全局配置表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS project_config (
                    id INT PRIMARY KEY,
                    rtsp_url VARCHAR(500),
                    current_zone VARCHAR(100),
                    auto_interval_minutes INT,
                    floors JSON
                )
            ''')
            # 确保有一条基础数据 id=1
            cursor.execute("SELECT id FROM project_config WHERE id = 1")
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO project_config (id, rtsp_url, current_zone, auto_interval_minutes, floors) 
                    VALUES (1, '', '塔楼A区', 0, '[]')
                """)

            # 2. 创建进度计划表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS project_plan (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    floor VARCHAR(100),
                    stage VARCHAR(100),
                    planned_start VARCHAR(50),
                    planned_end VARCHAR(50)
                )
            ''')
            conn.commit()
    except Exception as e:
        print(f"初始化配置表失败: {e}")
    finally:
        conn.close()

# ================= 辅助函数 =================
def get_config_from_db():
    """从 MySQL 提取全局配置（替代原先的 load_config）"""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM project_config WHERE id = 1")
            row = cursor.fetchone()
            if row:
                row['floors'] = json.loads(row['floors']) if row['floors'] else []
                return row
    except Exception as e:
        print(f"提取配置异常: {e}")
    finally:
        conn.close()
    return {"floors": [], "rtsp_url": "", "auto_interval_minutes": 0, "current_zone": "塔楼A区"}

def capture_rtsp_frame(rtsp_url, save_path):
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
            config = get_config_from_db()
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
    init_db_tables() # 启动时初始化表
    global main_loop
    main_loop = asyncio.get_running_loop()  
    t = threading.Thread(target=auto_capture_task, daemon=True)
    t.start()

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() 
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# ================= API 路由 =================

@app.get("/api/progress")
def get_progress():
    conn = get_db_connection()
    try:
        import pymysql
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT zone_name, floor, stage, is_poured FROM zone_states_v2")
            records = cursor.fetchall()
            return {"data": records}
    except Exception as e:
        print(f"获取看板数据异常: {e}")
        return {"data": []}
    finally:
        conn.close()

@app.get("/api/timeline/{zone_name}")
def get_timeline(zone_name: str):
    """获取单个区域的时间轴，并结合 MySQL 里的进度计划计算滞后状态"""
    plan_data = []
    conn = get_db_connection()
    try:
        # 1. 提取当前 MySQL 中的进度计划
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT floor, stage, planned_start, planned_end FROM project_plan")
            plan_data = cursor.fetchall()
            
        # 2. 提取时间轴
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT floor, stage, start_time, end_time FROM stage_timeline WHERE zone_name = %s ORDER BY id DESC", 
                (zone_name,)
            )
            records = cursor.fetchall()
    except Exception as e:
        print(f"获取时间轴异常: {e}")
        records = []
    finally:
        conn.close()
        
    # 3. 对比实际进度与计划进度
    for row in records:
        row["status"] = "未排期"  
        row["planned_start"] = "暂无计划"
        row["planned_end"] = "暂无计划"
        
        for p in plan_data:
            if str(p.get("floor", "")) == str(row["floor"]) and p.get("stage", "") == row["stage"]:
                row["planned_start"] = p.get("planned_start", "")
                row["planned_end"] = p.get("planned_end", "")
                row["status"] = "正常" 
                
                try:
                    if row["planned_end"]:
                        clean_plan_end = row["planned_end"].replace("/", "-")
                        planned_end_dt = datetime.strptime(clean_plan_end, "%Y-%m-%d").date()
                        
                        if row["end_time"]:
                            actual_dt = datetime.strptime(row["end_time"], "%Y-%m-%d %H:%M:%S").date()
                        else:
                            actual_dt = datetime.now().date()
                            
                        if actual_dt > planned_end_dt:
                            row["status"] = "滞后"
                except Exception:
                    pass 
                break 
                
    return {"data": records}

@app.get("/api/snapshot/latest")
def get_latest_snapshot():
    if os.path.exists(SNAPSHOT_PATH):
        return FileResponse(SNAPSHOT_PATH, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="尚无抓拍图片")

@app.post("/api/capture/manual")
def manual_capture():
    config = get_config_from_db()
    rtsp_url = config.get("rtsp_url", "")
    zone_name = config.get("current_zone", "塔楼A区")
        
    success, msg = capture_rtsp_frame(rtsp_url, SNAPSHOT_PATH)
    if success:
        result = parser.parse_instruction_with_image("请识别当前施工工序", SNAPSHOT_PATH, zone_name)
        result["位置"] = zone_name 
        manager.parse_json_log(result)
        notify_frontend()
        if result.get("当前作业工序", "识别失败") == "识别失败":
            return {"status": "warning", "message": "抓拍完成，但 AI 识别异常", "llm_result": result}
        return {"status": "success", "message": "智能识别完成，进度已更新！", "llm_result": result}
    else:
        raise HTTPException(status_code=500, detail=msg)

class ManualFixRequest(BaseModel):
    zone_name: str
    target_floor: str
    target_stage: str

@app.post("/api/progress/manual")
def manual_fix_progress(req: ManualFixRequest):
    try:
        manager.manual_fix_zone(req.zone_name, req.target_floor, req.target_stage)
        record_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""UPDATE stage_timeline SET end_time = %s 
                                  WHERE zone_name = %s AND end_time IS NULL""", 
                               (record_time, req.zone_name))
                cursor.execute("""INSERT INTO stage_timeline (zone_name, floor, stage, start_time) 
                                  VALUES (%s, %s, %s, %s)""", 
                               (req.zone_name, req.target_floor, req.target_stage, record_time))
            conn.commit()
        finally:
            conn.close()
            
        notify_frontend()
        return {"status": "success", "message": f"{req.zone_name} 已由人工指令强制变更为：{req.target_floor}层 - {req.target_stage}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"手动修改失败: {str(e)}")

# ================= 系统与配置相关 =================
class ConfigUpdate(BaseModel):
    rtsp_url: str
    current_zone: str
    auto_interval_minutes: int
    floors: list[str]

@app.get("/api/config")
def get_config():
    return get_config_from_db()

@app.post("/api/config")
def update_config(config_data: ConfigUpdate):
    """保存配置并写入 MySQL"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            floors_json = json.dumps(config_data.floors, ensure_ascii=False)
            cursor.execute("""
                UPDATE project_config 
                SET rtsp_url=%s, current_zone=%s, auto_interval_minutes=%s, floors=%s 
                WHERE id=1
            """, (
                config_data.rtsp_url,
                config_data.current_zone,
                config_data.auto_interval_minutes,
                floors_json
            ))
            conn.commit()
        return {"status": "success", "message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/project/reset")
def reset_project_api():
    try:
        manager.reset_project()
        return {"status": "success", "message": "系统数据已全部清空，项目已重置归零！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ================= 日志查询 =================
@app.get("/api/logs/latest")
def get_latest_log():
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM recognition_history ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return {"data": {
                    "位置": row["zone_name"],
                    "人数": row["workers"],
                    "当前作业工序": row["stage"],
                    "视觉确认描述": row["description"],
                    "识别时间": row["recognition_time"].strftime("%Y-%m-%d %H:%M:%S") if row["recognition_time"] else "",
                    "原始图片路径": row["image_path"]
                }}
    except Exception:
        pass
    finally:
        conn.close()
    return {"data": None}

@app.get("/api/timeline/details")
def get_timeline_details(
    zone_name: str, 
    start_time: str, 
    end_time: str = None,
    work_start: str = "00:00:00",  # 新增：接收前端的作业开始时间
    work_end: str = "23:59:59",    # 新增：接收前端的作业结束时间
    ignore_stage_time: str = "true" # 新增：接收前端的“放宽到整天计算”开关
):
    logs = []
    total_workers = 0
    count = 0
    
    # 将前端传来的字符串 boolean 转换为 Python bool
    is_ignore_strict = str(ignore_stage_time).lower() == 'true'

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            actual_start = start_time
            actual_end = end_time
            
            # 核心修复 1：如果前端勾选了“强制放宽”，则剥离掉具体的小时/分钟/秒，拉宽到当天的 00:00 到 23:59
            if is_ignore_strict:
                try:
                    dt_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    actual_start = dt_start.strftime("%Y-%m-%d 00:00:00")
                    if end_time:
                        dt_end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                        actual_end = dt_end.strftime("%Y-%m-%d 23:59:59")
                except Exception:
                    pass

            # 核心修复 2：在 SQL 查询中增加 TIME() 函数过滤，对接前端设置的 dailyTimeRange
            if actual_end:
                query = """SELECT * FROM recognition_history 
                           WHERE zone_name LIKE %s 
                           AND recognition_time >= %s 
                           AND recognition_time < %s 
                           AND TIME(recognition_time) >= %s 
                           AND TIME(recognition_time) <= %s
                           ORDER BY recognition_time DESC"""
                cursor.execute(query, (f"%{zone_name}%", actual_start, actual_end, work_start, work_end))
            else:
                query = """SELECT * FROM recognition_history 
                           WHERE zone_name LIKE %s 
                           AND recognition_time >= %s 
                           AND TIME(recognition_time) >= %s 
                           AND TIME(recognition_time) <= %s
                           ORDER BY recognition_time DESC"""
                cursor.execute(query, (f"%{zone_name}%", actual_start, work_start, work_end))
                
            rows = cursor.fetchall()
            print(f"🔍 [调试] 数据库查询完毕，原始记录有: {len(rows)} 条") # 👈 新增打印 1
            for row in rows:
                desc = row.get("description", "")
                print(f"📦 [调试] 正在分析第 {len(logs)+1} 条: 人数内容='{row['workers']}', 描述='{desc}'") #
                # 注意：这里依然保留了硬性过滤。如果大模型明确说“无有效人员”等，依然会被过滤。
                if "无法识别" in desc or "无有效" in desc or "图像内容为空" in desc:
                    
                    continue
                    
                logs.append({
                    "位置": row["zone_name"],
                    "人数": row["workers"],
                    "当前作业工序": row["stage"],
                    "视觉确认描述": desc,
                    "识别时间": row["recognition_time"].strftime("%Y-%m-%d %H:%M:%S")
                })
                
                workers_str = str(row["workers"])
                workers_cnt = 0
                
                # 核心修复 3：增加中文数字兼容，防止大模型返回中文如"两名工人"导致人数识别为 0
                nums = re.findall(r'\d+', workers_str)
                cn_num_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
                cn_match = None
                for cn_char, num_val in cn_num_map.items():
                    if cn_char in workers_str:
                        cn_match = num_val
                        break

                if nums:
                    workers_cnt = int(nums[0])
                elif cn_match:
                    workers_cnt = cn_match
                elif "多" in workers_str or "若干" in workers_str:
                    workers_cnt = 5
                    
                total_workers += workers_cnt
                count += 1
    except Exception as e:
        print(f"提取明细异常: {e}")
    finally:
        conn.close()

    avg_workers = round(total_workers / count, 1) if count > 0 else 0
    start_t_obj = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    if end_time:
        end_t_obj = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    else:
        end_t_obj = datetime.now()
        
    duration_days = max((end_t_obj - start_t_obj).total_seconds() / 86400, 1.0)
    total_man_days = round(avg_workers * duration_days, 1)

    return {"logs": logs, "avg_workers": avg_workers, "count": count, "total_man_days": total_man_days}

# ================= 计划上传与保存 =================
@app.post("/api/plan/upload")
async def upload_and_parse_plan(file: UploadFile = File(...)):
    raw_text = ""
    file_ext = file.filename.lower().split('.')[-1]
    
    try:
        file_bytes = await file.read()
        file_stream = io.BytesIO(file_bytes)

        if file_ext in ['xlsx', 'xls']:
            df = pd.read_excel(file_stream)
            raw_text = df.to_string()
        elif file_ext == 'pdf':
            with pdfplumber.open(file_stream) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text: raw_text += text + "\n"
            
        if not raw_text.strip():
            return {"status": "error", "message": "未能提取到文本"}
            
        parsed_plan = parser.parse_project_plan(raw_text)
        return {
            "status": "success", 
            "message": "解析成功", 
            "data": parsed_plan 
        }
    except Exception as e:
        return {"status": "error", "message": f"解析异常: {str(e)}"}

class PlanItem(BaseModel):
    floor: str = ""
    stage: str = "默认工序"
    planned_start: str = ""
    planned_end: str = ""

class PlanSaveRequest(BaseModel):
    plans: list[PlanItem]

@app.get("/api/plan")
def get_project_plan():
    """页面刷新时，前端调用此接口获取已保存的进度计划回显到表格中"""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 按插入顺序查出所有的计划
            cursor.execute("SELECT floor, stage, planned_start, planned_end FROM project_plan ORDER BY id ASC")
            rows = cursor.fetchall()
            return {"status": "success", "data": rows}
    except Exception as e:
        return {"status": "error", "message": f"获取计划失败: {str(e)}", "data": []}
    finally:
        conn.close()

@app.post("/api/plan/save")
def save_project_plan(req: PlanSaveRequest):
    """保存前端提交的进度计划表，并与全局楼层实现联动更新"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 保存计划至 MySQL
            cursor.execute("TRUNCATE TABLE project_plan")
            if req.plans:
                sql = "INSERT INTO project_plan (floor, stage, planned_start, planned_end) VALUES (%s, %s, %s, %s)"
                vals = [(p.floor, p.stage, p.planned_start, p.planned_end) for p in req.plans]
                cursor.executemany(sql, vals)
            
            # 2. 核心联动：提取不重复的楼层序列，同步覆写回 config 的 floors 里
            extracted_floors = []
            for p in req.plans:
                if p.floor and p.floor not in extracted_floors:
                    extracted_floors.append(p.floor)
            
            if extracted_floors:
                floors_json = json.dumps(extracted_floors, ensure_ascii=False)
                cursor.execute("UPDATE project_config SET floors=%s WHERE id=1", (floors_json,))
            
            conn.commit()
            
        return {"status": "success", "message": "进度计划已成功保存，全局楼层序列已同步更新！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存计划失败: {str(e)}")
    finally:
        conn.close()