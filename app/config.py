"""集中管理配置:优先读取项目根目录的 config.json,缺失字段用默认值。"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8477,
    "open_browser": True,
    # auto: 有可用 CUDA 则用 GPU,否则 CPU;也可强制 cpu / cuda
    "ct2_device": "auto",
    "models_dir": "models",
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:7b-instruct",
    "ollama_timeout": 120,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = PROJECT_ROOT / "config.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass  # 配置文件损坏时退回默认值,不让服务起不来
    return cfg
