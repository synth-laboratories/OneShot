#!/usr/bin/env python3
"""
Analyze container traces from OneShot Bench evaluation runs

Usage: python analyze_container_traces.py <run_directory>
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

def analyze_container_traces(run_dir):
    """Analyze traces from a container evaluation run"""
    run_path = Path(run_dir)

    # Look for traces in the standard locations
    trace_files = [
        run_path / "traces" / "traces.jsonl",
        run_path / "artifacts" / "traces.jsonl"
    ]

    traces_file = None
    for tf in trace_files:
        if tf.exists():
            traces_file = tf
            break

    if not traces_file:
        print("❌ No container traces found in run directory")
        print(f"   Looked for: {', '.join(str(f) for f in trace_files)}")
        return False

    # Load session information if available
    session_info_file = run_path / "traces" / "session_info.txt"
    session_info = {}
    if session_info_file.exists():
        try:
            with open(session_info_file) as f:
                for line in f:
                    if line.strip() and ": " in line:
                        key, value = line.split(": ", 1)
                        session_info[key.strip()] = value.strip()
        except Exception as e:
            print(f"⚠️  Warning: Could not read session info: {e}")

    session_id = session_info.get("Session ID", "unknown")
    task_id = session_info.get("Task ID", "unknown")

    print(f"📊 Analyzing container traces from: {traces_file}")
    if session_id != "unknown":
        print(f"🎯 Session ID: {session_id}")
    if task_id != "unknown":
        print(f"📝 Task ID: {task_id}")

    traces = []
    try:
        with open(traces_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠️  Warning: Failed to parse line {line_num}: {e}")
                    continue
    except Exception as e:
        print(f"❌ Error reading traces file: {e}")
        return False

    if not traces:
        print("❌ No valid traces found in file")
        return False

    print(f"✅ Loaded {len(traces)} API calls from container evaluation")

    # Display session delta if available
    session_summary_file = run_path / "traces" / "session_summary.md"
    if session_summary_file.exists():
        print("
📋 SESSION SUMMARY:"        try:
            with open(session_summary_file) as f:
                content = f.read()
                # Extract git changes section
                if "## Git Changes Summary" in content:
                    git_section = content.split("## Git Changes Summary")[1].split("##")[0]
                    print("Git Changes Made:")
                    for line in git_section.strip().split('\n'):
                        if line.strip() and not line.startswith('---'):
                            print(f"  {line}")
        except Exception as e:
            print(f"⚠️  Warning: Could not read session summary: {e}")

    # Basic statistics
    print("\n" + "="*60)
    print("📈 TRACE ANALYSIS SUMMARY")
    print("="*60)
    print(f"Session ID: {session_id}")
    print(f"Task ID: {task_id}")
    print(f"Total API Calls: {len(traces)}")

    # Time range
    if traces:
        timestamps = [t.get('ts_ms', 0) for t in traces if t.get('ts_ms')]
        if timestamps:
            start_time = min(timestamps)
            end_time = max(timestamps)
            duration_ms = end_time - start_time
            duration_sec = duration_ms / 1000
            print(".1f"
    # API endpoints analysis
    print("
🔗 API ENDPOINTS:"    endpoints = defaultdict(int)
    methods = defaultdict(int)

    for trace in traces:
        url = trace.get('url', 'unknown')
        method = trace.get('method', 'GET')

        # Clean up OpenAI URLs for readability
        if 'api.openai.com' in url:
            url = url.replace('https://api.openai.com/v1/', '')

        endpoints[url] += 1
        methods[method] += 1

    # Display top endpoints
    print("
  📍 Top API Endpoints:"    for url, count in sorted(endpoints.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {count:3d} calls: {url}")

    # Display HTTP methods
    print("
  📋 HTTP Methods:"    for method, count in sorted(methods.items(), key=lambda x: x[1], reverse=True):
        print(f"    {count:3d} calls: {method}")

    # Response analysis
    print("
📊 RESPONSE ANALYSIS:"    status_codes = defaultdict(int)
    response_sizes = []

    for trace in traces:
        # Try to get status code from meta_json
        meta = trace.get('meta_json', '{}')
        try:
            meta_data = json.loads(meta)
            status = meta_data.get('status_code')
            if status:
                status_codes[str(status)] += 1
        except:
            pass

        # Try to get response size
        response_json = trace.get('response_json', '{}')
        if response_json and response_json != '{}':
            try:
                response_data = json.loads(response_json)
                if isinstance(response_data, dict) and '_raw' not in response_data:
                    # This was a JSON response, estimate size
                    response_sizes.append(len(response_json))
            except:
                pass

    if status_codes:
        print("
  📈 HTTP Status Codes:"        for status, count in sorted(status_codes.items(), key=lambda x: x[1], reverse=True):
            print(f"    {count:3d} responses: HTTP {status}")

    if response_sizes:
        avg_size = sum(response_sizes) / len(response_sizes)
        print("
  📏 Response Sizes:"        print(f"    Average JSON response size: {avg_size:.0f} bytes")
        print(f"    Total JSON responses: {len(response_sizes)}")

    # Agent behavior insights
    print("
🧠 AGENT BEHAVIOR INSIGHTS:"    # Look for patterns in the API calls
    chat_completions = endpoints.get('chat/completions', 0)
    if chat_completions > 0:
        print(f"    🤖 Agent made {chat_completions} chat completion requests")

    # Check for iterative behavior
    if len(traces) > 1:
        # Calculate time gaps between calls
        sorted_traces = sorted(traces, key=lambda x: x.get('ts_ms', 0))
        gaps = []
        for i in range(1, len(sorted_traces)):
            gap = sorted_traces[i].get('ts_ms', 0) - sorted_traces[i-1].get('ts_ms', 0)
            if gap > 0:
                gaps.append(gap)

        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            print(".1f"
    # Success indicators
    success_indicators = []
    if chat_completions > 0:
        success_indicators.append("Agent actively used AI capabilities")
    if len(traces) > 5:
        success_indicators.append("Agent made multiple API calls (iterative behavior)")
    if status_codes.get('200', 0) > status_codes.get('400', 0):
        success_indicators.append("Most API calls succeeded")

    if success_indicators:
        print("
✅ EVALUATION INSIGHTS:"        for indicator in success_indicators:
            print(f"    ✓ {indicator}")

    print("\n" + "="*60)
    print("🎯 TRACE ANALYSIS COMPLETE")
    print("="*60)

    # Save enhanced analysis with session context
    analysis_file = run_path / "traces" / "detailed_analysis.json"
    analysis_file.parent.mkdir(parents=True, exist_ok=True)

    # Load git changes if available
    git_changes = []
    if session_summary_file.exists():
        try:
            with open(session_summary_file) as f:
                content = f.read()
                if "## Git Changes Summary" in content:
                    git_section = content.split("## Git Changes Summary")[1].split("##")[0]
                    for line in git_section.strip().split('\n'):
                        if line.strip() and not line.startswith('---') and '- **' in line:
                            git_changes.append(line.strip())
        except:
            pass

    analysis = {
        'session_info': {
            'session_id': session_id,
            'task_id': task_id,
            'analysis_timestamp': datetime.now().isoformat(),
            'run_directory': str(run_path),
            'traces_file': str(traces_file)
        },
        'summary': {
            'total_calls': len(traces),
            'time_range_ms': duration_ms if 'duration_ms' in locals() else None,
            'unique_endpoints': len(endpoints),
            'unique_methods': len(methods),
            'session_duration_sec': duration_sec if 'duration_sec' in locals() else None
        },
        'endpoints': dict(endpoints),
        'methods': dict(methods),
        'status_codes': dict(status_codes),
        'git_changes': git_changes,
        'insights': success_indicators,
        'raw_traces_count': len(traces)
    }

    with open(analysis_file, 'w') as f:
        json.dump(analysis, f, indent=2)

    print(f"💾 Enhanced analysis saved to: {analysis_file}")
    print(f"📄 Session summary available at: {session_summary_file}")

    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        success = analyze_container_traces(sys.argv[1])
        sys.exit(0 if success else 1)
    else:
        print("Usage: python analyze_container_traces.py <run_directory>")
        print("\nExample:")
        print("  python analyze_container_traces.py data/runs/2025-01-08__14-30-22")
        print("\nThis will analyze the container traces from that evaluation run.")
        sys.exit(1)
