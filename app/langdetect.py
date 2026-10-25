"""极简语言方向检测:按 CJK 字符在有效文字中的占比判断中/英。"""

import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[A-Za-z]")

# CJK 占比达到该阈值即视为中文输入(英文文本里夹杂少量中文术语也能正确判为英→中)
CJK_THRESHOLD = 0.3


def detect_direction(text: str) -> str:
    """返回 "zh2en" 或 "en2zh"。"""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    if cjk == 0 and latin == 0:
        return "zh2en"  # 没有有效文字时给一个兜底方向
    return "zh2en" if cjk / (cjk + latin) >= CJK_THRESHOLD else "en2zh"
