import subprocess
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class GitHelpers:
    """Git operations helper"""

    @staticmethod
    def run_git(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
        full_cmd = ['git'] + cmd
        logger.debug(f"Running git command: {' '.join(full_cmd)}")
        result = subprocess.run(full_cmd, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            logger.error(f"Git command failed: {result.stderr}")
            raise RuntimeError(f"Git command failed: {result.stderr}")
        return result

    @staticmethod
    def get_current_branch() -> str:
        result = GitHelpers.run_git(['branch', '--show-current'])
        return result.stdout.strip()

    @staticmethod
    def get_head_sha() -> str:
        result = GitHelpers.run_git(['rev-parse', 'HEAD'])
        return result.stdout.strip()

    @staticmethod
    def get_remote_urls() -> List[str]:
        result = GitHelpers.run_git(['remote', '-v'], check=False)
        urls: List[str] = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split()
                if len(parts) >= 2 and parts[1] not in urls:
                    urls.append(parts[1])
        return urls

    @staticmethod
    def stage_all() -> bool:
        result = GitHelpers.run_git(['add', '-A'], check=False)
        return result.returncode == 0

    @staticmethod
    def commit(message: str) -> Optional[str]:
        status = GitHelpers.run_git(['status', '--porcelain'], check=False)
        if not status.stdout.strip():
            logger.info("No changes to commit")
            return None
        result = GitHelpers.run_git(['commit', '-m', message], check=False)
        if result.returncode == 0:
            return GitHelpers.get_head_sha()
        return None

    @staticmethod
    def get_diff(start_commit: str, end_commit: str = 'HEAD') -> str:
        result = GitHelpers.run_git(['diff', '--binary', '-M', '-C', f'{start_commit}..{end_commit}', '--'])
        return result.stdout

    @staticmethod
    def get_staged_diff() -> str:
        result = GitHelpers.run_git(['diff', '--cached'])
        return result.stdout

    @staticmethod
    def get_touched_files(start_commit: str, end_commit: str = 'HEAD') -> List[str]:
        result = GitHelpers.run_git(['diff', '--name-status', f'{start_commit}..{end_commit}'])
        files: List[str] = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    files.append(parts[1])
        return files


