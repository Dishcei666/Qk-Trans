"""轻量引擎:CTranslate2 + OPUS-MT 中英双向模型(int8 量化,CPU 即可流畅运行)。

模型由 scripts/download_models.py 下载并转换到 models/ 目录,此处懒加载、常驻内存。
"""

import re
import sys
import threading
from pathlib import Path

import ctranslate2
from transformers import MarianTokenizer

from .base import EngineUnavailableError, TranslationEngine
from ..config import PROJECT_ROOT

# GPU 栈探测翻译的超时秒数:预编译 CUDA 内核对个别新架构显卡可能挂死,必须兜底
CUDA_PROBE_TIMEOUT = 20

# direction -> 模型目录名(与 scripts/download_models.py 的输出保持一致)
MODEL_DIRS = {
    "zh2en": "opus-mt-zh-en",
    "en2zh": "opus-mt-en-zh",
}

# OPUS-MT 位置编码上限 512,按句切分并留出余量,保证长文也不超限
MAX_PIECE_TOKENS = 220

# 句末为中文标点时直接切;英文句点/问叹号仅在后随空白或行尾时切,避免拆开 "3.14" 这类文本
_SENT_SPLIT = re.compile(r"(?<=[。！？!?])|(?<=[.!?])(?=\s|$)")


def _split_sentences(line: str):
    return [p for p in _SENT_SPLIT.split(line.strip()) if p]


