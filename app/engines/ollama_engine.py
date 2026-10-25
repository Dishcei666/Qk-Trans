"""LLM 引擎:调用本机 Ollama 服务翻译,译文更自然,依赖用户自行安装的 Ollama。"""

import re
import time

import requests

from .base import EngineUnavailableError, TranslationEngine

_SYSTEM_PROMPTS = {
    "zh2en": (
        "You are a professional Chinese-to-English translation engine. "
        "Translate the user's text into natural, accurate English. "
        "Output ONLY the translation - no explanations, no quotes, no extra notes. "
        "Preserve line breaks, lists, code blocks and other formatting. "
        "Keep proper nouns, brand names and code identifiers untranslated "
        "unless a well-known translation exists."
    ),
    "en2zh": (
        "你是一个专业的英译中翻译引擎。把用户提供的文本翻译成自然、通顺、地道的简体中文。"
        "只输出译文本身——不要解释、不要引号、不要额外说明。"
        "保留原文的换行、列表、代码块等格式。"
        "专有名词、品牌名和代码标识符保留原文,除非有通行译名。"
    ),
}

# 有的模型会在正文里加一段思考,直接剔除
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)


class OllamaEngine(TranslationEngine):
    name = "llm"

    def __init__(self, config: dict):
        self._base_url = config["ollama_base_url"].rstrip("/")
        self._model = config["ollama_model"]
        self._timeout = float(config.get("ollama_timeout", 120))
        self._cache = (0.0, None)  # (时间戳, 状态) 短缓存,避免状态轮询时反复请求

    # ---- 可用性探测 -----------------------------------------------------

    def _probe(self, max_age: float = 5.0) -> dict:
        now = time.monotonic()
        ts, cached = self._cache
        if cached is not None and now - ts < max_age:
            return cached

        running, models = False, []
        try:
            resp = requests.get(f"{self._base_url}/api/tags", timeout=2)
            if resp.ok:
                running = True
                models = [m.get("name", "") for m in resp.json().get("models", [])]
        except requests.RequestException:
            pass

        base = self._model.split(":")[0]
        pulled = any(m == self._model or m.split(":")[0] == base for m in models)
        info = {
            "server_running": running,
            "models": models,
            "model": self._model,
            "model_pulled": pulled,
            "available": running and pulled,
        }
        self._cache = (now, info)
        return info

    def is_available(self) -> bool:
        return self._probe()["available"]

    def status(self) -> dict:
        info = self._probe()
        if not info["server_running"]:
            info["hint"] = "未检测到 Ollama,请安装并启动后重试"
        elif not info["model_pulled"]:
            info["hint"] = f"尚未拉取模型 {self._model},请运行: ollama pull {self._model}"
        return info

    # ---- 翻译 -----------------------------------------------------------

    def translate(self, text: str, direction: str) -> str:
        system = _SYSTEM_PROMPTS.get(direction)
        if system is None:
            raise ValueError(f"未知方向: {direction}")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 4096},
        }
        try:
            resp = requests.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=(5, self._timeout),
            )
        except requests.ConnectionError:
            raise EngineUnavailableError(
                f"无法连接 Ollama({self._base_url}):请确认已安装并启动 Ollama。"
            )
        except requests.Timeout:
            raise EngineUnavailableError(
                "Ollama 响应超时:模型可能仍在加载,请稍后重试或换用更小的模型。"
            )

        if resp.status_code == 404:
            raise EngineUnavailableError(
                f"Ollama 中没有模型 {self._model}:请先运行 ollama pull {self._model}。"
            )
        if not resp.ok:
            raise EngineUnavailableError(
                f"Ollama 返回错误 {resp.status_code}:{resp.text[:200]}"
            )

        content = resp.json()["message"]["content"]
        content = _THINK_BLOCK.sub("", content).strip()
        # 去掉个别模型喜欢给译文包上的成对引号
        if len(content) >= 2 and content[0] == content[-1] and content[0] in "\"'“”«『":
            content = content[1:-1].strip()
        return content
