from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup


TABLE_CAPTION_RE = re.compile(r"^\s*表\s*([A-Za-z]?\d+(?:\.\d+)*)\s*(.*)$")
CONTINUED_RE = re.compile(r"[（(]\s*续\s*[）)]")
HEADER_KEYWORDS = (
    "牌号",
    "状态",
    "供应",
    "尺寸",
    "规格",
    "厚度",
    "直径",
    "长度",
    "宽度",
    "项目",
    "要求",
    "试验",
    "方法",
    "取样",
    "单位",
    "类别",
    "级",
    "缺陷",
    "性能",
    "强度",
    "伸长率",
    "硬度",
    "温度",
    "时间",
    "材料",
    "包覆率",
    "电导率",
)


def extract_structure_tables(raw_result: Any, ocr_pages: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Normalize PP-StructureV3 results into per-page table dictionaries."""
    tables_by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    if raw_result is None:
        return {}

    ocr_by_page = {int(page.get("page", 1)): page for page in ocr_pages}
    for fallback_index, page_result in enumerate(raw_result or [], start=1):
        page_data = structure_result_to_dict(page_result)
        res = page_data.get("res", page_data) if isinstance(page_data, dict) else {}
        page_number = page_number_from_result(res, fallback_index)
        ocr_items = ocr_items_for_page(res, ocr_by_page.get(page_number, {}))
        table_results = find_table_results(res)
        table_blocks = find_table_blocks(res)

        for table_index, table_res in enumerate(table_results, start=1):
            html = extract_html(table_res)
            block = table_blocks[table_index - 1] if table_index - 1 < len(table_blocks) else {}
            bbox = first_bbox(table_res) or first_bbox(block)
            rows = html_table_to_rows(html)
            if not rows:
                rows = markdown_table_to_rows(str(block.get("block_content", "") or ""))

            caption_raw = detect_caption(ocr_items, table_blocks, table_index, bbox)
            caption = normalize_caption(caption_raw) or f"表格{page_number}-{table_index}"
            keyed_rows, row_texts, headers = rows_to_keyed_rows(rows)
            tables_by_page[page_number].append(
                {
                    "table_index": table_index,
                    "page": page_number,
                    "caption": caption,
                    "caption_raw": caption_raw,
                    "continued": bool(caption_raw and CONTINUED_RE.search(caption_raw)),
                    "bbox": to_jsonable(bbox),
                    "html": html,
                    "headers": headers,
                    "rows": rows,
                    "keyed_rows": keyed_rows,
                    "row_texts": row_texts,
                    "source": "pp_structure_v3",
                    "structure_score": to_jsonable(table_res.get("structure_score")),
                }
            )

    return dict(tables_by_page)


def structure_result_to_dict(page_result: Any) -> Dict[str, Any]:
    if isinstance(page_result, dict):
        return page_result

    json_value = getattr(page_result, "json", None)
    if isinstance(json_value, dict):
        return json_value
    if callable(json_value):
        value = json_value()
        if isinstance(value, dict):
            return value

    data = getattr(page_result, "data", None)
    if isinstance(data, dict):
        return data

    result: Dict[str, Any] = {}
    for name in ("res", "markdown", "img"):
        if hasattr(page_result, name):
            result[name] = getattr(page_result, name)
    return result


def page_number_from_result(res: Dict[str, Any], fallback_index: int) -> int:
    page_index = res.get("page_index")
    if page_index is not None:
        try:
            return int(page_index) + 1
        except (TypeError, ValueError):
            pass
    return fallback_index


def find_table_results(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    direct = res.get("table_res_list") or res.get("tables") or []
    if isinstance(direct, list) and direct:
        return [item for item in direct if isinstance(item, dict)]

    candidates: List[Dict[str, Any]] = []
    for item in walk_dicts(res):
        if not isinstance(item, dict):
            continue
        if extract_html(item):
            candidates.append(item)
    return candidates


def find_table_blocks(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks = res.get("parsing_res_list") or []
    return [
        block
        for block in blocks
        if isinstance(block, dict) and str(block.get("block_label", "")).lower() == "table"
    ]


def walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def extract_html(table_res: Dict[str, Any]) -> str:
    for key in ("pred_html", "html", "table_html"):
        value = table_res.get(key)
        if isinstance(value, str) and "<table" in value.lower():
            return value

    structure = table_res.get("structure")
    if isinstance(structure, list):
        html = "".join(str(part) for part in structure)
        if "<table" in html.lower():
            return html

    content = table_res.get("block_content")
    if isinstance(content, str) and "<table" in content.lower():
        return content
    return ""


def first_bbox(value: Dict[str, Any]) -> Any:
    for key in ("bbox", "block_bbox", "coordinate", "table_bbox"):
        bbox = value.get(key)
        if bbox:
            return bbox
    return None


def ocr_items_for_page(res: Dict[str, Any], fallback_page: Dict[str, Any]) -> List[Dict[str, Any]]:
    overall = res.get("overall_ocr_res") or {}
    if overall:
        texts = overall.get("rec_texts") or []
        boxes = overall.get("rec_polys") or overall.get("rec_boxes") or []
        scores = overall.get("rec_scores") or []
        items = []
        for index, text in enumerate(texts):
            items.append(
                {
                    "text": str(text),
                    "box": to_jsonable(boxes[index]) if index < len(boxes) else [],
                    "score": to_jsonable(scores[index]) if index < len(scores) else None,
                }
            )
        if items:
            return items
    return fallback_page.get("items", []) or []


def html_table_to_rows(html: str) -> List[List[str]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table") or soup
    grid: List[List[str]] = []
    carry: Dict[Tuple[int, int], str] = {}

    for row_index, tr in enumerate(table.find_all("tr")):
        row: List[str] = []
        col_index = 0
        cells = tr.find_all(["th", "td"], recursive=False)
        for cell in cells:
            while (row_index, col_index) in carry:
                row.append(carry.pop((row_index, col_index)))
                col_index += 1

            text = clean_text(cell.get_text(" ", strip=True))
            rowspan = parse_span(cell.get("rowspan"))
            colspan = parse_span(cell.get("colspan"))
            for offset in range(colspan):
                row.append(text)
                for down in range(1, rowspan):
                    carry[(row_index + down, col_index + offset)] = text
            col_index += colspan

        while (row_index, col_index) in carry:
            row.append(carry.pop((row_index, col_index)))
            col_index += 1
        if any(cell for cell in row):
            grid.append(row)

    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid]


def markdown_table_to_rows(markdown: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [clean_text(cell) for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def rows_to_keyed_rows(rows: List[List[str]]) -> Tuple[List[Dict[str, str]], List[str], List[str]]:
    if not rows:
        return [], [], []

    content_rows = strip_caption_rows(rows)
    if not content_rows:
        return [], [], []

    header_count = infer_header_row_count(content_rows)
    header_rows = content_rows[:header_count]
    data_rows = content_rows[header_count:]
    headers = build_headers(header_rows)
    data_rows = repair_semantic_rows(data_rows, headers)
    keyed_rows: List[Dict[str, str]] = []
    row_texts: List[str] = []

    for row in data_rows:
        if not any(row):
            continue
        keyed: Dict[str, str] = {}
        parts: List[str] = []
        for index, value in enumerate(row):
            value = clean_text(value)
            if not value:
                continue
            header = headers[index] if index < len(headers) else f"列{index + 1}"
            keyed[header] = value
            parts.append(f"{header}：{value}")
        if keyed:
            keyed_rows.append(keyed)
            row_texts.append(" ".join(parts))

    return keyed_rows, row_texts, headers


def repair_semantic_rows(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    """Repair common structure-recognition artifacts without relying on a fixed table caption."""
    if not rows:
        return []
    width = max(len(headers), max((len(row) for row in rows), default=0))
    normalized = [(row + [""] * (width - len(row)))[:width] for row in rows]
    normalized = [row for row in normalized if not is_note_row(row)]
    normalized = repair_primary_state_alignment(normalized, headers)
    normalized = split_merged_record_rows(normalized, headers)
    normalized = split_embedded_state_records(normalized, headers)
    normalized = merge_state_fragments(normalized, headers)
    normalized = fill_down_context_values(normalized, headers)
    return normalized


def is_note_row(row: List[str]) -> bool:
    nonempty = [clean_text(cell) for cell in row if clean_text(cell)]
    if not nonempty:
        return True
    unique_values = set(nonempty)
    if len(unique_values) == 1 and len(nonempty[0]) >= 20:
        return True
    if len(nonempty) == 1 and len(nonempty[0]) >= 20 and not looks_like_data_row(nonempty):
        return True
    return False


def repair_primary_state_alignment(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    """Fix common rowspan drift between a primary material column and a state column."""
    primary_col, state_col = find_primary_state_columns(headers)
    if primary_col is None or state_col is None or state_col != primary_col + 1:
        return rows
    if not any(header_expects_measure(header) for header in headers[state_col + 1 :]):
        return rows

    repaired: List[List[str]] = []
    last_primary = ""
    last_state = ""
    carried_primary_state = ""
    width = max(len(headers), max((len(row) for row in rows), default=0))

    for source_row in rows:
        row = (list(source_row) + [""] * (width - len(source_row)))[:width]
        primary = clean_text(row[primary_col])
        state = clean_text(row[state_col])
        next_value = clean_text(row[state_col + 1]) if state_col + 1 < width else ""

        if not primary:
            state_grade, state_remainder = split_leading_material_grade(state)
            if state_grade and is_state_like(next_value):
                row[primary_col] = state_grade
                row[state_col] = next_value
                row = drop_cell(row, state_col + 1)
                place_method_remainder(row, headers, state_remainder)
                primary = clean_text(row[primary_col])
                state = clean_text(row[state_col])
                next_value = clean_text(row[state_col + 1]) if state_col + 1 < width else ""

        if is_low_information_primary_residue(row, headers, primary_col, state_col, last_primary):
            continue
        if last_primary and not primary and state and nonempty_indexes(row) == [state_col]:
            continue
        if last_primary and is_state_like(primary) and nonempty_indexes(row) == [primary_col]:
            continue

        if is_state_like(primary):
            if is_valid_primary_value(state) and is_state_like(next_value):
                row = drop_cell(row, primary_col)
                primary = clean_text(row[primary_col])
                state = clean_text(row[state_col])
                carried_primary_state = ""
            elif last_primary and has_stale_state_after_primary_state(row, headers, state_col):
                old_primary = primary
                old_state = state
                row[primary_col] = last_primary
                if old_primary == carried_primary_state or old_primary == last_state:
                    row[state_col] = old_state
                    carried_primary_state = old_primary
                else:
                    row[state_col] = old_primary
                primary = clean_text(row[primary_col])
                state = clean_text(row[state_col])
            elif last_primary and row_has_shifted_measure(row, headers, state_col):
                carried_primary_state = primary
                row = insert_cell(row, primary_col, last_primary)
                primary = clean_text(row[primary_col])
                state = clean_text(row[state_col])
            elif (
                last_primary
                and not state
                and state_col + 1 < width
                and state_col + 1 < len(headers)
                and cell_fits_measure_header(row[state_col + 1], headers[state_col + 1])
            ):
                row[primary_col] = last_primary
                row[state_col] = primary
                carried_primary_state = primary
                primary = clean_text(row[primary_col])
                state = clean_text(row[state_col])
            elif last_primary and is_sparse_state_residue(row, headers, primary_col, state_col):
                if repaired and clean_text(repaired[-1][primary_col]) == last_primary:
                    previous_state = clean_text(repaired[-1][state_col])
                    if primary == previous_state or primary == carried_primary_state or primary == last_state:
                        merge_residue_row(repaired[-1], row, headers, state_col)
                continue
        elif primary and not is_valid_primary_value(primary):
            if last_primary and is_state_like(state) and row_has_measure_after_state(row, headers, state_col):
                row[primary_col] = last_primary
                primary = clean_text(row[primary_col])
            else:
                continue
        elif is_valid_primary_value(primary) and is_valid_primary_value(state) and is_state_like(next_value):
            row = drop_cell(row, primary_col)
            primary = clean_text(row[primary_col])
            state = clean_text(row[state_col])
            carried_primary_state = ""
        elif is_valid_primary_value(primary) and should_remove_stale_state(row, headers, state_col):
            row = drop_cell(row, state_col)
            primary = clean_text(row[primary_col])
            state = clean_text(row[state_col])
            carried_primary_state = ""
        elif not primary and is_valid_primary_value(state) and is_state_like(next_value):
            row = drop_cell(row, primary_col)
            primary = clean_text(row[primary_col])
            state = clean_text(row[state_col])
            carried_primary_state = ""
        elif not primary and last_primary and is_state_like(state) and row_has_measure_after_state(row, headers, state_col):
            row[primary_col] = last_primary
            primary = clean_text(row[primary_col])

        if primary and is_valid_primary_value(primary):
            last_primary = primary
            if not is_state_like(state):
                carried_primary_state = ""
        if state and is_state_like(state):
            last_state = state

        if nonempty_indexes(row) == [state_col] and last_primary:
            continue
        repaired.append(row)

    return repaired


def find_primary_state_columns(headers: List[str]) -> Tuple[Optional[int], Optional[int]]:
    primary_col = next((index for index, header in enumerate(headers) if "牌号" in header), None)
    state_col = next((index for index, header in enumerate(headers) if "状态" in header or "供应" in header), None)
    return primary_col, state_col


def is_valid_primary_value(value: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned or is_state_like(cleaned):
        return False
    parts = [part for part in re.split(r"[、,，;\s]+", cleaned) if part]
    if not parts:
        return False
    return all(is_material_grade(part) for part in parts)


def split_leading_material_grade(value: str) -> Tuple[str, str]:
    cleaned = clean_text(value)
    match = re.match(r"^((?:包铝)?(?:\d[A-Z]?\d{2,4}[A-Z]?|[1-8][A-Z]\d{2,4}[A-Z]?))\b\s*(.*)$", cleaned)
    if not match:
        return "", ""
    return clean_text(match.group(1)), clean_text(match.group(2))


def place_method_remainder(row: List[str], headers: List[str], remainder: str) -> None:
    cleaned = clean_text(remainder)
    if not cleaned:
        return
    for index, header in enumerate(headers):
        if index >= len(row):
            break
        if ("开坏方式" in header or "开坯方式" in header) and not clean_text(row[index]):
            row[index] = cleaned
            return


def should_remove_stale_state(row: List[str], headers: List[str], state_col: int) -> bool:
    state = clean_text(row[state_col]) if state_col < len(row) else ""
    next_value = clean_text(row[state_col + 1]) if state_col + 1 < len(row) else ""
    next_header = headers[state_col + 1] if state_col + 1 < len(headers) else ""
    return is_state_like(state) and is_state_like(next_value) and header_expects_measure(next_header)


def has_stale_state_after_primary_state(row: List[str], headers: List[str], state_col: int) -> bool:
    state = clean_text(row[state_col]) if state_col < len(row) else ""
    next_value = clean_text(row[state_col + 1]) if state_col + 1 < len(row) else ""
    next_header = headers[state_col + 1] if state_col + 1 < len(headers) else ""
    return is_state_like(state) and (
        cell_fits_measure_header(next_value, next_header)
        or row_has_measure_after_state(row, headers, state_col)
    )


def row_has_measure_after_state(row: List[str], headers: List[str], state_col: int) -> bool:
    for index in range(state_col + 1, min(len(row), len(headers))):
        if cell_fits_measure_header(row[index], headers[index]):
            return True
    return False


def is_low_information_primary_residue(
    row: List[str],
    headers: List[str],
    primary_col: int,
    state_col: int,
    last_primary: str,
) -> bool:
    primary = clean_text(row[primary_col]) if primary_col < len(row) else ""
    state = clean_text(row[state_col]) if state_col < len(row) else ""
    indexes = nonempty_indexes(row)
    if not indexes:
        return True
    if primary and is_material_grade(primary) and not state and indexes == [primary_col]:
        return True
    if last_primary and primary == last_primary and not state and indexes == [primary_col]:
        return True
    if last_primary and primary == last_primary and not state and not row_has_measure_after_state(row, headers, state_col):
        return True
    return False


def is_sparse_state_residue(row: List[str], headers: List[str], primary_col: int, state_col: int) -> bool:
    primary = clean_text(row[primary_col]) if primary_col < len(row) else ""
    state = clean_text(row[state_col]) if state_col < len(row) else ""
    if not primary or not is_state_like(primary) or state:
        return False
    indexes = [index for index in nonempty_indexes(row) if index != primary_col]
    if not indexes:
        return True
    if any(index < state_col for index in indexes):
        return False
    measure_indexes = [index for index in indexes if index < len(headers) and header_expects_measure(headers[index])]
    return not measure_indexes


def merge_residue_row(target: List[str], residue: List[str], headers: List[str], state_col: int) -> None:
    for index in range(state_col + 1, min(len(target), len(residue), len(headers))):
        value = clean_text(residue[index])
        if not value:
            continue
        current = clean_text(target[index])
        if not current:
            target[index] = value


def split_embedded_state_records(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    primary_col, state_col = find_primary_state_columns(headers)
    if primary_col is None or state_col is None:
        return rows

    measure_cols = [
        index
        for index, header in enumerate(headers)
        if index > state_col and header_expects_measure(header)
    ]
    if not measure_cols:
        return rows

    repaired: List[List[str]] = []
    width = max(len(headers), max((len(row) for row in rows), default=0))
    for source_row in rows:
        row = (list(source_row) + [""] * (width - len(source_row)))[:width]
        split_col = find_embedded_state_column(row, headers, state_col, measure_cols)
        if split_col is None:
            repaired.append(row)
            continue

        original = list(row)
        embedded = list(row)
        for index in range(split_col, width):
            original[index] = ""

        embedded_state = clean_text(row[split_col])
        embedded = [""] * width
        embedded[primary_col] = clean_text(row[primary_col])
        embedded[state_col] = embedded_state
        trailing_values = [clean_text(value) for value in row[split_col + 1 :] if clean_text(value)]
        fill_measure_values(embedded, headers, measure_cols, trailing_values)

        if row_has_record_content(original, primary_col, state_col):
            repaired.append(original)
        if row_has_record_content(embedded, primary_col, state_col):
            repaired.append(embedded)
    return repaired


def find_embedded_state_column(
    row: List[str],
    headers: List[str],
    state_col: int,
    measure_cols: List[int],
) -> Optional[int]:
    for index in range(state_col + 1, min(len(row), len(headers))):
        if index in measure_cols:
            continue
        value = clean_text(row[index])
        if not value or not is_state_like(value):
            continue
        trailing_values = [clean_text(cell) for cell in row[index + 1 :] if clean_text(cell)]
        if not trailing_values:
            continue
        if any(is_numericish(value) or value in {"—", "-", "一"} for value in trailing_values):
            return index
    return None


def fill_measure_values(
    row: List[str],
    headers: List[str],
    measure_cols: List[int],
    values: List[str],
) -> None:
    value_index = 0
    for col in measure_cols:
        while value_index < len(values) and not cell_fits_measure_header(values[value_index], headers[col]):
            value_index += 1
        if value_index >= len(values):
            return
        row[col] = values[value_index]
        value_index += 1


def row_has_record_content(row: List[str], primary_col: int, state_col: int) -> bool:
    primary = clean_text(row[primary_col]) if primary_col < len(row) else ""
    state = clean_text(row[state_col]) if state_col < len(row) else ""
    if not primary and not state:
        return False
    return any(clean_text(value) for index, value in enumerate(row) if index not in (primary_col, state_col))


def merge_state_fragments(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    primary_col, state_col = find_primary_state_columns(headers)
    if primary_col is None or state_col is None:
        return rows

    repaired: List[List[str]] = []
    for source_row in rows:
        row = list(source_row)
        state = clean_text(row[state_col]) if state_col < len(row) else ""
        fragments: List[str] = []
        for index, value in enumerate(row):
            if index in (primary_col, state_col):
                continue
            header = headers[index] if index < len(headers) else ""
            if header_expects_measure(header):
                continue
            cleaned = clean_text(value)
            if cleaned and is_state_like(cleaned):
                fragments.append(cleaned)
                row[index] = ""
        if fragments:
            row[state_col] = join_state_values([state, *fragments])
        repaired.append(row)
    return repaired


def join_state_values(values: List[str]) -> str:
    result: List[str] = []
    seen = set()
    for value in values:
        for token in [part for part in re.split(r"[、,，;\s]+", clean_text(value)) if part]:
            key = token.upper()
            if key in seen:
                continue
            seen.add(key)
            result.append(token)
    return "、".join(result)


def row_has_shifted_measure(row: List[str], headers: List[str], state_col: int) -> bool:
    value = clean_text(row[state_col]) if state_col < len(row) else ""
    next_header = headers[state_col + 1] if state_col + 1 < len(headers) else ""
    return bool(value) and not is_state_like(value) and cell_fits_measure_header(value, next_header)


def header_expects_measure(header: str) -> bool:
    return any(keyword in header for keyword in ("厚度", "直径", "长度", "宽度", "尺寸", "规格", "mm"))


def cell_fits_measure_header(value: str, header: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned or not header_expects_measure(header):
        return False
    return is_numericish(cleaned) or cleaned in {"—", "-", "一", "鈥?", "鈭?"}


def is_state_like(value: str) -> bool:
    cleaned = clean_text(value).strip(" ;；,，、")
    if not cleaned or len(cleaned) > 80:
        return False
    tokens = [token for token in re.split(r"[、,，;/\s]+", cleaned) if token]
    if not tokens:
        return False
    return all(re.fullmatch(r"(?:O|F|W|M|H\d{1,4}[A-Z]?|T\d{1,5}[A-Z]?)", token, re.I) for token in tokens)


def drop_cell(row: List[str], index: int) -> List[str]:
    return row[:index] + row[index + 1 :] + [""]


def insert_cell(row: List[str], index: int, value: str) -> List[str]:
    return row[:index] + [value] + row[index:-1]


def nonempty_indexes(row: List[str]) -> List[int]:
    return [index for index, value in enumerate(row) if clean_text(value)]


def split_merged_record_rows(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    grade_cols = [index for index, header in enumerate(headers) if "牌号" in header]
    status_cols = [index for index, header in enumerate(headers) if "状态" in header or "供应" in header]
    if not grade_cols or not status_cols:
        return rows

    repaired: List[List[str]] = []
    for row in rows:
        split_col = -1
        grade_groups: List[str] = []
        for col in grade_cols:
            groups = split_material_groups(row[col] if col < len(row) else "")
            if len(groups) > 1:
                split_col = col
                grade_groups = groups
                break

        if split_col < 0:
            repaired.append(row)
            continue

        status_splits: Dict[int, List[str]] = {}
        matched_status = False
        for col in status_cols:
            value = row[col] if col < len(row) else ""
            parts = split_status_groups(value, len(grade_groups))
            if len(parts) == len(grade_groups) and len(set(parts)) > 1:
                matched_status = True
            status_splits[col] = parts

        if not matched_status:
            repaired.append(row)
            continue

        for group_index, group in enumerate(grade_groups):
            new_row = list(row)
            new_row[split_col] = group
            for col, parts in status_splits.items():
                if len(parts) == len(grade_groups):
                    new_row[col] = parts[group_index]
            repaired.append(new_row)
    return repaired


def fill_down_context_values(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    if not rows:
        return rows
    filled = [list(row) for row in rows]
    width = max((len(row) for row in filled), default=0)
    primary_col, _ = find_primary_state_columns(headers)
    carried_primaries = carried_primary_values(filled, primary_col)
    for col in range(width):
        header = headers[col] if col < len(headers) else ""
        if is_identity_column(header, col):
            continue
        is_constant_col = is_table_constant_column(filled, col, header)
        last_value = ""
        last_primary = ""
        run_count = 0
        for row_index, row in enumerate(filled):
            value = clean_text(row[col] if col < len(row) else "")
            current_primary = carried_primaries[row_index] if row_index < len(carried_primaries) else ""
            if value:
                if value == last_value:
                    run_count += 1
                else:
                    last_value = value
                    run_count = 1
                last_primary = current_primary
                continue
            same_primary = bool(current_primary and last_primary and current_primary == last_primary)
            if last_value and should_fill_down_context(header, last_value, run_count, same_primary, is_constant_col):
                row[col] = last_value
    return filled


def carried_primary_values(rows: List[List[str]], primary_col: Optional[int]) -> List[str]:
    values: List[str] = []
    current = ""
    for row in rows:
        if primary_col is not None and primary_col < len(row):
            primary = clean_text(row[primary_col])
            if primary:
                current = primary
        values.append(current)
    return values


def is_table_constant_column(rows: List[List[str]], col: int, header: str) -> bool:
    if not header_expects_measure(header):
        return False
    values = [clean_text(row[col]) for row in rows if col < len(row) and clean_text(row[col])]
    if len(values) < 2:
        return False
    counts: Dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    most_common = max(counts.values(), default=0)
    return most_common >= 2 and most_common / max(1, len(values)) >= 0.75


def is_identity_column(header: str, index: int) -> bool:
    if index <= 2 and any(keyword in header for keyword in ("牌号", "状态", "供应", "类别", "项目", "检验")):
        return True
    return False


def is_context_value(value: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned or len(cleaned) > 40:
        return False
    return is_numericish(cleaned) or bool(re.search(r"\d", cleaned)) or cleaned in {"一", "—", "-", "√", "✓"}


def should_fill_down_context(
    header: str,
    value: str,
    run_count: int,
    same_primary: bool,
    is_constant_col: bool,
) -> bool:
    cleaned = clean_text(value)
    if not cleaned:
        return False
    if "开坏方式" in header or "开坯方式" in header:
        return False
    if header_expects_measure(header):
        return same_primary or is_constant_col
    return run_count >= 2 and is_context_value(cleaned)


def is_method_marker(value: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned or is_numericish(cleaned) or is_state_like(cleaned):
        return False
    if any(mark in cleaned for mark in ("√", "✓", "L", "V", "厂")):
        return True
    return cleaned.upper() in {"SAC", "√ SAC", "✓ SAC"}


def split_material_groups(value: str) -> List[str]:
    cleaned = clean_text(value)
    if not cleaned:
        return []
    parts = [part.strip(" 、,，;；") for part in re.split(r"\s+(?=(?:包铝)?(?:\d[A-Z]?\d{2,4}[A-Z]?|[1-8][A-Z]\d{2,4}[A-Z]?))", cleaned) if part.strip()]
    if len(parts) <= 1:
        return [cleaned]
    if all(contains_material_grade(part) for part in parts):
        return parts
    return [cleaned]


def split_status_groups(value: str, expected_count: int) -> List[str]:
    cleaned = clean_text(value)
    if not cleaned or expected_count <= 1:
        return [cleaned] if cleaned else []
    parts = [part for part in cleaned.split() if part]
    if len(parts) == expected_count:
        return parts
    if len(parts) > expected_count:
        return [" ".join(parts[: 1 - expected_count]), *parts[1 - expected_count :]]
    return [cleaned for _ in range(expected_count)]


def strip_caption_rows(rows: List[List[str]]) -> List[List[str]]:
    stripped = []
    for row in rows:
        nonempty = [cell for cell in row if clean_text(cell)]
        if len(nonempty) == 1 and TABLE_CAPTION_RE.match(nonempty[0]):
            continue
        stripped.append(row)
    return stripped


def infer_header_row_count(rows: List[List[str]]) -> int:
    header_count = 0
    for index, row in enumerate(rows[:8]):
        if looks_like_data_row(row):
            return max(1, header_count or index)
        if row_is_headerish(row):
            header_count = index + 1
            continue
        if index == 0:
            return 1
        return max(1, header_count)
    return max(1, header_count)


def build_headers(header_rows: List[List[str]]) -> List[str]:
    width = max((len(row) for row in header_rows), default=0)
    headers: List[str] = []
    for col in range(width):
        parts: List[str] = []
        for row in header_rows:
            cell = clean_text(row[col]) if col < len(row) else ""
            if not cell or cell in parts:
                continue
            if is_unit_only(cell) and parts:
                parts[-1] = f"{parts[-1]}({cell})"
            elif not is_unit_only(cell):
                parts.append(cell)
        headers.append("/".join(parts) if parts else f"列{col + 1}")
    return dedupe_headers(headers)


def dedupe_headers(headers: List[str]) -> List[str]:
    seen: Dict[str, int] = defaultdict(int)
    result: List[str] = []
    for index, header in enumerate(headers, start=1):
        normalized = header or f"列{index}"
        seen[normalized] += 1
        if seen[normalized] == 1:
            result.append(normalized)
        else:
            result.append(f"{normalized}_{seen[normalized]}")
    return result


def row_is_headerish(row: List[str]) -> bool:
    nonempty = [clean_text(cell) for cell in row if clean_text(cell)]
    if not nonempty:
        return False
    combined = "".join(nonempty)
    if any(keyword in combined for keyword in HEADER_KEYWORDS):
        return True
    if all(is_unit_only(cell) for cell in nonempty):
        return True
    return False


def looks_like_data_row(row: List[str]) -> bool:
    nonempty = [clean_text(cell) for cell in row if clean_text(cell)]
    if not nonempty:
        return False
    first = nonempty[0]
    if is_material_grade(first) or is_numericish(first):
        return True
    numeric_cells = sum(1 for cell in nonempty if is_numericish(cell))
    if numeric_cells >= 2 and not row_is_headerish(row):
        return True
    return len(nonempty) >= 2 and not row_is_headerish(row)


def detect_caption(
    ocr_items: List[Dict[str, Any]],
    table_blocks: List[Dict[str, Any]],
    table_index: int,
    bbox: Any,
) -> str:
    block = table_blocks[table_index - 1] if table_index - 1 < len(table_blocks) else {}
    block_caption = caption_from_blocks(table_blocks, table_index)
    if block_caption:
        return block_caption

    table_box = bbox_to_rect(bbox or first_bbox(block))
    if table_box is None:
        return ""
    x1, y1, x2, _ = table_box
    candidates: List[Tuple[float, str]] = []
    for item in ocr_items:
        text = clean_text(str(item.get("text", "")))
        if not TABLE_CAPTION_RE.match(text):
            continue
        item_box = bbox_to_rect(item.get("box"))
        if item_box is None:
            continue
        ix1, iy1, ix2, iy2 = item_box
        if iy2 > y1 or y1 - iy2 > 220:
            continue
        if horizontal_overlap((x1, x2), (ix1, ix2)) < 0.15:
            continue
        candidates.append((y1 - iy2, text))
    if candidates:
        return sorted(candidates, key=lambda item: item[0])[0][1]
    return ""


def caption_from_blocks(table_blocks: List[Dict[str, Any]], table_index: int) -> str:
    block = table_blocks[table_index - 1] if table_index - 1 < len(table_blocks) else {}
    content = clean_text(str(block.get("block_content", "") or ""))
    for line in content.splitlines():
        if TABLE_CAPTION_RE.match(clean_text(line)):
            return clean_text(line)
    return ""


def normalize_caption(caption: str) -> str:
    cleaned = clean_text(caption)
    if not cleaned:
        return ""
    return clean_text(CONTINUED_RE.sub("", cleaned))


def bbox_to_rect(bbox: Any) -> Optional[Tuple[float, float, float, float]]:
    if bbox is None:
        return None
    value = to_jsonable(bbox)
    points: List[Tuple[float, float]] = []
    if isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value):
        x1, y1, x2, y2 = value
        return float(x1), float(y1), float(x2), float(y2)
    if isinstance(value, list):
        for point in value:
            if isinstance(point, list) and len(point) >= 2:
                try:
                    points.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    continue
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def horizontal_overlap(left: Tuple[float, float], right: Tuple[float, float]) -> float:
    overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    width = max(1.0, min(left[1] - left[0], right[1] - right[0]))
    return overlap / width


def parse_span(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def is_unit_only(text: str) -> bool:
    return bool(re.fullmatch(r"(mm|MPa|%|h|℃|°|MS/m|HBW|N/mm2|m)$", clean_text(text), re.I))


def is_numericish(text: str) -> bool:
    value = clean_text(text)
    return bool(re.match(r"^[≤≥<>±+\-−—一]?\s*\d", value)) or bool(re.search(r"\d+\s*[~～]\s*\d+", value))


def is_material_grade(text: str) -> bool:
    value = clean_text(text)
    return bool(re.match(r"^(包铝)?\d[A-Z]?\d{2,4}[A-Z]?$", value)) or bool(re.match(r"^[1-8][A-Z][0-9]{2}", value))


def contains_material_grade(text: str) -> bool:
    value = clean_text(text)
    return bool(re.search(r"(包铝)?(?:\d[A-Z]?\d{2,4}[A-Z]?|[1-8][A-Z]\d{2,4}[A-Z]?)", value))


def clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, dict):
        return {key: to_jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [to_jsonable(child) for child in value]
    return value
