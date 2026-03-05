import os
import json
from config import settings
from core.llm_parser import ConstructionLLMParser

def main():
    # 实例化解析器
    parser = ConstructionLLMParser(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model_name=settings.LLM_MODEL_NAME,
        log_file_path=settings.LOG_FILE_PATH
    )

    # 模拟测试：选择一张测试图片
    test_image = os.path.join(settings.TEST_MATERIALS_DIR, "混凝土浇筑阶段.jpg")
    
    if not os.path.exists(test_image):
        print(f"未找到测试图片: {test_image}，请检查路径。")
        return

    print("=== 开始执行视觉解析任务 ===")
    result = parser.parse_instruction_with_image("请识别当前施工工序", test_image)
    
    if result:
        print("\n解析结果摘要:")
        print(json.dumps(result, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    main()