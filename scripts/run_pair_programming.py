#!/usr/bin/env python3
"""
Direct script to run pair programming - bypasses uv run issues.
"""
import logging
import os
import sys
from pathlib import Path

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger(__name__)

# Add parent directory to path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

logger.info("=" * 60)
logger.info("PAIR PROGRAMMING SCRIPT")
logger.info("=" * 60)
logger.info(f"Python: {sys.executable}")
logger.info(f"Script: {__file__}")
logger.info(f"Args: {sys.argv}")
logger.info(f"REPO_ROOT: {REPO_ROOT}")
logger.info("=" * 60)

try:
    from scripts.run_re_bench import run_pair_programming
    logger.debug("Import successful")
except Exception as e:
    logger.error(f"Import failed: {e}", exc_info=True)
    sys.exit(1)

import argparse  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run pair programming session")
    parser.add_argument("--config", type=Path, required=True, help="Path to pair programming TOML config")
    parser.add_argument("--workspace-dir", type=Path, help="Use existing workspace directory")
    parser.add_argument("--cleanup", action="store_true", help="Remove workspace after session")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger.info(f"Starting pair programming with config: {args.config}")
    run_pair_programming(args)

