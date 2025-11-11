#!/usr/bin/env python3
"""
Integration test to verify Codex reasoning_effort configuration works correctly.

This script:
1. Creates a test git repo
2. Sets up Codex config with reasoning_effort
3. Runs Codex exec and captures startup output (before API calls)
4. Parses Codex output to verify reasoning_effort is set correctly
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

def run_test() -> int:
    """Run integration test for Codex reasoning_effort."""
    
    # Create temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_repo"
        test_dir.mkdir()
        
        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=test_dir,
            check=True,
            capture_output=True,
            timeout=30
        )
        (test_dir / "README.md").write_text("test\n")
        subprocess.run(
            ["git", "add", "README.md"],
            cwd=test_dir,
            check=True,
            capture_output=True,
            timeout=30
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=test_dir,
            check=True,
            capture_output=True,
            timeout=30
        )
        
        # Create Codex config directory
        codex_dir = test_dir / ".codex"
        codex_dir.mkdir()
        
        # Test 1: Config file with reasoning_effort (underscore format)
        print("\n=== TEST 1: Config file with reasoning_effort (underscore) ===")
        config1 = codex_dir / "config.toml"
        config1.write_text('''model_provider = "openai"
model = "gpt-5-nano"
reasoning_effort = "medium"
reasoning_summaries = "auto"
''')
        print(f"Created config: {config1}")
        print(config1.read_text())
        
        # Run Codex and capture output (it will show config before making API calls)
        env = os.environ.copy()
        # Set HOME to test_dir so Codex looks for .codex in the right place
        env["HOME"] = str(test_dir)
        
        result = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-5-nano", "echo test"],
            cwd=test_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        output = result.stdout + result.stderr
        print("\nCodex output:")
        print(output)
        
        # Check for reasoning effort in output
        if "reasoning effort: none" in output.lower():
            print("❌ FAIL: Codex shows 'reasoning effort: none'")
            return 1
        elif "reasoning effort: medium" in output.lower():
            print("✅ PASS: Codex shows 'reasoning effort: medium'")
        else:
            print("⚠️  Could not determine reasoning effort from output")
        
        # Test 2: -c flags with reasoning.effort (dotted format, quoted)
        print("\n=== TEST 2: -c flags with reasoning.effort=\"medium\" (quoted) ===")
        config2 = codex_dir / "config.toml"
        config2.write_text('''model_provider = "openai"
model = "gpt-5-nano"
''')
        
        result2 = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", 
             "-c", 'reasoning.effort="medium"',
             "-c", 'reasoning.summaries="auto"',
             "-m", "gpt-5-nano", "echo test"],
            cwd=test_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        output2 = result2.stdout + result2.stderr
        print("\nCodex output:")
        print(output2)
        
        if "reasoning effort: none" in output2.lower():
            print("❌ FAIL: Codex shows 'reasoning effort: none' with -c flags (quoted)")
            return 1
        elif "reasoning effort: medium" in output2.lower():
            print("✅ PASS: Codex shows 'reasoning effort: medium' with -c flags (quoted)")
            return 0
        else:
            print("⚠️  Could not determine reasoning effort from output")
        
        # Test 3: -c flags without quotes
        print("\n=== TEST 3: -c flags with reasoning.effort=medium (unquoted) ===")
        result3 = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", 
             "-c", "reasoning.effort=medium",
             "-c", "reasoning.summaries=auto",
             "-m", "gpt-5-nano", "echo test"],
            cwd=test_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        output3 = result3.stdout + result3.stderr
        print("\nCodex output:")
        print(output3)
        
        if "reasoning effort: none" in output3.lower():
            print("❌ FAIL: Codex shows 'reasoning effort: none' with -c flags (unquoted)")
            return 1
        elif "reasoning effort: medium" in output3.lower():
            print("✅ PASS: Codex shows 'reasoning effort: medium' with -c flags (unquoted)")
            return 0
        else:
            print("⚠️  Could not determine reasoning effort from output")
            return 1

if __name__ == "__main__":
    sys.exit(run_test())

