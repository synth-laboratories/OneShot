"""
Spec Bench Evaluator - Core evaluation logic with overrides support.

Handles running evaluations with custom OpenAI endpoints, repository overrides,
and comprehensive evaluation workflows.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .overrides import OverridesManager, EvaluationOverrides


@dataclass
class EvaluationResult:
    """Result of an evaluation run."""
    success: bool
    score: Optional[float] = None
    output_path: Optional[Path] = None
    error_message: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None


class SpecEvaluator:
    """Main evaluator class for Spec Bench evaluations."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path
        self.overrides_manager = OverridesManager()
        self.repo_root = Path(__file__).resolve().parents[2]

    def load_evaluation_config(self, config_path: Path) -> Dict[str, Any]:
        """Load evaluation configuration from TOML file."""
        try:
            import tomllib
            with open(config_path, 'rb') as f:
                return tomllib.load(f)
        except ImportError:
            # Fallback for older Python versions
            import toml
            with open(config_path) as f:
                return toml.load(f)

    def setup_openai_environment(self, overrides: EvaluationOverrides) -> Dict[str, str]:
        """Setup environment variables for OpenAI configuration."""
        env_vars = os.environ.copy()

        if overrides.openai_config:
            config = overrides.openai_config

            # Set base URL if specified
            if config.base_url:
                env_vars['OPENAI_BASE_URL'] = config.base_url

            # Set API key if specified
            if config.api_key:
                env_vars['OPENAI_API_KEY'] = config.api_key

            # Set OSS configuration
            if config.oss_base_url:
                env_vars['CODEX_OSS_BASE_URL'] = config.oss_base_url

        return env_vars

    def prepare_task_with_overrides(self, task_path: Path, overrides_path: Optional[Path] = None) -> Path:
        """Prepare a task with overrides applied.
        
        Returns a persistent temporary directory path that will not be automatically deleted.
        The caller is responsible for cleanup if needed.
        """
        if overrides_path:
            self.overrides_manager = OverridesManager(overrides_path)

        overrides = self.overrides_manager.load_overrides(overrides_path)

        # Apply overrides to task configuration
        task_config = self.load_task_config(task_path)
        self.overrides_manager.apply_overrides(task_config)

        # Create a persistent temporary directory (not auto-deleted)
        # Use mkdtemp instead of TemporaryDirectory so it persists after function returns
        temp_dir = tempfile.mkdtemp(prefix="oneshot_prepared_task_")
        temp_path = Path(temp_dir)
        prepared_path = temp_path / "prepared_task"
        prepared_path.mkdir()

        try:
            # Copy original task
            import shutil
            for item in task_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, prepared_path)
                else:
                    shutil.copytree(item, prepared_path / item.name)

            # Apply file injections
            if overrides.inject_files:
                for file_spec in overrides.inject_files:
                    file_path = prepared_path / file_spec['path']
                    file_path.parent.mkdir(parents=True, exist_ok=True)

                    with open(file_path, 'w') as f:
                        f.write(file_spec['content'])

            # Update tb_meta.json with overrides
            meta_path = prepared_path / "tb_meta.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)

                # Apply repository overrides
                if overrides.repo:
                    meta['repo'] = {
                        'git_url': overrides.repo.git_url,
                        'branch': overrides.repo.branch,
                        'start_commit_sha': overrides.repo.start_commit_sha,
                        'end_commit_sha': overrides.repo.end_commit_sha,
                        'subdir': overrides.repo.subdir,
                        'sparse_checkout': overrides.repo.sparse_checkout
                    }

                # Apply LM instructions override
                if overrides.lm_instructions:
                    meta['lm_instructions'] = overrides.lm_instructions

                with open(meta_path, 'w') as f:
                    json.dump(meta, f, indent=2)

            return prepared_path
        except Exception:
            # Clean up on error
            import shutil
            shutil.rmtree(temp_path, ignore_errors=True)
            raise

    def load_task_config(self, task_path: Path) -> Dict[str, Any]:
        """Load task configuration from tb_meta.json."""
        meta_path = task_path / "tb_meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"No tb_meta.json found in {task_path}")

        with open(meta_path) as f:
            return json.load(f)

    def run_evaluation(self, task_path: Path, overrides_path: Optional[Path] = None,
                      model: str = "gpt-4", rollouts: int = 1) -> EvaluationResult:
        """Run evaluation with overrides applied."""

        try:
            # Prepare task with overrides
            prepared_task = self.prepare_task_with_overrides(task_path, overrides_path)

            # Load overrides to get OpenAI configuration
            overrides = self.overrides_manager.load_overrides(overrides_path)
            env_vars = self.setup_openai_environment(overrides)

            # Set up evaluation environment
            env_vars.update({
                'PYTHONPATH': str(self.repo_root / 'src'),
                'SPEC_BENCH_MODEL': model,
                'SPEC_BENCH_ROLLOUTS': str(rollouts)
            })

            # Run the evaluation using the existing scripts
            run_script = self.repo_root / "scripts" / "run_codex_box.sh"
            cmd = [
                str(run_script),
                str(prepared_task)
            ]

            # Add any additional environment variables from overrides
            if overrides.environment_variables:
                env_vars.update(overrides.environment_variables)

            result = subprocess.run(
                cmd,
                env=env_vars,
                cwd=self.repo_root,
                capture_output=True,
                text=True
            )

            # Parse results
            success = result.returncode == 0
            output_path = None
            score = None

            if success:
                # Try to extract score from output or result files
                score = self.extract_score_from_output(result.stdout, prepared_task)
                output_path = self.find_evaluation_output(prepared_task)

            return EvaluationResult(
                success=success,
                score=score,
                output_path=output_path,
                error_message=result.stderr if not success else None,
                metrics={'stdout': result.stdout, 'stderr': result.stderr}
            )

        except Exception as e:
            return EvaluationResult(
                success=False,
                error_message=str(e)
            )

    def extract_score_from_output(self, output: str, task_path: Path) -> Optional[float]:
        """Extract evaluation score from output or result files."""
        # Look for score patterns in output
        import re

        score_patterns = [
            r'score:\s*([0-9.]+)',
            r'total_score:\s*([0-9.]+)',
            r'final_score:\s*([0-9.]+)',
            r'(\d+\.\d+)',
        ]

        for pattern in score_patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue

        # Look for result files
        result_files = ['results.json', 'evaluation.json', 'scores.json']
        for filename in result_files:
            result_path = task_path / filename
            if result_path.exists():
                try:
                    with open(result_path) as f:
                        data = json.load(f)
                    if 'score' in data:
                        return float(data['score'])
                    if 'total_score' in data:
                        return float(data['total_score'])
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue

        return None

    def find_evaluation_output(self, task_path: Path) -> Optional[Path]:
        """Find evaluation output directory or files."""
        # Look for common output directories
        output_patterns = [
            'evaluation_output',
            'results',
            'output',
            'eval_output'
        ]

        for pattern in output_patterns:
            output_path = task_path / pattern
            if output_path.exists() and output_path.is_dir():
                return output_path

        # Look for result files
        result_files = ['results.json', 'evaluation.json', 'scores.json']
        for filename in result_files:
            file_path = task_path / filename
            if file_path.exists():
                return file_path

        return task_path

    def run_batch_evaluation(self, config_path: Path) -> List[EvaluationResult]:
        """Run batch evaluation from configuration file."""
        config = self.load_evaluation_config(config_path)
        results = []

        for task in config.get('tasks', []):
            task_path = Path(task['prepared_dir'])
            overrides_path = None

            if task.get('apply_overrides', False) and 'overrides' in task:
                overrides_path = Path(task['overrides'])

            model = task.get('model', 'gpt-4')
            rollouts = task.get('rollouts', 1)

            result = self.run_evaluation(
                task_path=task_path,
                overrides_path=overrides_path,
                model=model,
                rollouts=rollouts
            )

            results.append(result)

        return results

    def create_custom_provider(self, name: str, base_url: str, **kwargs) -> None:
        """Create and save a custom model provider."""
        provider = self.overrides_manager.create_provider_config(name, base_url, **kwargs)
        self.overrides_manager._config_providers[name] = provider
        self.overrides_manager.save_provider_config()

    def get_supported_providers(self) -> List[str]:
        """Get list of supported model providers."""
        return list(self.overrides_manager._config_providers.keys()) + ['openai', 'oss']

    def validate_overrides(self, overrides_path: Path) -> bool:
        """Validate that overrides file is properly formatted."""
        try:
            self.overrides_manager.load_overrides(overrides_path)
            return True
        except Exception as e:
            print(f"Invalid overrides file: {e}")
            return False
