"""
Batch processing tools for worldbook import.

This package provides tools for converting worldbook JSON files
(e.g., SillyTavern Lorebook format) into graph data using
Gemini Batch API for cost-efficient processing.

Workflow:
1. lorebook_prep: Parse Lorebook JSON into processable format
2. global_summary: Generate global entity summary (1M context)
3. request_generator: Generate Batch API request JSONL
4. job_manager: Submit/monitor/download batch jobs
5. result_processor: Process results and merge graphs
"""
