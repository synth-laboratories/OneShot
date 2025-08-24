# Overrides - Managing Evaluation Sandbox

Control what files are included in or excluded from the evaluation sandbox using overrides.

## Understanding Overrides

Overrides allow you to customize the evaluation environment by:
- **Removing files/directories** from the cloned repository
- **Injecting new files** into the sandbox
- **Modifying repository settings** (URL, branch, commit)
- **Customizing LM instructions**
- **Setting environment variables**

## Basic Overrides Structure

Create `data/tasks/prepared/your-task/overrides.json`:

```json
{
  "remove_repo_paths": [
    "node_modules",
    ".git",
    "*.log",
    "temp/"
  ],
  "inject_files": [
    {
      "path": "AGENTS.md",
      "content": "# Implementation Guide\n\nCustom instructions for the agent..."
    },
    {
      "path": "test_data.json",
      "content": "{\"test\": true, \"config\": \"custom\"}"
    }
  ],
  "repo": {
    "git_url": "https://github.com/your-org/your-repo",
    "branch": "feature-branch",
    "start_commit_sha": "abc123",
    "end_commit_sha": "def456"
  },
  "lm_instructions": "Custom instructions for the language model agent...",
  "environment_variables": {
    "DEBUG": "true",
    "CUSTOM_CONFIG": "production"
  }
}
```

## File Removal (remove_repo_paths)

Remove files and directories from the sandbox:

```json
{
  "remove_repo_paths": [
    "node_modules",
    "dist/",
    "*.log",
    "temp/",
    ".env",
    ".git/",
    "docs/",
    "tests/__pycache__/"
  ]
}
```

**Patterns supported:**
- Exact filenames: `"package-lock.json"`
- Directories: `"node_modules/"` (trailing slash)
- Wildcards: `"*.log"`, `"temp/*"`
- Hidden files: `".env"`, `".git/"`

## File Injection (inject_files)

Add new files to the sandbox:

```json
{
  "inject_files": [
    {
      "path": "README_AGENT.md",
      "content": "# Agent Instructions\n\nThis file contains special instructions..."
    },
    {
      "path": "config/production.json",
      "content": "{\n  \"database\": \"prod\",\n  \"debug\": false\n}"
    },
    {
      "path": "test_data/sample.txt",
      "content": "Sample test data for evaluation"
    }
  ]
}
```

**Injection locations:**
- Root directory: `"AGENTS.md"`
- Subdirectories: `"config/production.json"`
- Nested paths: `"test_data/input/sample.txt"`

## Repository Overrides (repo)

Change repository settings:

```json
{
  "repo": {
    "git_url": "https://github.com/your-org/forked-repo",
    "branch": "feature-branch",
    "start_commit_sha": "abc123def456",
    "end_commit_sha": "fed654cba321",
    "subdir": "packages/core",
    "sparse_checkout": [
      "src/",
      "package.json",
      "README.md"
    ]
  }
}
```

**Repository options:**
- **`git_url`**: Clone from different repository
- **`branch`**: Use specific branch
- **`start_commit_sha`**: Pin to specific commit
- **`end_commit_sha`**: Ensure evaluation at specific commit
- **`subdir`**: Only include subdirectory (monorepo support)
- **`sparse_checkout`**: Only clone specific files/directories

## LM Instructions Override

Provide custom instructions to the language model:

```json
{
  "lm_instructions": "# Custom Agent Instructions\n\nYou are evaluating a Node.js project. Follow these specific guidelines:\n\n1. Use ES6+ syntax\n2. Follow the existing code style\n3. Focus on the core functionality\n4. Ignore cosmetic changes\n\n## Specific Requirements\n- Use async/await for all async operations\n- Add error handling for all network calls\n- Include JSDoc comments for exported functions\n- Follow the existing naming conventions"
}
```

## Environment Variables

Set environment variables in the sandbox:

```json
{
  "environment_variables": {
    "NODE_ENV": "production",
    "DEBUG": "app:*",
    "DATABASE_URL": "postgresql://localhost:5432/mydb",
    "API_KEY": "your-api-key-here",
    "LOG_LEVEL": "info"
  }
}
```

## Advanced Examples

### Development vs Production Setup

```json
{
  "inject_files": [
    {
      "path": "config.json",
      "content": "{\"env\": \"development\", \"debug\": true}"
    }
  ],
  "environment_variables": {
    "NODE_ENV": "development",
    "DEBUG": "true"
  }
}
```

### Minimal Test Environment

```json
{
  "remove_repo_paths": [
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    "*.log",
    ".git/",
    "docs/",
    "examples/",
    "scripts/"
  ],
  "inject_files": [
    {
      "path": "test-config.json",
      "content": "{\"testMode\": true, \"mockData\": true}"
    }
  ]
}
```

### Monorepo Subdirectory Focus

