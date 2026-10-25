"""文件翻译:txt/md、PDF、docx、srt/vtt 的文本提取、批量翻译与原格式重组。

统一处理模型:任何文件都解析成"行序列"(None 表示该行原样保留),
按批调用现有引擎翻译(引擎天然逐行保留),再按原格式重组输出。
"""

import re
from pathlib import Path

from .langdetect import detect_direction

SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx", ".srt", ".vtt"}
MAX_TEXT_CHARS = 400_000
BATCH_LINES = 40
# 网页预览最多返回的段对数,超出部分请下载文件查看
MAX_PREVIEW_PAIRS = 2000

# 字幕文本里的样式标签会影响翻译,翻译前剥掉
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# srt/vtt 时间轴行,如 "00:00:01,000 --> 00:00:04,000" / "00:01.000 --> 00:04.000"
_TIMING_RE = re.compile(r"\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}(:\d{2})?[.,]\d{3}")


class FileTranslateError(Exception):
    """面向用户的文件翻译错误,message 可直接展示。"""


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---- 提取:各类文件 → 行序列(None = 保留原样) --------------------------------


def _parse_text(path: Path):
    """txt/md:每行一个单元,空行保留。"""
    lines = _read_text(path).split("\n")
    return "text", [line if line.strip() else None for line in lines], None


# 段落重建:句末标点(段落到此结束)与"必然续行"的标点
_PARA_END_RE = re.compile(r"[.。??!:;。:;…\"”')]\s*$")
_CONT_END_CHARS = (",", ",", ";", ";", ":", ":")


def _merge_pdf_lines(raw_lines):
    """把版式行重建成逻辑段落。

    PDF 提取出的行是排版单位而非句子:软连字符换行(“infor-”“mation”)拼接回去;
    上一行没有句末标点且(下一行小写开头/上一行较长)时合并为同一段,
    短而无标点的行(标题)保持独立。
    """
    paragraphs, buf = [], ""
    for ln in raw_lines:
        ln = ln.strip()
        if not ln:
            continue
        if not buf:
            buf = ln
        elif buf.endswith("-"):
            buf = buf[:-1] + ln  # 软连字符换行
        elif buf.endswith(_CONT_END_CHARS):
            buf = buf + " " + ln  # 逗号/分号结尾,必为续行
        elif _PARA_END_RE.search(buf):
            paragraphs.append(buf)  # 句末标点,段落结束
            buf = ln
        elif ln[:1].islower() or len(buf) > 60:
            buf = buf + " " + ln  # 小写开头或长行折断,合并
        else:
            paragraphs.append(buf)  # 短且无标点:大概率是标题,独立成段
            buf = ln
    if buf:
        paragraphs.append(buf)
    return paragraphs


def _parse_pdf(path: Path):
    """PDF:逐页取文本块,重建成逻辑段落后按段输出;页与页之间插一个空行。"""
    import pymupdf

    lines, page_breaks = [], []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            if not blocks:
                continue
            raw_lines = []
            for block in sorted(blocks, key=lambda b: (round(b[1]), b[0])):
                raw_lines.extend(block[4].split("\n"))
            if lines:
                page_breaks.append(len(lines))
                lines.append(None)
            for para in _merge_pdf_lines(raw_lines):
                lines.append(para)
    if not any(l for l in lines):
        raise FileTranslateError("PDF 中没有提取到任何文字(可能是扫描件/纯图片 PDF)。")
    return "pdf", lines, page_breaks


def _parse_docx(path: Path):
    """docx:按文档段落,空段落保留;表格内容 v1 不处理。"""
    from docx import Document

    doc = Document(str(path))
    lines = [p.text if p.text.strip() else None for p in doc.paragraphs]
    if not any(lines):
        raise FileTranslateError("Word 文档中没有可翻译的段落(内容可能在表格里,v1 暂不支持)。")
    return "docx", lines, doc