class Ct2Engine(TranslationEngine):
    name = "lite"

    def __init__(self, config: dict):
        self._models_root = PROJECT_ROOT / config.get("models_dir", "models")
        self._device_pref = config.get("ct2_device", "auto")
        self._lock = threading.Lock()
        self._pairs = {}  # direction -> (translator, tokenizer)
        self._device_in_use = None
        self._cuda_broken = False   # GPU 探测失败后置位,之后一律用 CPU
        self._cuda_probed = False   # 只做一次 GPU 栈探测

    # ---- 基础信息 -------------------------------------------------------

    def _model_path(self, direction: str) -> Path:
        return self._models_root / MODEL_DIRS[direction]

    def is_available(self) -> bool:
        return all(
            (self._model_path(d) / "model.bin").exists() for d in MODEL_DIRS
        )

    def status(self) -> dict:
        return {
            "available": self.is_available(),
            "device": self._device_in_use,
            "loaded": sorted(self._pairs.keys()),
        }

    # ---- 模型加载 -------------------------------------------------------

    def _resolve_device(self) -> str:
        if self._device_in_use:
            return self._device_in_use
        if self._device_pref == "cpu" or self._cuda_broken:
            return "cpu"
        if self._device_pref == "cuda":
            return "cuda"
        try:
            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def _probe_cuda(self, translator, tokenizer) -> tuple:
        """首次用 GPU 前跑一次微型翻译验证 CUDA 栈可用。

        预编译内核在个别新架构显卡上可能加载成功但推理挂死,因此放到守护线程里
        带超时执行;返回 (是否可用, 失败原因),失败时由调用方回退 CPU。
        """
        if self._cuda_probed:
            return (not self._cuda_broken, None)
        self._cuda_probed = True
        done = threading.Event()
        error = []

        def run():
            try:
                tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode("hello"))
                translator.translate_batch([tokens])
            except Exception as e:
                error.append(f"{type(e).__name__}: {e}")
            finally:
                done.set()

        threading.Thread(target=run, daemon=True).start()
        if not done.wait(CUDA_PROBE_TIMEOUT):
            return (False, f"GPU 推理超过 {CUDA_PROBE_TIMEOUT}s 无响应(可能是不兼容的显卡/驱动)")
        if error:
            return (False, f"CUDA 探测出错({error[0]})")
        return (True, None)

    def _load_pair(self, direction: str):
        model_path = self._model_path(direction)
        if not (model_path / "model.bin").exists():
            raise EngineUnavailableError(
                "轻量引擎模型未就绪:请先运行 setup.bat 完成模型下载。"
            )

        device = self._resolve_device()
        if device == "cuda":
            try:
                translator = ctranslate2.Translator(
                    str(model_path), device="cuda", compute_type="float16"
                )
                tokenizer = MarianTokenizer.from_pretrained(str(model_path))
                ok, reason = self._probe_cuda(translator, tokenizer)
                if ok:
                    return translator, tokenizer, "cuda"
            except Exception as e:
                reason = f"CUDA 加载失败({type(e).__name__}: {e})"
            self._cuda_broken = True
            print(f"[Qk-Trans] {reason},轻量引擎改用 CPU(int8)。", file=sys.stderr)

        translator = ctranslate2.Translator(
            str(model_path), device="cpu", compute_type="int8"
        )
        tokenizer = MarianTokenizer.from_pretrained(str(model_path))
        return translator, tokenizer, "cpu"

    def _get_pair(self, direction: str):
        pair = self._pairs.get(direction)
        if pair is not None:
            return pair
        with self._lock:
            if direction in self._pairs:  # 双重检查,避免重复加载
                return self._pairs[direction]
            translator, tokenizer, device = self._load_pair(direction)
            self._device_in_use = device
            self._pairs[direction] = (translator, tokenizer)
            return translator, tokenizer

    # ---- 翻译 -----------------------------------------------------------

    # 句末标点(含半角句点)
    _TERMINAL_PUNCT = re.compile(r"[.。．!！?？…]$")

    # 剥掉补句号所带出的句末标点:最多一个,且剥完必须还有正文
    _ADDED_TERMINAL = re.compile(r"\s*([.。．!！?？])\s*$")

    # 单词输入(无空格无标点):译文若为同一片段重复多次,折叠为一次
    _SINGLE_WORD_RE = re.compile(r"^\S+$")
    _REPEAT_RE = re.compile(r"^(.+?)(?:\s*[.。,、,]?\s*\1)+$")

    @classmethod
    def _encode_units(cls, tokenizer, text: str):
        """把文本切成 (units, chunks)。

        units: 按输入行组织,每行是若干 (token_ids, patched) 片段;空行为 None。
        chunks: 全部片段展平,供一次性批量翻译。

        片段没有句末标点时(OPUS-MT 对这类"裸"输入容易重复生成,
        如 "translation" -> "翻译翻译"),先补一个句号再翻译,
        译文端再剥掉由此带出的句末标点(patched 标记只落在片段序列的最后一片)。
        """
        units, chunks = [], []
        for line in text.split("\n"):
            if not line.strip():
                units.append(None)
                continue
            line_units = []
            for sent in _split_sentences(line):
                patched = not cls._TERMINAL_PUNCT.search(sent)
                single = bool(cls._SINGLE_WORD_RE.match(sent))
                ids = tokenizer.encode(sent + "." if patched else sent)
                parts = (
                    [ids[i : i + MAX_PIECE_TOKENS] for i in range(0, len(ids), MAX_PIECE_TOKENS)]
                    or [ids]
                )
                for j, part in enumerate(parts):
                    last = j == len(parts) - 1
                    line_units.append((part, patched and last, single and last))
                    chunks.append((part, patched and last, single and last))
            units.append(line_units)
        return units, chunks

    @classmethod
    def _strip_terminal(cls, text: str) -> str:
        m = cls._ADDED_TERMINAL.search(text)
        if m and text[: m.start()].strip():
            return text[: m.start()].rstrip()
        return text

    @classmethod
    def _decode(cls, tokenizer, result, patched: bool, single: bool) -> str:
        ids = tokenizer.convert_tokens_to_ids(result.hypotheses[0])
        text = tokenizer.decode(ids, skip_special_tokens=True)
        if patched:
            text = cls._strip_terminal(text)
        if single:
            m = cls._REPEAT_RE.match(text.strip())
            if m and m.group(1):
                text = m.group(1)
        return text

    def translate(self, text: str, direction: str) -> str:
        if direction not in MODEL_DIRS:
            raise ValueError(f"未知方向: {direction}")
        translator, tokenizer = self._get_pair(direction)

        units, chunks = self._encode_units(tokenizer, text)
        token_chunks = [tokenizer.convert_ids_to_tokens(ids) for ids, _, _ in chunks]
        if token_chunks:
            results = translator.translate_batch(token_chunks, beam_size=4, max_batch_size=32)
            translated = [
                self._decode(tokenizer, r, patched, single)
                for r, (_, patched, single) in zip(results, chunks)
            ]
        else:
            translated = []

        # 英文译文句子间用空格连接;中文译文直接拼接(句末标点已由模型生成)
        joiner = " " if direction == "zh2en" else ""
        it = iter(translated)
        lines = []
        for unit in units:
            if unit is None:
                lines.append("")
            else:
                lines.append(joiner.join(next(it) for _ in unit))
        return "\n".join(lines)
