from __future__ import annotations  # 延迟解析类型注解，减少运行时类型引用限制

import json  # 读写中间 JSON 文件
import os  # 读取和写入环境变量
import re  # 文本清洗、数字识别、JSON 块提取
from difflib import SequenceMatcher  # 轻量文本相似度计算
from pathlib import Path  # 统一路径处理
from typing import Any, Dict, Iterable, List  # 类型注解，提高代码可读性

import yaml  # 读取 config.yaml

## python-dotenv 是便利依赖，但不能让它成为硬依赖，所以这里做可选导入
try:
    from dotenv import load_dotenv  # 优先使用成熟库加载 .env
except Exception:
    load_dotenv = None  # 如果环境没有安装 python-dotenv，就使用下方内置加载逻辑


## 加载配置文件：同时加载 .env，保证 API key 可以从环境变量或 .env 中读取
def load_config(path: Path) -> Dict[str, Any]:
    load_env_file()  # 先加载 .env，后续 LLMClient 可以直接读 os.environ
    with path.open("r", encoding="utf-8") as file:  # 使用 UTF-8 读取中文配置
        return yaml.safe_load(file) or {}  # YAML 为空时返回空字典，避免 None 传播


## 加载 .env：如果安装了 python-dotenv 就调用库，否则使用简易 parser
def load_env_file(path: str | Path = ".env") -> None:
    if load_dotenv is not None:  # 如果可选依赖存在
        load_dotenv(path)  # 由 python-dotenv 处理注释、引号等细节
        return  # 使用库加载后直接返回

    env_path = Path(path)  # 规范化 .env 路径
    if not env_path.exists():  # 如果项目里没有 .env
        return  # 不报错，因为用户可能通过系统环境变量提供 key
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():  # 逐行读取 .env
        line = raw_line.strip()  # 去掉行首行尾空白
        if not line or line.startswith("#") or "=" not in line:  # 跳过空行、注释行和非法行
            continue  # 继续下一行
        key, value = line.split("=", 1)  # 只按第一个等号切分，允许 value 中包含等号
        key = key.strip()  # 清理变量名
        value = value.strip().strip('"').strip("'")  # 清理变量值和简单引号
        if key and key not in os.environ:  # 不覆盖系统里已经存在的环境变量
            os.environ[key] = value  # 写入当前进程环境变量


## 确保目录存在：所有输出目录创建都统一走这里
def ensure_dir(path: str | Path) -> Path:
    target = Path(path)  # 将字符串或 Path 统一为 Path
    target.mkdir(parents=True, exist_ok=True)  # 递归创建目录，已存在时不报错
    return target  # 返回 Path，方便调用者继续拼接子路径


## 保存 JSON：用于 raw_document、chunks、model_outputs 等中间结果
def save_json(path: str | Path, data: Any) -> None:
    target = Path(path)  # 统一输出路径类型
    target.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在
    with target.open("w", encoding="utf-8") as file:  # 使用 UTF-8 保留中文
        json.dump(data, file, ensure_ascii=False, indent=2)  # ensure_ascii=False 让中文可直接阅读


## 根据配置收集输入文件：支持单文件和批量目录，并去重
def collect_input_files(config: Dict[str, Any]) -> List[Path]:
    input_config = config.get("input", {})  # 读取 input 配置块
    supported = {item.lower() for item in input_config.get("supported_extensions", [])}  # 支持的扩展名集合
    files: List[Path] = []  # 暂存找到的文件

    file_path = str(input_config.get("file_path") or "").strip()  # 单文件路径
    if file_path:  # 如果配置了单文件
        candidate = Path(file_path)  # 转成 Path
        if candidate.exists() and candidate.is_file():  # 路径存在且是文件
            files.append(candidate)  # 加入待处理列表

    input_dir = str(input_config.get("input_dir") or "").strip()  # 批量目录路径
    if input_dir:  # 如果配置了批量目录
        base = Path(input_dir)  # 转成 Path
        pattern = "**/*" if input_config.get("recursive", True) else "*"  # 根据 recursive 决定是否递归
        for candidate in base.glob(pattern):  # 遍历目录下候选路径
            if candidate.is_file() and (not supported or candidate.suffix.lower() in supported):  # 过滤文件类型
                files.append(candidate)  # 加入待处理列表

    deduped: List[Path] = []  # 保存去重后的文件
    seen = set()  # 用 resolved 绝对路径去重
    for item in files:  # 遍历原始文件列表
        resolved = item.resolve()  # 解析成绝对路径
        if resolved not in seen:  # 如果还没有出现过
            seen.add(resolved)  # 记录该路径
            deduped.append(item)  # 保留原始 Path，避免显示路径过长
    return deduped  # 返回最终输入文件列表


## 文本归一化：相似度比较和输出清洗都用这一个入口
def normalize_text(value: Any, field_name: str = "") -> str:
    if value is None:  # None 表示字段缺失
        return ""  # 统一成空字符串，便于比较和写 Excel

    text = str(value).strip()  # 转字符串并去除两端空白
    text = re.sub(r"\s+", " ", text)  # 将多空白压成单个空格，减少格式差异影响

    if "状态" in field_name and text.lower() in {"0", "o"}:  # OCR 常把状态 O 识别成 0 或 o
        return "O"  # 统一修正为大写字母 O

    if text in {"一", "-", "--", "—", "－"}:  # 常见空值占位符或 OCR 横线
        return ""  # 统一视为空值

    return text  # 返回清洗后的文本


