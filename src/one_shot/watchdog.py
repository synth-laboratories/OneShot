#!/usr/bin/env python3
"""
watchdog.py - Container monitoring with multiple termination detection strategies
Exit codes:
  0 - Task completed successfully
  1 - Task failed or error
  2 - Timeout reached
  3 - Token limit exceeded
  4 - Container died unexpectedly
  5 - Manual stop requested
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


class ContainerWatchdog:
    def __init__(self, container_name, run_dir, timeout=1800, token_limit=100000):
        self.container_name = container_name
        self.run_dir = Path(run_dir)
        self.timeout = timeout
        self.token_limit = token_limit
        self.start_time = time.time()
        self.stop_requested = False
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        
        # Create log directory
        self.log_dir = self.run_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Patterns for completion detection
        self.completion_patterns = [
            r"task.*completed",
            r"all tests pass",
            r"successfully completed",
            r"task finished",
            r"done\.",
            r"finished\.",
            r"✓.*complete",
            r"✅.*done",
        ]
        
        self.failure_patterns = [
            r"task.*failed",
            r"error:.*fatal",
            r"unrecoverable error",
            r"giving up",
            r"cannot continue",
        ]
        
        self.token_patterns = [
            r"tokens?[:\s]+(\d+)",
            r"usage[:\s]+(\d+)",
            r"token_count[:\s]+(\d+)",
        ]
    
    def _handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        self.log(f"Received signal {signum}, requesting stop...")
        self.stop_requested = True
    
    def log(self, message):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] WATCHDOG: {message}", flush=True)
    
    def check_container_status(self):
        """Check if container is still running"""
        try:
            result = subprocess.run(
                ["docker", "inspect", self.container_name, "--format", "{{.State.Status}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            status = result.stdout.strip()
            return status == "running"
        except Exception as e:
            self.log(f"Error checking container: {e}")
            return False
    
    def check_sentinel_file(self):
        """Check for completion sentinel file in container"""
        try:
            # Check if container has completion marker
            result = subprocess.run(
                ["docker", "exec", self.container_name, 
                 "test", "-f", "/app/artifacts/completion.json"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                # File exists, check its content
                result = subprocess.run(
                    ["docker", "exec", self.container_name,
                     "cat", "/app/artifacts/completion.json"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if data.get("completed"):
                        self.log("Completion sentinel detected in container")
                        return True
        except Exception as e:
            # Container might be dead
            pass
        return False
    
    def check_tool_server(self):
        """Check tool server completion endpoint"""
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_name, 
                 "curl", "-s", "http://localhost:5555/status"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get("completed"):
                    self.log("Tool server reports completion")
                    return True
        except Exception:
            pass
        return False
    
    def analyze_output(self):
        """Analyze container output for completion/failure patterns"""
        try:
            # Get recent container logs
            result = subprocess.run(
                ["docker", "logs", "--tail", "100", self.container_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            output = result.stdout + result.stderr
            
            # Check for completion patterns
            for pattern in self.completion_patterns:
                if re.search(pattern, output, re.IGNORECASE):
                    self.log(f"Completion pattern detected: {pattern}")
                    return "completed"
            
            # Check for failure patterns
            for pattern in self.failure_patterns:
                if re.search(pattern, output, re.IGNORECASE):
                    self.log(f"Failure pattern detected: {pattern}")
                    return "failed"
            
            # Check token usage
            total_tokens = 0
            for pattern in self.token_patterns:
                matches = re.findall(pattern, output, re.IGNORECASE)
                for match in matches:
                    try:
                        tokens = int(match)
                        total_tokens = max(total_tokens, tokens)
                    except ValueError:
                        pass
            
            if total_tokens > self.token_limit:
                self.log(f"Token limit exceeded: {total_tokens} > {self.token_limit}")
                return "token_limit"
            
        except Exception as e:
            self.log(f"Error analyzing output: {e}")
        
        return None
    
    def check_idle_time(self):
        """Check if container has been idle (no new output)"""
        try:
            # Get container log file modification time
            result = subprocess.run(
                ["docker", "inspect", self.container_name, 
                 "--format", "{{.LogPath}}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            log_path = result.stdout.strip()
            if log_path and os.path.exists(log_path):
                mtime = os.path.getmtime(log_path)
                idle_time = time.time() - mtime
                if idle_time > 300:  # 5 minutes idle
                    self.log(f"Container idle for {idle_time:.0f}s")
                    return True
        except Exception:
            pass
        return False
    
    def capture_artifacts(self):
        """Copy artifacts from container before it stops"""
        try:
            self.log("Capturing artifacts...")
            artifacts_dir = self.run_dir / "artifacts"
            artifacts_dir.mkdir(exist_ok=True)
            
            # Copy ALL artifacts from container
            subprocess.run(
                ["docker", "cp", 
                 f"{self.container_name}:/app/artifacts/.",
                 str(artifacts_dir)],
                capture_output=True,
                timeout=10
            )
            
            # Capture final logs
            subprocess.run(
                ["docker", "logs", self.container_name],
                stdout=open(self.log_dir / "container_output.log", "w"),
                stderr=subprocess.STDOUT,
                timeout=10
            )
            
            # If we have bootstrap.log, copy it to logs dir
            bootstrap_log = artifacts_dir / "bootstrap.log"
            if bootstrap_log.exists():
                import shutil
                shutil.copy(bootstrap_log, self.log_dir / "bootstrap.log")
            
        except Exception as e:
            self.log(f"Error capturing artifacts: {e}")
    
    def run(self):
        """Main monitoring loop"""
        self.log(f"Starting monitor for {self.container_name}")
        self.log(f"Timeout: {self.timeout}s, Token limit: {self.token_limit}")
        
        check_interval = 5  # seconds
        last_check = 0
        
        while not self.stop_requested:
            elapsed = time.time() - self.start_time
            
            # Check timeout
            if elapsed > self.timeout:
                self.log(f"Timeout reached ({self.timeout}s)")
                self.capture_artifacts()
                return 2
            
            # Rate limit checks
            if time.time() - last_check < check_interval:
                time.sleep(1)
                continue
            last_check = time.time()
            
            # Check container health
            if not self.check_container_status():
                self.log("Container stopped or died")
                self.capture_artifacts()
                # Check if it was intentional completion
                if self.check_sentinel_file():
                    return 0
                return 4
            
            # Check completion methods WHILE CONTAINER IS STILL RUNNING
            if self.check_sentinel_file() or self.check_tool_server():
                self.log("Completion detected - capturing artifacts while container is alive...")
                self.capture_artifacts()
                self.log("Artifacts captured successfully - now stopping container...")
                # Stop the container gracefully
                try:
                    subprocess.run(["docker", "stop", "-t", "10", self.container_name], 
                                 capture_output=True, timeout=15)
                    self.log("Container stopped gracefully")
                except Exception as e:
                    self.log(f"Warning: Could not stop container: {e}")
                return 0
            
            # Analyze output
            status = self.analyze_output()
            if status == "completed":
                self.log("Completion detected (via logs) - capturing artifacts...")
                self.capture_artifacts()
                self.log("Artifacts captured - stopping container...")
                try:
                    subprocess.run(["docker", "stop", "-t", "10", self.container_name], 
                                 capture_output=True, timeout=15)
                except:
                    pass
                return 0
            elif status == "failed":
                self.log("Failure detected - capturing artifacts...")
                self.capture_artifacts()
                try:
                    subprocess.run(["docker", "stop", "-t", "10", self.container_name], 
                                 capture_output=True, timeout=15)
                except:
                    pass
                return 1
            elif status == "token_limit":
                self.log("Token limit reached - capturing artifacts...")
                self.capture_artifacts()
                try:
                    subprocess.run(["docker", "stop", "-t", "10", self.container_name], 
                                 capture_output=True, timeout=15)
                except:
                    pass
                return 3
            
            # Check for idle timeout
            if elapsed > 600 and self.check_idle_time():
                self.log("Container idle timeout")
                self.capture_artifacts()
                return 2
            
            # Log progress every 30 seconds
            if int(elapsed) % 30 == 0:
                self.log(f"Still monitoring... ({elapsed:.0f}s elapsed)")
        
        # Manual stop requested
        self.log("Stop requested")
        self.capture_artifacts()
        return 5


def main():
    parser = argparse.ArgumentParser(description="Container watchdog monitor")
    parser.add_argument("container_name", help="Name of container to monitor")
    parser.add_argument("run_dir", help="Run directory for outputs")
    parser.add_argument("--timeout", type=int, default=1800,
                       help="Timeout in seconds (default: 1800)")
    parser.add_argument("--token-limit", type=int, default=100000,
                       help="Token limit (default: 100000)")
    
    args = parser.parse_args()
    
    watchdog = ContainerWatchdog(
        args.container_name,
        args.run_dir,
        args.timeout,
        args.token_limit
    )
    
    exit_code = watchdog.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()