def _parse_subtitle(path: Path):
    """srt/vtt:解析为块序列;返回 (行序列, 块结构)。

    行序列只包含各块的文本行;块结构记录每块的前置行(index/cue id)与时间轴行,
    重组时按块还原。"""
    text = _read_text(path)
    is_vtt = path.suffix.lower() == ".vtt"

    # vtt 文件头(WEBVTT 行及其后到首个空行的元数据)原样保留
    header_lines = []
    body = text
    if is_vtt:
        normalized = text.lstrip("\ufeff").split("\n")
        if normalized and normalized[0].strip().upper().startswith("WEBVTT"):
            header_lines = [normalized[0]]
            body = "\n".join(normalized[1:])
            # NOTE/STYLE 等元数据块到第一个空行为止,一并保留
            head, sep, rest = body.partition("\n\n")
            if head.strip() and not _TIMING_RE.search(head):
                header_lines.extend(head.split("\n"))
                body = rest if sep else ""

    blocks, lines = [], []
    for raw_block in re.split(r"\n\s*\n", body):
        block_lines = raw_block.replace("\r", "").split("\n")
        block_lines = [ln for ln in block_lines if ln != ""]
        if not block_lines:
            continue
        # 分出:前置行(时间轴之前的内容)+ 文本行
        timing_idx = next((i for i, ln in enumerate(block_lines) if _TIMING_RE.search(ln)), None)
        if timing_idx is None:
            continue  # 损坏块,跳过
        block = {
            "pre": block_lines[: timing_idx + 1],  # cue id(如有)+ 时间轴
            "slots": [],  # 文本行在行序列中的下标
        }
        for ln in block_lines[timing_idx + 1 :]:
            clean = _TAG_RE.sub("", ln).strip()
            if clean:
                block["slots"].append(len(lines))
                lines.append(clean)
            else:
                block["slots"].append(None)  # 空行/纯样式行,重组时留空
        blocks.append(block)
    if not blocks:
        raise FileTranslateError("字幕文件中没有解析到任何带时间轴的条目。")
    return "subtitle", lines, {"header": header_lines, "blocks": blocks}


def parse_file(path: Path):
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise FileTranslateError(f"暂不支持 {ext} 格式,支持:{'、'.join(sorted(SUPPORTED_EXTS))}")
    if ext == ".pdf":
        kind, lines, extra = _parse_pdf(path)
    elif ext == ".docx":
        kind, lines, extra = _parse_docx(path)
    elif ext in (".srt", ".vtt"):
        kind, lines, extra = _parse_subtitle(path)
    else:
        kind, lines, extra = _parse_text(path)
    total = sum(len(l) for l in lines if l)
    if total > MAX_TEXT_CHARS:
        raise FileTranslateError(
            f"提取到 {total} 字符,超过单文件 {MAX_TEXT_CHARS} 字符上限,请拆分后再翻译。"
        )
    return kind, lines, extra


# ---- 批量翻译 ----------------------------------------------------------------


def translate_lines(lines, engine, direction, progress=None):
    """就地翻译行序列中所有非 None 行,返回 (方向, 行数)。

    分批拼接为多行文本调用引擎(引擎逐行保留);
    若某批返回行数不匹配(LLM 可能合并行),退回逐行翻译保证对齐。
    """
    idx = [i for i, l in enumerate(lines) if l]
    if not idx:
        return direction, 0
    if direction == "auto":
        direction = detect_direction("\n".join(lines[i] for i in idx[:200]))

    batches = [idx[i : i + BATCH_LINES] for i in range(0, len(idx), BATCH_LINES)]
    for done, batch in enumerate(batches, 1):
        joined = "\n".join(lines[i] for i in batch)
        try:
            out = engine.translate(joined, direction)
            parts = out.split("\n")
            if len(parts) != len(batch):
                raise ValueError("行数不匹配")
        except Exception:
            parts = [engine.translate(lines[i], direction) for i in batch]
        for i, t in zip(batch, parts):
            lines[i] = t
        if progress:
            progress(done / len(batches))
    return direction, len(idx)


# ---- 重组输出 ----------------------------------------------------------------


def _lang_tag(direction: str) -> str:
    return "zh" if direction == "en2zh" else "en"


def _plain_join(lines):
    """行序列 → 文本(None 空行标记转回空字符串)。"""
    return "\n".join(l if l is not None else "" for l in lines)


def _write_subtitle_output(struct, lines, out_path):
    parts = []
    for ln in struct["header"]:
        parts.append(ln)
    if parts:
        parts.append("")
    for block in struct["blocks"]:
        parts.extend(block["pre"])
        for slot in block["slots"]:
            parts.append(lines[slot] if slot is not None else "")
        parts.append("")
    out_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def _write_subtitle_bilingual(struct, originals, lines, out_path):
    parts = []
    for block in struct["blocks"]:
        timing = block["pre"][-1] if block["pre"] else ""
        src = " ".join(originals[s] for s in block["slots"] if s is not None)
        dst = " ".join(lines[s] for s in block["slots"] if s is not None)
        parts.append(timing)
        parts.append(f"原文:{src}")
        parts.append(f"译文:{dst}")
        parts.append("")
    out_path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def _write_docx_output(doc, lines, out_path):
    """纯译文:替换各段落文本(保留段落样式,行内格式合并)。"""
    i = 0
    for para in doc.paragraphs:
        if lines[i] is not None:
            para.text = lines[i]
        i += 1
    doc.save(str(out_path))


