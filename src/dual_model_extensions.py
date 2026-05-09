from __future__ import annotations  # 允许使用较新的类型注解语法

from typing import Any, Dict, List  # 类型注解

from .utils import normalize_text, text_similarity  # 文本归一化和相似度工具


## 比较模型 A/B 的行数据：按行、按字段逐格生成相似度明细
def compare_rows(
    model_a_rows: List[Dict[str, Any]],  # 模型 A 归一化后的行列表
    model_b_rows: List[Dict[str, Any]],  # 模型 B 归一化后的行列表
    fields: List[str],  # 模板字段清单
    threshold: float,  # 人工复核相似度阈值，默认 0.8
) -> List[Dict[str, Any]]:
    max_rows = max(len(model_a_rows), len(model_b_rows), 1)  # 以更长的模型结果为准，至少比较一行
    comparison: List[Dict[str, Any]] = []  # 保存逐字段比对结果

    ## 外层按行比较：如果两个模型输出行数不同，缺失的一侧按空值处理
    for row_index in range(max_rows):
        left = model_a_rows[row_index] if row_index < len(model_a_rows) else {}  # 当前模型 A 行
        right = model_b_rows[row_index] if row_index < len(model_b_rows) else {}  # 当前模型 B 行
        position = _position_label(row_index, left, right)  # 生成人工可读位置，如“行1 / 固溶”

        ## 内层按字段比较：每个字段生成一条记录，方便 Excel 逐项筛选
        for field in fields:
            left_value = normalize_text(left.get(field), field)  # 清洗模型 A 字段值
            right_value = normalize_text(right.get(field), field)  # 清洗模型 B 字段值
            similarity = text_similarity(left_value, right_value, field)  # 计算字段相似度
            comparison.append(
                {
                    "区域": "规范解读比对",  # 表示该记录来自规范解读结果的逐格比对
                    "位置": position,  # 行位置，用于人工复核定位
                    "字段": field,  # 字段名
                    "model_a_value": left_value,  # 模型 A 值，后续写 Excel 时替换成模型标签列名
                    "model_b_value": right_value,  # 模型 B 值，后续写 Excel 时替换成模型标签列名
                    "比对结论": conclusion(similarity, left_value, right_value, threshold),  # 中文结论
                    "相似度": round(similarity, 3),  # 相似度保留三位小数
                    "说明": explanation(similarity, threshold),  # 阈值说明
                    "需要人工复核": similarity < threshold,  # 低于阈值则进入人工校验
                }
            )
    return comparison  # 返回完整比对明细


## 根据相似度和空值情况生成中文结论
def conclusion(similarity: float, left_value: str, right_value: str, threshold: float) -> str:
    if similarity >= 1:  # 完全一致
        return "完全一致"  # 返回模板中的一致结论
    if similarity >= threshold:  # 达到阈值但不是完全一致
        return "基本一致"  # 认为无需人工复核
    if not left_value or not right_value:  # 一边为空一边有值
        return "明显差异"  # 空值差异通常需要复核
    return "明显差异"  # 低于阈值统一标明显差异


## 生成比对说明：主要说明是否低于人工复核阈值
def explanation(similarity: float, threshold: float) -> str:
    if similarity >= 1:  # 完全一致
        return "归一化后完全相同"  # 说明无差异
    if similarity >= threshold:  # 达到阈值
        return f"相似度不低于阈值 {threshold:.2f}"  # 说明无需复核
    return f"相似度低于阈值 {threshold:.2f}"  # 说明需要人工复核


## 生成行位置标签：优先使用“热处理方式”让人工校验更容易定位
def _position_label(row_index: int, left: Dict[str, Any], right: Dict[str, Any]) -> str:
    row_name = normalize_text(left.get("热处理方式") or right.get("热处理方式"))  # 尝试读取当前行热处理方式
    if row_name:  # 如果有可读行名
        return f"行{row_index + 1} / {row_name}"  # 输出行号和行名
    return f"行{row_index + 1}"  # 没有行名时只输出行号
