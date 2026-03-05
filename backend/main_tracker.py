import os
import json
import time
from datetime import datetime
from config import settings
from core.spatial_engine import ProjectProgressManager

def watch_and_parse(manager, file_path):
    print(f"[{datetime.now()}] 启动进度追踪引擎...")
    print(f"正在监控日志文件: {file_path}")
    print(f"本地数据库路径: {settings.DB_FILE_PATH}")
    print("-" * 50)
    
    # 文件不存在时初始化空文件
    if not os.path.exists(file_path):
        open(file_path, 'a').close()

    with open(file_path, 'r', encoding='utf-8') as f:
        # 移动到文件末尾，仅处理新增的增量日志
        f.seek(0, os.SEEK_END)
        
        while True:
            line = f.readline()
            if not line:
                time.sleep(2)  # 无新内容时休眠
                continue
            
            try:
                record = json.loads(line.strip())
                manager.parse_json_log(record)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"处理数据行时发生错误: {e}")

def main():
    # 实例化进度管理器
    manager = ProjectProgressManager(db_path=settings.DB_FILE_PATH)
    
    # 预留：如需数据回滚或修正，可在此处取消注释调用
    # manager.manual_fix_zone("云端工厂东北角", 5)

    try:
        watch_and_parse(manager, settings.LOG_FILE_PATH)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now()}] 监控进程已安全终止。数据已保存。")

if __name__ == "__main__":
    main()