## 将模型返回结果归一化为表格行：兼容 dict/list/带 sheet 名的多种 JSON 结构
def normalize_result_rows(raw_result: Any, fields: Iterable[str], sheet_name: str = "规范解读") -> List[Dict[str, str]]:
    if raw_result is None:  # 模型没有返回有效 JSON
        return []  # 返回空行列表

    if isinstance(raw_result, dict):  # 如果顶层是对象
        if sheet_name in raw_result:  # 优先读取指定 sheet 名，如“规范解读”
            raw_result = raw_result[sheet_name]  # 取出实际行数据
        elif "rows" in raw_result:  # 兼容 {"rows": [...]} 结构
            raw_result = raw_result["rows"]  # 取出 rows
        elif "data" in raw_result:  # 兼容 {"data": [...]} 结构
            raw_result = raw_result["data"]  # 取出 data
        else:
            raw_result = [raw_result]  # 单对象视为单行

    if isinstance(raw_result, list):  # 如果已经是列表
        rows = raw_result  # 直接作为行集合
    else:
        rows = [raw_result]  # 其他类型包装成单行，后续会变成空字段

    normalized_rows: List[Dict[str, str]] = []  # 存放字段对齐后的行
    for raw_row in rows:  # 遍历模型返回的每一行
        if isinstance(raw_row, dict):  # 只有 dict 才能按字段取值
            normalized_rows.append({field: normalize_text(raw_row.get(field), field) for field in fields})  # 严格按字段清单输出
        else:
            normalized_rows.append({field: "" for field in fields})  # 非 dict 行无法解析，输出空字段

    return normalized_rows  # 返回与模板字段一致的行列表


## 计算字段相似度：空值、完全一致、数字、普通文本分别处理
def text_similarity(left: Any, right: Any, field_name: str = "") -> float:
    left_text = normalize_text(left, field_name)  # 清洗左侧模型值
    right_text = normalize_text(right, field_name)  # 清洗右侧模型值

    if not left_text and not right_text:  # 两边都为空
        return 1.0  # 认为一致
    if not left_text or not right_text:  # 一边为空一边非空
        return 0.0  # 明显差异
    if left_text == right_text:  # 清洗后完全一致
        return 1.0  # 相似度满分

    left_num = parse_number(left_text)  # 尝试解析左侧数字
    right_num = parse_number(right_text)  # 尝试解析右侧数字
    if left_num is not None and right_num is not None:  # 两边都能解析成数字
        scale = max(abs(left_num), abs(right_num), 1.0)  # 用较大绝对值归一化差异，避免除零
        return max(0.0, 1.0 - abs(left_num - right_num) / scale)  # 数值越接近相似度越高

    return SequenceMatcher(None, left_text.lower(), right_text.lower()).ratio()  # 普通文本使用序列相似度


## 尝试把字符串解析为纯数字：只有完整数字字符串才返回 float
def parse_number(value: str) -> float | None:
    compact = value.replace(",", "")  # 去掉千分位逗号
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", compact):  # 匹配整数或小数
        return float(compact)  # 返回浮点数用于数值比较
    return None  # 不是纯数字则返回 None


## 从模型文本响应中提取 JSON：兼容纯 JSON、Markdown 代码块、前后夹杂说明文字
def extract_json_object(text: str) -> Any:
    cleaned = text.strip()  # 去掉首尾空白
    if cleaned.startswith("```"):  # 如果模型输出了 Markdown 代码块
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()  # 去掉开头 ```json
        cleaned = re.sub(r"```$", "", cleaned).strip()  # 去掉末尾 ```

    try:
        return json.loads(cleaned)  # 优先按完整 JSON 解析
    except json.JSONDecodeError:
        pass  # 如果不是纯 JSON，则尝试截取 JSON 片段

    first_object = cleaned.find("{")  # 找第一个对象起点
    first_array = cleaned.find("[")  # 找第一个数组起点
    candidates = [idx for idx in (first_object, first_array) if idx >= 0]  # 保留有效起点
    if not candidates:  # 如果找不到 JSON 起点
        raise ValueError("No JSON object or array found in model response.")  # 明确提示模型响应不可解析

    start = min(candidates)  # 使用最靠前的 JSON 起点
    end = cleaned.rfind("}") if cleaned[start] == "{" else cleaned.rfind("]")  # 根据起点类型找对应终点
    if end <= start:  # 如果终点不存在或位置异常
        raise ValueError("Incomplete JSON in model response.")  # 提示 JSON 不完整
    return json.loads(cleaned[start : end + 1])  # 解析截取出的 JSON 片段


## 读取环境变量：用于 LLMClient 解析 API key 等敏感配置
def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)  # 从当前进程环境变量读取
    return value if value not in (None, "") else default  # 空值时返回默认值
