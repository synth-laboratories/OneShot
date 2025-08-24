"""
Spec Bench - Specialized evaluation framework for synthetic environments.

This module provides tools for running evaluations with overrides, managing task
preparation, and orchestrating benchmark evaluations.
"""

from .evaluator import SpecEvaluator
from .overrides import OverridesManager
from .task_runner import TaskRunner

__version__ = "0.1.0"
__all__ = ["SpecEvaluator", "OverridesManager", "TaskRunner"]
