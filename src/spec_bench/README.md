# Spec Bench - Advanced Evaluation Framework

Spec Bench is a specialized evaluation framework for running AI coding evaluations with comprehensive override support, including custom OpenAI endpoints, repository configurations, and file injections.

## Features

- **OpenAI Endpoint Customization**: Support for regional endpoints, custom providers, and OSS-compatible hosts
- **Repository Overrides**: Clone from different repositories and branches
- **File Injection**: Inject custom documentation and specifications
- **Environment Management**: Configure environment variables and system settings
- **Batch Evaluation**: Run multiple evaluation tasks with different configurations
- **Comprehensive Overrides**: Full control over task preparation and execution

## Quick Start

### Basic Evaluation with OpenAI Override

```python
from spec_bench import TaskRunner
from pathlib import Path

runner = TaskRunner()

# Quick evaluation with custom OpenAI endpoint
result = runner.quick_eval(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    model="gpt-4",
    openai_base_url="https://eu.api.openai.com/v1"
)

print(f"Success: {result.success}, Score: {result.score}")
```

### Evaluation with Overrides File

```python
# Run with comprehensive overrides
result = runner.run_with_overrides(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    overrides_path=Path("data/tasks/prepared/high-sokoban/overrides.json"),
    model="gpt-4",
    rollouts=3
)
```

### Batch Evaluation from Config

```python
# Run multiple tasks from TOML configuration
results = runner.run_from_config(Path("configs/env_bench.toml"))
summary = runner.get_evaluation_summary(results)
print(f"Success rate: {summary['success_rate']:.2%}")
```

## OpenAI Endpoint Configuration

### Environment Variables

```bash
# Regional OpenAI endpoint
export OPENAI_BASE_URL="https://eu.api.openai.com/v1"
export OPENAI_API_KEY="your-api-key"

# OSS-compatible provider
export CODEX_OSS_BASE_URL="http://localhost:11434/v1"
export OPENAI_API_KEY="your-oss-api-key"
```

### Custom Provider Configuration

```python
# Set up a custom provider
runner.setup_custom_provider(
    name="myproxy",
    base_url="https://my-proxy.example.com/v1",
    env_key="MY_API_KEY",
    wire_api="responses"
)

# Use the custom provider
result = runner.quick_eval(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    model="myproxy:gpt-4"
)
```

### Config File Setup

Create `~/.codex/config.toml`:

```toml
model_provider = "myproxy"
model = "o4-mini"

[model_providers.myproxy]
name = "OpenAI-compatible"
base_url = "https://my-proxy.example.com/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

## Overrides Configuration

### Basic Overrides Structure

```json
{
  "remove_repo_paths": [
    "old_directory",
    "obsolete_files"
  ],
  "inject_files": [
    {
      "path": "AGENTS.md",
      "content": "# Agent Implementation Guide\n\nYour documentation here..."
    }
  ],
  "lm_instructions": "Custom instructions for the language model...",
  "repo": {
    "git_url": "https://github.com/your-org/your-repo",
    "branch": "feature-branch",
    "start_commit_sha": "abc123",
    "end_commit_sha": "def456"
  },
  "openai": {
    "base_url": "https://custom.api.com/v1",
    "api_key": "custom-key",
    "model": "gpt-4-turbo",
    "provider": "custom-provider"
  },
  "environment_variables": {
    "CUSTOM_VAR": "value",
    "DEBUG_MODE": "true"
  }
}
```

### Regional OpenAI Setup

```python
# EU regional endpoint
env_vars = runner.setup_regional_openai("eu", "gpt-4")
overrides = runner.create_environment_override(env_vars)

result = runner.run_with_docker_overrides(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    docker_overrides=overrides
)
```

### OSS Provider Setup

```python
# Ollama or vLLM setup
env_vars = runner.setup_oss_provider(
    base_url="http://localhost:11434/v1",
    model="codellama:34b",
    api_key="ollama-key"
)

result = runner.run_with_docker_overrides(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    docker_overrides=runner.create_environment_override(env_vars)
)
```

## Advanced Usage

### Custom Evaluation Workflow

```python
from spec_bench import SpecEvaluator

evaluator = SpecEvaluator()

# Validate setup before running
if not evaluator.validate_setup(task_path, overrides_path):
    print("Setup validation failed")
    exit(1)

# Run evaluation
result = evaluator.run_evaluation(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    overrides_path=Path("overrides.json"),
    model="gpt-4",
    rollouts=5
)

# Process results
if result.success:
    print(f"Evaluation successful! Score: {result.score}")
    if result.output_path:
        print(f"Output available at: {result.output_path}")
else:
    print(f"Evaluation failed: {result.error_message}")
```

### Working with Overrides Manager

```python
from spec_bench.overrides import OverridesManager

manager = OverridesManager(overrides_path)

