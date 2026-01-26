"""
Phase 4-5: Batch job lifecycle management (submit/monitor/download).

Handles submission of batch jobs to Gemini Batch API, monitoring
progress, and downloading results.

Run:
    cd backend
    # Submit job
    python -m app.tools.batch.job_manager submit \
        --input data/goblin_slayer/batch_requests.jsonl \
        --display-name "goblin-slayer-worldbook"

    # Monitor and download results
    python -m app.tools.batch.job_manager monitor \
        --job-name batches/xxxxx \
        --output data/goblin_slayer/batch_results.jsonl \
        --poll-interval 60

    # Check status only
    python -m app.tools.batch.job_manager status \
        --job-name batches/xxxxx

    # List all jobs
    python -m app.tools.batch.job_manager list
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from app.config import settings


# Job states that indicate completion
COMPLETED_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def get_client(api_key: Optional[str] = None) -> genai.Client:
    """Get Gemini API client."""
    return genai.Client(api_key=api_key or settings.gemini_api_key)


def submit_batch_job(
    input_path: Path,
    display_name: str,
    model: str = "gemini-2.0-flash",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Submit a batch job to Gemini Batch API.

    Args:
        input_path: Path to batch requests JSONL file
        display_name: Display name for the job
        model: Target model
        api_key: Optional API key

    Returns:
        Job information including job name
    """
    client = get_client(api_key)

    # Upload the JSONL file
    print(f"Uploading {input_path}...")
    uploaded_file = client.files.upload(
        file=str(input_path),
        config=types.UploadFileConfig(
            display_name=display_name,
            mime_type='jsonl'
        )
    )
    print(f"  Uploaded: {uploaded_file.name}")

    # Create batch job
    print(f"Creating batch job...")
    batch_job = client.batches.create(
        model=model,
        src=uploaded_file.name,
        config=types.CreateBatchJobConfig(display_name=display_name)
    )

    job_info = {
        "job_name": batch_job.name,
        "display_name": display_name,
        "model": model,
        "state": batch_job.state.name if hasattr(batch_job.state, 'name') else str(batch_job.state),
        "created_at": datetime.now().isoformat(),
        "source_file": uploaded_file.name,
        "input_file": str(input_path),
    }

    return job_info


