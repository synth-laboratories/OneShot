#!/usr/bin/env python3
"""
Re-bench Banking77: Compare baseline performance with/without agent patch.

Usage:
    python re_bench_compare.py <run_dir> [--output re_bench.txt]
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import shutil
import sys
import asyncio
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Callable
from dataclasses import dataclass
from datetime import datetime

# Try to import tiktoken for token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# Try to load .env file for API keys
env_candidates = [
    Path.cwd() / ".env",
    Path(__file__).parent.parent / ".env",
]

try:
    from dotenv import load_dotenv
    for env_file in env_candidates:
        if env_file.exists():
            load_dotenv(env_file, override=False)
            break
except ImportError:
    pass

# Optional import of structured LM scorer
LM_SCORER_AVAILABLE = False
LM_SCORER_ERROR = None
try:
    scorer_module_path = Path(__file__).resolve().parent.parent / "src" / "synth_bench" / "evaluation"
    sys.path.insert(0, str(scorer_module_path))
    from lm_rubric_scorer_structured import LMRubricScorerStructured  # type: ignore
    LM_SCORER_AVAILABLE = True
except Exception as import_err:
    LM_SCORER_AVAILABLE = False
    LM_SCORER_ERROR = str(import_err)


# Model pricing lookup table (from monorepo/backend/app/routes/prompt_learning/shared/model_pricing.py)
MODEL_PRICES: Dict[str, Dict[str, Dict[str, float]]] = {
    "openai": {
        "gpt-5": {"input": 0.00000125, "output": 0.00001000},
        "gpt-5-mini": {"input": 0.00000025, "output": 0.00000200},
        "gpt-5-nano": {"input": 0.00000005, "output": 0.00000040},
        "gpt-4.1": {"input": 0.00000200, "output": 0.00000800},
        "gpt-4.1-mini": {"input": 0.00000040, "output": 0.00000160},
        "gpt-4.1-nano": {"input": 0.00000010, "output": 0.00000040},
        "gpt-4o-mini": {"input": 0.00000015, "output": 0.00000060},
        "gpt-4o": {"input": 0.00000250, "output": 0.00001000},
    },
    "groq": {
        "openai/gpt-oss-20b": {"input": 0.000000075, "output": 0.000000300},
        "openai/gpt-oss-120b": {"input": 0.000000150, "output": 0.000000600},
        "moonshotai/kimi-k2-0905": {"input": 0.000001000, "output": 0.000003000},
        "meta/llama-guard-4-12b": {"input": 0.000000200, "output": 0.000000200},
        "qwen/qwen3-32b": {"input": 0.000000290, "output": 0.000000590},
        "meta/llama-3.3-70b-versatile": {"input": 0.000000590, "output": 0.000000790},
        "meta/llama-3.1-8b-instant": {"input": 0.000000050, "output": 0.000000080},
    },
    "google": {
        "gemini-2.5-pro": {"input": 0.00000125, "output": 0.00001000},
        "gemini-2.5-pro-gt200k": {"input": 0.00000250, "output": 0.00001500},
        "gemini-2.5-flash": {"input": 0.00000030, "output": 0.00000250},
        "gemini-2.5-flash-lite": {"input": 0.00000010, "output": 0.00000040},
    },
}


def _get_tiktoken_encoding(model: str) -> Any:
    """Get tiktoken encoding for a model."""
    if not TIKTOKEN_AVAILABLE:
        return None
    
    try:
        # Map model names to tiktoken encodings
        # Default to cl100k_base (GPT-4, GPT-3.5) for most models
        if "gpt-4" in model.lower() or "gpt-3.5" in model.lower() or "gpt-5" in model.lower():
            return tiktoken.get_encoding("cl100k_base")
        elif "gpt-3" in model.lower():
            return tiktoken.get_encoding("p50k_base")
        else:
            # Default to cl100k_base for unknown models
            return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _count_tokens(text: str, encoding: Any = None) -> int:
    """Count tokens in text using tiktoken or fallback to character heuristic."""
    if encoding:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass
    
    # Fallback: ~4 characters per token heuristic
    return max(1, int(len(text) / 4))


def _extract_text_from_content(content: Any) -> str:
    """Extract text from message content (can be string, list, or dict)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    if isinstance(content, dict):
        return content.get("text") or content.get("content") or str(content)
    return str(content) if content else ""


class DockerReaper:
    """Tracks and cleans up Docker containers created during evaluation."""
    
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.container_names: List[str] = []
        self.container_ids: List[str] = []
        self.container_prefix = f"re-bench-{run_id}"
    
    def track_container(self, name: Optional[str] = None) -> str:
        """Generate a unique container name and track it."""
        if name is None:
            # Generate unique name with timestamp
            timestamp = int(time.time() * 1000)  # milliseconds
            name = f"{self.container_prefix}-{timestamp}"
        self.container_names.append(name)
        return name
    
    def cleanup(self, verbose: bool = False):
        """Find and kill all tracked containers and any matching our prefix."""
        containers_to_kill = []
        
        # Add explicitly tracked containers
        containers_to_kill.extend(self.container_names)
        containers_to_kill.extend(self.container_ids)
        
        # Find all containers matching our prefix (in case we missed tracking some)
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={self.container_prefix}", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    container_name = line.strip()
                    if container_name and container_name not in containers_to_kill:
                        containers_to_kill.append(container_name)
        except Exception as e:
            if verbose:
                print(f"Warning: Could not list containers: {e}")
        
        # Kill and remove containers
        killed_count = 0
        for container in containers_to_kill:
            if not container:
                continue
            try:
                # Try to stop the container first (graceful)
                subprocess.run(
                    ["docker", "stop", container],
                    capture_output=True,
                    timeout=5,
                )
                # Then remove it
                subprocess.run(
                    ["docker", "rm", "-f", container],
                    capture_output=True,
                    timeout=5,
                )
                killed_count += 1
                if verbose:
                    print(f"  Cleaned up container: {container}")
            except Exception as e:
                if verbose:
                    print(f"  Warning: Could not clean up container {container}: {e}")
        
        if killed_count > 0:
            print(f"🧹 Cleaned up {killed_count} Docker container(s)")
        elif verbose:
            print("  No containers to clean up")