# Load and inspect overrides
overrides = manager.load_overrides()
print(f"Repository: {overrides.repo.git_url}")
print(f"OpenAI config: {overrides.openai_config}")

# Get environment variables for OpenAI
env_vars = manager.get_openai_env_vars()
print("Required environment variables:", env_vars)
```

### File Injection Examples

```python
# Inject multiple documentation files
inject_files = [
    {
        "path": "AGENTS.md",
        "content": "# Implementation Guide\n\nDetailed instructions for agents..."
    },
    {
        "path": "sokoban/README.md",
        "content": "# Sokoban Implementation\n\nSpecific implementation details..."
    }
]

overrides = runner.create_environment_override(
    env_vars={"DEBUG": "true"},
    inject_files=inject_files,
    lm_instructions="Follow the AGENTS.md guide carefully..."
)

result = runner.run_with_docker_overrides(
    task_path=Path("data/tasks/prepared/high-sokoban"),
    docker_overrides=overrides
)
```

## Configuration Files

### TOML Configuration Example

```toml
name = "spec_bench_eval"
parallel = 1

[[tasks]]
prepared_dir = "/path/to/prepared/task"
model = "gpt-4"
rollouts = 3
apply_overrides = true
overrides = "/path/to/overrides.json"

[[tasks]]
prepared_dir = "/path/to/another/task"
model = "claude-3"
rollouts = 1
apply_overrides = false
```

### JSON Overrides Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "remove_repo_paths": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Paths to remove from repository"
    },
    "inject_files": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "path": {"type": "string"},
          "content": {"type": "string"}
        },
        "required": ["path", "content"]
      },
      "description": "Files to inject into the repository"
    },
    "lm_instructions": {
      "type": "string",
      "description": "Custom instructions for the language model"
    },
    "repo": {
      "type": "object",
      "properties": {
        "git_url": {"type": "string"},
        "branch": {"type": "string"},
        "start_commit_sha": {"type": "string"},
        "end_commit_sha": {"type": "string"},
        "subdir": {"type": "string"},
        "sparse_checkout": {
          "type": "array",
          "items": {"type": "string"}
        }
      },
      "required": ["git_url"]
    },
    "openai": {
      "type": "object",
      "properties": {
        "base_url": {"type": "string"},
        "api_key": {"type": "string"},
        "model": {"type": "string"},
        "provider": {"type": "string"}
      }
    },
    "environment_variables": {
      "type": "object",
      "additionalProperties": {"type": "string"}
    }
  }
}
```

## Troubleshooting

### Common Issues

1. **API Key Not Set**
   ```bash
   export OPENAI_API_KEY="your-key-here"
   ```

2. **Invalid Overrides JSON**
   ```python
   from spec_bench import TaskRunner
   runner = TaskRunner()
   if not runner.evaluator.validate_overrides(Path("overrides.json")):
       print("Fix your overrides file")
   ```

3. **Provider Not Configured**
   ```python
   runner.setup_custom_provider("myprovider", "https://api.example.com/v1")
   ```

4. **Missing Dependencies**
   ```bash
   pip install tomli tomli_w  # For TOML support
   ```

### Debug Mode

```python
import os
os.environ['DEBUG'] = 'true'

# Enable verbose logging
result = runner.run_with_overrides(
    task_path=task_path,
    overrides_path=overrides_path,
    model="gpt-4"
)

if not result.success:
    print("Detailed error:", result.error_message)
    print("Debug info:", result.metrics)
```

## API Reference

### TaskRunner Methods

- `run_with_overrides(task_path, overrides_path, model="gpt-4", rollouts=1)` - Run with overrides
- `run_from_config(config_path)` - Run batch from TOML config
- `quick_eval(task_path, model="gpt-4", openai_base_url=None, api_key=None)` - Simple evaluation
- `setup_custom_provider(name, base_url, env_key, wire_api)` - Configure custom provider
- `validate_setup(task_path, overrides_path=None)` - Validate configuration
- `get_evaluation_summary(results)` - Summarize batch results

### SpecEvaluator Methods

- `run_evaluation(task_path, overrides_path, model, rollouts)` - Core evaluation
- `run_batch_evaluation(config_path)` - Batch evaluation
- `validate_overrides(overrides_path)` - Validate overrides file
- `create_custom_provider(name, base_url, **kwargs)` - Create provider

### OverridesManager Methods

- `load_overrides(overrides_path)` - Load overrides configuration
- `apply_overrides(task_config)` - Apply overrides to task
- `get_openai_env_vars()` - Get OpenAI environment variables
- `resolve_openai_config(overrides_config)` - Resolve OpenAI settings

## Examples

See the `examples/` directory for complete working examples:

- `examples/basic_evaluation.py` - Simple evaluation setup
- `examples/custom_provider.py` - Custom OpenAI provider setup
- `examples/batch_evaluation.py` - Multi-task evaluation
- `examples/regional_openai.py` - Regional endpoint configuration
