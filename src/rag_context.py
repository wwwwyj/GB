from __future__ import annotations  # 支持新式类型注解

import re  # 文本切片前的空行规整
from dataclasses import dataclass  # 用 dataclass 表示 RAG chunk
from typing import Any, Dict, List  # 类型注解

from sklearn.feature_extraction.text import TfidfVectorizer  # 默认本地 TF-IDF 召回
from sklearn.metrics.pairwise import cosine_similarity  # 计算查询和 chunk 的余弦相似度


## RAG 切片数据结构：保留来源文件、页码、类型和元数据，便于人工复核追溯
@dataclass
class Chunk:
    chunk_id: str  # chunk 唯一编号，如 page_3_text_1
    source_file: str  # 来源文件名
    page: int  # 来源页码
    type: str  # chunk 类型：text/table
    content: str  # chunk 文本内容
    metadata: Dict[str, Any]  # 扩展元数据，如表格编号

    ## 转成普通 dict：用于 JSON 保存和 DataFrame 写 Excel
    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,  # chunk 编号
            "source_file": self.source_file,  # 来源文件
            "page": self.page,  # 页码
            "type": self.type,  # 类型
            "content": self.content,  # 内容
            "metadata": self.metadata,  # 元数据
        }


## RAG 上下文模块：负责从 document 构建 chunks，并为低相似度字段召回证据
class RagContext:
    ## 初始化：读取 rag 配置块，包含阈值、chunk 大小、top_k、embedding 开关
    def __init__(self, config: Dict[str, Any]):
        self.config = config  # 保存全局配置
        self.rag_config = config.get("rag", {})  # 单独缓存 RAG 配置

    ## 构建 chunks：页文本按长度切片，表格按完整表格作为 chunk
    def build_chunks(self, document: Dict[str, Any]) -> List[Chunk]:
        chunk_size = int(self.rag_config.get("chunk_size", 1200))  # 每个文本 chunk 的最大字符数
        overlap = int(self.rag_config.get("chunk_overlap", 180))  # 相邻 chunk 的重叠字符数
        chunks: List[Chunk] = []  # 保存所有切片
        source_file = document.get("file_name") or document.get("source_file", "")  # 优先使用短文件名

        ## 遍历页内容：文本和表格分别处理，方便后续按类型召回
        for page in document.get("pages", []):
            page_number = int(page.get("page", 1))  # 当前页码
            text = page.get("text", "")  # 当前页文本
            for index, part in enumerate(split_text(text, chunk_size, overlap), start=1):  # 按长度切分页文本
                chunks.append(
                    Chunk(
                        chunk_id=f"page_{page_number}_text_{index}",  # 文本 chunk 编号
                        source_file=source_file,  # 来源文件
                        page=page_number,  # 来源页
                        type="text",  # chunk 类型
                        content=part,  # chunk 内容
                        metadata={},  # 文本 chunk 暂无额外元数据
                    )
                )

            ## 表格不拆太碎：完整表格作为一个 chunk，保留行列上下文
            for table in page.get("tables", []):
                row_texts = table.get("row_texts") or []  # 结构化表格优先使用“列标题：内容”的行文本
                rows = table.get("rows", [])  # 表格二维数组
                if row_texts:
                    caption = table.get("caption", "")
                    table_text = "\n".join([caption, *row_texts]).strip()
                elif rows:
                    table_text = "\n".join(" | ".join("" if cell is None else str(cell) for cell in row) for row in rows)  # 表格转为可检索文本
                else:  # 空表格跳过
                    continue  # 继续下一个表格
                chunks.append(
                    Chunk(
                        chunk_id=f"page_{page_number}_table_{table.get('table_index', 1)}",  # 表格 chunk 编号
                        source_file=source_file,  # 来源文件
                        page=page_number,  # 来源页
                        type="table",  # 类型为表格
                        content=table_text,  # 表格文本
                        metadata={"table_index": table.get("table_index"), "caption": table.get("caption", "")},  # 保留表格编号
                    )
                )
        return chunks  # 返回所有切片

    ## 根据查询召回相关 chunk：默认 TF-IDF；如果配置开启 embedding，则优先尝试 embedding
    def retrieve(self, query: str, chunks: List[Chunk]) -> List[Dict[str, Any]]:
        if not self.rag_config.get("enabled", True) or not chunks:  # RAG 关闭或没有 chunk
            return []  # 不召回
        top_k = int(self.rag_config.get("top_k", 5))  # 召回条数
        if self.rag_config.get("use_embeddings", False):  # 如果配置启用 embedding
            embedded = self._retrieve_by_embedding(query, chunks, top_k)  # 尝试 embedding 召回
            if embedded:  # 如果 embedding 可用且有结果
                return embedded  # 返回 embedding 结果
        return self._retrieve_by_tfidf(query, chunks, top_k)  # 默认使用无需下载模型的 TF-IDF 召回

    ## TF-IDF 召回：字符 n-gram 对中文、标准号、材料牌号都比较稳
    def _retrieve_by_tfidf(self, query: str, chunks: List[Chunk], top_k: int) -> List[Dict[str, Any]]:
        corpus = [chunk.content for chunk in chunks]  # 所有 chunk 文本组成语料
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))  # 使用字符级 2-4 gram
        matrix = vectorizer.fit_transform(corpus + [query])  # 最后一行是查询向量
        scores = cosine_similarity(matrix[-1], matrix[:-1]).flatten()  # 查询与每个 chunk 的余弦分数
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]  # 取 top_k
        return [chunks[index].to_dict() | {"score": round(float(score), 4)} for index, score in ranked if score > 0]  # 返回带分数的 chunk dict

    ## embedding 召回：当前作为可选增强，不默认启用，避免下载模型和环境依赖
    def _retrieve_by_embedding(self, query: str, chunks: List[Chunk], top_k: int) -> List[Dict[str, Any]]:
        model_name = str(self.rag_config.get("embedding_model") or "").strip()  # 读取 embedding 模型名或本地路径
        if not model_name:  # 没有配置模型
            return []  # 放弃 embedding，回退 TF-IDF
        try:
            from sentence_transformers import SentenceTransformer  # 延迟导入可选依赖
        except Exception:
            return []  # 未安装依赖时回退 TF-IDF

        model = SentenceTransformer(model_name)  # 加载 embedding 模型
        corpus_embeddings = model.encode([chunk.content for chunk in chunks], normalize_embeddings=True)  # 编码 chunk
        query_embedding = model.encode([query], normalize_embeddings=True)  # 编码查询
        scores = cosine_similarity(query_embedding, corpus_embeddings).flatten()  # 计算余弦相似度
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]  # 取 top_k
        return [chunks[index].to_dict() | {"score": round(float(score), 4)} for index, score in ranked]  # 返回带分数的 chunk


## 文本切片函数：按固定长度切分，并保留 overlap 避免句子被切断后丢上下文
def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()  # 将过多空行压缩为双换行
    if not cleaned:  # 空文本
        return []  # 无切片
    if len(cleaned) <= chunk_size:  # 文本本身短于 chunk 大小
        return [cleaned]  # 直接作为一个 chunk

    chunks: List[str] = []  # 保存切片结果
    start = 0  # 当前切片起点
    while start < len(cleaned):  # 循环直到覆盖全文
        end = min(start + chunk_size, len(cleaned))  # 当前切片终点
        chunks.append(cleaned[start:end])  # 添加切片文本
        if end == len(cleaned):  # 已经到文本末尾
            break  # 结束循环
        start = max(end - overlap, start + 1)  # 向后推进，同时保留 overlap
    return chunks  # 返回切片列表
