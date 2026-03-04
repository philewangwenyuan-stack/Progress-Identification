import os

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- API 配置 ---
LLM_API_KEY = "0fdd0bfcc78113400f51761527886029"
LLM_BASE_URL = "https://jcpt-open.cscec.com/aijsxmywyapi/0510250001/v1.0/qwen_vl_max_public"
LLM_MODEL_NAME = "qwen-vl-max"

# --- 路径配置 ---
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
DB_DIR = os.path.join(DATA_DIR, "db")
TEST_MATERIALS_DIR = os.path.join(BASE_DIR, "test_materials")

# 具体文件路径
LOG_FILE_PATH = os.path.join(LOG_DIR, "recognition_history.jsonl")
DB_FILE_PATH = os.path.join(DB_DIR, "construction_progress.db")

# 初始化时自动创建必要的目录
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(TEST_MATERIALS_DIR, exist_ok=True)