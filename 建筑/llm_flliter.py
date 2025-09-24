import os
from openai import OpenAI
import requests
import time
import json
import ezdxf
import re
from pathlib import Path

OUTPUT_DIR = Path("outputs")

# === 安全起见，优先从环境变量读取密钥；若确实需要，可保留你原来的常量 ===
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "sk-zk2bde5aaadc89b97a2276e724c496cfc44685cf6e5c3b50")
BASE_URL = "https://api.zhizengzeng.com/v1/"

TEMPLATE_HINT = (
    "请严格使用如下模板输出（不要添加额外说明、不要换模板）：\n"
    "类别: 停车位\n"
    "候选图层: [用逗号分隔的原始图层名，例如 A-CRPK, PARKING, 车位-线]\n"
    "理由: <不超过80字>\n"
)


def read_dxf_layers(dxf_file_path):
    """读取DXF文件中的所有图层名"""
    try:
        doc = ezdxf.readfile(dxf_file_path)
        layers = [layer.dxf.name for layer in doc.layers]
        return layers
    except Exception as e:
        print(f"读取DXF文件时出错: {e}")
        return []


def chat_completions4(query):
    """与LLM模型对话（强制输出模板）"""
    client = OpenAI(api_key=API_SECRET_KEY, base_url=BASE_URL)
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个专业的CAD图纸分析助手。请根据提供的图层名称，分析哪些图层可能代表地下车库停车位相关图层，不需要分析填充/剖面线相关图层。"
                        "回答必须使用中文，并严格按照给定模板输出。"
                    )
                },
                {"role": "user", "content": TEMPLATE_HINT + query}
            ],
            temperature=0.2
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"API调用出错: {e}")
        return f"API调用失败: {e}"

# --- 正则解析部分：将 LLM 文本转成结构化数据 ---
RESULT_REGEX = re.compile(
    r"类别\s*[:：]\s*(?P<category>[^\n]+?)\s*"
    r"(?:\r?\n)+\s*候选图层\s*[:：]\s*\[(?P<candidates>[^\]]*)\]\s*"
    r"(?:\r?\n)+\s*理由\s*[:：]\s*(?P<reason>.+?)\s*$",
    re.S
)


def _split_candidates(raw: str):
    # 兼容中英文逗号、分号、空格
    parts = re.split(r"[，,;；\s]+", raw.strip())
    # 去空、去重，保持顺序
    seen = set()
    result = []
    for p in parts:
        name = p.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def extract_structured_result(llm_text: str, expected_category: str, all_layers):
    """
    用正则从 LLM 文本中提取：类别/候选图层/理由。
    若未按模板返回，则回退策略：在文本中按原始图层名做精确匹配。
    """
    parsed = {
        "category": expected_category,     # 回填期望类别，保证有值
        "candidates": [],
        "reason": ""
    }

    if not isinstance(llm_text, str):
        return parsed

    m = RESULT_REGEX.search(llm_text.strip())
    if m:
        cat = m.group("category").strip()
        cands_raw = m.group("candidates")
        reason = m.group("reason").strip()

        candidates = _split_candidates(cands_raw)
        # 仅保留出现在原始all_layers中的合法图层名
        candidates = [c for c in candidates if c in all_layers]

        parsed["category"] = cat or expected_category
        parsed["candidates"] = candidates
        parsed["reason"] = reason[:200]  # 限一个大致长度，避免过长
        return parsed

    # --- 回退策略：未对齐模板时，扫描所有图层名，使用更严格的边界匹配 ---
    found = []
    text = llm_text
    # 定义“词”字符集：数字/字母/下划线/常见中文范围，避免作为更长标识的一部分被命中
    word_class = r"0-9A-Za-z_\u4e00-\u9fff"
    for name in all_layers:
        try:
            if not name:
                continue
            # 跳过过短或纯数字的图层名，避免例如 "0" 被误命中
            if len(name) <= 1 or name.isdigit():
                continue
            pattern = re.compile(
                rf"(?<![{word_class}])" + re.escape(name) + rf"(?![{word_class}])",
                re.IGNORECASE
            )
            if pattern.search(text):
                found.append(name)
        except Exception:
            pass

    # 去重保持顺序
    seen = set()
    filtered = [x for x in found if not (x in seen or seen.add(x))]

    parsed["candidates"] = filtered
    parsed["reason"] = "未匹配到标准模板，已回退为基于边界的候选抽取。"
    return parsed


def analyze_parking_layers_with_llm(layers):
    """使用LLM仅分析停车位相关图层 + 解析为结构化结果"""
    layers_str = ", ".join(layers)

    parking_query = f"请分析以下CAD图纸的图层名称，判断哪些图层可能是停车位的图层：{layers_str}"
    parking_text = chat_completions4(parking_query)
    print("停车位图层分析完成")

    parking_parsed = extract_structured_result(parking_text, "停车位", layers)

    return {
        "raw": {
            "parking_layers": parking_text
        },
        "structured": {
            "parking": parking_parsed
        }
    }


def save_to_json(data, filename):
    """保存数据到JSON文件"""
    try:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到 {filename}")
    except Exception as e:
        print(f"保存JSON文件时出错: {e}")


def main(output_dir: Path = None):
    global OUTPUT_DIR
    if output_dir is None:
        output_dir = OUTPUT_DIR
    else:
        OUTPUT_DIR = output_dir

    # 读取DXF文件图层（可按需修改路径）
    dxf_file = r"C:\Users\30473\Desktop\研一\cad\展示\建筑\地下室建筑图\建筑平面图_人防平面.dxf"
    print(f"正在读取 {dxf_file} 的图层信息...")
    layers = read_dxf_layers(dxf_file)

    if not layers:
        print("未能读取到图层信息")
        return

    print(f"找到 {len(layers)} 个图层:")
    for layer in layers:
        print(f"  - {layer}")

    layers_str = ", ".join(layers)
    query = f"请分析以下CAD图纸的图层名称，判断哪些图层最可能是停车位相关图层：{layers_str}"

    # 使用LLM分析并解析
    print("\n开始使用LLM分析停车位相关图层...")
    llm_analysis_text = chat_completions4(query)
    print(llm_analysis_text)

    # 结构化输出
    structured = extract_structured_result(llm_analysis_text, "停车位", layers)
    final_results = {
        "schema_version": 1,
        "dxf_file": dxf_file,
        "total_layers": len(layers),
        "all_layers": layers,
        "raw": {"parking_layers": llm_analysis_text},
        "structured": {"parking": structured},
        "analysis_timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    # 保存到JSON文件
    output_file = output_dir / "llm_parking_layers_analysis.json"
    save_to_json(final_results, output_file)


if __name__ == "__main__":
    main()
