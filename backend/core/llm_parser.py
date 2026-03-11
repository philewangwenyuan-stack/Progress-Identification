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
            # 1. 处理图片 (Base64)
            if not image_path.startswith(('http://', 'https://')):
                with open(image_path, "rb") as image_file:
                    base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                    final_image_url = f"data:image/jpeg;base64,{base64_image}"
            else:
                final_image_url = image_path

            # 2. 调用 API
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
            
            if forced_zone:
                parsed_data["位置"] = forced_zone
            
            # --- 删除原有的 self._save_to_local(parsed_data) ---
            return parsed_data
            
        except Exception as e:
            print(f"解析出错: {e}")
            return {}

    # 注意：请将原文件最底部的 _save_to_local 方法整段删掉

    #进度计划解析
    def parse_project_plan(self, raw_text: str) -> list:
        """专门用于解析上传的进度计划文本，输出标准化 JSON"""
        plan_prompt = """
        你是一个专业的建筑工程项目管理助手。
        我将为你提供一份施工进度计划表的文本（可能来自PDF或Excel提取）。
        请你从中提取出【楼层】、【计划开始时间】和【计划结束时间】，并严格按照以下 JSON 数组格式返回，不要包含任何额外的解释或Markdown标记：
        [
            {
                "floor": "这里填提取到的楼层数字或名称，例如 1 或 B1",  
                "stage": "这里填提取到的施工阶段，例如 模板阶段、钢筋阶段、混凝土阶段等",
                "planned_start": "YYYY-MM-DD格式", 
                "planned_end": "YYYY-MM-DD格式"
            }
        ]
        注意：
        1. JSON 的键名必须严格是 floor、planned_start、planned_end，绝对不能用中文键名！
        2. 如果某些文本不是具体的楼层任务计划，请忽略它们。
        3. 如果你比较混乱主要提取塔楼部分的计划，其他部分的可以不提取了，专注塔楼就好。
        4. 如果有多个同样的楼层，把他们合并成一个对象，计划开始时间取最早的，计划结束时间取最晚的。
        5. 如果遇到1-5层、6-10层这种格式的文本，尝试把它们拆开成多个对象，每个对象对应一个楼层。时间按照平均分配的方式处理。
        """

        try:
            # 截断过长的文本，防止大模型处理超长Excel时崩溃或乱码（保留前 20000 个字符）
            safe_text = raw_text[:20000] if len(raw_text) > 20000 else raw_text

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": plan_prompt},
                    {"role": "user", "content": f"提取以下文本：\n{safe_text}"},
                ],
                temperature=0.1, # 降低温度，让模型的输出更加老实和确定
            )
            
            result_str = response.choices[0].message.content
            
# 👇 --- 新增的强力调试代码：把大模型的原话打印到终端 --- 👇
            print("\n" + "="*50)
            print("🤖 大模型针对 Excel 解析的原始返回内容如下：")
            print(result_str)
            print("="*50 + "\n")
            # 👆 -------------------------------------------------------- 👆
            # 【核心修复】：暴力清洗大模型返回的“脏数据”
            # 1. 移除可能存在的 Markdown 代码块标记
            result_str = result_str.replace("```json", "").replace("```", "").strip()
            
            # 2. 利用正则或字符串查找，强行提取出最外层的方括号 [...] 包含的内容
            start_idx = result_str.find('[')
            end_idx = result_str.rfind(']')
            
            if start_idx != -1 and end_idx != -1:
                json_str = result_str[start_idx:end_idx+1]
            else:
                json_str = result_str
                
            # 解析 JSON
            parsed_data = json.loads(json_str)
            
            # 兼容处理嵌套
            if isinstance(parsed_data, dict):
                for key in parsed_data:
                    if isinstance(parsed_data[key], list):
                        parsed_data = parsed_data[key]
                        break
            
            # 【终极防弹装甲】：强制纠正大模型胡乱生成的中文键名
            if isinstance(parsed_data, list):
                cleaned_plan = []
                for item in parsed_data:
                    if isinstance(item, dict):
                        cleaned_item = {
                            # 如果没有 floor，就去抓取可能叫 '楼层' 或 '施工层' 的字段
                            "floor": str(item.get("floor", item.get("楼层", item.get("施工层", "")))),
                            # 新增下面这一行，把 stage 也提取出来
                            "stage": str(item.get("stage", item.get("工序", item.get("施工工序", "")))),
                            "planned_start": str(item.get("planned_start", item.get("计划开始时间", item.get("开始时间", "")))),
                            "planned_end": str(item.get("planned_end", item.get("计划结束时间", item.get("结束时间", ""))))
                        }
                        # 只要提取到了楼层，就认为是有效数据
                        if cleaned_item["floor"]:
                            cleaned_plan.append(cleaned_item)
                return cleaned_plan
                        
            return []
            
        except Exception as e:
            print(f"进度计划解析出错: {e}")
            try:
                print(f"【大模型返回的脏字符串内容为】:\n{result_str[:500]} ...")
            except: pass
            return []

    def _save_to_local(self, data: dict):
        """将解析结果追加到本地 JSONL 文件中"""
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        print(f"-> 结果已存入日志文件。")