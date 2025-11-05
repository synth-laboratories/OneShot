#!/usr/bin/env python3
"""
Spec Bench CLI - Command line interface for running evaluations with overrides.

Usage:
    uv run python -m spec_bench.cli run-with-overrides <task_path> <overrides_path> [options]
    uv run python -m spec_bench.cli quick-eval <task_path> [options]
    uv run python -m spec_bench.cli batch-eval <config_path>
    uv run python -m spec_bench.cli setup-provider <name> <base_url> [options]
    uv run python -m spec_bench.cli validate <task_path> [overrides_path]
    uv run python -m spec_bench.cli list-providers
"""

import argparse
import sys
from pathlib import Path

from .task_runner import TaskRunner
from .overrides import OverridesManager


def run_with_overrides(args: argparse.Namespace) -> None:
    """Run evaluation with overrides."""
    runner = TaskRunner()

    task_path = Path(args.task_path)
    overrides_path = Path(args.overrides_path)

    if not task_path.exists():
        print(f"Error: Task path {task_path} does not exist")
        sys.exit(1)

    if not overrides_path.exists():
        print(f"Error: Overrides path {overrides_path} does not exist")
        sys.exit(1)

    # Set verbosity levels
    quiet = args.verbose == 0
    verbose = args.verbose >= 2

    if verbose:
        print("Running evaluation with overrides (verbose mode)...")
        print(f"Task: {task_path}")
        print(f"Overrides: {overrides_path}")
        print(f"Model: {args.model}")
        print(f"Rollouts: {args.rollouts}")
    elif quiet:
        print("[spec_bench] Starting evaluation with overrides...")

    result = runner.run_with_overrides(
        task_path=task_path,
        overrides_path=overrides_path,
        model=args.model,
        rollouts=args.rollouts
    )

    if result.success:
        status_icon = "✓" if verbose else "✓"
        print(f"{status_icon} Evaluation completed successfully!")
        if result.score is not None:
            print(".3f")
        if result.output_path and verbose:
            print(f"Output: {result.output_path}")
    else:
        status_icon = "✗" if verbose else "✗"
        print(f"{status_icon} Evaluation failed!")
        if result.error_message:
            print(f"Error: {result.error_message}")
        sys.exit(1)


def quick_eval(args: argparse.Namespace) -> None:
    """Run quick evaluation."""
    runner = TaskRunner()

    task_path = Path(args.task_path)

    if not task_path.exists():
        print(f"Error: Task path {task_path} does not exist")
        sys.exit(1)

    # Set verbosity levels
    quiet = args.verbose == 0
    verbose = args.verbose >= 2

    if verbose:
        print("Running quick evaluation (verbose mode)...")
        print(f"Task: {task_path}")
        print(f"Model: {args.model}")
        if args.openai_base_url:
            print(f"OpenAI Base URL: {args.openai_base_url}")
    elif quiet:
        print("[spec_bench] Starting quick evaluation...")

    result = runner.quick_eval(
        task_path=task_path,
        model=args.model,
        openai_base_url=args.openai_base_url,
        api_key=args.api_key
    )

    if result.success:
        status_icon = "✓" if verbose else "✓"
        print(f"{status_icon} Quick evaluation completed successfully!")
        if result.score is not None:
            print(".3f")
        if result.output_path and verbose:
            print(f"Output: {result.output_path}")
    else:
        status_icon = "✗" if verbose else "✗"
        print(f"{status_icon} Quick evaluation failed!")
        if result.error_message:
            print(f"Error: {result.error_message}")
        sys.exit(1)


def batch_eval(args: argparse.Namespace) -> None:
    """Run batch evaluation from config."""
    runner = TaskRunner()

    config_path = Path(args.config_path)

    if not config_path.exists():
        print(f"Error: Config path {config_path} does not exist")
        sys.exit(1)

    # Set verbosity levels
    quiet = args.verbose == 0
    verbose = args.verbose >= 2

    if verbose:
        print("Running batch evaluation (verbose mode)...")
        print(f"Config: {config_path}")
    elif quiet:
        print("[spec_bench] Starting batch evaluation...")

    results = runner.run_from_config(config_path)
    summary = runner.get_evaluation_summary(results)

    print("\nBatch evaluation completed!")
    print(f"Total tasks: {summary['total']}")
    print(f"Successful: {summary['successful']}")
    print(f"Failed: {summary['failed']}")
    print(".1%")

    if summary['average_score'] > 0:
        print(".3f")

    if verbose and results:
        print("\nDetailed results:")
        for i, result in enumerate(results, 1):
            status = "✓" if result.success else "✗"
            score = ".3f" if result.score else "N/A"
            print(f"  {i}. {status} Score: {score}")
            if not result.success and result.error_message:
                print(f"     Error: {result.error_message}")


def setup_provider(args: argparse.Namespace) -> None:
    """Set up a custom provider."""
    runner = TaskRunner()

    print(f"Setting up custom provider '{args.name}'...")
    print(f"Base URL: {args.base_url}")

    if args.env_key != "OPENAI_API_KEY":
        print(f"API Key Environment Variable: {args.env_key}")

    if args.wire_api != "responses":
        print(f"Wire API: {args.wire_api}")

    try:
        runner.setup_custom_provider(
            name=args.name,
            base_url=args.base_url,
            env_key=args.env_key,
            wire_api=args.wire_api
        )
        print(f"✓ Custom provider '{args.name}' configured successfully!")
        print(f"Use with: model='{args.name}:gpt-4'")
    except Exception as e:
        print(f"✗ Failed to configure provider: {e}")
        sys.exit(1)


