import sqlite3
import json
import os

def get_floor_sequence():
    """读取自定义楼层配置"""
    config_path = "data/project_config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("floors", ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"])
    return [str(i) for i in range(1, 21)]

class ZoneProgressTracker:
    def __init__(self, zone_name, db_path="data/db/construction_progress.db"):
        self.zone_name = zone_name
        self.db_path = db_path
        
        # 初始化数据库并加载状态
        state = self._load_state_from_db()
        self.current_floor = str(state['floor']) # 强制转为字符串支持"夹层"
        self.current_stage = state['stage']
        self.is_current_floor_poured = bool(state['is_poured'])

        self.zone_name = zone_name
        self.db_path = db_path
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
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 升级DB：使用 v2 表，floor 字段改为 TEXT
            cursor.execute('''CREATE TABLE IF NOT EXISTS zone_states_v2 
                            (zone_name TEXT PRIMARY KEY, floor TEXT, stage TEXT, is_poured INTEGER)''')
            # 增加时间轴明细表
            cursor.execute('''CREATE TABLE IF NOT EXISTS stage_timeline 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT,
                             zone_name TEXT, floor TEXT, stage TEXT, start_time TEXT, end_time TEXT)''')
            
            cursor.execute("SELECT floor, stage, is_poured FROM zone_states_v2 WHERE zone_name = ?", (self.zone_name,))
            row = cursor.fetchone()
            if row:
                return {'floor': row[0], 'stage': row[1], 'is_poured': row[2]}
            else:
                default_floor = get_floor_sequence()[0]
                cursor.execute("INSERT INTO zone_states_v2 VALUES (?, ?, ?, ?)", 
                               (self.zone_name, default_floor, "未开始", 0))
                conn.commit()
                return {'floor': default_floor, 'stage': "未开始", 'is_poured': 0}

    def _save_state_to_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE zone_states_v2 SET floor = ?, stage = ?, is_poured = ? WHERE zone_name = ?", 
                           (self.current_floor, self.current_stage, int(self.is_current_floor_poured), self.zone_name))
            conn.commit()

    def _record_timeline(self, record_time, is_close_current=False, new_stage=None):
        """记录台账明细（起止时间）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if is_close_current:
                # 结束当前阶段
                cursor.execute("""UPDATE stage_timeline SET end_time = ? 
                                  WHERE zone_name = ? AND floor = ? AND stage = ? AND end_time IS NULL""", 
                               (record_time, self.zone_name, self.current_floor, self.current_stage))
            if new_stage:
                # 开启新阶段
                cursor.execute("""INSERT INTO stage_timeline (zone_name, floor, stage, start_time) 
                                  VALUES (?, ?, ?, ?)""", 
                               (self.zone_name, self.current_floor, new_stage, record_time))
            conn.commit()

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

        # 1. 拦截识别失败
        if ai_stage == "识别失败":
             return {"status": "error", "msg": f"[{record_time}] AI识别失败，跳过更新"}

        # 2. 初始状态处理
        if self.current_stage == "未开始":
            self._record_timeline(record_time, new_stage=ai_stage)
            self.current_stage = ai_stage
            self._save_state_to_db()
            return {"status": "updated", "msg": f"[{record_time}] {self.zone_name} 开始进入 {ai_stage}"}

        # 3. 拦截状态未变的情况
        if ai_stage == self.current_stage:
            return {"status": "ignored", "msg": f"[{self.zone_name}] 状态未改变 ({self.current_stage})"}

        old_stage = self.current_stage
        
        # 💡 定义工序单向流转权重（数值越大阶段越靠后）
        STAGE_WEIGHTS = {
            "未开始": 0,
            "模板阶段": 1,
            "钢筋阶段": 2,
            "混凝土阶段": 3
        }
        
        # 判断是否属于爬层动作
        is_climbing = (old_stage == "混凝土阶段" and ai_stage in ["模板阶段", "钢筋阶段"])
        
        # 🚀 新增防御：同层状态下的单向流转检查，防止 AI 误报导致工序倒退
        if not is_climbing:
            old_weight = STAGE_WEIGHTS.get(old_stage, -1)
            new_weight = STAGE_WEIGHTS.get(ai_stage, -1)
            
            # 如果新识别出的权重比当前老权重低，说明大模型看错了历史状态，强行过滤！
            if new_weight < old_weight:
                return {"status": "ignored", "msg": f"[{record_time}] ⚠️ 视觉误报拦截: {self.current_floor}层已在【{old_stage}】，过滤滞后画面识别【{ai_stage}】"}

        # ---- 过了防御机制后，执行正常的状态机流转 ----
        
        if is_climbing:
            next_floor = self._get_next_floor()
            if next_floor != self.current_floor:
                # 爬层处理
                self._record_timeline(record_time, is_close_current=True)
                old_floor = self.current_floor
                self.current_floor = next_floor
                self.current_stage = ai_stage
                self.is_current_floor_poured = False
                self._record_timeline(record_time, new_stage=ai_stage)
                result_msg = f"[{record_time}] {self.zone_name} 进度正式跃升: {old_floor}层({old_stage}) -> {self.current_floor}层({ai_stage})"
            else:
                # 封顶处理
                self._record_timeline(record_time, is_close_current=True, new_stage=ai_stage)
                self.current_stage = ai_stage
                result_msg = f"[{record_time}] {self.zone_name} 已封顶，工序更新: {old_stage} -> {ai_stage}"
        else:
            # 同楼层正常前进
            self._record_timeline(record_time, is_close_current=True, new_stage=ai_stage)
            self.current_stage = ai_stage
            result_msg = f"[{record_time}] {self.zone_name} 工序更新: {old_stage} -> {ai_stage}"

        # 触发浇筑锁定检查
        if ai_stage == "混凝土阶段" and not self.is_current_floor_poured:
            self.is_current_floor_poured = True
            result_msg += f" | 触发{self.current_floor}层浇筑锁定"
            self._trigger_platform_sync(self.current_floor, description)

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
        # 这里的 print 会在终端打印出被拦截的信息，方便调试
        print(result["msg"])

    def manual_fix_zone(self, zone_name, target_floor, target_stage="模板阶段"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 解决 BUG: 判断该区域是否已存在，存在则更新，不存在(如重置后)则插入新记录
            cursor.execute("SELECT 1 FROM zone_states_v2 WHERE zone_name = ?", (zone_name,))
            if cursor.fetchone():
                cursor.execute("UPDATE zone_states_v2 SET floor = ?, stage = ?, is_poured = 0 WHERE zone_name = ?", 
                               (str(target_floor), target_stage, zone_name))
            else:
                cursor.execute("INSERT INTO zone_states_v2 (zone_name, floor, stage, is_poured) VALUES (?, ?, ?, 0)", 
                               (zone_name, str(target_floor), target_stage))
            conn.commit()
        
        # 核心修复：更新数据库后，立刻通知该区域的 AI 刷新内存缓存。
        # 如果大模型内存里还没有这个区域(刚重置完)，就立刻实例化它，保证后续抓拍不会乱！
        if zone_name in self.zones:
            self.zones[zone_name].refresh_from_db()    
        else:
            self.zones[zone_name] = ZoneProgressTracker(zone_name, db_path=self.db_path)
            
        print(f"!!! [手动修正] {zone_name} 已强制重置为 {target_floor} 层 {target_stage}")
        
        # ==== 核心修复：更新数据库后，立刻通知该区域的 AI 刷新内存缓存 ====
        if zone_name in self.zones:
            self.zones[zone_name].refresh_from_db()    

    # ==== 新增：毁灭级重置功能 ====
    def reset_project(self):
        """标准安全重置：通过 SQL 清空数据内容，避免 Windows 文件锁定冲突"""
        
        # 1. 安全清空所有表里的数据（不删物理文件，彻底告别 500 报错）
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.cursor()
            try:
                # 瞬间清空两张核心表里的所有数据
                cursor.execute("DELETE FROM zone_states_v2")
                cursor.execute("DELETE FROM stage_timeline")
                # 将时间轴的自增 ID 归零 (防报错处理)
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='stage_timeline'")
            except Exception as e:
                pass # 首次运行如果没有表，直接忽略即可
            conn.commit()
        
        # 2. 彻底清空大模型在内存里的状态机缓存
        self.zones.clear()
        
        print("!!! [系统提示] 数据库内容已通过 SQL 安全清空，内存已释放，全盘重置完成 !!!")