import json
import base64
import os
from datetime import datetime
from openai import OpenAI

class ConstructionLLMParser:
    def __init__(self, api_key: str, base_url: str, model_name: str, log_file_path: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name
        self.log_file_path = log_file_path
            
        self.system_prompt = """
        你是一个建筑施工现场的语义解析引擎。请结合文字描述和照片，解析为严格的JSON格式。
        输出必须包含字段：位置、人数、当前作业工序、视觉确认描述。
        [特殊判定规则]：
        1. 只要有少量混凝土浇筑迹象，即判定为“混凝土阶段”。
        2. 模板或支模架未搭设完成，统一判定为“模板阶段”。
        3. 如果有钢筋绑扎作业，判定为“钢筋阶段”。
        只有这三种状态，不要写其他的，请直接返回 JSON 字符串，不要包含解释。
        """

    # 增加 forced_zone 参数
    def parse_instruction_with_image(self, text_instruction: str, image_path: str, forced_zone: str = None) -> dict:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在处理并解析图片: {os.path.basename(image_path)}")
        
        try:
            # 1. 处理图片 (Base64) - 保持不变
            if not image_path.startswith(('http://', 'https://')):
                with open(image_path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                    final_image_url = f"data:image/jpeg;base64,{base64_image}"
            else:
                final_image_url = image_path

            # 2. 调用 API - 保持不变
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": text_instruction if text_instruction else "请解析图片内容。"},
                            {"type": "image_url", "image_url": {"url": final_image_url}},
                        ],
                    },
                ],
                response_format={"type": "json_object"}
            )
            
            # 3. 解析结果并注入系统时间与来源
            parsed_data = json.loads(response.choices[0].message.content)
            parsed_data["识别时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            parsed_data["原始图片路径"] = image_path
            
            # 【修复核心 1】：在落盘保存日志前，强行修正为真实的施工区域！
            if forced_zone:
                parsed_data["位置"] = forced_zone
            
            # 4. 存储数据
            self._save_to_local(parsed_data)
            
            return parsed_data
            
        except Exception as e:
            print(f"解析出错: {e}")
            return {}

    #进度计划解析
    def parse_project_plan(self, raw_text: str) -> list:
        """专门用于解析上传的进度计划文本，输出标准化 JSON"""
        plan_prompt = """
        你是一个专业的建筑工程项目管理助手。
        我将为你提供一份施工进度计划表的文本（可能来自PDF或Excel提取）。
        请你从中提取出所有的施工任务节点，并严格按照以下 JSON 数组格式返回，不要包含任何额外的解释或Markdown标记：
        [
            {
                "zone_name": "区域", 
                "floor": "楼层",  
                "planned_start": "2024-05-01 00:00:00", 
                "planned_end": "2024-05-05 23:59:59"
            }
        ]
        注意：
        1. 时间格式请尽量统一为 YYYY-MM-DD HH:MM:SS。
        2. 如果某些文本不是具体的任务计划，请忽略它们。
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": plan_prompt},
                    {"role": "user", "content": f"以下是进度计划提取的文本：\n{raw_text}"},
                ],
                response_format={"type": "json_object"} # 如果模型支持强制JSON输出
            )
            
            result_str = response.choices[0].message.content
            # 兼容处理大模型可能返回的嵌套结构
            parsed_data = json.loads(result_str)
            
            # 如果大模型返回的是 {"data": [...]} 这种格式，做一层解包
            if isinstance(parsed_data, dict):
                for key in parsed_data:
                    if isinstance(parsed_data[key], list):
                        return parsed_data[key]
                        
            return parsed_data if isinstance(parsed_data, list) else []
            
        except Exception as e:
            print(f"进度计划解析出错: {e}")
            return []


    def _save_to_local(self, data: dict):
        """将解析结果追加到本地 JSONL 文件中"""
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        print(f"-> 结果已存入日志文件。")