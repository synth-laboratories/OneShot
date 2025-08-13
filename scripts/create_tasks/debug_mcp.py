#!/usr/bin/env python3
"""
Debug wrapper for the MCP server that shows all stdin/stdout traffic
Run this in a separate terminal to monitor MCP communication
"""

import sys
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

# Colors for terminal output
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
RED = '\033[0;31m'
CYAN = '\033[0;36m'
NC = '\033[0m'

def log(prefix, color, message):
    """Log a message with timestamp and color"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{color}[{timestamp}] {prefix}{NC} {message}", flush=True)

def pretty_json(data):
    """Pretty print JSON data"""
    try:
        if isinstance(data, str):
            data = json.loads(data)
        return json.dumps(data, indent=2)
    except:
        return str(data)

def main():
    print(f"{GREEN}{'='*60}{NC}")
    print(f"{GREEN}OneShot MCP Server Debug Monitor{NC}")
    print(f"{GREEN}{'='*60}{NC}")
    print()
    
    # Path to the actual MCP server
    server_path = Path(__file__).parent / "mcp_oneshot_server.py"
    
    print(f"{BLUE}Server:{NC} {server_path}")
    print(f"{BLUE}Mode:{NC} stdio (JSON-RPC over stdin/stdout)")
    print(f"{BLUE}Log:{NC} /tmp/oneshot_mcp_server.out")
    print()
    
    # Start the MCP server as a subprocess
    log("STARTUP", GREEN, "Starting MCP server subprocess...")
    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
    )
    
    # Thread to read stderr (logs)
    def read_stderr():
        for line in proc.stderr:
            if line.strip():
                log("STDERR", YELLOW, line.strip())
    
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    
    print(f"{GREEN}Server is running. Monitoring communication...{NC}")
    print(f"{CYAN}Waiting for MCP requests from Codex...{NC}")
    print()
    
    # Monitor stdin (what would come from Codex)
    import select
    import os
    
    # Make stdin non-blocking
    import fcntl
    fl = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
    
    try:
        while True:
            # Check for input from stdin (simulating Codex)
            ready = select.select([sys.stdin], [], [], 0.1)
            if ready[0]:
                line = sys.stdin.readline()
                if line:
                    log("REQUEST", BLUE, "Received from stdin:")
                    print(f"  {pretty_json(line.strip())}")
                    
                    # Send to MCP server
                    proc.stdin.write(line)
                    proc.stdin.flush()
            
            # Check for output from MCP server
            ready = select.select([proc.stdout], [], [], 0.1)
            if ready[0]:
                line = proc.stdout.readline()
                if line:
                    log("RESPONSE", GREEN, "From MCP server:")
                    print(f"  {pretty_json(line.strip())}")
                    
                    # Forward to stdout (back to Codex)
                    sys.stdout.write(line)
                    sys.stdout.flush()
            
            # Check if process is still alive
            if proc.poll() is not None:
                log("ERROR", RED, f"MCP server exited with code {proc.returncode}")
                break
                
    except KeyboardInterrupt:
        log("SHUTDOWN", YELLOW, "Stopping MCP server...")
        proc.terminate()
        proc.wait(timeout=5)
        print(f"\n{GREEN}Server stopped.{NC}")
    except Exception as e:
        log("ERROR", RED, str(e))
        proc.terminate()

if __name__ == "__main__":
    main()