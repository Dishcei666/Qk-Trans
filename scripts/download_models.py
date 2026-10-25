"""下载 OPUS-MT 中英双向模型并转换为 CTranslate2 int8 格式。

首次安装时运行一次(需要联网),之后整个翻译系统完全离线。
用法:
    python scripts/download_models.py

端点选择:尊重环境变量 HF_ENDPOINT;否则自动探测,
huggingface.co 不可达时切换国内镜像 hf-mirror.com。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
HF_STAGING_DIR = MODELS_DIR / "_staging"

# 输出目录名必须与 app/engines/ct2_engine.py 中 MODEL_DIRS 一致
MODELS = {
    "opus-mt-zh-en": "Helsinki-NLP/opus-mt-zh-en",
    "opus-mt-en-zh": "Helsinki-NLP/opus-mt-en-zh",
}

# 分词器文件:转换后复制到输出目录(注意不能复制 config.json,那是 CTranslate2 自己的格式)
TOKENIZER_FILES = ["source.spm", "target.spm", "vocab.json", "tokenizer_config.json", "generation_config.json"]


def pick_endpoint() -> str:
    if os.environ.get("HF_ENDPOINT"):
        return os.environ["HF_ENDPOINT"]
    import requests
    try:
        requests.head("https://huggingface.co", timeout=5)
        return "https://huggingface.co"
    except requests.RequestException:
        print("* huggingface.co 不可达,改用镜像 https://hf-mirror.com")
        return "https://hf-mirror.com"


def download_one(repo_id: str, dest: Path):
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError

    # 转换器需要原始 config.json + 权重;分词器运行期需要 spm/vocab
    wanted = ["config.json"] + TOKENIZER_FILES
    for name in wanted:
        try:
            hf_hub_download(repo_id, name, local_dir=str(dest))
        except EntryNotFoundError:
            print(f"  (仓库中无 {name},跳过)")

    # 优先 safetensors,旧仓库退回 pytorch_model.bin;其余格式(tf/onnx/msgpack)一律不要
    for weight in ("model.safetensors", "pytorch_model.bin"):
        try:
            hf_hub_download(repo_id, weight, local_dir=str(dest))
            return
        except EntryNotFoundError:
            continue
    raise RuntimeError(f"{repo_id} 上找不到可用的权重文件")


def convert(src: Path, out: Path):
    """调用 ct2-transformers-converter 把 transformers 模型转成 CTranslate2 int8。"""
    env = os.environ.copy()
    scripts_dir = ROOT / ".venv" / "Scripts"
    env["PATH"] = str(scripts_dir) + os.pathsep + env.get("PATH", "")

    # shutil.which 只查当前进程的 PATH,未必包含 venv,因此先直接探测 venv 内的入口
    candidates = [
        scripts_dir / "ct2-transformers-converter.exe",
        scripts_dir / "ct2-transformers-converter",
    ]
    exe = next((str(p) for p in candidates if p.exists()), None) or shutil.which(
        "ct2-transformers-converter"
    )
    if exe is None:
        raise RuntimeError(
            "找不到 ct2-transformers-converter,请确认已安装 requirements.txt 中的依赖"
        )
    cmd = [
        exe,
        "--model", str(src),
        "--output_dir", str(out),
        "--quantization", "int8",
        "--force",
    ]
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"模型转换失败(退出码 {result.returncode})")
    if not (out / "model.bin").exists():
        raise RuntimeError("转换完成但未生成 model.bin,目录结构异常")


def verify() -> bool:
    """对两个方向的模型各做一次真实翻译自检。"""
    import ctranslate2
    from transformers import MarianTokenizer

    checks = [
        ("opus-mt-zh-en", "开源让世界更美好。", "zh→en"),
        ("opus-mt-en-zh", "Translation models are ready.", "en→zh"),
    ]
    for sub, sample, tag in checks:
        path = MODELS_DIR / sub
        tokenizer = MarianTokenizer.from_pretrained(str(path))
        translator = ctranslate2.Translator(str(path), device="cpu", compute_type="int8")
        ids = tokenizer.encode(sample)
        tokens = tokenizer.convert_ids_to_tokens(ids)
        result = translator.translate_batch([tokens])[0].hypotheses[0]
        output = tokenizer.decode(tokenizer.convert_tokens_to_ids(result), skip_special_tokens=True)
        print(f"  [{tag}] {sample!r} -> {output!r}")
    return True


def main():
    MODELS_DIR.mkdir(exist_ok=True)
    os.environ["HF_ENDPOINT"] = pick_endpoint()
    print(f"* HuggingFace 端点: {os.environ['HF_ENDPOINT']}")

    for sub, repo in MODELS.items():
        out = MODELS_DIR / sub
        if (out / "model.bin").exists() and (out / "vocab.json").exists():
            print(f"* {sub} 已存在,跳过")
            continue

        print(f"* 下载 {repo}(约 300 MB)...")
        staging = HF_STAGING_DIR / sub
        download_one(repo, staging)

        print(f"* 转换为 CTranslate2 int8 格式(约 1 分钟)...")
        out.mkdir(parents=True, exist_ok=True)
        convert(staging, out)

        # 复制分词器文件;config.json 除外(输出目录里的是 CTranslate2 自己的)
        for name in TOKENIZER_FILES:
            if (staging / name).exists():
                shutil.copy2(staging / name, out / name)
        shutil.rmtree(staging, ignore_errors=True)
        print(f"* {sub} 完成")

    shutil.rmtree(HF_STAGING_DIR, ignore_errors=True)

    print("* 自检翻译:")
    verify()
    print("* 全部完成,翻译系统已可离线使用。")


if __name__ == "__main__":
    main()
