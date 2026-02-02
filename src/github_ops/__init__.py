"""
GitHub Operations Package
=========================
Modules for GitHub operations, test validation, and workflow orchestration.

Modules:
- pr_creator: Creates PRs with generated tests
- test_runner: Runs tests to validate generated code
- validation_loop: Orchestrates generate → test → retry loop
- workflow_runner: Main entry point for GitHub Actions
"""

from .pr_creator import PRCreator, PRCreationResult, GeneratedTestFile

__all__ = [
    "PRCreator",
    "PRCreationResult",
    "GeneratedTestFile",
]
