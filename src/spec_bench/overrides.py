"""
Overrides management for Spec Bench evaluations.

Handles configuration overrides including OpenAI endpoint customization,
repository settings, file injections, and evaluation parameters.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict


@dataclass
class ModelProvider:
    """Configuration for a model provider."""
    name: str
    base_url: str
    env_key: str = "OPENAI_API_KEY"
    wire_api: str = "responses"
    headers: Optional[Dict[str, str]] = None


@dataclass
class OpenAIConfig:
    """OpenAI API configuration with endpoint overrides."""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: str = "gpt-4"
    provider: Optional[str] = None
    oss_base_url: Optional[str] = None
    custom_providers: Optional[Dict[str, ModelProvider]] = None


@dataclass
class RepositoryConfig:
    """Repository configuration for task execution."""
    git_url: str
    branch: str = "main"
    start_commit_sha: Optional[str] = None
    end_commit_sha: Optional[str] = None
    subdir: str = ""
    sparse_checkout: Optional[List[str]] = None


@dataclass
class EvaluationOverrides:
    """Complete evaluation overrides configuration."""
    remove_repo_paths: Optional[List[str]] = None
    inject_files: Optional[List[Dict[str, Any]]] = None
    lm_instructions: Optional[str] = None
    repo: Optional[RepositoryConfig] = None
    openai_config: Optional[OpenAIConfig] = None
    environment_variables: Optional[Dict[str, str]] = None


class OverridesManager:
    """Manages evaluation overrides including OpenAI configuration."""

    def __init__(self, overrides_path: Optional[Path] = None):
        self.overrides_path = overrides_path
        self._overrides: Optional[EvaluationOverrides] = None
        self._config_providers: Dict[str, ModelProvider] = {}

    def load_config_providers(self, config_path: Optional[Path] = None) -> Dict[str, ModelProvider]:
        """Load custom providers from config file."""
        if config_path is None:
            config_path = Path.home() / ".codex" / "config.toml"

        providers = {}

        if config_path.exists():
            try:
                import tomllib
                with open(config_path, 'rb') as f:
                    config = tomllib.load(f)

                if 'model_providers' in config:
                    for name, provider_config in config['model_providers'].items():
                        providers[name] = ModelProvider(
                            name=provider_config.get('name', name),
                            base_url=provider_config['base_url'],
                            env_key=provider_config.get('env_key', 'OPENAI_API_KEY'),
                            wire_api=provider_config.get('wire_api', 'responses'),
                            headers=provider_config.get('headers', {})
                        )
            except Exception as e:
                print(f"Warning: Could not load config providers from {config_path}: {e}")

        return providers

    def resolve_openai_config(self, overrides_config: Optional[Dict[str, Any]] = None) -> OpenAIConfig:
        """Resolve OpenAI configuration from environment and overrides."""

        # Load config providers first
        self._config_providers = self.load_config_providers()

        # Start with environment variables
        config = OpenAIConfig()

        # Check for OSS configuration
        config.oss_base_url = os.getenv('CODEX_OSS_BASE_URL')

        # Check for OpenAI base URL override
        config.base_url = os.getenv('OPENAI_BASE_URL')

        # Get API key
        config.api_key = os.getenv('OPENAI_API_KEY')

        # Check for provider specification
        if 'model_provider' in (overrides_config or {}):
            provider_name = overrides_config['model_provider']
            if provider_name in self._config_providers:
                provider = self._config_providers[provider_name]
                config.provider = provider_name
                config.base_url = provider.base_url
                config.api_key = os.getenv(provider.env_key, config.api_key)

        # Override with explicit configuration
        if overrides_config and 'openai' in overrides_config:
            openai_overrides = overrides_config['openai']
            if 'base_url' in openai_overrides:
                config.base_url = openai_overrides['base_url']
            if 'api_key' in openai_overrides:
                config.api_key = openai_overrides['api_key']
            if 'model' in openai_overrides:
                config.model = openai_overrides['model']
            if 'provider' in openai_overrides:
                config.provider = openai_overrides['provider']

        # Set custom providers reference
        config.custom_providers = self._config_providers

        return config

    def load_overrides(self, overrides_path: Optional[Path] = None) -> EvaluationOverrides:
        """Load evaluation overrides from JSON file."""
        path = overrides_path or self.overrides_path
        if not path or not path.exists():
            return EvaluationOverrides()

        try:
            with open(path) as f:
                data = json.load(f)

            # Parse repository config
            repo_config = None
            if 'repo' in data:
                repo_data = data['repo']
                repo_config = RepositoryConfig(
                    git_url=repo_data['git_url'],
                    branch=repo_data.get('branch', 'main'),
                    start_commit_sha=repo_data.get('start_commit_sha'),
                    end_commit_sha=repo_data.get('end_commit_sha'),
                    subdir=repo_data.get('subdir', ''),
                    sparse_checkout=repo_data.get('sparse_checkout', [])
                )

            # Resolve OpenAI configuration
            openai_config = self.resolve_openai_config(data)

            # Parse environment variables
            env_vars = data.get('environment_variables', {})

            overrides = EvaluationOverrides(
                remove_repo_paths=data.get('remove_repo_paths', []),
                inject_files=data.get('inject_files', []),
                lm_instructions=data.get('lm_instructions'),
                repo=repo_config,
                openai_config=openai_config,
                environment_variables=env_vars
            )

            self._overrides = overrides
            return overrides

        except Exception as e:
            print(f"Warning: Could not load overrides from {path}: {e}")
            return EvaluationOverrides()

    def apply_overrides(self, task_config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply overrides to task configuration."""
        if not self._overrides:
            self.load_overrides()

        if not self._overrides:
            return task_config

        # Apply repository overrides
        if self._overrides.repo:
            task_config['repo'] = asdict(self._overrides.repo)

        # Apply OpenAI configuration
        if self._overrides.openai_config:
            task_config['openai'] = asdict(self._overrides.openai_config)

        # Apply environment variables
        if self._overrides.environment_variables:
            if 'env' not in task_config:
                task_config['env'] = {}
            task_config['env'].update(self._overrides.environment_variables)

        # Apply file injections and path removals
        if self._overrides.inject_files:
            task_config['inject_files'] = self._overrides.inject_files

        if self._overrides.remove_repo_paths:
            task_config['remove_paths'] = self._overrides.remove_repo_paths

        # Apply LM instructions
        if self._overrides.lm_instructions:
            task_config['lm_instructions'] = self._overrides.lm_instructions

        return task_config

    def get_openai_env_vars(self) -> Dict[str, str]:
        """Get environment variables for OpenAI configuration."""
        if not self._overrides or not self._overrides.openai_config:
            return {}

        config = self._overrides.openai_config
        env_vars = {}

        if config.base_url:
            env_vars['OPENAI_BASE_URL'] = config.base_url

        if config.api_key:
            env_vars['OPENAI_API_KEY'] = config.api_key

        if config.oss_base_url:
            env_vars['CODEX_OSS_BASE_URL'] = config.oss_base_url

        return env_vars

    def create_provider_config(self, name: str, base_url: str, **kwargs) -> ModelProvider:
        """Create a new model provider configuration."""
        return ModelProvider(
            name=name,
            base_url=base_url,
            **kwargs
        )

    def save_provider_config(self, config_path: Optional[Path] = None) -> None:
        """Save current providers to config file."""
        if config_path is None:
            config_path = Path.home() / ".codex" / "config.toml"

        config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            import tomli_w

            config = {
                'model_providers': {
                    name: asdict(provider)
                    for name, provider in self._config_providers.items()
                }
            }

            with open(config_path, 'wb') as f:
                tomli_w.dump(config, f)

        except ImportError:
            print("tomli_w not available, cannot save provider config")
        except Exception as e:
            print(f"Error saving provider config: {e}")