def get_model_cost(provider: str, model: str, input_tokens: int, output_tokens: int, 
                   cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Calculate cost in USD using pricing lookup table.
    
    Args:
        provider: Provider name (e.g., "openai", "groq", "google")
        model: Model identifier
        input_tokens: Input tokens (non-cached)
        output_tokens: Output tokens
        cache_read_tokens: Cached input tokens (typically cheaper or free)
        cache_write_tokens: Cache write tokens (typically free)
    
    Returns:
        Total cost in USD
    """
    provider_lower = provider.lower()
    model_lower = model.lower()
    
    # Try exact match first
    if provider_lower in MODEL_PRICES and model_lower in MODEL_PRICES[provider_lower]:
        rates = MODEL_PRICES[provider_lower][model_lower]
        # Cached tokens are typically free or much cheaper - assume free for now
        # Non-cached input tokens + output tokens
        cost = (input_tokens * rates["input"]) + (output_tokens * rates["output"])
        return cost
    
    # Try case-insensitive match
    if provider_lower in MODEL_PRICES:
        for model_key, rates in MODEL_PRICES[provider_lower].items():
            if model_key.lower() == model_lower:
                cost = (input_tokens * rates["input"]) + (output_tokens * rates["output"])
                return cost
    
    # Unknown model - return 0
    return 0.0


def get_task_name_from_run(run_dir: Path) -> Optional[str]:
    """Extract task name/ID from run directory metadata or artifacts.
    
    Tries multiple sources:
    1. metadata.json in run_dir
    2. tb_meta.json in artifacts
    3. Task directory name from path structure
    
    Returns:
        Task name/ID (e.g., "re-bench-banking77" or "banking77") or None
    """
    # Try metadata.json first
    metadata_path = run_dir / "metadata.json"
    if metadata_path.exists():
        try:
            with open(metadata_path) as f:
                metadata = json.load(f)
                task_id = metadata.get("task_id")
                if task_id:
                    return task_id
        except Exception:
            pass
    
    # Try to find task directory from artifacts
    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.exists():
        # Look for tb_meta.json in artifacts
        tb_meta_path = artifacts_dir / "tb_meta.json"
        if tb_meta_path.exists():
            try:
                with open(tb_meta_path) as f:
                    task_meta = json.load(f)
                    task_id = task_meta.get("task_id")
                    if task_id:
                        return task_id
            except Exception:
                pass
    
    # Try to infer from directory structure
    # Run dir is typically: data/runs/<run_id>/
    # Task dir is typically: data/tasks/prepared/<task_id>/
    if "runs" in run_dir.parts:
        runs_idx = run_dir.parts.index("runs")
        if runs_idx > 0:
            base_path = Path(*run_dir.parts[:runs_idx])
            tasks_dir = base_path / "tasks" / "prepared"
            if tasks_dir.exists():
                # Try to find task by matching run metadata
                for task_dir in tasks_dir.iterdir():
                    if task_dir.is_dir():
                        meta_path = task_dir / "tb_meta.json"
                        if meta_path.exists():
                            try:
                                with open(meta_path) as f:
                                    task_meta = json.load(f)
                                    # Could match on repo URL, commit, etc.
                                    return task_meta.get("task_id")
                            except Exception:
                                pass
    
    return None


def extract_baseline_task_name(task_id: Optional[str]) -> Optional[str]:
    """Extract baseline task name from task ID.
    
    Converts "re-bench-banking77" -> "banking77"
    Or extracts from other task ID formats.
    
    Args:
        task_id: Full task ID (e.g., "re-bench-banking77")
        
    Returns:
        Baseline task name (e.g., "banking77") or None
    """
    if not task_id:
        return None
    
    # Remove "re-bench-" prefix if present
    if task_id.startswith("re-bench-"):
        return task_id.replace("re-bench-", "", 1)
    
    # Could handle other formats here
    return task_id


def extract_run_metadata(run_dir: Path) -> Dict[str, Any]:
    """Extract run metadata from metadata.json and codex-config files."""
    metadata = {}
    
    # Load metadata.json
    metadata_json = run_dir / "metadata.json"
    if metadata_json.exists():
        try:
            with open(metadata_json) as f:
                meta_data = json.load(f)
                metadata.update(meta_data)
        except Exception:
            pass
    
    # Extract task name from task_id or task_dir
    if "task_id" in metadata:
        metadata["task_name"] = metadata["task_id"]
    elif "task_dir" in metadata:
        # Extract task name from task_dir path
        task_dir = Path(metadata["task_dir"])
        metadata["task_name"] = task_dir.name
    else:
        # Try to get task name from run directory
        task_id = get_task_name_from_run(run_dir)
        if task_id:
            metadata["task_name"] = task_id
    
    # Load codex-config.toml (pre-run or regular)
    artifacts_dir = run_dir / "artifacts"
    config_files = [
        artifacts_dir / "codex-config.pre-run.toml",
        artifacts_dir / "codex-config.toml",
    ]
    
    for config_file in config_files:
        if config_file.exists():
            try:
                import tomllib
                with open(config_file, "rb") as f:
                    config_data = tomllib.load(f)
                    # Extract model info
                    if "model" in config_data:
                        metadata["model"] = config_data["model"]
                    if "model_provider" in config_data:
                        metadata["model_provider"] = config_data["model_provider"]
                    # Extract reasoning settings
                    if "model_providers" in config_data:
                        for provider_name, provider_config in config_data["model_providers"].items():
                            if isinstance(provider_config, dict):
                                if "model_reasoning_effort" in provider_config:
                                    metadata["reasoning_effort"] = provider_config["model_reasoning_effort"]
                                if "reasoning_summaries" in provider_config:
                                    metadata["reasoning_summaries"] = provider_config["reasoning_summaries"]
                    break
            except ImportError:
                # Fallback: try parsing as TOML manually (basic parsing)
                try:
                    config_text = config_file.read_text()
                    for line in config_text.splitlines():
                        line = line.strip()
                        if line.startswith("model ="):
                            metadata["model"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("model_provider ="):
                            metadata["model_provider"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif "model_reasoning_effort" in line:
                            metadata["reasoning_effort"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif "reasoning_summaries" in line:
                            metadata["reasoning_summaries"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                except Exception:
                    pass
            except Exception:
                pass
    
    return metadata


def extract_trace_metrics(run_dir: Path) -> Dict[str, Any]:
    """Extract metrics from v3 traces (SQLite or JSON) or codex logs.
    
    Returns metrics including:
    - input_tokens, output_tokens (cached vs non-cached)
    - tool_calls_count
    - time_taken_seconds
    - cost_usd
    """
    metrics = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls_count": 0,
        "time_taken_seconds": 0.0,
        "cost_usd": 0.0,
        "llm_calls": 0,
    }
    
    artifacts_dir = run_dir / "artifacts"
    
    # Try to load from SQLite trace database first
    trace_db_path = artifacts_dir / "clean_traces.sqlite3"
    if trace_db_path.exists():
        try:
            conn = sqlite3.connect(str(trace_db_path))
            conn.row_factory = sqlite3.Row
            
            # Query LLM call records from events table
            # Look for LMCAISEvent or similar events with token usage
            # Note: cost_usd is stored as INTEGER (cents), call_records contains detailed token info
            cursor = conn.execute("""
                SELECT 
                    event_type,
                    model_name,
                    provider,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    cost_usd,
                    latency_ms,
                    call_records,
                    event_time,
                    created_at
                FROM events
                WHERE event_type = 'cais'
                ORDER BY event_time ASC, created_at ASC
            """)
            
            rows = cursor.fetchall()
            for row in rows:
                metrics["llm_calls"] += 1
                
                # Try to extract from call_records first (most detailed)
                call_records_data = None
                if row["call_records"]:
                    try:
                        call_records_data = json.loads(row["call_records"]) if isinstance(row["call_records"], str) else row["call_records"]
                    except Exception:
                        pass
                
                # Extract token counts
                input_toks = 0
                output_toks = 0
                cache_read = 0
                cache_write = 0
                
                if call_records_data and isinstance(call_records_data, list):
                    # Extract from call records (most accurate)
                    for call_record in call_records_data:
                        if isinstance(call_record, dict):
                            usage = call_record.get("usage") or {}
                            if isinstance(usage, dict):
                                input_toks += usage.get("input_tokens", 0) or 0
                                output_toks += usage.get("output_tokens", 0) or 0
                                cache_read += usage.get("cache_read_tokens", 0) or 0
                                cache_write += usage.get("cache_write_tokens", 0) or 0
                            
                            # Count tool calls
                            tool_calls = call_record.get("output_tool_calls") or call_record.get("tool_calls") or []
                            if isinstance(tool_calls, list):
                                metrics["tool_calls_count"] += len(tool_calls)
                else:
                    # Fallback to direct columns
                    input_toks = row["input_tokens"] or 0
                    output_toks = row["output_tokens"] or 0
                
                metrics["input_tokens"] += input_toks
                metrics["output_tokens"] += output_toks
                metrics["cache_read_tokens"] += cache_read
                metrics["cache_write_tokens"] += cache_write
                
                # Cost (cost_usd is stored as INTEGER cents, convert to dollars)
                if row["cost_usd"]:
                    metrics["cost_usd"] += row["cost_usd"] / 100.0  # Convert cents to dollars
                elif row["model_name"] and row["provider"]:
                    # Calculate cost using pricing table
                    non_cached_input = max(0, input_toks - cache_read)
                    cost = get_model_cost(
                        row["provider"] or "openai",
                        row["model_name"] or "",
                        non_cached_input,
                        output_toks,
                        cache_read,
                        cache_write
                    )
                    metrics["cost_usd"] += cost
                
                # Time tracking (latency_ms is in milliseconds)
                if row["latency_ms"]:
                    metrics["time_taken_seconds"] += row["latency_ms"] / 1000.0
            
            conn.close()
            
            # If we got metrics from SQLite, return them
            if metrics["llm_calls"] > 0:
                return metrics
        except Exception as e:
            print(f"Warning: Could not extract metrics from SQLite trace: {e}")
    
    # Fallback: Try JSON trace file
    trace_json_path = artifacts_dir / "clean_session_trace.json"
    if trace_json_path.exists():
        try:
            with open(trace_json_path) as f:
                trace_data = json.load(f)
            
            # Extract from event_history or similar structure
            events = trace_data.get("event_history", [])
            for event in events:
                if event.get("event_type") in ["cais", "lm", "lmcais"]:
                    metrics["llm_calls"] += 1
                    
                    # Extract token usage
                    usage = event.get("usage") or {}
                    if isinstance(usage, dict):
                        metrics["input_tokens"] += usage.get("input_tokens", 0)
                        metrics["output_tokens"] += usage.get("output_tokens", 0)
                        metrics["cache_read_tokens"] += usage.get("cache_read_tokens", 0)
                        metrics["cache_write_tokens"] += usage.get("cache_write_tokens", 0)
                    
                    # Extract tool calls
                    tool_calls = event.get("tool_calls") or event.get("output_tool_calls") or []
                    if isinstance(tool_calls, list):
                        metrics["tool_calls_count"] += len(tool_calls)
                    
                    # Calculate cost
                    model = event.get("model_name", "")
                    provider = event.get("provider", "openai")
                    input_toks = usage.get("input_tokens", 0)
                    output_toks = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_tokens", 0)
                    cache_write = usage.get("cache_write_tokens", 0)
                    non_cached_input = max(0, input_toks - cache_read)
                    
                    if event.get("cost_usd"):
                        metrics["cost_usd"] += event["cost_usd"]
                    else:
                        metrics["cost_usd"] += get_model_cost(
                            provider, model, non_cached_input, output_toks, cache_read, cache_write
                        )
            
            if metrics["llm_calls"] > 0:
                return metrics
        except Exception as e:
            print(f"Warning: Could not extract metrics from JSON trace: {e}")
    
    # Fallback: Try codex session JSONL files
    codex_sessions_dir = artifacts_dir / "codex-sessions"
    if codex_sessions_dir.exists():
        try:
            model = None
            provider = "openai"  # Default
            first_timestamp = None
            last_timestamp = None
            encoding = None
            
            # First pass: collect all events and determine model
            events = []
            for jsonl_file in codex_sessions_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                            events.append(event)
                            
                            event_type = event.get("type", "")
                            payload = event.get("payload", {})
                            
                            # Extract model from turn_context events
                            if event_type == "turn_context":
                                model_str = payload.get("model", "")
                                if model_str:
                                    # Parse model string like "gpt-5-nano" or "groq:llama-3.3-70b-versatile"
                                    if ":" in model_str:
                                        provider, model = model_str.split(":", 1)
                                    else:
                                        provider = "openai"
                                        model = model_str
                        except json.JSONDecodeError:
                            continue
                        except Exception:
                            continue
            
            # Second pass: extract token counts from API-reported totals and count other metrics
            last_token_count = None
            for event in events:
                event_type = event.get("type", "")
                payload = event.get("payload", {})
                
                # Extract timestamps for duration calculation
                timestamp_str = event.get("timestamp", "")
                if timestamp_str:
                    try:
                        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if first_timestamp is None:
                            first_timestamp = ts
                        last_timestamp = ts
                    except Exception:
                        pass
                
                # Extract token counts from token_count events (these are cumulative totals from API)
                # These include full conversation history, so they're the most accurate
                if event_type == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info", {})
                    if info:
                        total_usage = info.get("total_token_usage", {})
                        if total_usage:
                            # Use the last (most recent) token count event - these are cumulative totals
                            last_token_count = total_usage
                
                # Count tool calls and LLM calls
                if event_type == "response_item":
                    item_type = payload.get("type", "")
                    if item_type == "function_call":
                        metrics["tool_calls_count"] += 1
                    elif item_type == "message":
                        role = payload.get("role", "")
                        if role == "assistant":
                            metrics["llm_calls"] += 1
            
            # Use the cumulative token counts from API (most accurate - includes full conversation history)
            if last_token_count:
                metrics["input_tokens"] = last_token_count.get("input_tokens", 0) or 0
                metrics["output_tokens"] = last_token_count.get("output_tokens", 0) or 0
                metrics["cache_read_tokens"] = last_token_count.get("cached_input_tokens", 0) or 0
                # Note: reasoning_output_tokens are included in output_tokens already
            else:
                # Fallback: manual token counting if token_count events not available
                if model:
                    encoding = _get_tiktoken_encoding(model)
                    for event in events:
                        event_type = event.get("type", "")
                        payload = event.get("payload", {})
                        
                        if event_type == "response_item":
                            item_type = payload.get("type", "")
                            
                            if item_type == "message":
                                role = payload.get("role", "")
                                content = payload.get("content", [])
                                text = _extract_text_from_content(content)
                                
                                if role == "user" or role == "system":
                                    tokens = _count_tokens(text, encoding)
                                    metrics["input_tokens"] += tokens
                                elif role == "assistant":
                                    tokens = _count_tokens(text, encoding)
                                    metrics["output_tokens"] += tokens
                            
                            elif item_type == "function_call":
                                name = payload.get("name", "")
                                arguments = payload.get("arguments", "")
                                func_text = f"function_call:{name}({arguments})"
                                tokens = _count_tokens(func_text, encoding)
                                metrics["output_tokens"] += tokens
                            
                            elif item_type == "function_call_output":
                                output = payload.get("output", "")
                                output_text = json.dumps(output) if not isinstance(output, str) else output
                                tokens = _count_tokens(output_text, encoding)
                                metrics["input_tokens"] += tokens
                        
                        elif event_type == "event_msg":
                            msg_type = payload.get("type", "")
                            if msg_type == "user_message":
                                message = payload.get("message", "")
                                tokens = _count_tokens(message, encoding)
                                metrics["input_tokens"] += tokens
                            elif msg_type == "agent_message":
                                message = payload.get("message", "")
                                tokens = _count_tokens(message, encoding)
                                metrics["output_tokens"] += tokens
                            elif msg_type == "agent_reasoning":
                                text = payload.get("text", "")
                                tokens = _count_tokens(text, encoding)
                                metrics["output_tokens"] += tokens
            
            # Calculate duration from timestamps
            if first_timestamp and last_timestamp:
                duration = (last_timestamp - first_timestamp).total_seconds()
                metrics["time_taken_seconds"] = duration
            
            # Calculate cost if we have model info and token counts
            if model and metrics["input_tokens"] > 0:
                # Use cache info if available from token_count events
                cache_read = metrics.get("cache_read_tokens", 0)
                cache_write = metrics.get("cache_write_tokens", 0)
                non_cached_input = max(0, metrics["input_tokens"] - cache_read)
                metrics["cost_usd"] = get_model_cost(
                    provider, model, non_cached_input, metrics["output_tokens"],
                    cache_read, cache_write
                )
            
            # Return metrics if we found any data
            if metrics["llm_calls"] > 0 or metrics["input_tokens"] > 0:
                return metrics
        except Exception as e:
            print(f"Warning: Could not extract metrics from codex sessions: {e}")
            import traceback
            traceback.print_exc()
    
    # Fallback: Try codex logs (basic parsing)
    codex_log = artifacts_dir / "codex-run.log"
    if codex_log.exists():
        try:
            log_text = codex_log.read_text()
            # Simple regex parsing
            for line in log_text.splitlines():
                m = re.search(r"tokens used:\s*(\d+)", line)
                if m:
                    metrics["input_tokens"] += int(m.group(1))  # Rough estimate
                if "FunctionCall:" in line:
                    metrics["tool_calls_count"] += 1
        except Exception:
            pass
    
    return metrics


def extract_patch(run_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extract patch from agent run artifacts. Returns (patch_content, patch_source)."""
    artifacts_dir = run_dir / "artifacts"
    
    # Check diff files in priority order
    diff_candidates = [
        ("diff.patch", "diff.patch"),
        ("container_git_diff_from_baseline.patch", "container_git_diff_from_baseline.patch"),
        ("container_git_diff.patch", "container_git_diff.patch"),
    ]
    
    for filename, source_name in diff_candidates:
        diff_path = artifacts_dir / filename
        if diff_path.exists() and diff_path.stat().st_size > 0:
            try:
                content = diff_path.read_text()
                # Validate it looks like a patch
                if content.strip() and (
                    content.startswith("diff --git") 
                    or content.startswith("---") 
                    or "+++" in content
                ):
                    return content, source_name
            except Exception as e:
                print(f"Warning: Could not read {diff_path}: {e}")
                continue
    
    return None, None


def get_baseline_sha(run_dir: Path) -> Optional[str]:
    """Get baseline commit SHA from artifacts."""
    baseline_sha_path = run_dir / "artifacts" / "baseline_sha.txt"
    if baseline_sha_path.exists():
        try:
            sha = baseline_sha_path.read_text().strip()
            if sha:
                return sha
        except Exception as e:
            print(f"Warning: Could not read baseline_sha.txt: {e}")
    return None


def setup_repo(repo_url: str, branch: str, baseline_sha: Optional[str] = None) -> Path:
    """Set up clean synth-ai repository in temp directory."""
    temp_dir = tempfile.mkdtemp(prefix="re_bench_repo_")
    repo_dir = Path(temp_dir)
    
    print(f"Cloning repository to {repo_dir}...")
    # Clone repo with full history if we need to checkout a specific SHA
    if baseline_sha:
        # Full clone to allow checking out specific SHA
        subprocess.run(
            ["git", "clone", "--branch", branch, repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
        )
        print(f"Checking out baseline SHA: {baseline_sha}")
        result = subprocess.run(
            ["git", "checkout", baseline_sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Warning: Could not checkout {baseline_sha}: {result.stderr}")
            print("Trying to fetch the SHA...")
            # Try fetching the SHA first
            fetch_result = subprocess.run(
                ["git", "fetch", "origin", baseline_sha],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            if fetch_result.returncode == 0:
                result = subprocess.run(
                    ["git", "checkout", baseline_sha],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
            if result.returncode != 0:
                print(f"Warning: Could not checkout {baseline_sha} after fetch")
                print("Continuing with HEAD...")
    else:
        # Shallow clone is fine if no specific SHA needed
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
        )
    
    return repo_dir


def run_baseline(
    repo_dir: Path, 
    output_file: Path, 
    split: str = "test",
    seeds: Optional[str] = None,
    model: Optional[str] = None,
    env_file: Optional[Path] = None,
    verbose: bool = False,
    rebuild: bool = False,
    docker_reaper: Optional["DockerReaper"] = None,
    task_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run baseline in Docker container and return results.
    
    Args:
        repo_dir: Repository directory
        output_file: Output file path
        split: Data split (default: "test")
        seeds: Comma-separated seeds
        model: Model name
        env_file: Environment file path
        verbose: Verbose output
        rebuild: Force rebuild Docker image
        docker_reaper: Docker reaper for cleanup
        task_name: Task name for baseline (e.g., "banking77"). If None, defaults to "banking77" for backward compatibility.
    """
    print(f"Running baseline on {split} split in Docker container...")
    if model:
        print(f"Using model: {model}")
    
    # Build Docker image with synth-ai
    # Use a stable image tag so we can reuse the image across runs
    image_tag = "re-bench-baseline:latest"
    
    # Check if image already exists
    check_result = subprocess.run(
        ["docker", "images", "-q", image_tag],
        capture_output=True,
        text=True,
    )
    
    if rebuild and check_result.stdout.strip():
        print(f"Force rebuild requested - removing existing Docker image: {image_tag}")
        subprocess.run(
            ["docker", "rmi", "-f", image_tag],
            capture_output=True,
        )
        need_build = True
    elif check_result.stdout.strip():
        print(f"Using existing Docker image: {image_tag}")
        need_build = False
    else:
        print(f"Building Docker image: {image_tag}")
        print("(This may take a few minutes on first run - installing Rust and synth-ai dependencies)")
        need_build = True
    
    # Create a temporary Dockerfile
    dockerfile_content = """FROM python:3.11-slim

RUN apt-get update && apt-get install -y \\
    git \\
    curl \\
    build-essential \\
    cmake \\
    && rm -rf /var/lib/apt/lists/*

# Install Rust (needed for some synth-ai dependencies)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install uv (which provides uvx)
RUN pip install --no-cache-dir uv

# Pre-install common test tools to cache dependencies
# This avoids downloading dependencies on every test run
RUN uvx --version || true
RUN uvx pytest --version || true
RUN uvx ruff --version || true
RUN uvx ty --version || true

# Pre-install synth-ai to cache dependencies (much faster on subsequent runs)
# This downloads all dependencies once during build, not on every run
RUN uvx synth-ai --version || echo "synth-ai will be installed on first use"

WORKDIR /workspace
"""
    
    dockerfile_path = repo_dir / "Dockerfile.baseline"
    dockerfile_path.write_text(dockerfile_content)
    
    try:
        # Build image (only if it doesn't exist)
        if need_build:
            print("Building Docker image (this may take several minutes)...")
            build_result = subprocess.run(
                ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path), str(repo_dir)],
                capture_output=not verbose,  # Show output if verbose
                text=True,
            )
            
            if build_result.returncode != 0:
                if verbose:
                    print(build_result.stderr)
                raise RuntimeError(f"Docker build failed:\n{build_result.stderr}")
            print("✅ Docker image built successfully")
        
        # Use provided task_name or default to "banking77" for backward compatibility
        baseline_task_name = task_name or "banking77"
        
        # Prepare command
        cmd = [
            "uvx", "synth-ai", "baseline", baseline_task_name,
            "--split", split,
            "--output", "/output/results.json",
        ]
        
        if seeds:
            cmd.extend(["--seeds", seeds])
        
        if model:
            cmd.extend(["--model", model])
        
        if verbose:
            cmd.append("--verbose")
        
        # Prepare environment variables
        env_vars = {}
        
        # Load from env_file if provided
        if env_file and env_file.exists():
            try:
                from dotenv import dotenv_values
                env_vars.update({k: v for k, v in dotenv_values(env_file).items() if v})
            except ImportError:
                # Fallback: simple parsing
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            env_vars[key.strip()] = value.strip().strip('"').strip("'")
        
        # Add current environment variables (for API keys)
        for key in ["GROQ_API_KEY", "OPENAI_API_KEY", "SYNTH_API_KEY"]:
            if key in os.environ:
                env_vars[key] = os.environ[key]
        
        # Check for required API keys and warn if missing
        if "GROQ_API_KEY" not in env_vars:
            print("⚠️  WARNING: GROQ_API_KEY not found in environment or env_file!")
            print("   The baseline will fail without a valid API key.")
            print("   Set GROQ_API_KEY environment variable or use --env-file")
        
        if verbose:
            print(f"Passing {len(env_vars)} environment variables to Docker container")
            if env_vars:
                print("  Environment variables:", ", ".join(env_vars.keys()))
        
        # Prepare Docker run command
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{repo_dir}:/workspace",
            "-v", f"{output_file.parent}:/output",
            "-w", "/workspace",
        ]
        
        # Add container name for tracking (if reaper provided)
        if docker_reaper:
            container_name = docker_reaper.track_container()
            docker_cmd.insert(2, "--name")
            docker_cmd.insert(3, container_name)
        
        # Add environment variables
        for key, value in env_vars.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
        
        # Add image and command
        docker_cmd.append(image_tag)
        docker_cmd.extend(cmd)
        
        print("Running baseline in Docker container...")
        
        # Run in Docker
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
        )
        
        if result.returncode != 0:
            print("Docker run failed. Full output:")
            if result.stdout:
                print("STDOUT:", result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            raise RuntimeError(
                f"Baseline run failed in Docker:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )
        
        if verbose and result.stdout:
            print(result.stdout)
        
        # Load results
        results_path = output_file.parent / "results.json"
        if not results_path.exists():
            raise FileNotFoundError(f"Baseline output file not created: {results_path}")
        
        with open(results_path) as f:
            results = json.load(f)
        
        # Check for errors and print summary
        aggregate = results.get("aggregate_metrics", {})
        success_rate = aggregate.get("success_rate", 0.0)
        total_tasks = aggregate.get("total_tasks", 0)
        
        if success_rate == 0.0 and total_tasks > 0:
            print(f"⚠️  WARNING: All {total_tasks} tasks failed!")
            # Print first few errors as examples
            task_results = results.get("results", [])
            error_samples = []
            for task in task_results[:3]:  # Show first 3 errors
                error_msg = task.get("error", "Unknown error")
                if error_msg:
                    # Truncate long error messages
                    if len(error_msg) > 100:
                        error_msg = error_msg[:100] + "..."
                    error_samples.append(f"  - {error_msg}")
            
            if error_samples:
                print("Sample errors:")
                for err in error_samples:
                    print(err)
        
        # Move results to expected location
        if results_path != output_file:
            output_file.write_text(json.dumps(results, indent=2))
            results_path.unlink()
        
        return results
        
    finally:
        # Cleanup Dockerfile
        if dockerfile_path.exists():
            dockerfile_path.unlink()
        
        # Optionally remove image (commented out to allow reuse)
        # subprocess.run(["docker", "rmi", image_tag], capture_output=True)


def apply_patch(repo_dir: Path, patch_content: str) -> bool:
    """Apply patch to repository. Returns True if successful."""
    print("Applying patch...")
    
    # Write patch to temp file
    patch_file = repo_dir / "agent.patch"
    patch_file.write_text(patch_content)
    
    # Try clean apply first
    result = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        print("Patch applied successfully (clean)")
        return True
    
    # Try 3-way merge
    print("Clean apply failed, trying 3-way merge...")
    result = subprocess.run(
        ["git", "apply", "--3way", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        print("Patch applied successfully (3-way)")
        return True
    
    print(f"ERROR: Patch application failed:\n{result.stderr}")
    return False


def get_patch_summary(repo_dir: Path) -> Dict[str, Any]:
    """Get summary of changes in patch."""
    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return {"files_changed": 0, "summary": "Unable to get patch summary"}
    
    # Parse git diff --stat output
    lines = result.stdout.strip().split("\n")
    if not lines:
        return {"files_changed": 0, "summary": "No changes"}
    
    # Last line has totals
    last_line = lines[-1]
    parts = last_line.split()
    
    files_changed = 0
    lines_added = 0
    lines_deleted = 0
    
    for line in lines[:-1]:
        if "|" in line:
            files_changed += 1
    
    if len(parts) >= 4:
        try:
            lines_added = int(parts[-2].replace("+", ""))
            lines_deleted = int(parts[-1].replace("-", ""))
        except (ValueError, IndexError):
            pass
    
    return {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "summary": result.stdout.strip(),
    }


def load_task_metadata(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Load task metadata from task directory.
    
    Tries to find task directory by:
    1. Extracting task_id from run metadata
    2. Searching common task directory locations
    3. Falling back to "re-bench-banking77" for backward compatibility
    """
    # Try to get task ID from run directory
    task_id = get_task_name_from_run(run_dir)
    
    # Try to find task directory
    task_dir = None
    
    # Try relative to run directory first
    if task_id:
        task_dir = run_dir.parent.parent / "tasks" / "prepared" / task_id
        if not task_dir.exists():
            task_dir = Path("data/tasks/prepared") / task_id
    
    # Fall back to banking77 for backward compatibility
    if not task_dir or not task_dir.exists():
        task_dir = run_dir.parent.parent / "tasks" / "prepared" / "re-bench-banking77"
        if not task_dir.exists():
            task_dir = Path("data/tasks/prepared/re-bench-banking77")
    
    meta_path = task_dir / "tb_meta.json"
    if not meta_path.exists():
        return None
    
    try:
        with open(meta_path) as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load task metadata: {e}")
        return None


def collect_repo_artifacts_for_lm(repo_dir: Path) -> Dict[str, Any]:
    """Collect a small set of relevant files from the repo for LM evaluation."""
    artifacts: Dict[str, Any] = {"files": {}}
    relevant_files = [
        "README.md",
        "readme.md",
        "README.rst",
        "CONTRIBUTING.md",
    ]
    for file_name in relevant_files:
        file_path = repo_dir / file_name
        if file_path.exists():
            try:
                artifacts["files"][file_name] = file_path.read_text()
            except Exception:
                pass
    return artifacts


async def run_lm_evaluation(repo_dir: Path, task_meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run LLM-based rubric evaluation on the repository."""
    if not LM_SCORER_AVAILABLE:
        return None
    
    # Check for API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️  WARNING: OPENAI_API_KEY not found - skipping LLM evaluation")
        return None
    
    try:
        artifacts = collect_repo_artifacts_for_lm(repo_dir)
        scorer = LMRubricScorerStructured(model="gpt-5-nano", temperature=0.1)
        lm_result = await scorer.evaluate_task(task_meta, artifacts)
        
        return {
            "weighted_score": lm_result.weighted_score,
            "rubric_scores": [
                {
                    "rubric_id": s.rubric_id,
                    "score": s.score,
                    "reasoning": s.reasoning,
                    "evidence": s.evidence,
                    "suggestions": getattr(s, "suggestions", None),
                }
                for s in lm_result.rubric_scores
            ],
            "summary": lm_result.summary,
            "metadata": lm_result.metadata,
        }
    except Exception as e:
        print(f"⚠️  WARNING: LLM evaluation failed: {e}")
        return None


@dataclass
class PassPassTest:
    """Abstraction for pass->pass tests that run in Docker."""
    name: str
    command: List[str]
    timeout: int = 10
    docker_image: str = "re-bench-baseline:latest"
    parse_output: Optional[Callable[[str], Dict[str, Any]]] = None
    
    def run(self, repo_dir: Path, verbose: bool = False, docker_reaper: Optional["DockerReaper"] = None) -> Dict[str, Any]:
        """Run the test in Docker and return results."""
        print(f"Running {self.name} in Docker...")
        if verbose:
            print(f"  Command: {' '.join(self.command)}")
            print(f"  Timeout: {self.timeout}s")
        
        # Create cache directory for uv (persists across runs)
        cache_dir = Path.home() / ".cache" / "uv"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{repo_dir}:/workspace",
            "-v", f"{cache_dir}:/root/.cache/uv",  # Mount uv cache
            "-w", "/workspace",
        ]
        
        # Add container name for tracking (if reaper provided)
        if docker_reaper:
            container_name = docker_reaper.track_container()
            docker_cmd.insert(2, "--name")
            docker_cmd.insert(3, container_name)
        
        docker_cmd.append(self.docker_image)
        docker_cmd.extend(self.command)
        
        start_time = time.time()
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            elapsed = time.time() - start_time
            output = result.stdout + result.stderr
            
            # Use custom parser if provided, otherwise use default
            if self.parse_output:
                parsed = self.parse_output(output)
            else:
                parsed = {}
            
            success = result.returncode == 0
            
            if verbose or not success:
                status = "✅ PASS" if success else "❌ FAIL"
                print(f"{self.name}: {status} (took {elapsed:.2f}s, exit code: {result.returncode})")
                
                if not success:
                    # Show error output
                    print(f"  Error output ({len(output)} chars):")
                    # Show last 500 chars (most relevant)
                    error_snippet = output[-500:] if len(output) > 500 else output
                    for line in error_snippet.split("\n"):
                        if line.strip():
                            print(f"    {line}")
            
            return {
                "success": success,
                "returncode": result.returncode,
                "output": output,  # Store full output for debugging
                "timeout": False,
                "elapsed_seconds": elapsed,
                **parsed,
            }
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            print(f"⚠️  {self.name} timed out after {self.timeout}s (elapsed: {elapsed:.2f}s)")
            print(f"  Command: {' '.join(self.command)}")
            print("  This may indicate the test is slow or hanging. Consider increasing timeout.")
            
            # Note: subprocess.TimeoutExpired doesn't provide partial output easily
            # The process is killed, so we can't get stdout/stderr
            
            return {
                "success": True,  # Don't penalize for timeout
                "returncode": 0,
                "output": f"Timeout after {self.timeout}s",
                "timeout": True,
                "elapsed_seconds": elapsed,
            }


def _parse_pytest_output(output: str) -> Dict[str, Any]:
    """Parse pytest output to extract test counts."""
    passed = output.count(" PASSED")
    failed = output.count(" FAILED")
    error = output.count(" ERROR")
    total = passed + failed + error
    return {
        "passed": passed,
        "failed": failed,
        "error": error,
        "total": total,
    }


def _parse_ruff_output(output: str) -> Dict[str, Any]:
    """Parse ruff output to extract error/violation count."""
    import re
    
    # Look for "Found X errors" or "Found X error"
    found_pattern = r"Found (\d+) error"
    match = re.search(found_pattern, output)
    if match:
        error_count = int(match.group(1))
    else:
        # Fallback: count non-empty lines that aren't ruff headers
        error_count = len([line for line in output.split("\n") 
                          if line.strip() 
                          and not line.startswith("ruff")
                          and not line.startswith("Downloading")
                          and not line.startswith("Installed")
                          and "error" in line.lower()])
    
    return {
        "error_count": error_count,
        "violation_count": error_count,  # Alias for clarity
    }


def _parse_type_check_output(output: str) -> Dict[str, Any]:
    """Parse type checker output to extract diagnostic/violation count."""
    import re
    
    # Look for "Found X diagnostics" or "Found X diagnostic"
    found_pattern = r"Found (\d+) diagnostic"
    match = re.search(found_pattern, output)
    if match:
        diagnostic_count = int(match.group(1))
    else:
        # Fallback: count lines with "error[" pattern
        diagnostic_count = len([line for line in output.split("\n") 
                               if "error[" in line.lower()])
    
    return {
        "error_count": diagnostic_count,
        "violation_count": diagnostic_count,  # Alias for clarity
        "diagnostic_count": diagnostic_count,
    }


# Define pass->pass tests
PYTEST_TEST = PassPassTest(
    name="pytest",
    command=["uv", "run", "pytest", "tests/unit", "-v", "--tb=short"],
    timeout=10,
    parse_output=_parse_pytest_output,
)

RUFF_TEST = PassPassTest(
    name="ruff",
    command=["uvx", "ruff", "check", "."],
    timeout=10,
    parse_output=_parse_ruff_output,
)

TY_CHECK_TEST = PassPassTest(
    name="ty_check",
    command=["uvx", "ty", "check"],
    timeout=10,
    parse_output=_parse_type_check_output,
)


def run_pytest(repo_dir: Path, verbose: bool = False, timeout: int = 10, docker_reaper: Optional["DockerReaper"] = None) -> Dict[str, Any]:
    """Run pytest unit tests in Docker and return results."""
    # Check if pyproject.toml exists - if so, sync dependencies first for faster runs
    # The uv cache is mounted, so dependencies should be fast to install
    pyproject_exists = (repo_dir / "pyproject.toml").exists()
    
    if pyproject_exists:
        # Use a shell command to sync first, then run pytest
        # uv sync will use cached packages from the mounted cache directory
        # This is much faster than uv run which creates a new venv each time
        test = PassPassTest(
            name="pytest",
            command=["sh", "-c", "uv sync --quiet && uv run pytest tests/unit -v --tb=short"],
            timeout=timeout,
            parse_output=_parse_pytest_output,
        )
    else:
        # Fallback to direct uv run
        test = PassPassTest(
            name="pytest",
            command=["uv", "run", "pytest", "tests/unit", "-v", "--tb=short"],
            timeout=timeout,
            parse_output=_parse_pytest_output,
        )
    
    result = test.run(repo_dir, verbose, docker_reaper=docker_reaper)
    # Ensure pytest-specific logic: success requires no failures or errors
    if not result.get("timeout"):
        result["success"] = result["returncode"] == 0 and result.get("failed", 0) == 0 and result.get("error", 0) == 0
    return result


def run_ruff_check(repo_dir: Path, verbose: bool = False, timeout: int = 10, docker_reaper: Optional["DockerReaper"] = None) -> Dict[str, Any]:
    """Run ruff check in Docker and return results."""
    test = PassPassTest(
        name="ruff",
        command=["uvx", "ruff", "check", "."],
        timeout=timeout,
        parse_output=_parse_ruff_output,
    )
    return test.run(repo_dir, verbose, docker_reaper=docker_reaper)


def run_type_check(repo_dir: Path, verbose: bool = False, timeout: int = 10, docker_reaper: Optional["DockerReaper"] = None) -> Dict[str, Any]:
    """Run type checking in Docker and return results."""
    # Use ty check
    test = PassPassTest(
        name=TY_CHECK_TEST.name,
        command=TY_CHECK_TEST.command,
        timeout=timeout,
        docker_image=TY_CHECK_TEST.docker_image,
        parse_output=TY_CHECK_TEST.parse_output,
    )
    result = test.run(repo_dir, verbose, docker_reaper=docker_reaper)
    result["tool"] = "ty_check"
    return result


def run_code_quality_checks(repo_dir: Path, verbose: bool = False, timeout: int = 10, docker_reaper: Optional["DockerReaper"] = None) -> Dict[str, Any]:
    """Run all code quality checks (pytest, ruff, type check) in Docker with timeout."""
    results = {
        "pytest": run_pytest(repo_dir, verbose, timeout, docker_reaper=docker_reaper),
        "ruff": run_ruff_check(repo_dir, verbose, timeout, docker_reaper=docker_reaper),
        "type_check": run_type_check(repo_dir, verbose, timeout, docker_reaper=docker_reaper),
    }
    
    # Calculate overall score: all must pass for 1.0, otherwise 0.0
    # Timeouts are treated as pass (not penalized)
    all_passed = all(r["success"] for r in results.values())
    
    # Print summary
    print("\nCode Quality Checks Summary:")
    for check_name, result in results.items():
        if result.get("timeout"):
            status = "⏱️ TIMEOUT"
            elapsed = result.get("elapsed_seconds", 0)
            print(f"  {check_name}: {status} (took {elapsed:.2f}s before timeout)")
        elif result["success"]:
            status = "✅ PASS"
            elapsed = result.get("elapsed_seconds", 0)
            if check_name in ["ruff", "type_check"]:
                violations = result.get("violation_count", 0)
                print(f"  {check_name}: {status} (0 violations, took {elapsed:.2f}s)")
            else:
                print(f"  {check_name}: {status} (took {elapsed:.2f}s)")
        else:
            status = "❌ FAIL"
            elapsed = result.get("elapsed_seconds", 0)
            returncode = result.get("returncode", -1)
            if check_name in ["ruff", "type_check"]:
                violations = result.get("violation_count", 0)
                print(f"  {check_name}: {status} ({violations} violations, took {elapsed:.2f}s, exit code: {returncode})")
            else:
                print(f"  {check_name}: {status} (took {elapsed:.2f}s, exit code: {returncode})")
            # Show error details
            output = result.get("output", "")
            if output:
                print("    Error details:")
                # Show first 10 lines of error
                error_lines = [line for line in output.split("\n") if line.strip()][:10]
                for line in error_lines:
                    print(f"      {line}")
                if len(output.split("\n")) > 10:
                    print(f"      ... (truncated, {len(output)} total chars)")
    
    return {
        **results,
        "all_passed": all_passed,
        "score": 1.0 if all_passed else 0.0,
    }


def compare_code_quality(
    baseline_checks: Dict[str, Any],
    patched_checks: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare code quality checks before and after patch."""
    # Check for degradations
    # For pytest: degradation if tests fail when they passed before
    # For ruff/ty_check: degradation if violation count increases
    degradations = []
    violation_comparisons = {}
    
    for check_name in ["pytest", "ruff", "type_check"]:
        baseline_check = baseline_checks.get(check_name, {})
        patched_check = patched_checks.get(check_name, {})
        
        baseline_success = baseline_check.get("success", True)
        patched_success = patched_check.get("success", True)
        baseline_timeout = baseline_check.get("timeout", False)
        patched_timeout = patched_check.get("timeout", False)
        
        if check_name == "pytest":
            # For pytest: degradation if baseline passed but patched failed (not timeout)
            if baseline_success and not patched_success and not patched_timeout:
                degradations.append(check_name)
                violation_comparisons[check_name] = {
                    "baseline": "PASS",
                    "patched": "FAIL",
                    "degraded": True,
                }
            else:
                violation_comparisons[check_name] = {
                    "baseline": "PASS" if baseline_success else "FAIL",
                    "patched": "PASS" if patched_success else "FAIL",
                    "degraded": False,
                }
        else:
            # For ruff and ty_check: compare violation counts
            baseline_violations = baseline_check.get("violation_count", 0)
            patched_violations = patched_check.get("violation_count", 0)
            
            # If timeout, use a high number to indicate unknown
            if baseline_timeout:
                baseline_violations = None
            if patched_timeout:
                patched_violations = None
            
            violation_comparisons[check_name] = {
                "baseline_violations": baseline_violations,
                "patched_violations": patched_violations,
                "degraded": False,
            }
            
            # Degradation if violations increased (and we have valid counts)
            if baseline_violations is not None and patched_violations is not None:
                if patched_violations > baseline_violations:
                    degradations.append(check_name)
                    violation_comparisons[check_name]["degraded"] = True
            elif baseline_violations is None and patched_violations is not None and patched_violations > 0:
                # Baseline timed out but patched has violations - can't compare, assume no degradation
                pass
            elif baseline_violations is not None and patched_violations is None:
                # Patched timed out - can't compare, assume no degradation
                pass
    
    # Calculate pass->pass score
    # Score is 1.0 if no degradations, 0.0 if any degradation
    # Timeouts are treated as pass (not penalized)
    pass_pass_score = 1.0 if not degradations else 0.0
    
    return {
        "baseline_all_passed": baseline_checks.get("all_passed", True),
        "patched_all_passed": patched_checks.get("all_passed", True),
        "degradations": degradations,
        "pass_pass_score": pass_pass_score,
        "violation_comparisons": violation_comparisons,
        "baseline_checks": baseline_checks,
        "patched_checks": patched_checks,
    }


def query_rewards(
    reward_types: List[Dict[str, Any]],
    reward_type: Optional[str] = None,
    subtype: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query rewards by type and/or subtype.
    
    Examples:
        # Get all baseline deltas
        query_rewards(rewards, reward_type="baseline_delta")
        
        # Get all qualitative rubrics
        query_rewards(rewards, reward_type="qualitative_rubric")
        
        # Get specific rubric subtype
        query_rewards(rewards, reward_type="qualitative_rubric", subtype="code_quality")
        
        # Get all quantitative metrics
        query_rewards(rewards, reward_type="quantitative_metric")
        
        # Get cost specifically
        query_rewards(rewards, reward_type="quantitative_metric", subtype="cost_usd")
        
        # Get all pass->pass tests
        query_rewards(rewards, reward_type="pass_pass_test")
        
        # Get combined score
        query_rewards(rewards, reward_type="combined_score")
    """
    results = reward_types
    
    if reward_type:
        results = [r for r in results if r.get("type") == reward_type]
    
    if subtype is not None:
        results = [r for r in results if r.get("subtype") == subtype]
    
    return results


def build_reward_types(
    comparison: Dict[str, Any],
    combined_scores: Optional[Dict[str, Any]],
    baseline_rubrics: Optional[Dict[str, Any]],
    patched_rubrics: Optional[Dict[str, Any]],
    code_quality_comparison: Optional[Dict[str, Any]],
    trace_metrics: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build standardized reward types for querying and analysis.
    
    Returns a list of reward objects with standardized types and subtypes.
    Each reward has:
    - type: Main reward category (e.g., "baseline_delta", "qualitative_rubric", "pass_pass_test", "quantitative_metric", "combined_score")
    - subtype: Optional subtype for more specific querying (e.g., rubric_id, test_name, metric_name)
    - value: The reward value (score, metric value, etc.)
    - metadata: Additional context (weights, units, etc.)
    """
    rewards = []
    
    # 1. Baseline Delta (improvement/regression)
    if combined_scores:
        baseline_delta = combined_scores.get("baseline_delta", 0.0)
        baseline_delta_normalized = combined_scores.get("baseline_delta_normalized", 0.0)
        rewards.append({
            "type": "baseline_delta",
            "subtype": None,
            "value": baseline_delta,
            "normalized_value": baseline_delta_normalized,
            "metadata": {
                "baseline_score": comparison.get("baseline_score", 0.0),
                "patched_score": comparison.get("patched_score", 0.0),
                "absolute_improvement": comparison.get("absolute_improvement", 0.0),
                "relative_lift_percent": comparison.get("relative_lift_percent", 0.0),
                "weight": combined_scores.get("baseline_delta_weight", 0.0),
            }
        })
    
    # 2. Qualitative Rubrics (LLM-based evaluation)
    if patched_rubrics and patched_rubrics.get("rubric_scores"):
        for rubric in patched_rubrics["rubric_scores"]:
            rewards.append({
                "type": "qualitative_rubric",
                "subtype": rubric.get("rubric_id", "unknown"),
                "value": rubric.get("score", 0.0),
                "metadata": {
                    "weight": rubric.get("weight", 0.0),
                    "reasoning": rubric.get("reasoning", ""),
                    "evidence": rubric.get("evidence", ""),
                    "suggestions": rubric.get("suggestions"),
                }
            })
        
        # Also add overall weighted rubric score
        if patched_rubrics.get("weighted_score") is not None:
            rewards.append({
                "type": "qualitative_rubric",
                "subtype": "weighted_average",
                "value": patched_rubrics["weighted_score"],
                "metadata": {
                    "summary": patched_rubrics.get("summary", ""),
                }
            })
    
    # 3. Pass->Pass Tests (code quality checks)
    if code_quality_comparison:
        baseline_checks = code_quality_comparison.get("baseline_checks", {})
        patched_checks = code_quality_comparison.get("patched_checks", {})
        
        for test_name in ["pytest", "ruff", "type_check"]:
            baseline_test = baseline_checks.get(test_name, {})
            patched_test = patched_checks.get(test_name, {})
            
            # Score: 1.0 if pass->pass, 0.0 if degradation
            baseline_passed = baseline_test.get("success", True) and not baseline_test.get("timeout", False)
            patched_passed = patched_test.get("success", True) and not patched_test.get("timeout", False)
            score = 1.0 if (baseline_passed and patched_passed) else 0.0
            
            metadata = {
                "baseline_success": baseline_passed,
                "patched_success": patched_passed,
                "baseline_timeout": baseline_test.get("timeout", False),
                "patched_timeout": patched_test.get("timeout", False),
                "baseline_elapsed_seconds": baseline_test.get("elapsed_seconds", 0.0),
                "patched_elapsed_seconds": patched_test.get("elapsed_seconds", 0.0),
            }
            
            # Add violation counts for ruff and type_check
            if test_name in ["ruff", "type_check"]:
                baseline_violations = baseline_test.get("violation_count", 0)
                patched_violations = patched_test.get("violation_count", 0)
                metadata.update({
                    "baseline_violations": baseline_violations,
                    "patched_violations": patched_violations,
                    "violation_delta": patched_violations - baseline_violations,
                })
            
            rewards.append({
                "type": "pass_pass_test",
                "subtype": test_name,
                "value": score,
                "metadata": metadata,
            })
        
        # Overall pass->pass score
        pass_pass_score = code_quality_comparison.get("pass_pass_score", 1.0)
        rewards.append({
            "type": "pass_pass_test",
            "subtype": "overall",
            "value": pass_pass_score,
            "metadata": {
                "degradations": code_quality_comparison.get("degradations", []),
                "weight": combined_scores.get("pass_pass_weight", 0.0) if combined_scores else 0.0,
            }
        })
    
    # 4. Quantitative Metrics (from trace metrics)
    if trace_metrics:
        quantitative_metrics = {
            "cost_usd": ("cost", "USD"),
            "time_taken_seconds": ("time", "seconds"),
            "input_tokens": ("tokens", "count"),
            "output_tokens": ("tokens", "count"),
            "cache_read_tokens": ("tokens", "count"),
            "cache_write_tokens": ("tokens", "count"),
            "tool_calls_count": ("tool_calls", "count"),
            "llm_calls": ("llm_calls", "count"),
        }
        
        for metric_key, (category, unit) in quantitative_metrics.items():
            value = trace_metrics.get(metric_key)
            if value is not None:
                rewards.append({
                    "type": "quantitative_metric",
                    "subtype": metric_key,
                    "value": value,
                    "metadata": {
                        "category": category,
                        "unit": unit,
                    }
                })
    
    # 5. Combined Score (final weighted score)
    if combined_scores and combined_scores.get("combined_score") is not None:
        rewards.append({
            "type": "combined_score",
            "subtype": None,
            "value": combined_scores["combined_score"],
            "metadata": {
                "baseline_delta_weight": combined_scores.get("baseline_delta_weight", 0.0),
                "rubric_weight": combined_scores.get("rubric_weight", 0.0),
                "pass_pass_weight": combined_scores.get("pass_pass_weight", 0.0),
            }
        })
    
    return rewards


def combine_scores(
    baseline_score: float,
    patched_score: float,
    baseline_rubrics: Optional[Dict[str, Any]],
    patched_rubrics: Optional[Dict[str, Any]],
    code_quality_comparison: Optional[Dict[str, Any]] = None,
    baseline_delta_weight: float = 0.4,
    rubric_weight: float = 0.4,
    pass_pass_weight: float = 0.2,
) -> Dict[str, Any]:
    """Combine baseline delta with rubric scores into a final score."""
    # Calculate baseline delta score (normalized to 0-1)
    baseline_delta = patched_score - baseline_score
    # Normalize: assume max improvement is +1.0 (100% improvement)
    # Negative deltas are penalized, positive are rewarded
    baseline_delta_normalized = max(0.0, min(1.0, 0.5 + baseline_delta))
    
    # Get rubric scores
    baseline_rubric_score = baseline_rubrics.get("weighted_score", 0.0) if baseline_rubrics else 0.0
    patched_rubric_score = patched_rubrics.get("weighted_score", 0.0) if patched_rubrics else 0.0
    
    # Use patched rubric score (what the agent achieved)
    rubric_score = patched_rubric_score
    
    # Get pass->pass score
    pass_pass_score = code_quality_comparison.get("pass_pass_score", 1.0) if code_quality_comparison else 1.0
    
    # Normalize weights (ensure they sum to 1.0)
    total_weight = baseline_delta_weight + rubric_weight + pass_pass_weight
    if total_weight != 1.0:
        baseline_delta_weight /= total_weight
        rubric_weight /= total_weight
        pass_pass_weight /= total_weight
    
    # Combine scores
    combined_score = (
        (baseline_delta_normalized * baseline_delta_weight) +
        (rubric_score * rubric_weight) +
        (pass_pass_score * pass_pass_weight)
    )
    
    return {
        "baseline_delta": baseline_delta,
        "baseline_delta_normalized": baseline_delta_normalized,
        "baseline_rubric_score": baseline_rubric_score,
        "patched_rubric_score": patched_rubric_score,
        "rubric_score": rubric_score,
        "pass_pass_score": pass_pass_score,
        "combined_score": combined_score,
        "baseline_delta_weight": baseline_delta_weight,
        "rubric_weight": rubric_weight,
        "pass_pass_weight": pass_pass_weight,
    }


def compare_results(baseline: Dict, patched: Dict) -> Dict[str, Any]:
    """Compare baseline and patched results."""
    baseline_metrics = baseline.get("aggregate_metrics", {})
    patched_metrics = patched.get("aggregate_metrics", {})
    
    baseline_score = baseline_metrics.get("mean_outcome_reward", 0.0)
    patched_score = patched_metrics.get("mean_outcome_reward", 0.0)
    
    absolute_improvement = patched_score - baseline_score
    relative_lift = (absolute_improvement / baseline_score * 100) if baseline_score > 0 else 0.0
    
    # Per-seed comparison if available
    baseline_results = baseline.get("results", [])
    patched_results = patched.get("results", [])
    
    seeds_improved = 0
    seeds_regressed = 0
    seeds_unchanged = 0
    
    if baseline_results and patched_results:
        # Create lookup by seed
        baseline_by_seed = {r["seed"]: r.get("outcome_reward", 0.0) for r in baseline_results}
        patched_by_seed = {r["seed"]: r.get("outcome_reward", 0.0) for r in patched_results}
        
        all_seeds = set(baseline_by_seed.keys()) | set(patched_by_seed.keys())
        
        for seed in all_seeds:
            baseline_reward = baseline_by_seed.get(seed, 0.0)
            patched_reward = patched_by_seed.get(seed, 0.0)
            
            if patched_reward > baseline_reward:
                seeds_improved += 1
            elif patched_reward < baseline_reward:
                seeds_regressed += 1
            else:
                seeds_unchanged += 1
    
    # Determine status
    if relative_lift > 0.1:  # > 0.1% improvement
        status = "✅ Improvement"
    elif relative_lift < -0.1:  # > 0.1% regression
        status = "❌ Regression"
    else:
        status = "⚖️  No Change"
    
    return {
        "baseline_score": baseline_score,
        "patched_score": patched_score,
        "absolute_improvement": absolute_improvement,
        "relative_lift_percent": relative_lift,
        "status": status,
        "seeds_improved": seeds_improved,
        "seeds_regressed": seeds_regressed,
        "seeds_unchanged": seeds_unchanged,
        "baseline_metrics": baseline_metrics,
        "patched_metrics": patched_metrics,
    }


def generate_report(
    comparison: Dict[str, Any],
    run_dir: Path,
    patch_source: Optional[str],
    baseline_sha: Optional[str],
    patch_summary: Dict[str, Any],
    baseline_rubrics: Optional[Dict[str, Any]] = None,
    patched_rubrics: Optional[Dict[str, Any]] = None,
    combined_scores: Optional[Dict[str, Any]] = None,
    code_quality_comparison: Optional[Dict[str, Any]] = None,
    trace_metrics: Optional[Dict[str, Any]] = None,
    evaluation_time_seconds: Optional[float] = None,
    run_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate formatted report."""
    baseline_metrics = comparison["baseline_metrics"]
    patched_metrics = comparison["patched_metrics"]
    
    # Extract task name from run directory
    task_id = get_task_name_from_run(run_dir) or "re-bench-banking77"
    task_display_name = task_id.replace("re-bench-", "").title() if task_id.startswith("re-bench-") else task_id.title()
    
    lines = []
    lines.append("=" * 60)
    lines.append(f"Re-Bench {task_display_name}: Baseline Comparison Results")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Run ID: {run_dir.name}")
    lines.append(f"Task: {task_id}")
    if patch_source:
        lines.append(f"Patch Source: {patch_source}")
    if baseline_sha:
        lines.append(f"Baseline SHA: {baseline_sha}")
    
    # Add run metadata
    if run_metadata:
        lines.append("")
        lines.append("RUN METADATA:")
        if run_metadata.get("model"):
            model_str = run_metadata["model"]
            if run_metadata.get("model_provider"):
                model_str = f"{run_metadata['model_provider']}:{model_str}"
            lines.append(f"  Model: {model_str}")
        if run_metadata.get("reasoning_effort"):
            lines.append(f"  Reasoning Effort: {run_metadata['reasoning_effort']}")
        if run_metadata.get("reasoning_summaries"):
            lines.append(f"  Reasoning Summaries: {run_metadata['reasoning_summaries']}")
        if run_metadata.get("start_time"):
            lines.append(f"  Start Time: {run_metadata['start_time']}")
    
    lines.append("")
    lines.append("BASELINE (Without Patch):")
    lines.append(f"  Mean Outcome Reward: {comparison['baseline_score']:.4f} ({comparison['baseline_score']*100:.2f}%)")
    lines.append(f"  Success Rate: {baseline_metrics.get('success_rate', 0.0)*100:.2f}%")
    lines.append(f"  Total Tasks: {baseline_metrics.get('total_tasks', 0)}")
    lines.append(f"  Successful Tasks: {baseline_metrics.get('successful_tasks', 0)}")
    lines.append(f"  Failed Tasks: {baseline_metrics.get('failed_tasks', 0)}")
    lines.append("")
    lines.append("WITH PATCH (Agent's Changes):")
    lines.append(f"  Mean Outcome Reward: {comparison['patched_score']:.4f} ({comparison['patched_score']*100:.2f}%)")
    lines.append(f"  Success Rate: {patched_metrics.get('success_rate', 0.0)*100:.2f}%")
    lines.append(f"  Total Tasks: {patched_metrics.get('total_tasks', 0)}")
    lines.append(f"  Successful Tasks: {patched_metrics.get('successful_tasks', 0)}")
    lines.append(f"  Failed Tasks: {patched_metrics.get('failed_tasks', 0)}")
    lines.append("")
    lines.append("IMPROVEMENT:")
    lines.append(f"  Absolute: {comparison['absolute_improvement']:+.4f} ({comparison['absolute_improvement']*100:+.2f} percentage points)")
    lines.append(f"  Relative: {comparison['relative_lift_percent']:+.2f}% lift")
    lines.append(f"  Status: {comparison['status']}")
    lines.append("")
    
    # Add LLM Rubric Scores and Combined Score Table
    if patched_rubrics or combined_scores:
        lines.append("=" * 60)
        lines.append("SCORING BREAKDOWN")
        lines.append("=" * 60)
        lines.append("")
        
        # Baseline Delta Row
        if combined_scores:
            delta = combined_scores["baseline_delta"]
            delta_pct = delta * 100
            delta_sign = "+" if delta >= 0 else ""
            lines.append(f"Baseline Delta: {comparison['baseline_score']:.0%} → {comparison['patched_score']:.0%} ({delta_sign}{delta_pct:.2f}pp)")
            lines.append(f"  Normalized Score: {combined_scores['baseline_delta_normalized']:.0%}")
            lines.append("")
        
        # LLM Rubric Scores Table
        if patched_rubrics and patched_rubrics.get("rubric_scores"):
            lines.append("LLM Rubric Scores (Patched Version):")
            lines.append("-" * 60)
            lines.append(f"{'Rubric':<25} {'Weight':<10} {'Score':<10} {'Status':<10}")
            lines.append("-" * 60)
            
            # Get task metadata for rubric info
            task_meta = load_task_metadata(run_dir)
            rubrics_dict = {}
            if task_meta and task_meta.get("evaluation", {}).get("rubrics"):
                for r in task_meta["evaluation"]["rubrics"]:
                    rubrics_dict[r["id"]] = r
            
            for rs in patched_rubrics["rubric_scores"]:
                rid = rs.get("rubric_id", "?")
                rscore = rs.get("score", 0.0)
                rweight = rubrics_dict.get(rid, {}).get("weight", 0.0) if rubrics_dict else 0.0
                
                if rscore >= 1.0:
                    status = "✅ PASS"
                elif rscore >= 0.5:
                    status = "⚠️ PARTIAL"
                else:
                    status = "❌ FAIL"
                
                lines.append(f"{rid:<25} {rweight:.0%}        {rscore:.0%}        {status:<10}")
            
            lines.append("-" * 60)
            if patched_rubrics.get("weighted_score") is not None:
                lines.append(f"{'Weighted Average':<25} {'':<10} {patched_rubrics['weighted_score']:.0%}        {'':<10}")
            lines.append("")
        
        # Code Quality Checks (Pass->Pass)
        if code_quality_comparison:
            lines.append("Code Quality Checks (Pass->Pass):")
            lines.append("-" * 60)
            lines.append(f"{'Check':<20} {'Baseline':<20} {'Patched':<20} {'Status':<12}")
            lines.append("-" * 60)
            
            baseline_checks = code_quality_comparison.get("baseline_checks", {})
            patched_checks = code_quality_comparison.get("patched_checks", {})
            degradations = code_quality_comparison.get("degradations", [])
            violation_comparisons = code_quality_comparison.get("violation_comparisons", {})
            
            for check_name in ["pytest", "ruff", "type_check"]:
                baseline_check = baseline_checks.get(check_name, {})
                patched_check = patched_checks.get(check_name, {})
                
                baseline_success = baseline_check.get("success", True)
                patched_success = patched_check.get("success", True)
                baseline_timeout = baseline_check.get("timeout", False)
                patched_timeout = patched_check.get("timeout", False)
                
                # Format baseline status
                if baseline_timeout:
                    baseline_status = "⏱️ TIMEOUT"
                elif check_name == "pytest":
                    baseline_status = "✅ PASS" if baseline_success else "❌ FAIL"
                else:
                    # For ruff/ty_check, show violation count
                    baseline_violations = baseline_check.get("violation_count", 0)
                    if baseline_success:
                        baseline_status = "✅ PASS (0 violations)"
                    else:
                        baseline_status = f"❌ {baseline_violations} violations"
                
                # Format patched status
                if patched_timeout:
                    patched_status = "⏱️ TIMEOUT"
                elif check_name == "pytest":
                    patched_status = "✅ PASS" if patched_success else "❌ FAIL"
                else:
                    # For ruff/ty_check, show violation count
                    patched_violations = patched_check.get("violation_count", 0)
                    if patched_success:
                        patched_status = "✅ PASS (0 violations)"
                    else:
                        patched_status = f"❌ {patched_violations} violations"
                
                # Determine overall status
                if check_name in degradations:
                    status = "⚠️ DEGRADED"
                elif baseline_timeout or patched_timeout:
                    status = "⏱️ TIMEOUT"
                elif check_name == "pytest":
                    if baseline_success and patched_success:
                        status = "✅ PASS->PASS"
                    else:
                        status = "❌ FAIL"
                else:
                    # For ruff/ty_check, check violation comparison
                    comp = violation_comparisons.get(check_name, {})
                    if comp.get("degraded", False):
                        status = "⚠️ DEGRADED"
                    elif comp.get("baseline_violations") is not None and comp.get("patched_violations") is not None:
                        baseline_v = comp["baseline_violations"]
                        patched_v = comp["patched_violations"]
                        if patched_v <= baseline_v:
                            status = f"✅ OK ({baseline_v}→{patched_v})"
                        else:
                            status = f"⚠️ DEGRADED ({baseline_v}→{patched_v})"
                    else:
                        status = "✅ OK"
                
                lines.append(f"{check_name:<20} {baseline_status:<20} {patched_status:<20} {status:<12}")
            
            lines.append("-" * 60)
            pass_pass_score = code_quality_comparison.get("pass_pass_score", 1.0)
            lines.append(f"{'Pass->Pass Score':<20} {'':<20} {'':<20} {pass_pass_score:.0%}")
            lines.append("")
        
        # Combined Score
        if combined_scores:
            lines.append("=" * 60)
            lines.append("FINAL COMBINED SCORE")
            lines.append("=" * 60)
            lines.append(f"Baseline Delta (weight: {combined_scores['baseline_delta_weight']:.0%}): {combined_scores['baseline_delta_normalized']:.0%}")
            if patched_rubrics:
                lines.append(f"LLM Rubric Score (weight: {combined_scores['rubric_weight']:.0%}): {combined_scores['rubric_score']:.0%}")
            if code_quality_comparison:
                lines.append(f"Pass->Pass Score (weight: {combined_scores['pass_pass_weight']:.0%}): {combined_scores['pass_pass_score']:.0%}")
            lines.append("-" * 60)
            lines.append(f"COMBINED SCORE: {combined_scores['combined_score']:.0%}")
            lines.append("")
        
        # Trace Metrics (Quantitative)
        if trace_metrics and trace_metrics.get("llm_calls", 0) > 0:
            lines.append("=" * 60)
            lines.append("QUANTITATIVE METRICS (from v3 traces)")
            lines.append("=" * 60)
            lines.append("")
            
            # Extract metrics
            input_tokens = trace_metrics.get("input_tokens", 0)
            output_tokens = trace_metrics.get("output_tokens", 0)
            cache_read = trace_metrics.get("cache_read_tokens", 0)
            non_cached_input = max(0, input_tokens - cache_read)
            tool_calls = trace_metrics.get("tool_calls_count", 0)
            llm_calls = trace_metrics.get("llm_calls", 0)
            time_seconds = trace_metrics.get("time_taken_seconds", 0.0)
            cost_usd = trace_metrics.get("cost_usd", 0.0)
            
            # Format tokens in millions with 3 decimals
            input_tokens_m = input_tokens / 1_000_000
            output_tokens_m = output_tokens / 1_000_000
            cache_read_m = cache_read / 1_000_000
            non_cached_input_m = non_cached_input / 1_000_000
            
            # Format time in minutes with 2 decimals
            time_minutes = time_seconds / 60.0 if time_seconds > 0 else 0.0
            
            # Create table
            lines.append("Metric                              Value")
            lines.append("-" * 60)
            lines.append(f"{'Input tokens (total)':<35} {input_tokens_m:.3f}M")
            if cache_read > 0:
                lines.append(f"{'Input tokens (cached)':<35} {cache_read_m:.3f}M")
                lines.append(f"{'Input tokens (non-cached)':<35} {non_cached_input_m:.3f}M")
            lines.append(f"{'Output tokens':<35} {output_tokens_m:.3f}M")
            lines.append(f"{'LLM calls':<35} {llm_calls}")
            lines.append(f"{'Tool calls':<35} {tool_calls}")
            if time_seconds > 0:
                lines.append(f"{'Time taken':<35} {time_minutes:.2f} min")
            else:
                lines.append(f"{'Time taken':<35} N/A")
            lines.append(f"{'Cost (USD)':<35} ${cost_usd:.4f}")
            lines.append("")
    
    lines.append("PER-SEED COMPARISON:")
    lines.append(f"  Seeds improved: {comparison['seeds_improved']}")
    lines.append(f"  Seeds regressed: {comparison['seeds_regressed']}")
    lines.append(f"  Seeds unchanged: {comparison['seeds_unchanged']}")
    lines.append("")
    lines.append("PATCH SUMMARY:")
    lines.append(f"  Files changed: {patch_summary.get('files_changed', 0)}")
    lines.append(f"  Lines added: {patch_summary.get('lines_added', 0)}")
    lines.append(f"  Lines deleted: {patch_summary.get('lines_deleted', 0)}")
    if patch_summary.get('summary'):
        lines.append("")
        lines.append("  Change summary:")
        for line in patch_summary['summary'].split('\n')[:10]:  # Limit to 10 lines
            lines.append(f"    {line}")
    lines.append("")
    
    # Evaluation time
    if evaluation_time_seconds is not None:
        evaluation_minutes = evaluation_time_seconds / 60.0
        lines.append("=" * 60)
        lines.append(f"EVALUATION TIME: {evaluation_minutes:.2f} minutes ({evaluation_time_seconds:.1f} seconds)")
        lines.append("=" * 60)
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compare baseline performance with/without agent patch"
    )
    parser.add_argument("run_dir", type=Path, help="Path to run directory")
    parser.add_argument(
        "--output", 
        type=Path, 
        help="Output file path (default: re_bench_comparison.txt in run_dir)"
    )
    parser.add_argument(
        "--split",
        default="test",
        help="Data split to use (default: test). Use 'train' for more seeds."
    )
    parser.add_argument(
        "--seeds",
        help="Comma-separated seeds to evaluate (overrides split defaults). Default: 0-9 (10 seeds)"
    )
    parser.add_argument(
        "--num-seeds",
        type=int,
        default=10,
        help="Number of seeds to evaluate if --seeds not specified (default: 10)"
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/synth-laboratories/synth-ai",
        help="Repository URL (default: synth-ai)"
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Repository branch (default: main)"
    )
    parser.add_argument(
        "--model",
        help="Model identifier to use (overrides baseline default, e.g., 'groq:llama-3.3-70b-versatile')"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file to load (for API keys like GROQ_API_KEY)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--keep-repo",
        action="store_true",
        help="Keep temporary repository directory (for debugging)"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild Docker image (useful after Dockerfile changes)"
    )
    
    args = parser.parse_args()
    
    # Track evaluation start time
    evaluation_start_time = time.time()
    
    # Initialize Docker reaper to track and clean up containers
    docker_reaper = DockerReaper(args.run_dir.name)
    
    if not args.run_dir.exists():
        print(f"Error: Run directory not found: {args.run_dir}")
        sys.exit(1)
    
    # Extract run metadata
    run_metadata = extract_run_metadata(args.run_dir)
    
    # Auto-detect .env file if not provided
    if not args.env_file:
        # Check current directory first
        current_dir_env = Path(".env")
        if current_dir_env.exists():
            args.env_file = current_dir_env
            print(f"Auto-detected .env file: {current_dir_env}")
        else:
            # Check run_dir
            run_dir_env = args.run_dir / ".env"
            if run_dir_env.exists():
                args.env_file = run_dir_env
                print(f"Auto-detected .env file: {run_dir_env}")
    
    # Extract patch
    patch_content, patch_source = extract_patch(args.run_dir)
    if not patch_content:
        print("Error: No valid patch found in run artifacts")
        sys.exit(1)
    
    print(f"Found patch: {patch_source}")
    
    # Get baseline SHA
    baseline_sha = get_baseline_sha(args.run_dir)
    if baseline_sha:
        print(f"Baseline SHA: {baseline_sha}")
    
    # Set default seeds if not specified
    seeds_to_use = args.seeds
    if not seeds_to_use:
        seeds_to_use = ",".join(str(i) for i in range(args.num_seeds))
        print(f"Using {args.num_seeds} seeds: {seeds_to_use}")
    
    # Set up repository
    repo_dir = None
    try:
        repo_dir = setup_repo(args.repo_url, args.branch, baseline_sha)
        
        # Run baseline without patch
        baseline_output = repo_dir / "baseline_without_patch.json"
        # Extract task name for baseline
        task_id = get_task_name_from_run(args.run_dir)
        baseline_task_name = extract_baseline_task_name(task_id) if task_id else None
        
        baseline_results = run_baseline(
            repo_dir, 
            baseline_output, 
            split=args.split,
            seeds=seeds_to_use,
            model=args.model,
            env_file=args.env_file,
            verbose=args.verbose,
            rebuild=args.rebuild,
            docker_reaper=docker_reaper,
            task_name=baseline_task_name,
        )
        
        # Apply patch
        if not apply_patch(repo_dir, patch_content):
            print("ERROR: Failed to apply patch. Cannot continue comparison.")
            sys.exit(1)
        
        # Get patch summary
        patch_summary = get_patch_summary(repo_dir)
        
        # Run baseline with patch
        patched_output = repo_dir / "baseline_with_patch.json"
        patched_results = run_baseline(
            repo_dir,
            patched_output,
            split=args.split,
            seeds=seeds_to_use,
            model=args.model,
            env_file=args.env_file,
            verbose=args.verbose,
            rebuild=False,  # Don't rebuild again for patched run
            docker_reaper=docker_reaper,
            task_name=baseline_task_name,
        )
        
        # Clone baseline repo once for code quality checks and LLM evaluation
        # (we need a clean baseline copy since repo_dir has the patch applied)
        baseline_repo_dir = None
        try:
            baseline_repo_dir = setup_repo(args.repo_url, args.branch, baseline_sha)
            
            # Run code quality checks
            print("\nRunning code quality checks...")
            baseline_checks = run_code_quality_checks(baseline_repo_dir, verbose=args.verbose, docker_reaper=docker_reaper)
            print(f"✅ Baseline checks: {'PASSED' if baseline_checks['all_passed'] else 'FAILED'}")
            
            patched_checks = run_code_quality_checks(repo_dir, verbose=args.verbose, docker_reaper=docker_reaper)
            print(f"✅ Patched checks: {'PASSED' if patched_checks['all_passed'] else 'FAILED'}")
            
            code_quality_comparison = compare_code_quality(baseline_checks, patched_checks)
            if code_quality_comparison["degradations"]:
                print(f"⚠️  WARNING: Degradations detected: {', '.join(code_quality_comparison['degradations'])}")
                # Show violation details for ruff/ty_check
                violation_comparisons = code_quality_comparison.get("violation_comparisons", {})
                for check_name in ["ruff", "type_check"]:
                    comp = violation_comparisons.get(check_name, {})
                    if comp.get("degraded", False):
                        baseline_v = comp.get("baseline_violations", "?")
                        patched_v = comp.get("patched_violations", "?")
                        print(f"  {check_name}: violations increased from {baseline_v} to {patched_v}")
            else:
                print("✅ No degradations - all checks pass->pass")
                # Show violation improvements if any
                violation_comparisons = code_quality_comparison.get("violation_comparisons", {})
                for check_name in ["ruff", "type_check"]:
                    comp = violation_comparisons.get(check_name, {})
                    baseline_v = comp.get("baseline_violations")
                    patched_v = comp.get("patched_violations")
                    if baseline_v is not None and patched_v is not None:
                        if patched_v < baseline_v:
                            print(f"  {check_name}: violations improved from {baseline_v} to {patched_v} ✅")
                        elif patched_v == baseline_v and patched_v > 0:
                            print(f"  {check_name}: violations unchanged ({baseline_v})")
            
            # Run LLM rubric evaluations in parallel
            baseline_rubrics = None
            patched_rubrics = None
            task_meta = load_task_metadata(args.run_dir)
            
            if task_meta:
                print("\nRunning LLM rubric evaluation (baseline and patched in parallel)...")
                
                async def run_both_evaluations():
                    """Run both baseline and patched evaluations concurrently."""
                    baseline_task = run_lm_evaluation(baseline_repo_dir, task_meta)
                    patched_task = run_lm_evaluation(repo_dir, task_meta)
                    return await asyncio.gather(baseline_task, patched_task)
                
                baseline_rubrics, patched_rubrics = asyncio.run(run_both_evaluations())
                
                if baseline_rubrics:
                    print(f"✅ Baseline LLM rubric score: {baseline_rubrics['weighted_score']:.0%}")
                if patched_rubrics:
                    print(f"✅ Patched LLM rubric score: {patched_rubrics['weighted_score']:.0%}")
            else:
                print("⚠️  Could not load task metadata - skipping LLM evaluation")
        finally:
            # Cleanup baseline repo clone
            if baseline_repo_dir and not args.keep_repo:
                shutil.rmtree(baseline_repo_dir, ignore_errors=True)
        
        # Extract trace metrics from run directory
        print("\nExtracting trace metrics...")
        trace_metrics = extract_trace_metrics(args.run_dir)
        if trace_metrics["llm_calls"] > 0:
            print(f"✅ Extracted metrics: {trace_metrics['llm_calls']} LLM calls, "
                  f"{trace_metrics['input_tokens']:,} input tokens, "
                  f"{trace_metrics['output_tokens']:,} output tokens, "
                  f"{trace_metrics['tool_calls_count']} tool calls, "
                  f"${trace_metrics['cost_usd']:.4f} cost")
        else:
            print("⚠️  No trace metrics found - metrics will be empty")
        
        # Compare results
        comparison = compare_results(baseline_results, patched_results)
        
        # Combine scores
        baseline_score = comparison["baseline_score"]
        patched_score = comparison["patched_score"]
        combined_scores = combine_scores(
            baseline_score,
            patched_score,
            baseline_rubrics,
            patched_rubrics,
            code_quality_comparison=code_quality_comparison,
            baseline_delta_weight=0.4,
            rubric_weight=0.4,
            pass_pass_weight=0.2,
        )
        
        # Calculate evaluation time
        evaluation_time_seconds = time.time() - evaluation_start_time
        
        # Generate report
        report = generate_report(
            comparison,
            args.run_dir,
            patch_source,
            baseline_sha,
            patch_summary,
            baseline_rubrics=baseline_rubrics,
            patched_rubrics=patched_rubrics,
            combined_scores=combined_scores,
            code_quality_comparison=code_quality_comparison,
            trace_metrics=trace_metrics,
            evaluation_time_seconds=evaluation_time_seconds,
            run_metadata=run_metadata,
        )
        
        # Output report
        print("\n" + report)
        
        # Save to file
        output_path = args.output or (args.run_dir / "re_bench_comparison.txt")
        output_path.write_text(report)
        print(f"\nReport saved to: {output_path}")
        
        # Also save JSON comparison
        json_output = args.run_dir / "re_bench_comparison.json"
        
        # Build standardized reward types for querying
        reward_types = build_reward_types(
            comparison,
            combined_scores,
            baseline_rubrics,
            patched_rubrics,
            code_quality_comparison,
            trace_metrics,
        )
        
        comparison_json = {
            "run_id": args.run_dir.name,
            "baseline_results": baseline_results,
            "patched_results": patched_results,
            "comparison": comparison,
            "patch_summary": patch_summary,
            "baseline_rubrics": baseline_rubrics,
            "patched_rubrics": patched_rubrics,
            "combined_scores": combined_scores,
            "code_quality_comparison": code_quality_comparison,
            "trace_metrics": trace_metrics,
            "run_metadata": run_metadata,
            "evaluation_time_seconds": evaluation_time_seconds,
            "reward_types": reward_types,  # Standardized reward types for querying
        }
        json_output.write_text(json.dumps(comparison_json, indent=2))
        print(f"JSON comparison saved to: {json_output}")
        
        # Also save to task folder for aggregate analysis
        if run_metadata.get("task_dir"):
            task_dir = Path(run_metadata["task_dir"])
            if task_dir.exists():
                # Create evaluation_results directory if it doesn't exist
                eval_results_dir = task_dir / "evaluation_results"
                eval_results_dir.mkdir(exist_ok=True)
                
                # Save with run_id as filename for easy aggregation
                task_json_output = eval_results_dir / f"{args.run_dir.name}.json"
                task_json_output.write_text(json.dumps(comparison_json, indent=2))
                print(f"JSON saved to task folder: {task_json_output}")
        
    finally:
        # Cleanup Docker containers
        docker_reaper.cleanup(verbose=args.verbose)
        
        # Cleanup temporary repository
        if repo_dir and not args.keep_repo:
            print(f"\nCleaning up temporary repository: {repo_dir}")
            shutil.rmtree(repo_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

