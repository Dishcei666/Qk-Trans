"""文件翻译任务:内存任务表 + 单工作线程串行执行,进度就地更新。"""

import queue
import threading
import time
import uuid


class FileJobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker_started = False

    # ---- 任务管理 -------------------------------------------------------

    def submit(self, fn) -> str:
        """提交一个任务。fn(job) 在工作线程执行,通过 job["update"] 汇报进度。"""
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "state": "queued",
            "progress": 0.0,
            "error": None,
            "created": time.time(),
        }

        def update(progress: float):
            job["progress"] = max(0.0, min(1.0, progress))

        job["update"] = update
        with self._lock:
            self._jobs[job_id] = job
            if not self._worker_started:
                self._worker_started = True
                self._worker.start()
        self._queue.put((job_id, fn))
        return job_id

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)

    def status(self, job_id: str):
        """对外状态(过滤内部字段)。"""
        job = self.get(job_id)
        if job is None:
            return None
        return {k: v for k, v in job.items() if k not in ("update",)}

    # ---- 工作线程 -------------------------------------------------------

    def _run(self):
        while True:
            job_id, fn = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            job["state"] = "running"
            try:
                fn(job)
                job["state"] = "done"
                job["progress"] = 1.0
            except Exception as e:
                job["state"] = "error"
                job["error"] = str(e) or type(e).__name__
