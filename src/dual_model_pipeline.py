from __future__ import annotations  # 支持更灵活的类型注解

import json  # 构造 JSON schema 示例和表格上下文
from pathlib import Path  # 读取 prompts 文件路径
from typing import Any, Dict, List  # 类型注解

import yaml  # 读取 prompts/prompts.yaml

from .utils import extract_json_object, normalize_result_rows  # 模型响应 JSON 解析和字段行归一化


def prompt_lines(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


## 双模型抽取流水线：负责确定运行模型、构造提示词、调用模型、归一化输出
class DualModelPipeline:
    ## 初始化流水线：缓存字段清单、sheet 名和提示词配置
    def __init__(self, config: Dict[str, Any]):
        self.config = config  # 保存全局配置
        self.fields = config.get("extraction", {}).get("fields", [])  # 从配置读取提取字段清单
        self.sheet_name = config.get("extraction", {}).get("sheet_name", "规范解读")  # 输出 sheet 名
        self.prompt_config = self._load_prompt_config()  # 读取 prompts.yaml，占位给后续字段分组提示词使用

    ## 对文档执行抽取：根据 single/dual 模式调用一个或两个模型
    def extract(self, document: Dict[str, Any], chunks: List[Any]) -> Dict[str, Any]:
        model_keys = self._active_model_keys()  # 计算本次运行需要调用哪些模型
        outputs: Dict[str, Any] = {}  # 保存每个模型的输出
        for model_key in model_keys:  # 逐个模型执行抽取
            outputs[model_key] = self._extract_with_model(model_key, document, chunks)  # 保存模型响应和归一化行
        return outputs  # 返回 {"model_a": ..., "model_b": ...}

    ## 根据 runtime.mode 决定运行模型：dual 跑 A/B，single 只跑指定模型
    def _active_model_keys(self) -> List[str]:
        runtime = self.config.get("runtime", {})  # 读取运行时配置
        if runtime.get("mode", "dual") == "single":  # 单模型模式
            return [runtime.get("single_model", "model_a")]  # 返回配置指定的单模型
        return ["model_a", "model_b"]  # 默认双模型模式

    ## 单个模型抽取：支持跳过模型调用，便于验证解析和 Excel 输出
    def _extract_with_model(self, model_key: str, document: Dict[str, Any], chunks: List[Any]) -> Dict[str, Any]:
        label = self.config.get("models", {}).get(model_key, {}).get("label", model_key)  # 读取模型显示标签
        if self.config.get("runtime", {}).get("skip_model_calls", False):  # 如果开启空跑模式
            rows = [{field: "" for field in self.fields}]  # 生成一行空模板，字段与提取结果 Excel 对齐
            return {"label": label, "raw_response": "", "rows": rows, "error": "skip_model_calls=true"}  # 返回可写 Excel 的占位输出

        from .llm_client import LLMClient  # 延迟导入模型客户端；skip-model-calls 时不要求安装 openai

        prompt, system_prompt = self._build_prompt(document, chunks)  # 构造用户提示词和系统提示词
        client = LLMClient.from_config(model_key, self.config)  # 根据模型配置创建 API 客户端
        raw_response = client.extract(prompt=prompt, system_prompt=system_prompt)  # 调用模型并得到原始文本响应

        try:
            parsed = extract_json_object(raw_response)  # 从模型响应中解析 JSON
            rows = normalize_result_rows(parsed, self.fields, self.sheet_name)  # 将 JSON 对齐为模板字段行
            return {"label": label, "raw_response": raw_response, "rows": rows, "error": ""}  # 返回成功结果
        except Exception as exc:
            return {"label": label, "raw_response": raw_response, "rows": [], "error": str(exc)}  # JSON 解析失败时保留原始响应和错误

    ## 读取 prompts 配置：当前主要是占位，后续你补字段分组提示词后可扩展使用
    def _load_prompt_config(self) -> Dict[str, Any]:
        prompts_file = Path(self.config.get("extraction", {}).get("prompts_file", "prompts/prompts.yaml"))  # prompts 文件路径
        if not prompts_file.exists():  # 如果 prompts 文件不存在
            return {}  # 返回空配置，继续使用默认提示词
        with prompts_file.open("r", encoding="utf-8") as file:  # 读取 YAML 文件
            return yaml.safe_load(file) or {}  # YAML 为空时返回空字典

    ## 构造模型提示词：字段清单来自 Excel 模板，文档内容来自解析器
    def _build_prompt(self, document: Dict[str, Any], chunks: List[Any]) -> tuple[str, str]:
        g1_group = (self.prompt_config.get("prompt_groups") or {}).get("G-1-General", {})
        general_fields = g1_group.get("fields") or ["规范号", "规范版本", "规范年代", "规范名称", "材料类型", "应用场景"]
        field_lines = "\n".join(f"- {field}" for field in general_fields)  # G-1 只抽取通用字段
        context = self._document_context(document)  # 将页文本和表格合并为模型上下文
        schema_example = json.dumps(
            {self.sheet_name: [{field: None for field in general_fields}]},  # 构造目标 JSON 示例
            ensure_ascii=False,  # 保留中文字段名
            indent=2,  # 让示例更容易阅读
        )
        group_instructions = "\n".join(
            [
                *prompt_lines(g1_group.get("system_prompt")),
                "",
                *prompt_lines(g1_group.get("user_prompt")),
            ]
        ).replace("{content}", "见下方【文档内容】")

        prompt = f"""请从标准文件内容中抽取 G-1-General 通用信息字段。

字段清单：
{field_lines}

提示词规则：
{group_instructions}

输出要求：
1. 只输出 JSON，不要输出 Markdown 代码块。
2. JSON 顶层键使用 "{self.sheet_name}"。
3. "{self.sheet_name}" 的值为对象数组，且只输出 1 个对象。
4. 只填写字段清单中的 G-1 字段，不要抽取牌号、状态、制品形式、规格范围等 G-2 字段。
5. 文档中没有的信息填“无”，不要编造。

JSON 示例：
{schema_example}

【文档内容】
{context}
"""  # 默认通用抽取提示词；后续可根据 prompts.yaml 做字段分组提示词
        system_prompt = "你是严谨的材料标准信息抽取专家，必须基于给定文档内容抽取字段。"  # 系统角色提示
        return prompt, system_prompt  # 返回用户提示词和系统提示词

    ## 汇总文档上下文：把页文本和表格串起来，并限制最大长度避免超上下文
    def _document_context(self, document: Dict[str, Any]) -> str:
        parts: List[str] = []  # 保存上下文片段
        for page in document.get("pages", []):  # 遍历解析后的每一页
            text = page.get("text", "")  # 当前页文本
            if text:  # 如果当前页有文本
                parts.append(f"[page {page.get('page')}]\n{text}")  # 加入页码标记，便于模型引用来源
            for table in page.get("tables", []):  # 遍历当前页表格
                rows = table.get("rows", [])  # 表格二维行列
                if rows:  # 如果表格非空
                    parts.append(f"[page {page.get('page')} table {table.get('table_index')}]\n{json.dumps(rows, ensure_ascii=False)}")  # 表格序列化为 JSON 文本
        return "\n\n".join(parts)[:120000]  # 合并上下文，并做长度上限保护
