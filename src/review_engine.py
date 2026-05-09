from __future__ import annotations  # 支持新式类型注解

from typing import Any, Dict, List  # 类型注解

from .dual_model_extensions import compare_rows  # 字段逐格相似度比对函数


## 复核引擎：负责从模型输出生成比对明细、人工校验表和最终结果初稿
class ReviewEngine:
    ## 初始化：读取字段清单和人工复核阈值
    def __init__(self, config: Dict[str, Any]):
        self.config = config  # 保存全局配置
        self.fields = config.get("extraction", {}).get("fields", [])  # 模板字段清单
        self.threshold = float(config.get("rag", {}).get("similarity_threshold", 0.8))  # 低相似度阈值

    ## 对模型 A/B 结果做逐字段比对
    def compare(self, model_outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        model_a_rows = model_outputs.get("model_a", {}).get("rows", [])  # 读取模型 A 行数据
        model_b_rows = model_outputs.get("model_b", {}).get("rows", [])  # 读取模型 B 行数据
        return compare_rows(model_a_rows, model_b_rows, self.fields, self.threshold)  # 返回逐字段相似度明细

    ## 构建人工校验表：只保留低于阈值的字段，并追加 RAG 召回内容
    def build_manual_review(self, comparison: List[Dict[str, Any]], chunks: List[Any], rag: Any) -> List[Dict[str, Any]]:
        model_a_label = self.config.get("models", {}).get("model_a", {}).get("label", "模型A")  # Excel 中模型 A 列名
        model_b_label = self.config.get("models", {}).get("model_b", {}).get("label", "模型B")  # Excel 中模型 B 列名
        rows: List[Dict[str, Any]] = []  # 人工校验行集合

        ## 遍历比对明细：只有“需要人工复核”的记录才进入人工校验 sheet
        for item in comparison:
            if not item.get("需要人工复核"):  # 相似度达到阈值
                continue  # 跳过，不进入人工校验表
            query = f"{item['字段']} {item.get('model_a_value', '')} {item.get('model_b_value', '')}"  # 用字段名和两侧答案构造 RAG 查询
            contexts = rag.retrieve(query, chunks)  # 召回相关原文片段
            rows.append(
                {
                    "区域": item["区域"],  # 比对区域
                    "位置": item["位置"],  # 行位置
                    "字段": item["字段"],  # 字段名
                    model_a_label: item.get("model_a_value", ""),  # 模型 A 结果
                    model_b_label: item.get("model_b_value", ""),  # 模型 B 结果
                    "比对结论": item.get("比对结论", ""),  # 比对结论
                    "相似度": f"{float(item.get('相似度', 0)):.3f}",  # 三位小数字符串，贴合模板
                    "说明": item.get("说明", ""),  # 相似度说明
                    "RAG召回内容": "\n\n".join(format_context(context) for context in contexts),  # 多个召回片段拼接
                    "最终建议值": suggest_value(item.get("model_a_value", ""), item.get("model_b_value", "")),  # 规则型初始建议
                    "人工复核结果": "",  # 留给人工填写
                    "复核备注": "",  # 留给人工填写
                }
            )
        return rows  # 返回人工校验行

    ## 构建最终结果初稿：当前优先采用模型 A，若只有模型 B 则采用模型 B
    def build_final_rows(
        self,
        model_outputs: Dict[str, Any],  # 模型输出集合
        comparison: List[Dict[str, Any]],  # 比对明细，预留后续智能合并使用
        review_rows: List[Dict[str, Any]],  # 人工校验行，预留后续回填使用
    ) -> List[Dict[str, Any]]:
        model_a_rows = model_outputs.get("model_a", {}).get("rows", [])  # 模型 A 行
        model_b_rows = model_outputs.get("model_b", {}).get("rows", [])  # 模型 B 行
        base_rows = model_a_rows or model_b_rows or [{field: "" for field in self.fields}]  # 选择最终结果初稿来源
        final_rows: List[Dict[str, Any]] = []  # 最终结果行集合

        for row in base_rows:  # 遍历基础行
            final_rows.append({field: row.get(field, "") for field in self.fields})  # 严格按字段清单输出
        return final_rows  # 返回最终结果初稿


## 给低相似度字段生成初始建议值：优先保留非空值；两边都有值时选更长的一个
def suggest_value(left: str, right: str) -> str:
    if left and not right:  # 只有模型 A 有值
        return left  # 建议模型 A
    if right and not left:  # 只有模型 B 有值
        return right  # 建议模型 B
    return left if len(str(left)) >= len(str(right)) else right  # 两边都有时选信息量更长的一侧


## 格式化 RAG 召回片段：把 chunk_id、页码、分数和正文合并成 Excel 单元格文本
def format_context(context: Dict[str, Any]) -> str:
    return f"[{context.get('chunk_id')} page={context.get('page')} score={context.get('score')}]\n{context.get('content', '')}"  # 人工可读召回格式