```json
{
  "repo": {
    "git_url": "https://github.com/your-org/monorepo",
    "branch": "main",
    "subdir": "packages/web-app",
    "sparse_checkout": [
      "packages/web-app/",
      "package.json",
      "yarn.lock"
    ]
  },
  "remove_repo_paths": [
    "packages/mobile-app/",
    "packages/desktop-app/",
    "tools/",
    "docs/"
  ]
}
```

## Using Overrides with Commands

### Run with Overrides (Docker)

```bash
# Method 1: Create overrides.json and run normally
echo '{"remove_repo_paths": ["node_modules"]}' > data/tasks/prepared/your-task/overrides.json
scripts/run_codex_box.sh data/tasks/prepared/your-task

# Method 2: Use the CLI with overrides
uv run one-shot run-with-overrides data/tasks/prepared/your-task overrides.json
```

### Run with Overrides (Modal)

```bash
# Modal automatically uses overrides.json if present
SANDBOX_BACKEND=modal scripts/run_codex_box.sh data/tasks/prepared/your-task
```

### Batch Evaluation with Overrides

```bash
# All tasks in batch will use their individual overrides.json
python scripts/eval_rollouts.py run configs/your_config.toml
```

## Override Precedence

1. **Task-specific overrides.json** (highest priority)
2. **CLI-provided overrides file**
3. **Default settings** (lowest priority)

## Validation

Validate your overrides file:

```bash
# Check syntax and structure
uv run one-shot validate data/tasks/prepared/your-task overrides.json

# Or check manually
python -c "import json; print('Valid JSON') if json.load(open('overrides.json')) else print('Invalid')"
```

## Debugging Overrides

### Check Applied Overrides

The evaluation log shows which overrides were applied:

```bash
# Check the run logs
cat data/runs/<run_id>/logs/eval.log

# Look for override-related messages:
# [overrides] Using overrides file: /path/to/overrides.json
# [overrides] Applying repo overrides to tb_meta.json
# [overrides] Writing remove_repo_paths to overlay_files/remove_repo_paths.txt
```

### Inspect Sandbox Contents

After running, check what files are actually in the sandbox:

```bash
# List files in the prepared task (before Docker)
ls -la data/tasks/prepared/your-task/

# Check Docker image contents (after run)
docker run --rm your-image ls -la /app/repo/
```

## Common Patterns

### Clean Development Environment

```json
{
  "remove_repo_paths": [
    "node_modules/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "*.log",
    ".DS_Store",
    "coverage/"
  ],
  "inject_files": [
    {
      "path": ".env",
      "content": "NODE_ENV=development\nDEBUG=true"
    }
  ]
}
```

### Minimal API Testing

```json
{
  "remove_repo_paths": [
    "frontend/",
    "docs/",
    "scripts/",
    "tests/e2e/"
  ],
  "inject_files": [
    {
      "path": "test_config.json",
      "content": "{\"api_url\": \"http://localhost:3000\", \"mock_responses\": true}"
    }
  ],
  "environment_variables": {
    "API_MOCK": "true",
    "TEST_MODE": "true"
  }
}
```

### Language-Specific Setup

**Python:**
```json
{
  "remove_repo_paths": [
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".coverage"
  ],
  "inject_files": [
    {
      "path": "requirements-test.txt",
      "content": "pytest==7.4.0\npytest-cov==4.0.0"
    }
  ]
}
```

**Go:**
```json
{
  "remove_repo_paths": [
    "vendor/",
    "*.test",
    "coverage.out"
  ],
  "inject_files": [
    {
      "path": "config_test.yaml",
      "content": "database: \"sqlite://test.db\"\ndebug: true"
    }
  ]
}
```

## Troubleshooting

### Overrides Not Applied

**Check file location:**
- Must be named `overrides.json`
- Must be in task directory: `data/tasks/prepared/your-task/overrides.json`

**Check syntax:**
```bash
python -c "import json; json.load(open('overrides.json')); print('Valid JSON')"
```

### File Injection Issues

**Path problems:**
- Use forward slashes: `"config/prod.json"`
- Paths are relative to repo root
- Parent directories are created automatically

**Content encoding:**
- All content should be plain text
- Use `\n` for line breaks in JSON strings
- Escape special characters: `\"` for quotes

### Repository Override Issues

**Git access:**
- Ensure repository is public or you have access
- Check branch name spelling
- Verify commit SHAs exist

**Sparse checkout:**
- Only works with Git 2.25+
- Patterns are relative to repo root
- Empty sparse_checkout array = full clone

### Performance Impact

**Large removals:**
- Many `remove_repo_paths` can slow Docker build
- Use specific patterns instead of wildcards when possible
- Consider using `sparse_checkout` for very large repos

**Many injections:**
- Large files impact Docker image size
- Consider external file mounting for large data files
- Use compression for text content when possible
