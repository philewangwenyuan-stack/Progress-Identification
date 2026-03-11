import pymysql
import json
import os
from config.settings import MYSQL_CONFIG  # 引入 MySQL 配置

def get_db_connection():
    """获取 MySQL 数据库连接"""
    return pymysql.connect(
        host=MYSQL_CONFIG['host'],
        port=MYSQL_CONFIG['port'],
        user=MYSQL_CONFIG['user'],
        password=MYSQL_CONFIG['password'],
        database=MYSQL_CONFIG['database'],
        charset=MYSQL_CONFIG['charset'],
        autocommit=False  # 保持手动提交事务的习惯
    )

def get_floor_sequence():
    """【核心修复】：读取 MySQL 中的全局联动楼层配置，彻底抛弃本地 JSON"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT floors FROM project_config WHERE id = 1")
            row = cursor.fetchone()
            if row and row[0]:
                # 兼容处理：确保提取出来的是 Python 列表
                if isinstance(row[0], str):
                    return json.loads(row[0])
                return row[0]
    except Exception as e:
        print(f"获取楼层序列异常: {e}")
    finally:
        conn.close()
    return [str(i) for i in range(1, 21)]

class ZoneProgressTracker:
    def __init__(self, zone_name):
        self.zone_name = zone_name
        
        # 初始化数据库并加载状态
        state = self._load_state_from_db()
        self.current_floor = str(state['floor'])
        self.current_stage = state['stage']
        self.is_current_floor_poured = bool(state['is_poured'])

    def refresh_from_db(self):
        """强制从数据库读取最新状态，覆盖 AI 的内存缓存"""
        state = self._load_state_from_db()
        self.current_floor = str(state['floor'])
        self.current_stage = state['stage']
        self.is_current_floor_poured = bool(state['is_poured'])

    def _load_state_from_db(self):
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # MySQL 表结构定义
                cursor.execute('''CREATE TABLE IF NOT EXISTS zone_states_v2 
                                (zone_name VARCHAR(255) PRIMARY KEY, 
                                 floor VARCHAR(50), 
                                 stage VARCHAR(100), 
                                 is_poured TINYINT(1))''')
                
                cursor.execute('''CREATE TABLE IF NOT EXISTS stage_timeline 
                                (id INT AUTO_INCREMENT PRIMARY KEY,
                                 zone_name VARCHAR(255), 
                                 floor VARCHAR(50), 
                                 stage VARCHAR(100), 
                                 start_time DATETIME, 
                                 end_time DATETIME)''')
                conn.commit()
                
                cursor.execute("SELECT floor, stage, is_poured FROM zone_states_v2 WHERE zone_name = %s", (self.zone_name,))
                row = cursor.fetchone()
                
                if row:
                    return {'floor': row[0], 'stage': row[1], 'is_poured': row[2]}
                else:
                    default_floor = get_floor_sequence()[0]
                    cursor.execute("INSERT INTO zone_states_v2 (zone_name, floor, stage, is_poured) VALUES (%s, %s, %s, %s)", 
                                   (self.zone_name, default_floor, "未开始", 0))
                    conn.commit()
                    return {'floor': default_floor, 'stage': "未开始", 'is_poured': 0}
        finally:
            conn.close()

    def _save_state_to_db(self):
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE zone_states_v2 SET floor = %s, stage = %s, is_poured = %s WHERE zone_name = %s", 
                               (self.current_floor, self.current_stage, int(self.is_current_floor_poured), self.zone_name))
                conn.commit()
        finally:
            conn.close()

    def _record_timeline(self, record_time, is_close_current=False, new_stage=None):
        """记录台账明细（起止时间）"""
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                if is_close_current:
                    # 结束当前阶段
                    cursor.execute("""UPDATE stage_timeline SET end_time = %s 
                                      WHERE zone_name = %s AND floor = %s AND stage = %s AND end_time IS NULL""", 
                                   (record_time, self.zone_name, self.current_floor, self.current_stage))
                if new_stage:
                    # 开启新阶段
                    cursor.execute("""INSERT INTO stage_timeline (zone_name, floor, stage, start_time) 
                                      VALUES (%s, %s, %s, %s)""", 
                                   (self.zone_name, self.current_floor, new_stage, record_time))
                conn.commit()
        finally:
            conn.close()

    def _get_next_floor(self):
        seq = get_floor_sequence()
        try:
            idx = seq.index(self.current_floor)
            return seq[idx + 1] if idx + 1 < len(seq) else self.current_floor
        except ValueError:
            return self.current_floor

    def process_ai_record(self, record):
        ai_stage = record.get("当前作业工序", "识别失败")
        record_time = record.get("识别时间", "未知时间")
        description = record.get("视觉确认描述", "")

        if ai_stage == "识别失败":
             return {"status": "error", "msg": f"[{record_time}] AI识别失败，跳过更新"}

        if self.current_stage == "未开始":
            self._record_timeline(record_time, new_stage=ai_stage)
            self.current_stage = ai_stage
            self._save_state_to_db()
            return {"status": "updated", "msg": f"[{record_time}] {self.zone_name} 开始进入 {ai_stage}"}

        if ai_stage == self.current_stage:
            return {"status": "ignored", "msg": f"[{self.zone_name}] 状态未改变 ({self.current_stage})"}

        old_stage = self.current_stage
        
        STAGE_WEIGHTS = {
            "未开始": 0,
            "模板阶段": 1,
            "钢筋阶段": 2,
            "混凝土阶段": 3
        }
        
        is_climbing = (old_stage == "混凝土阶段" and ai_stage in ["模板阶段", "钢筋阶段"])
        
        if not is_climbing:
            old_weight = STAGE_WEIGHTS.get(old_stage, -1)
            new_weight = STAGE_WEIGHTS.get(ai_stage, -1)
            
            if new_weight < old_weight:
                return {"status": "ignored", "msg": f"[{record_time}] ⚠️ 视觉误报拦截: {self.current_floor}层已在【{old_stage}】，过滤滞后画面识别【{ai_stage}】"}

        if is_climbing:
            next_floor = self._get_next_floor()
            if next_floor != self.current_floor:
                self._record_timeline(record_time, is_close_current=True)
                old_floor = self.current_floor
                self.current_floor = next_floor
                self.current_stage = ai_stage
                self.is_current_floor_poured = False
                self._record_timeline(record_time, new_stage=ai_stage)
                result_msg = f"[{record_time}] {self.zone_name} 进度正式跃升: {old_floor}层({old_stage}) -> {self.current_floor}层({ai_stage})"
            else:
                self._record_timeline(record_time, is_close_current=True, new_stage=ai_stage)
                self.current_stage = ai_stage
                result_msg = f"[{record_time}] {self.zone_name} 已封顶，工序更新: {old_stage} -> {ai_stage}"
        else:
            self._record_timeline(record_time, is_close_current=True, new_stage=ai_stage)
            self.current_stage = ai_stage
            result_msg = f"[{record_time}] {self.zone_name} 工序更新: {old_stage} -> {ai_stage}"

        if ai_stage == "混凝土阶段" and not self.is_current_floor_poured:
            self.is_current_floor_poured = True
            result_msg += f" | 触发{self.current_floor}层浇筑锁定"
            self._trigger_platform_sync(self.current_floor, description)

        self._save_state_to_db()
        return {"status": "updated", "msg": result_msg}

    def _trigger_platform_sync(self, floor, desc):
        print(f"====> [数据贯通 API] 区域: {self.zone_name} | 楼层: {floor} | 动作: 更新孪生体着色状态")


class ProjectProgressManager:
    def __init__(self):
        self.zones = {}

    def parse_json_log(self, json_record):
        zone_name = json_record.get("位置")
        if not zone_name or zone_name == "识别失败": 
            return
            
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute('''CREATE TABLE IF NOT EXISTS recognition_history 
                                (id INT AUTO_INCREMENT PRIMARY KEY,
                                 zone_name VARCHAR(255), 
                                 workers VARCHAR(50), 
                                 stage VARCHAR(100), 
                                 description TEXT,
                                 recognition_time DATETIME,
                                 image_path VARCHAR(500))''')
                
                cursor.execute("""INSERT INTO recognition_history 
                                  (zone_name, workers, stage, description, recognition_time, image_path)
                                  VALUES (%s, %s, %s, %s, %s, %s)""",
                               (zone_name, 
                                str(json_record.get("人数", "")), 
                                json_record.get("当前作业工序", ""), 
                                json_record.get("视觉确认描述", ""), 
                                json_record.get("识别时间", ""),
                                json_record.get("原始图片路径", "")))
                conn.commit()
        except Exception as e:
            print(f"记录保存至MySQL失败: {e}")
        finally:
            conn.close()

        if zone_name not in self.zones:
            self.zones[zone_name] = ZoneProgressTracker(zone_name)
            
        result = self.zones[zone_name].process_ai_record(json_record)
        print(result["msg"])

    def manual_fix_zone(self, zone_name, target_floor, target_stage="模板阶段"):
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # 【核心修复】：为防止刚开机时表不存在引发 500 报错，前置建表逻辑！
                cursor.execute('''CREATE TABLE IF NOT EXISTS zone_states_v2 
                                (zone_name VARCHAR(255) PRIMARY KEY, 
                                 floor VARCHAR(50), 
                                 stage VARCHAR(100), 
                                 is_poured TINYINT(1))''')
                cursor.execute('''CREATE TABLE IF NOT EXISTS stage_timeline 
                                (id INT AUTO_INCREMENT PRIMARY KEY,
                                 zone_name VARCHAR(255), 
                                 floor VARCHAR(50), 
                                 stage VARCHAR(100), 
                                 start_time DATETIME, 
                                 end_time DATETIME)''')

                cursor.execute("SELECT 1 FROM zone_states_v2 WHERE zone_name = %s", (zone_name,))
                if cursor.fetchone():
                    cursor.execute("UPDATE zone_states_v2 SET floor = %s, stage = %s, is_poured = 0 WHERE zone_name = %s", 
                                   (str(target_floor), target_stage, zone_name))
                else:
                    cursor.execute("INSERT INTO zone_states_v2 (zone_name, floor, stage, is_poured) VALUES (%s, %s, %s, %s)", 
                                   (zone_name, str(target_floor), target_stage, 0))
                conn.commit()
        finally:
            conn.close()
        
        if zone_name in self.zones:
            self.zones[zone_name].refresh_from_db()    
        else:
            self.zones[zone_name] = ZoneProgressTracker(zone_name)
            
        print(f"!!! [手动修正] {zone_name} 已强制重置为 {target_floor} 层 {target_stage}")

    def reset_project(self):
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                try:
                    cursor.execute("TRUNCATE TABLE zone_states_v2")
                    cursor.execute("TRUNCATE TABLE stage_timeline")
                    cursor.execute("TRUNCATE TABLE recognition_history")
                except Exception as e:
                    print(f"重置表时发生警告(可能是表尚不存在): {e}")
                conn.commit()
        finally:
            conn.close()
        
        self.zones.clear()
        print("!!! [系统提示] MySQL 数据库内容已安全清空，内存已释放，全盘重置完成 !!!")