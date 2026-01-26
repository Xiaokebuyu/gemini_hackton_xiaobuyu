#!/bin/bash
# Quick batch job status check
JOB_NAME="${1:-batches/k53t9h9nwt4hmzder8g8hbqfup4sqrg71egg}"
./venv/bin/python -c "
from google import genai
from app.config import settings
c = genai.Client(api_key=settings.gemini_api_key)
j = c.batches.get(name='$JOB_NAME')
print(f'Job: {j.name}')
print(f'State: {j.state.name}')
if hasattr(j, 'batch_stats') and j.batch_stats:
    s = j.batch_stats
    total = s.total_request_count or 0
    succeeded = s.succeeded_request_count or 0
    failed = s.failed_request_count or 0
    print(f'Progress: {succeeded}/{total} succeeded, {failed} failed')
"