def validate_setup(args: argparse.Namespace) -> None:
    """Validate evaluation setup."""
    runner = TaskRunner()

    task_path = Path(args.task_path)
    overrides_path = Path(args.overrides_path) if args.overrides_path else None

    if not task_path.exists():
        print(f"Error: Task path {task_path} does not exist")
        sys.exit(1)

    print("Validating setup...")
    print(f"Task: {task_path}")
    if overrides_path:
        print(f"Overrides: {overrides_path}")

    is_valid = runner.validate_setup(task_path, overrides_path)

    if is_valid:
        print("✓ Setup validation passed!")
        if overrides_path:
            # Show some override details
            manager = OverridesManager(overrides_path)
            overrides = manager.load_overrides()
            if overrides.repo:
                print(f"Repository: {overrides.repo.git_url}")
                print(f"Branch: {overrides.repo.branch}")
            if overrides.openai_config and overrides.openai_config.base_url:
                print(f"OpenAI Base URL: {overrides.openai_config.base_url}")
            if overrides.inject_files:
                print(f"Files to inject: {len(overrides.inject_files)}")
    else:
        print("✗ Setup validation failed!")
        sys.exit(1)


def list_providers(args: argparse.Namespace) -> None:
    """List available providers."""
    runner = TaskRunner()

    providers = runner.list_providers()
    print("Available model providers:")
    for provider in providers:
        print(f"  - {provider}")

    # Also check for config file providers
    manager = OverridesManager()
    config_providers = manager.load_config_providers()
    if config_providers:
        print("\nProviders from config file:")
        for name, provider in config_providers.items():
            print(f"  - {name}: {provider.base_url}")


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        description="Spec Bench CLI - Advanced evaluation framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run evaluation with overrides
  uv run one-shot run-with-overrides ./task ./overrides.json

  # Quick evaluation with custom OpenAI endpoint
  uv run one-shot quick-eval ./task --model gpt-4 --openai-base-url https://eu.api.openai.com/v1

  # Batch evaluation from config
  uv run one-shot batch-eval ./config.toml

  # Set up custom provider
  uv run one-shot setup-provider myproxy https://my-proxy.example.com/v1

  # Validate setup
  uv run one-shot validate ./task ./overrides.json

  # List available providers
  uv run one-shot list-providers

Environment Variables:
  OPENAI_API_KEY        Your OpenAI API key
  OPENAI_BASE_URL       Custom OpenAI base URL
  CODEX_OSS_BASE_URL    OSS-compatible provider base URL
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Run with overrides command
    run_parser = subparsers.add_parser(
        'run-with-overrides',
        help='Run evaluation with overrides file'
    )
    run_parser.add_argument('task_path', help='Path to prepared task')
    run_parser.add_argument('overrides_path', help='Path to overrides JSON file')
    run_parser.add_argument('--model', default='gpt-4', help='Model to use')
    run_parser.add_argument('--rollouts', type=int, default=1, help='Number of rollouts')
    run_parser.add_argument('-v', '--verbose', action='count', default=0,
                          help='Increase verbosity (use -vv for full output)')
    run_parser.set_defaults(func=run_with_overrides)

    # Quick eval command
    quick_parser = subparsers.add_parser(
        'quick-eval',
        help='Run quick evaluation with minimal setup'
    )
    quick_parser.add_argument('task_path', help='Path to prepared task')
    quick_parser.add_argument('--model', default='gpt-4', help='Model to use')
    quick_parser.add_argument('--openai-base-url', help='Custom OpenAI base URL')
    quick_parser.add_argument('--api-key', help='API key (if not in environment)')
    quick_parser.add_argument('-v', '--verbose', action='count', default=0,
                            help='Increase verbosity (use -vv for full output)')
    quick_parser.set_defaults(func=quick_eval)

    # Batch eval command
    batch_parser = subparsers.add_parser(
        'batch-eval',
        help='Run batch evaluation from TOML config'
    )
    batch_parser.add_argument('config_path', help='Path to TOML configuration file')
    batch_parser.add_argument('-v', '--verbose', action='count', default=0,
                            help='Increase verbosity (use -vv for full output)')
    batch_parser.set_defaults(func=batch_eval)

    # Setup provider command
    provider_parser = subparsers.add_parser(
        'setup-provider',
        help='Set up a custom model provider'
    )
    provider_parser.add_argument('name', help='Provider name')
    provider_parser.add_argument('base_url', help='Provider base URL')
    provider_parser.add_argument('--env-key', default='OPENAI_API_KEY', help='Environment variable for API key')
    provider_parser.add_argument('--wire-api', default='responses', help='Wire API type')
    provider_parser.set_defaults(func=setup_provider)

    # Validate command
    validate_parser = subparsers.add_parser(
        'validate',
        help='Validate evaluation setup'
    )
    validate_parser.add_argument('task_path', help='Path to prepared task')
    validate_parser.add_argument('overrides_path', nargs='?', help='Path to overrides JSON file')
    validate_parser.set_defaults(func=validate_setup)

    # List providers command
    list_parser = subparsers.add_parser(
        'list-providers',
        help='List available model providers'
    )
    list_parser.set_defaults(func=list_providers)

    return parser


def main() -> None:
    """Main CLI entry point."""
    parser = create_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
