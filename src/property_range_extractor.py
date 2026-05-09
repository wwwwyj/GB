from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple


GENERAL_FIELDS = ["规范号", "规范版本", "规范年代", "规范名称", "材料类型", "应用场景"]
G2_FIELDS = [
    "规范原始材料牌号",
    "材料典型特性",
    "热处理方式",
    "制品形式",
    "规格分段数",
    "英制规格区间in",
    "公制规格区间mm",
    "成分相似牌号",
]

PRODUCT_WORDS = ("板材", "带材", "圆棒", "方棒", "六角棒", "棒材", "管材", "线材", "箔材", "型材")
COMMON_SPEC_WORDS = ("长度",)
MEASURE_WORDS = ("厚度", "直径", "宽度", "长度", "尺寸", "规格", "mm")


@dataclass
class ProductSpec:
    product: str
    metric_intervals: List[str]


class PropertyRangeExtractor:
    """Rule-first G-2 extractor for grade/state/product/spec range tables."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.fields = config.get("extraction", {}).get("fields", [])

    def extract(self, document: Dict[str, Any], general_row: Dict[str, Any]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for page in document.get("pages", []):
            for table in page.get("tables", []):
                if not self.is_target_table(table):
                    continue
                for keyed in table.get("keyed_rows") or []:
                    rows.extend(self.expand_keyed_row(keyed, general_row, page.get("page"), table))
        return rows

    def is_target_table(self, table: Dict[str, Any]) -> bool:
        headers = [clean_text(header) for header in table.get("headers") or []]
        joined = " ".join(headers)
        has_grade = "牌号" in joined
        has_state = "状态" in joined or "供应" in joined
        return bool(has_grade and has_state)

    def expand_keyed_row(
        self,
        keyed: Dict[str, Any],
        general_row: Dict[str, Any],
        page_number: Any,
        table: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        grade_value = first_value_by_header(keyed, ("牌号",))
        state_value = first_value_by_header(keyed, ("状态", "供应"))
        if not grade_value or not state_value:
            return []

        grades = split_grade_values(grade_value)
        states = split_state_values(state_value)
        product_specs = extract_product_specs(keyed)
        if not grades or not states or not product_specs:
            return []

        rows: List[Dict[str, str]] = []
        for grade, state, product_spec in itertools.product(grades, states, product_specs):
            metric = join_intervals(product_spec.metric_intervals)
            inch = convert_metric_intervals_to_inches(product_spec.metric_intervals)
            row = {field: "" for field in self.fields}
            for field in GENERAL_FIELDS:
                row[field] = clean_general_text(general_row.get(field, "无")) or "无"
            row["规范原始材料牌号"] = grade
            row["材料典型特性"] = "无"
            row["热处理方式"] = state
            row["制品形式"] = product_spec.product
            row["规格分段数"] = str(len(product_spec.metric_intervals))
            row["英制规格区间in"] = inch or "无"
            row["公制规格区间mm"] = metric or "无"
            row["成分相似牌号"] = "无"
            row["_来源页码"] = str(page_number or "")
            row["_来源表格"] = clean_text(table.get("caption", ""))
            rows.append({field: row.get(field, "") for field in self.fields})
        return rows


def first_value_by_header(row: Dict[str, Any], keywords: Iterable[str]) -> str:
    for header, value in row.items():
        if any(keyword in clean_text(header) for keyword in keywords):
            cleaned = clean_text(value)
            if cleaned:
                return cleaned
    return ""


def extract_product_specs(row: Dict[str, Any]) -> List[ProductSpec]:
    product_specs: List[ProductSpec] = []
    common_intervals: List[str] = []

    for header, value in row.items():
        header_text = clean_text(header)
        value_text = clean_text(value)
        if not is_measure_header(header_text):
            continue
        intervals = parse_metric_intervals(value_text)
        if not intervals:
            continue

        products = product_forms_from_header(header_text)
        if products:
            for product in products:
                product_specs.append(ProductSpec(product=product, metric_intervals=list(intervals)))
        elif any(word in header_text for word in COMMON_SPEC_WORDS):
            common_intervals.extend(intervals)

    if common_intervals and product_specs:
        for spec in product_specs:
            for interval in common_intervals:
                if interval not in spec.metric_intervals:
                    spec.metric_intervals.append(interval)

    return merge_duplicate_product_specs(product_specs)


def merge_duplicate_product_specs(specs: List[ProductSpec]) -> List[ProductSpec]:
    merged: Dict[str, List[str]] = {}
    for spec in specs:
        intervals = merged.setdefault(spec.product, [])
        for interval in spec.metric_intervals:
            if interval not in intervals:
                intervals.append(interval)
    return [ProductSpec(product=product, metric_intervals=intervals) for product, intervals in merged.items()]


def product_forms_from_header(header: str) -> List[str]:
    text = clean_text(header).replace(" ", "")
    products: List[str] = []
    if "方棒或六角棒" in text or "方棒/六角棒" in text:
        products.extend(["方棒", "六角棒"])
    for word in PRODUCT_WORDS:
        if word in text and word not in products:
            products.append(word)
    if "方棒" in products and "六角棒" in products and "棒材" in products:
        products = [product for product in products if product != "棒材"]
    return products


def is_measure_header(header: str) -> bool:
    text = clean_text(header)
    return any(word in text for word in MEASURE_WORDS)


def split_grade_values(value: str) -> List[str]:
    cleaned = clean_text(value)
    parts = [part.strip() for part in re.split(r"[、,，;；\s]+", cleaned) if part.strip()]
    return [part for part in parts if is_grade_like(part)]


def split_state_values(value: str) -> List[str]:
    cleaned = clean_text(value)
    parts = [part.strip() for part in re.split(r"[、,，;；\s]+", cleaned) if part.strip()]
    return [part for part in parts if is_state_like(part)]


def is_grade_like(value: str) -> bool:
    text = clean_text(value)
    return bool(re.fullmatch(r"(?:包铝)?(?:\d[A-Z]?\d{2,4}[A-Z]?|[1-8][A-Z]\d{2,4}[A-Z]?)", text, re.I))


def is_state_like(value: str) -> bool:
    text = clean_text(value)
    return bool(re.fullmatch(r"(?:O|F|W|M|H\d{1,4}[A-Z]?|T\d{1,5}[A-Z]?)", text, re.I))


def parse_metric_intervals(value: str) -> List[str]:
    text = normalize_range_text(value)
    if not text or is_empty_spec_value(text):
        return []
    segments = [segment.strip() for segment in re.split(r"[;；]", text) if segment.strip()]
    intervals: List[str] = []
    for segment in segments:
        interval = parse_one_metric_interval(segment)
        if interval:
            intervals.append(interval)
    return intervals


def parse_one_metric_interval(segment: str) -> str:
    text = normalize_range_text(segment)
    pattern = re.compile(
        r"^(?P<lop>>=|≤|<=|<|>|≥)?\s*(?P<left>\d+(?:\.\d+)?)\s*(?:~|～|—|–|-|至|到)\s*"
        r"(?P<uop><=|≤|<|>=|≥|>)?\s*(?P<right>\d+(?:\.\d+)?)$"
    )
    match = pattern.match(text)
    if not match:
        single = re.fullmatch(r"(?P<value>\d+(?:\.\d+)?)", text)
        if single:
            value = single.group("value")
            return f"[{value}, {value}]"
        return ""

    left = match.group("left")
    right = match.group("right")
    lower_bracket = "(" if match.group("lop") in {">", "<"} else "["
    upper_bracket = ")" if match.group("uop") in {"<", ">"} else "]"
    return f"{lower_bracket}{left}, {right}{upper_bracket}"


def normalize_range_text(value: str) -> str:
    text = clean_text(value)
    text = text.replace("＞", ">").replace("＜", "<").replace("≥", ">=").replace("≤", "<=")
    text = text.replace("~", "~").replace("～", "~").replace("－", "-").replace("—", "-").replace("–", "-")
    text = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_empty_spec_value(value: str) -> bool:
    text = clean_text(value)
    if text in {"", "—", "-", "一", "无"}:
        return True
    compact = re.sub(r"[.\s]", "", text)
    return bool(compact) and set(compact) == {"0"} and len(compact) >= 3


def join_intervals(intervals: List[str]) -> str:
    return "；".join(intervals)


def convert_metric_intervals_to_inches(intervals: List[str]) -> str:
    converted = [convert_one_interval_to_inches(interval) for interval in intervals]
    return "；".join(interval for interval in converted if interval)


def convert_one_interval_to_inches(interval: str) -> str:
    match = re.fullmatch(r"([\[\(])\s*([\d.]+)\s*,\s*([\d.]+)\s*([\]\)])", interval)
    if not match:
        return ""
    left_bracket, left, right, right_bracket = match.groups()
    return f"{left_bracket}{mm_to_in(left)}, {mm_to_in(right)}{right_bracket}"


def mm_to_in(value: str) -> str:
    number = Decimal(value) / Decimal("25.4")
    rounded = number.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return format(rounded.normalize(), "f")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def clean_general_text(value: Any) -> str:
    return str(value or "").replace("\xa0", " ").strip()
