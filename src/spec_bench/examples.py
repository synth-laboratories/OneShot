"""
Spec Bench Examples - Usage examples for the evaluation framework.

This file contains practical examples of how to use the Spec Bench framework
for running evaluations with custom OpenAI endpoints and comprehensive overrides.
"""

from pathlib import Path
from spec_bench import TaskRunner
from spec_bench.overrides import OverridesManager


def example_quick_evaluation():
    """Example 1: Quick evaluation with custom OpenAI endpoint."""
    runner = TaskRunner()

    # Quick evaluation with EU regional endpoint
    result = runner.quick_eval(
        task_path=Path("data/tasks/prepared/high-sokoban"),
        model="gpt-4",
        openai_base_url="https://eu.api.openai.com/v1"
    )

    print(f"Quick eval result: Success={result.success}, Score={result.score}")
    return result


def example_evaluation_with_overrides():
    """Example 2: Evaluation with comprehensive overrides."""
    runner = TaskRunner()

    # Run with overrides that include custom repository, file injections, and OpenAI config
    result = runner.run_with_overrides(
        task_path=Path("data/tasks/prepared/high-sokoban"),
        overrides_path=Path("data/tasks/prepared/high-sokoban/overrides.json"),
        model="gpt-4",
        rollouts=3
    )

    print(f"Overrides eval result: Success={result.success}, Score={result.score}")
    return result


def example_custom_provider_setup():
    """Example 3: Setting up a custom model provider."""
    runner = TaskRunner()

    # Set up a custom provider for your proxy or OSS model
    runner.setup_custom_provider(
        name="my-custom-provider",
        base_url="https://my-custom-api.example.com/v1",
        env_key="MY_CUSTOM_API_KEY",
        wire_api="responses"
    )

    print("Custom provider 'my-custom-provider' configured")
    print("Use with: model='my-custom-provider:gpt-4'")


def example_regional_openai():
    """Example 4: Using regional OpenAI endpoints."""
    runner = TaskRunner()

    # Set up environment variables for EU regional endpoint
    env_vars = runner.setup_regional_openai("eu", "gpt-4")

    # Create overrides with regional configuration
    overrides = runner.create_environment_override(
        env_vars=env_vars,
        repo_config={
            "git_url": "https://github.com/synth-laboratories/Horizons",
            "branch": "rust-port"
        }
    )

    print("EU regional OpenAI configuration created")
    print("Environment variables:", env_vars)
    return overrides


def example_oss_provider():
    """Example 5: Using OSS-compatible providers (Ollama, vLLM)."""
    runner = TaskRunner()

    # Set up for Ollama or vLLM
    env_vars = runner.setup_oss_provider(
        base_url="http://localhost:11434/v1",
        model="codellama:34b",
        api_key="ollama-key"
    )

    print("OSS provider configuration created")
    print("Environment variables:", env_vars)
    return env_vars


def example_batch_evaluation():
    """Example 6: Running batch evaluations."""
    runner = TaskRunner()

    # Run multiple tasks from TOML configuration
    results = runner.run_from_config(Path("configs/env_bench.toml"))

    # Get summary of all results
    summary = runner.get_evaluation_summary(results)

    print("Batch evaluation summary:")
    print(f"  Total tasks: {summary['total']}")
    print(f"  Successful: {summary['successful']}")
    print(f"  Success rate: {summary['success_rate']:.1%}")
    if summary['average_score'] > 0:
        print(f"  Average score: {summary['average_score']:.3f}")

    return summary


def example_validation():
    """Example 7: Validating evaluation setup."""
    runner = TaskRunner()

    # Validate setup before running
    is_valid = runner.validate_setup(
        task_path=Path("data/tasks/prepared/high-sokoban"),
        overrides_path=Path("data/tasks/prepared/high-sokoban/overrides.json")
    )

    if is_valid:
        print("✓ Setup validation passed - ready to run evaluation")
    else:
        print("✗ Setup validation failed - check configuration")

    return is_valid


def example_docker_overrides():
    """Example 8: Running with Docker-specific overrides."""
    runner = TaskRunner()

    # Create overrides with file injections and repository changes
    docker_overrides = {
        "remove_repo_paths": [
            "old_directory",
            "obsolete_files"
        ],
        "inject_files": [
            {
                "path": "AGENTS.md",
                "content": "# Agent Implementation Guide\n\nYour custom documentation here..."
            }
        ],
        "repo": {
            "git_url": "https://github.com/your-org/your-repo",
            "branch": "feature-branch"
        },
        "environment_variables": {
            "DEBUG": "true",
            "CUSTOM_VAR": "value"
        }
    }

    # Run with Docker overrides
    result = runner.run_with_docker_overrides(
        task_path=Path("data/tasks/prepared/high-sokoban"),
        docker_overrides=docker_overrides,
        model="gpt-4"
    )

    print(f"Docker overrides result: Success={result.success}")
    return result


def example_working_with_overrides_manager():
    """Example 9: Direct usage of OverridesManager."""

    # Load and manipulate overrides directly
    manager = OverridesManager(Path("overrides.json"))
    overrides = manager.load_overrides()

    print("Loaded overrides:")
    if overrides.repo:
        print(f"  Repository: {overrides.repo.git_url}")
        print(f"  Branch: {overrides.repo.branch}")

    if overrides.openai_config:
        print(f"  OpenAI Base URL: {overrides.openai_config.base_url}")

    if overrides.inject_files:
        print(f"  Files to inject: {len(overrides.inject_files)}")

    # Get environment variables needed
    env_vars = manager.get_openai_env_vars()
    print(f"  Required env vars: {list(env_vars.keys())}")

    return overrides


if __name__ == "__main__":
    """Run all examples (for testing purposes)."""
    print("Running Spec Bench Examples...")
    print("=" * 50)

    # Run examples
    example_validation()
    print()

    example_regional_openai()
    print()

    example_oss_provider()
    print()

    example_custom_provider_setup()
    print()

    example_working_with_overrides_manager()
    print()

    print("Examples completed!")
    print("\nTo use in your code:")
    print("  from spec_bench import TaskRunner")
    print("  runner = TaskRunner()")
    print("  result = runner.quick_eval(task_path, model='gpt-4')")