def get_job_status(
    job_name: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get current status of a batch job.

    Args:
        job_name: Batch job name
        api_key: Optional API key

    Returns:
        Job status information
    """
    client = get_client(api_key)
    batch_job = client.batches.get(name=job_name)

    state_name = batch_job.state.name if hasattr(batch_job.state, 'name') else str(batch_job.state)

    status = {
        "job_name": batch_job.name,
        "state": state_name,
        "is_complete": state_name in COMPLETED_STATES,
    }

    # Add batch stats if available
    if hasattr(batch_job, 'batch_stats') and batch_job.batch_stats:
        stats = batch_job.batch_stats
        status["batch_stats"] = {
            "total_request_count": getattr(stats, 'total_request_count', None),
            "succeeded_request_count": getattr(stats, 'succeeded_request_count', None),
            "failed_request_count": getattr(stats, 'failed_request_count', None),
        }

    # Add destination file if available
    if hasattr(batch_job, 'dest') and batch_job.dest:
        if hasattr(batch_job.dest, 'file_name'):
            status["result_file"] = batch_job.dest.file_name

    return status


def download_results(
    job_name: str,
    output_path: Path,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Download results from a completed batch job.

    Args:
        job_name: Batch job name
        output_path: Output path for results
        api_key: Optional API key

    Returns:
        Download information
    """
    client = get_client(api_key)
    batch_job = client.batches.get(name=job_name)

    state_name = batch_job.state.name if hasattr(batch_job.state, 'name') else str(batch_job.state)

    if state_name != "JOB_STATE_SUCCEEDED":
        raise ValueError(f"Job not succeeded: {state_name}")

    if not hasattr(batch_job, 'dest') or not batch_job.dest:
        raise ValueError("No destination file available")

    result_file = batch_job.dest.file_name

    print(f"Downloading results from {result_file}...")

    # Download file content
    content = client.files.download(file=result_file)

    # Write to output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Handle different content types
    if isinstance(content, bytes):
        output_path.write_bytes(content)
    elif hasattr(content, 'read'):
        output_path.write_bytes(content.read())
    else:
        output_path.write_text(str(content), encoding="utf-8")

    return {
        "job_name": job_name,
        "result_file": result_file,
        "output_path": str(output_path),
        "downloaded_at": datetime.now().isoformat(),
    }


def monitor_job(
    job_name: str,
    output_path: Path,
    poll_interval: int = 60,
    timeout: int = 86400,  # 24 hours
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Monitor a batch job until completion and download results.

    Args:
        job_name: Batch job name
        output_path: Output path for results
        poll_interval: Seconds between status checks
        timeout: Maximum wait time in seconds
        api_key: Optional API key

    Returns:
        Final job information
    """
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise TimeoutError(f"Job monitoring timed out after {timeout}s")

        status = get_job_status(job_name, api_key)
        state = status["state"]

        # Print progress
        timestamp = datetime.now().strftime("%H:%M:%S")
        progress = ""
        if "batch_stats" in status:
            stats = status["batch_stats"]
            succeeded = stats.get("succeeded_request_count", 0) or 0
            total = stats.get("total_request_count", 0) or 0
            if total > 0:
                progress = f" ({succeeded}/{total} requests)"

        print(f"[{timestamp}] Job state: {state}{progress}")

        if status["is_complete"]:
            break

        time.sleep(poll_interval)

    # Download results if succeeded
    if state == "JOB_STATE_SUCCEEDED":
        print("Job succeeded, downloading results...")
        download_info = download_results(job_name, output_path, api_key)
        status.update(download_info)
    else:
        print(f"Job ended with state: {state}")

    return status


def list_jobs(
    api_key: Optional[str] = None,
    limit: int = 20,
) -> list:
    """
    List recent batch jobs.

    Args:
        api_key: Optional API key
        limit: Maximum number of jobs to list

    Returns:
        List of job summaries
    """
    client = get_client(api_key)

    jobs = []
    for batch_job in client.batches.list(config=types.ListBatchJobsConfig(page_size=limit)):
        state_name = batch_job.state.name if hasattr(batch_job.state, 'name') else str(batch_job.state)
        job_info = {
            "name": batch_job.name,
            "display_name": getattr(batch_job, 'display_name', None),
            "state": state_name,
        }
        jobs.append(job_info)
        if len(jobs) >= limit:
            break

    return jobs


def cmd_submit(args: argparse.Namespace) -> None:
    """Handle submit command."""
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    job_info = submit_batch_job(
        input_path=input_path,
        display_name=args.display_name,
        model=args.model,
    )

    print(f"\nJob submitted successfully:")
    print(f"  Job name: {job_info['job_name']}")
    print(f"  Display name: {job_info['display_name']}")
    print(f"  State: {job_info['state']}")
    print(f"\nTo monitor: python -m app.tools.batch.job_manager monitor --job-name {job_info['job_name']} --output results.jsonl")

    # Save job info
    if args.job_file:
        job_file = Path(args.job_file)
        job_file.parent.mkdir(parents=True, exist_ok=True)
        job_file.write_text(json.dumps(job_info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJob info saved to: {job_file}")


def cmd_monitor(args: argparse.Namespace) -> None:
    """Handle monitor command."""
    output_path = Path(args.output)

    result = monitor_job(
        job_name=args.job_name,
        output_path=output_path,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )

    print(f"\nJob monitoring complete:")
    print(f"  Final state: {result['state']}")
    if "output_path" in result:
        print(f"  Results saved to: {result['output_path']}")


def cmd_status(args: argparse.Namespace) -> None:
    """Handle status command."""
    status = get_job_status(args.job_name)
    print(json.dumps(status, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    """Handle list command."""
    jobs = list_jobs(limit=args.limit)
    print(f"Recent batch jobs ({len(jobs)}):")
    for job in jobs:
        name = job.get("display_name") or job.get("name", "unknown")
        print(f"  - {job['name']}: {name} [{job['state']}]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gemini Batch API job manager"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Submit command
    submit_parser = subparsers.add_parser("submit", help="Submit a batch job")
    submit_parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input batch requests JSONL file"
    )
    submit_parser.add_argument(
        "--display-name", "-n",
        required=True,
        help="Display name for the job"
    )
    submit_parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Target model (default: gemini-2.0-flash)"
    )
    submit_parser.add_argument(
        "--job-file",
        default=None,
        help="Save job info to this file"
    )

    # Monitor command
    monitor_parser = subparsers.add_parser("monitor", help="Monitor job and download results")
    monitor_parser.add_argument(
        "--job-name", "-j",
        required=True,
        help="Batch job name"
    )
    monitor_parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output path for results"
    )
    monitor_parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Polling interval in seconds (default: 60)"
    )
    monitor_parser.add_argument(
        "--timeout",
        type=int,
        default=86400,
        help="Timeout in seconds (default: 86400 = 24h)"
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Check job status")
    status_parser.add_argument(
        "--job-name", "-j",
        required=True,
        help="Batch job name"
    )

    # List command
    list_parser = subparsers.add_parser("list", help="List recent jobs")
    list_parser.add_argument(
        "--limit", "-l",
        type=int,
        default=20,
        help="Maximum jobs to list (default: 20)"
    )

    args = parser.parse_args()

    if args.command == "submit":
        cmd_submit(args)
    elif args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