def _write_docx_bilingual(originals, lines, out_path, styles):
    """对照译文:新建文档,原文段(引用样式)+ 译文段交替。"""
    from docx import Document
    from docx.shared import RGBColor

    doc = Document()
    for i, (src, dst) in enumerate(zip(originals, lines)):
        style = styles[i]
        if dst is None:  # 空段落
            doc.add_paragraph("")
            continue
        p1 = doc.add_paragraph(style=style)
        run = p1.add_run(src)
        run.italic = True
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        try:
            doc.add_paragraph(dst, style=style)
        except Exception:
            doc.add_paragraph(dst)
    doc.save(str(out_path))


# ---- 主入口 ------------------------------------------------------------------


def translate_file(src_path: Path, out_dir: Path, engine, direction, bilingual, progress=None):
    """翻译一个文件,返回结果信息 dict。progress接收 0~1 进度。"""
    kind, lines, extra = parse_file(src_path)
    originals = list(lines)  # 翻译前留底,对照模式使用

    direction, count = translate_lines(lines, engine, direction, progress)

    stem = src_path.stem
    tag = _lang_tag(direction)
    out_dir.mkdir(parents=True, exist_ok=True)

    if kind == "text":
        ext = src_path.suffix.lower()
        if bilingual:
            pairs = []
            for src, dst in zip(originals, lines):
                if dst is None:
                    continue
                pairs.append(src)
                pairs.append(dst)
                pairs.append("")
            out_path = out_dir / f"{stem}.对照.txt"
            out_path.write_text("\n".join(pairs).strip() + "\n", encoding="utf-8")
        else:
            out_path = out_dir / f"{stem}.{tag}{ext}"
            out_path.write_text(_plain_join(lines), encoding="utf-8")

    elif kind == "pdf":
        if bilingual:
            pairs = []
            for i, (src, dst) in enumerate(zip(originals, lines)):
                if i in (extra or []):
                    pairs.append("───── 第 {} 页 ─────".format(len([b for b in extra if b < i]) + 1))
                if dst is None:
                    continue
                pairs.append(src)
                pairs.append(dst)
                pairs.append("")
            out_path = out_dir / f"{stem}.对照.txt"
            out_path.write_text("\n".join(pairs).strip() + "\n", encoding="utf-8")
        else:
            out_path = out_dir / f"{stem}.{tag}.txt"
            out_path.write_text(_plain_join(lines), encoding="utf-8")

    elif kind == "subtitle":
        ext = src_path.suffix.lower()
        if bilingual:
            out_path = out_dir / f"{stem}.对照.txt"
            _write_subtitle_bilingual(extra, originals, lines, out_path)
        else:
            out_path = out_dir / f"{stem}.{tag}{ext}"
            _write_subtitle_output(extra, lines, out_path)

    elif kind == "docx":
        if bilingual:
            styles = []
            for para in extra.paragraphs:
                try:
                    styles.append(para.style.name if para.style else None)
                except Exception:
                    styles.append(None)
            out_path = out_dir / f"{stem}.对照.docx"
            _write_docx_bilingual(originals, lines, out_path, styles)
        else:
            out_path = out_dir / f"{stem}.{tag}.docx"
            _write_docx_output(extra, lines, out_path)

    return {
        "kind": kind,
        "direction": direction,
        "segments": count,
        "out_name": out_path.name,
        "out_path": str(out_path),
        "pairs": _preview_pairs(kind, lines, originals, extra),
        "pairs_truncated": count > MAX_PREVIEW_PAIRS,
    }


def _preview_pairs(kind, lines, originals, extra):
    """构建网页预览用的 (原文, 译文) 段对;字幕按整条字幕聚合,其余按行。"""
    pairs = []
    if kind == "subtitle":
        for block in extra["blocks"]:
            src = " ".join(originals[s] for s in block["slots"] if s is not None)
            dst = " ".join(lines[s] for s in block["slots"] if s is not None)
            if src:
                timing = block["pre"][-1].split("-->")[0].strip() if block["pre"] else ""
                pairs.append((f"[{timing}] {src}" if timing else src, dst))
    else:
        for src, dst in zip(originals, lines):
            if dst is not None:
                pairs.append((src, dst))
    return pairs[:MAX_PREVIEW_PAIRS]
