import subprocess
from pathlib import Path
from typing import Dict, Any


class WorktreeReadiness:
    """Check and fix worktree readiness"""

    @staticmethod
    def check_readiness() -> Dict[str, Any]:
        issues = []

        result = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True)
        if not result.stdout.strip():
            issues.append({
                "id": "no_git_name",
                "severity": "error",
                "message": "Git user.name not configured",
                "fix_suggestion": "Set git user.name",
                "commands": ["git config user.name 'Your Name'"]
            })

        result = subprocess.run(['git', 'config', 'user.email'], capture_output=True, text=True)
        if not result.stdout.strip():
            issues.append({
                "id": "no_git_email",
                "severity": "error",
                "message": "Git user.email not configured",
                "fix_suggestion": "Set git user.email",
                "commands": ["git config user.email 'your@email.com'"]
            })

        git_dir = Path('.git')
        if (git_dir / 'MERGE_HEAD').exists():
            issues.append({
                "id": "merge_in_progress",
                "severity": "error",
                "message": "Merge in progress",
                "fix_suggestion": "Complete or abort the merge",
                "commands": ["git merge --abort"]
            })

        if (git_dir / 'rebase-merge').exists() or (git_dir / 'rebase-apply').exists():
            issues.append({
                "id": "rebase_in_progress",
                "severity": "error",
                "message": "Rebase in progress",
                "fix_suggestion": "Complete or abort the rebase",
                "commands": ["git rebase --abort"]
            })

        result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
        if 'UU ' in result.stdout or 'AA ' in result.stdout:
            issues.append({
                "id": "merge_conflicts",
                "severity": "error",
                "message": "Unresolved merge conflicts",
                "fix_suggestion": "Resolve conflicts or reset",
                "commands": []
            })

        if Path('.gitmodules').exists():
            result = subprocess.run(['git', 'submodule', 'status'], capture_output=True, text=True)
            if result.stdout and '-' in result.stdout:
                issues.append({
                    "id": "uninitialized_submodules",
                    "severity": "warning",
                    "message": "Submodules not initialized",
                    "fix_suggestion": "Initialize submodules",
                    "commands": ["git submodule update --init --recursive"]
                })

        ok = len([i for i in issues if i['severity'] == 'error']) == 0
        return {"ok": ok, "issues": issues, "summary": f"Found {len(issues)} issues" if issues else "Worktree is ready"}

    @staticmethod
    def autofix_readiness() -> Dict[str, Any]:
        check_result = WorktreeReadiness.check_readiness()
        fixed = []

        for issue in check_result['issues']:
            if issue['id'] == 'no_git_name':
                subprocess.run(['git', 'config', 'user.name', 'OneShot User'], check=True)
                fixed.append(issue['id'])
            elif issue['id'] == 'no_git_email':
                subprocess.run(['git', 'config', 'user.email', 'oneshot@localhost'], check=True)
                fixed.append(issue['id'])
            elif issue['id'] == 'uninitialized_submodules':
                subprocess.run(['git', 'submodule', 'update', '--init', '--recursive'], check=True)
                fixed.append(issue['id'])

        final_check = WorktreeReadiness.check_readiness()
        return {
            "ok": final_check['ok'],
            "fixed": fixed,
            "remaining_issues": final_check['issues'],
            "summary": f"Fixed {len(fixed)} issues, {len(final_check['issues'])} remaining"
        }


