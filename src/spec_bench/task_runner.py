"""
Task Runner - High-level interface for running Spec Bench evaluations.

Provides a simple API for running evaluations with various overrides and configurations.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List

from .evaluator import SpecEvaluator, EvaluationResult
from .overrides import OverridesManager


class TaskRunner:
    """High-level task runner for Spec Bench evaluations."""

    def __init__(self, repo_root: Optional[Path] = None):
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]

        self.repo_root = repo_root
        self.evaluator = SpecEvaluator()
        self.overrides_manager = OverridesManager()

    def run_with_overrides(
        self,
        task_path: Path,
        overrides_path: Path,
        model: str = "gpt-4",
        rollouts: int = 1
    ) -> EvaluationResult:
        """Run evaluation with overrides applied."""
        return self.evaluator.run_evaluation(
            task_path=task_path,
            overrides_path=overrides_path,
            model=model,
            rollouts=rollouts
        )

    def run_from_config(self, config_path: Path) -> List[EvaluationResult]:
        """Run batch evaluation from configuration file."""
        return self.evaluator.run_batch_evaluation(config_path)

    def quick_eval(
        self,
        task_path: Path,
        model: str = "gpt-4",
        openai_base_url: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> EvaluationResult:
        """Quick evaluation with minimal setup."""
        # Set environment variables if provided
        env_updates = {}
        if openai_base_url:
            env_updates['OPENAI_BASE_URL'] = openai_base_url
        if api_key:
            env_updates['OPENAI_API_KEY'] = api_key

        if env_updates:
            os.environ.update(env_updates)

        return self.evaluator.run_evaluation(
            task_path=task_path,
            model=model,
            rollouts=1
        )

    def setup_custom_provider(
        self,
        name: str,
        base_url: str,
        env_key: str = "OPENAI_API_KEY",
        wire_api: str = "responses"
    ) -> None:
        """Set up a custom model provider."""
        self.evaluator.create_custom_provider(
            name=name,
            base_url=base_url,
            env_key=env_key,
            wire_api=wire_api
        )

    def list_providers(self) -> List[str]:
        """List available model providers."""
        return self.evaluator.get_supported_providers()

    def validate_setup(self, task_path: Path, overrides_path: Optional[Path] = None) -> bool:
        """Validate evaluation setup."""
        try:
            # Check task exists
            if not task_path.exists():
                print(f"Task path does not exist: {task_path}")
                return False

            # Check overrides if provided
            if overrides_path:
                if not overrides_path.exists():
                    print(f"Overrides file does not exist: {overrides_path}")
                    return False

                if not self.evaluator.validate_overrides(overrides_path):
                    return False

            # Check required environment variables
            required_envs = []
            if not os.getenv('OPENAI_API_KEY') and not overrides_path:
                required_envs.append('OPENAI_API_KEY')

            if required_envs:
                print(f"Missing required environment variables: {required_envs}")
                return False

            return True

        except Exception as e:
            print(f"Setup validation failed: {e}")
            return False

    def get_evaluation_summary(self, results: List[EvaluationResult]) -> Dict[str, Any]:
        """Generate summary of evaluation results."""
        if not results:
            return {"total": 0, "successful": 0, "failed": 0, "average_score": 0}

        successful = [r for r in results if r.success]
        scores = [r.score for r in successful if r.score is not None]

        summary = {
            "total": len(results),
            "successful": len(successful),
            "failed": len(results) - len(successful),
            "success_rate": len(successful) / len(results),
            "average_score": sum(scores) / len(scores) if scores else 0,
            "results": [
                {
                    "success": r.success,
                    "score": r.score,
                    "error": r.error_message,
                    "output_path": str(r.output_path) if r.output_path else None
                }
                for r in results
            ]
        }

        return summary

    def run_with_docker_overrides(
        self,
        task_path: Path,
        docker_overrides: Dict[str, Any],
        model: str = "gpt-4"
    ) -> EvaluationResult:
        """Run evaluation with Docker-specific overrides."""
        # Create temporary overrides file
        import tempfile
        import json

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(docker_overrides, f, indent=2)
            temp_overrides_path = Path(f.name)

        try:
            return self.run_with_overrides(
                task_path=task_path,
                overrides_path=temp_overrides_path,
                model=model
            )
        finally:
            # Clean up temporary file
            temp_overrides_path.unlink(missing_ok=True)

    def create_environment_override(
        self,
        env_vars: Dict[str, str],
        repo_config: Optional[Dict[str, Any]] = None,
        inject_files: Optional[List[Dict[str, Any]]] = None,
        lm_instructions: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create an environment override configuration."""
        override = {
            "environment_variables": env_vars
        }

        if repo_config:
            override["repo"] = repo_config

        if inject_files:
            override["inject_files"] = inject_files

        if lm_instructions:
            override["lm_instructions"] = lm_instructions

        return override

    def setup_regional_openai(
        self,
        region: str = "eu",
        model: str = "gpt-4"
    ) -> Dict[str, str]:
        """Set up regional OpenAI endpoint."""
        base_urls = {
            "us": "https://api.openai.com/v1",
            "eu": "https://eu.api.openai.com/v1",
            "asia": "https://asia.api.openai.com/v1"
        }

        if region not in base_urls:
            raise ValueError(f"Unsupported region: {region}. Supported: {list(base_urls.keys())}")

        return {
            "OPENAI_BASE_URL": base_urls[region],
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
            "SPEC_BENCH_MODEL": model
        }

    def setup_oss_provider(
        self,
        base_url: str,
        model: str = "gpt-4",
        api_key: Optional[str] = None
    ) -> Dict[str, str]:
        """Set up OpenAI-compatible OSS provider."""
        env_vars = {
            "CODEX_OSS_BASE_URL": base_url,
            "SPEC_BENCH_MODEL": model
        }

        if api_key:
            env_vars["OPENAI_API_KEY"] = api_key

        return env_vars
