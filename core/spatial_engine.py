import sqlite3

class ZoneProgressTracker:
    def __init__(self, zone_name, initial_floor=1, db_path="construction_progress.db"):
        self.zone_name = zone_name
        self.db_path = db_path
        
        # 初始化数据库并加载状态
        state = self._load_state_from_db(initial_floor)
        self.current_floor = state['floor']
        self.current_stage = state['stage']
        self.is_current_floor_poured = bool(state['is_poured'])
        self.last_update_time = None

    def _load_state_from_db(self, default_floor):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS zone_states 
                            (zone_name TEXT PRIMARY KEY, floor INTEGER, stage TEXT, is_poured INTEGER)''')
            cursor.execute("SELECT floor, stage, is_poured FROM zone_states WHERE zone_name = ?", (self.zone_name,))
            row = cursor.fetchone()
            if row:
                return {'floor': row[0], 'stage': row[1], 'is_poured': row[2]}
            else:
                cursor.execute("INSERT INTO zone_states VALUES (?, ?, ?, ?)", 
                               (self.zone_name, default_floor, "未开始", 0))
                conn.commit()
                return {'floor': default_floor, 'stage': "未开始", 'is_poured': 0}

    def _save_state_to_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE zone_states SET floor = ?, stage = ?, is_poured = ? WHERE zone_name = ?", 
                           (self.current_floor, self.current_stage, int(self.is_current_floor_poured), self.zone_name))
            conn.commit()

    def process_ai_record(self, record):
        ai_stage = record.get("当前作业工序")
        record_time = record.get("识别时间")
        description = record.get("视觉确认描述")

        if ai_stage == self.current_stage:
            return {"status": "ignored", "msg": f"[{self.zone_name}] 状态未改变 ({self.current_stage})"}

        old_stage = self.current_stage
        self.current_stage = ai_stage
        self.last_update_time = record_time

        result_msg = f"[{record_time}] {self.zone_name} 工序更新: {old_stage} -> {ai_stage}"

        # 核心逻辑：触发混凝土浇筑
        if ai_stage == "混凝土浇筑阶段" and not self.is_current_floor_poured:
            self.is_current_floor_poured = True
            result_msg = f"[{record_time}] {self.zone_name} 第 {self.current_floor} 层触发浇筑锁定，准备通知BIM更新"
            self._trigger_platform_sync(self.current_floor, description)

        # 核心逻辑：楼层自动爬升
        if old_stage == "混凝土浇筑阶段" and ai_stage in ["模板阶段", "钢筋阶段"]:
            self.current_floor += 1
            self.is_current_floor_poured = False
            result_msg = f"[{record_time}] {self.zone_name} 进度正式跃升至第 {self.current_floor} 层"

        self._save_state_to_db()
        return {"status": "updated", "msg": result_msg}

    def _trigger_platform_sync(self, floor, desc):
        """此处预留对接数字孪生平台或高精地图的同步接口"""
        print(f"====> [数据贯通 API] 区域: {self.zone_name} | 楼层: {floor} | 动作: 更新孪生体着色状态")


class ProjectProgressManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.zones = {}

    def parse_json_log(self, json_record):
        zone_name = json_record.get("位置")
        if not zone_name: 
            return
            
        if zone_name not in self.zones:
            self.zones[zone_name] = ZoneProgressTracker(zone_name, db_path=self.db_path)
            
        result = self.zones[zone_name].process_ai_record(json_record)
        print(result["msg"])

    def manual_fix_zone(self, zone_name, target_floor, target_stage="模板阶段"):
        """手动修正大跳层或识别异常"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE zone_states SET floor = ?, stage = ?, is_poured = 0 WHERE zone_name = ?", 
                           (target_floor, target_stage, zone_name))
            conn.commit()
        print(f"!!! [手动修正] {zone_name} 已强制重置为第 {target_floor} 层 {target_stage}")