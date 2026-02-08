"""
通用 Gemini Batch API 执行器

从 GraphExtractor._run_batch_job() 提取的通用模式，供 unified_pipeline 中
边重标注、主线增强、实体提取等步骤复用。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from app.config import settings


class BatchRunner:
    """通用 Gemini Batch API 执行器"""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        verbose: bool = True,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.model = model
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.verbose = verbose
        self._log_fn = log_fn or (lambda msg: print(msg) if self.verbose else None)

    def _log(self, msg: str) -> None:
        self._log_fn(msg)

    def run_batch(
        self,
        requests: List[Tuple[str, str]],
        temp_dir: Path,
        display_name: str,
        temperature: float = 0.2,
        response_mime_type: str = "application/json",
        max_output_tokens: int = 65536,
    ) -> Dict[str, str]:
        """
        提交一组 (key, prompt) 到 Batch API 并等待结果。

        Args:
            requests: (key, prompt) 对列表
            temp_dir: 临时文件目录（存放 JSONL）
            display_name: 批任务显示名称
            temperature: 生成温度
            response_mime_type: 响应 MIME 类型
            max_output_tokens: 最大输出 token 数

        Returns:
            key -> raw response text 的映射
        """
        if not requests:
            return {}

        temp_dir.mkdir(parents=True, exist_ok=True)

        # 1. 写入 JSONL
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        requests_path = temp_dir / f"batch_{display_name}_{ts}.jsonl"

        with requests_path.open("w", encoding="utf-8") as f:
            for key, prompt in requests:
                req_body: Dict[str, Any] = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generation_config": {
                        "response_mime_type": response_mime_type,
                        "temperature": temperature,
                        "max_output_tokens": max_output_tokens,
                    },
                }
                line = {"key": key, "request": req_body}
                f.write(json.dumps(line, ensure_ascii=False))
                f.write("\n")

        self._log(f"    Wrote {len(requests)} requests to {requests_path.name}")

        # 2. 上传
        uploaded_file = self.client.files.upload(
            file=str(requests_path),
            config=types.UploadFileConfig(
                display_name=f"{display_name}-{ts}",
                mime_type="jsonl",
            ),
        )
        self._log(f"    Uploaded: {uploaded_file.name}")

        # 3. 创建批任务
        batch_job = self.client.batches.create(
            model=self.model,
            src=uploaded_file.name,
            config=types.CreateBatchJobConfig(display_name=display_name),
        )
        job_name = batch_job.name
        self._log(f"    Batch job created: {job_name}")

        # 4. 轮询等待
        self._log("    Monitoring batch job...")
        while True:
            batch_job = self.client.batches.get(name=job_name)
            state = (
                batch_job.state.name
                if hasattr(batch_job.state, "name")
                else str(batch_job.state)
            )

            progress = ""
            if hasattr(batch_job, "batch_stats") and batch_job.batch_stats:
                stats = batch_job.batch_stats
                succeeded = getattr(stats, "succeeded_request_count", 0) or 0
                total = getattr(stats, "total_request_count", 0) or 0
                if total > 0:
                    progress = f" ({succeeded}/{total})"

            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log(f"    [{timestamp}] State: {state}{progress}")

            if state in {
                "JOB_STATE_SUCCEEDED",
                "JOB_STATE_FAILED",
                "JOB_STATE_CANCELLED",
            }:
                break

            time.sleep(30)

        if state != "JOB_STATE_SUCCEEDED":
            raise RuntimeError(f"Batch job failed with state: {state}")

        # 5. 下载结果
        result_file = batch_job.dest.file_name
        self._log(f"    Downloading results from {result_file}...")
        results_path = temp_dir / f"batch_{display_name}_{ts}_results.jsonl"
        content = self.client.files.download(file=result_file)

        if isinstance(content, bytes):
            results_path.write_bytes(content)
        elif hasattr(content, "read"):
            results_path.write_bytes(content.read())
        else:
            results_path.write_text(str(content), encoding="utf-8")

        # 6. 解析结果
        results: Dict[str, str] = {}
        with results_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    key = data.get("key", "")
                    response = data.get("response", {})

                    text = ""
                    if "candidates" in response:
                        for candidate in response["candidates"]:
                            content_obj = candidate.get("content", {})
                            for part in content_obj.get("parts", []):
                                if "text" in part:
                                    text += part["text"]

                    if key and text:
                        results[key] = text
                except Exception as e:
                    self._log(f"    Warning: Failed to parse result line: {e}")

        self._log(f"    Parsed {len(results)}/{len(requests)} results")
        return results
