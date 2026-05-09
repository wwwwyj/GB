from __future__ import annotations  # 支持类自身类型注解等新式写法

from dataclasses import dataclass  # 用 dataclass 表达模型配置，比裸 dict 更清楚
from typing import Any, Dict  # 类型注解

import httpx  # OpenAI SDK 底层 HTTP 客户端配置
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI  # OpenAI-compatible API 客户端和异常类型

from .utils import env_value  # 统一读取环境变量


## 模型配置档案：每个模型 A/B 都会转换成这个结构，便于 LLMClient 使用
@dataclass
class ModelProfile:
    key: str  # 配置中的模型键名，如 model_a/model_b
    provider: str  # 服务提供商名称，目前主要用于记录，如 siliconflow
    label: str  # Excel 中展示的模型标签
    model_name: str  # API 调用使用的模型名称
    base_url: str  # OpenAI-compatible API base_url
    api_key: str  # 直接写在 config.yaml 中的 API key
    api_key_env: str  # API key 对应环境变量名
    timeout: float = 120.0  # 请求超时时间
    max_retries: int = 3  # OpenAI SDK 最大重试次数
    disable_proxy: bool = True  # 是否禁用系统代理，参考原 llm_client.py 的写法


## LLMClient：封装 SiliconFlow/OpenAI-compatible chat completion 调用
class LLMClient:
    """OpenAI-compatible client, following the SiliconFlow style in the reference llm_client.py."""  # 类说明，方便 IDE 提示

    ## 初始化客户端：解析 API key、构建 httpx.Client、构建 OpenAI SDK 客户端
    def __init__(self, profile: ModelProfile):
        self.profile = profile  # 保存模型配置档案
        api_key = profile.api_key or env_value(profile.api_key_env) or env_value(f"{profile.key.upper()}_API_KEY")  # 优先读 config.yaml，再兼容读环境变量
        if not api_key:  # 如果没有 API key
            raise ValueError(f"Missing API key. Set models.{profile.key}.api_key in config.yaml.")  # 提示用户补 config.yaml

        http_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=min(30.0, profile.timeout),  # 连接超时不超过 30 秒
                read=profile.timeout,  # 读取响应超时
                write=profile.timeout,  # 写请求体超时
                pool=profile.timeout,  # 连接池等待超时
            ),
            trust_env=not profile.disable_proxy,  # disable_proxy=True 时不读取系统代理
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),  # 控制连接池规模
        )
        self.client = OpenAI(
            api_key=api_key,  # API key
            base_url=profile.base_url,  # SiliconFlow 或其他兼容服务地址
            timeout=profile.timeout,  # SDK 总超时
            max_retries=profile.max_retries,  # SDK 自动重试
            http_client=http_client,  # 使用自定义 httpx 客户端
        )

    ## 从 config.yaml 创建客户端：把 dict 配置转换成强结构 ModelProfile
    @classmethod
    def from_config(cls, key: str, config: Dict[str, Any]) -> "LLMClient":
        model_config = config.get("models", {}).get(key, {})  # 读取 model_a/model_b 配置块
        profile = ModelProfile(
            key=key,  # 保留模型键名
            provider=str(model_config.get("provider", "siliconflow")),  # 服务商，默认 siliconflow
            label=str(model_config.get("label") or model_config.get("model_name") or key),  # 展示标签
            model_name=str(model_config.get("model_name", "")),  # API 模型名
            base_url=str(model_config.get("base_url", "https://api.siliconflow.cn/v1")),  # API 地址
            api_key=str(model_config.get("api_key", "")).strip(),  # 直接从 config.yaml 读取 API key
            api_key_env=str(model_config.get("api_key_env", "SILICONFLOW_API_KEY")),  # key 环境变量名
            timeout=float(model_config.get("timeout", 120)),  # 超时时间
            max_retries=int(model_config.get("max_retries", 3)),  # 重试次数
            disable_proxy=bool(model_config.get("disable_proxy", True)),  # 是否禁用代理
        )
        return cls(profile)  # 用配置档案创建客户端

    ## 执行一次抽取调用：输入用户 prompt 和 system prompt，返回模型文本响应
    def extract(self, prompt: str, system_prompt: str = "You are a careful document information extraction expert.") -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.profile.model_name,  # 使用当前模型名
                messages=[
                    {"role": "system", "content": system_prompt},  # 系统提示词，约束模型角色和输出要求
                    {"role": "user", "content": prompt},  # 用户提示词，包含字段清单和文档内容
                ],
                temperature=0.1,  # 低温度，减少结构化抽取时的随机性
            )
            content = response.choices[0].message.content or ""  # 读取第一条回复内容
            print(f"[model call ok] {self.profile.label} content_len={len(content)}")  # 打印调用成功和响应长度
            return content  # 返回原始模型文本
        except (APIConnectionError, APITimeoutError, httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RuntimeError(f"{self.profile.label} API connection error: {exc}") from exc  # 网络和超时错误统一包装
        except APIStatusError as exc:
            raise RuntimeError(f"{self.profile.label} API status error {exc.status_code}: {exc}") from exc  # HTTP 状态错误带状态码
        except Exception as exc:
            raise RuntimeError(f"{self.profile.label} API error: {exc}") from exc  # 其他未知错误统一包装
