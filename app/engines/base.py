"""翻译引擎统一接口与异常定义。"""


class EngineUnavailableError(Exception):
    """引擎不可用时抛出,message 面向用户,可直接展示。"""


class TranslationEngine:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def status(self) -> dict:
        """供 /api/status 使用,要求快速返回。"""
        raise NotImplementedError

    def translate(self, text: str, direction: str) -> str:
        """direction: "zh2en" 或 "en2zh"。"""
        raise NotImplementedError
