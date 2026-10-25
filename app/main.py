"""FastAPI 入口:翻译 API + 静态页面托管 + 启动时自动打开浏览器。

运行: python -m app.main
"""

import threading
import time
import uuid
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, load_config
from .engines.base import EngineUnavailableError
from .engines.ct2_engine import Ct2Engine
from .engines.ollama_engine import OllamaEngine
from .filejobs import FileJobManager
from .filetranslate import MAX_TEXT_CHARS, SUPPORTED_EXTS, translate_file
from .langdetect import detect_direction

config = load_config()
engines = {"lite": Ct2Engine(config), "llm": OllamaEngine(config)}
file_jobs = FileJobManager()
FILES_DIR = PROJECT_ROOT / "files"
UPLOAD_DIR = FILES_DIR / "uploads"
TRANSLATION_DIR = FILES_DIR / "translations"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI(title="Qk-Trans", docs_url=None, redoc_url=None, openapi_url=None)

DIRECTIONS = ("auto", "zh2en", "en2zh")


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    engine: str = "lite"
    direction: str = "auto"


@app.get("/api/status")
def api_status():
    return {
        "engines": {name: eng.status() for name, eng in engines.items()},
    }


@app.post("/api/translate")
def api_translate(req: TranslateRequest):
    engine = engines.get(req.engine)
    if engine is None:
        raise HTTPException(404, f"未知引擎: {req.engine}")
    if req.direction not in DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {DIRECTIONS} 之一")

    direction = detect_direction(req.text) if req.direction == "auto" else req.direction
    start = time.perf_counter()
    try:
        result = engine.translate(req.text, direction)
    except EngineUnavailableError as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # 引擎内部异常不应打挂服务
        raise HTTPException(500, f"翻译失败:{type(e).__name__}: {e}")
    elapsed_ms = round((time.perf_counter() - start) * 1000)

    return {
        "translation": result,
        "engine": req.engine,
        "direction": direction,
        "elapsed_ms": elapsed_ms,
    }


# ---- 文件翻译 ---------------------------------------------------------------


@app.post("/api/file/translate")
async def api_file_translate(
    file: UploadFile = File(...),
    engine: str = Form("lite"),
    direction: str = Form("auto"),
    bilingual: bool = Form(False),
):
    eng = engines.get(engine)
    if eng is None:
        raise HTTPException(404, f"未知引擎: {engine}")
    if direction not in DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {DIRECTIONS} 之一")
    if not eng.is_available():
        try:
            eng.translate("ping", "zh2en")  # 触发具体的不可用原因
        except EngineUnavailableError as e:
            raise HTTPException(503, str(e))
        except Exception:
            pass
        raise HTTPException(503, "引擎当前不可用")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            400, f"暂不支持 {ext or '(无扩展名)'} 格式,支持:{'、'.join(sorted(SUPPORTED_EXTS))}"
        )
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件超过 20MB 上限,请拆分后再试。")
    if len(content) == 0:
        raise HTTPException(400, "文件为空。")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}{ext}"
    upload_path.write_bytes(content)

    def run(job):
        result = translate_file(
            upload_path, TRANSLATION_DIR, eng, direction, bilingual,
            progress=job["update"],
        )
        job.update(result)

    job_id = file_jobs.submit(run)
    return {"job_id": job_id, "filename": file.filename}


@app.get("/api/file/status/{job_id}")
def api_file_status(job_id: str):
    job = file_jobs.status(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在(服务重启后任务记录会丢失)。")
    return job


@app.get("/api/file/download/{job_id}")
def api_file_download(job_id: str):
    job = file_jobs.status(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在。")
    if job["state"] != "done":
        raise HTTPException(409, f"任务尚未完成(当前状态:{job['state']})。")
    return FileResponse(job["out_path"], filename=job["out_name"])


@app.get("/api/file/preview/{job_id}")
def api_file_preview(job_id: str):
    """网页内预览:结构化返回原文/译文段对。"""
    job = file_jobs.status(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在(服务重启后任务记录会丢失)。")
    if job["state"] != "done":
        raise HTTPException(409, f"任务尚未完成(当前状态:{job['state']})。")
    return {
        "kind": job.get("kind"),
        "direction": job.get("direction"),
        "segments": job.get("segments"),
        "pairs": job.get("pairs", []),
        "truncated": job.get("pairs_truncated", False),
    }


# 静态页面挂载必须放在 API 路由之后
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "web"), html=True), name="web")


def _warmup():
    """后台预加载轻量引擎的双向模型,消除首次翻译的冷启动等待。"""
    time.sleep(0.5)
    for direction in ("zh2en", "en2zh"):
        try:
            engines["lite"].translate("你好" if direction == "zh2en" else "hello", direction)
        except Exception:
            pass  # 模型未就绪时静默跳过,由 /api/status 与翻译接口给出明确提示


def main():
    host, port = config["host"], config["port"]
    if config.get("open_browser", True):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    threading.Thread(target=_warmup, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
