"""
Workflow Runner
===============
Main entry point for the GitHub Actions workflow.

This module orchestrates:
1. Test generation
2. Test validation (running the tests)
3. Retry loop with error feedback
4. Output JSON for workflow to consume

Usage (from GitHub Actions):
    python -m src.github_ops.workflow_runner \
        --repo "owner/repo" \
        --pr 123 \
        --target-repo "/path/to/repo" \
        --max-retries 2 \
        --output-json "result.json"
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from src.github_ops.test_runner import TestRunner, TestResult

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.pipeline import ContractTestPipeline, PipelineResult
from src.test_generator.generator import GeneratorConfig


@dataclass
class WorkflowResult:
    """Result for GitHub Actions workflow."""
    tests_pass: bool = False
    has_tests: bool = False
    attempts: int = 0
    language: str = "unknown"
    generated_files: list = None
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.generated_files is None:
            self.generated_files = []
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(asdict(self), indent=2)


def run_tests_for_language(
    test_dir: Path,
    language: str,
    repo_path: Path
) -> tuple[bool, str]:
    """
    Run tests using the TestRunner class.
    
    Returns:
        Tuple of (success, error_output)
    """
    print(f"\n[Runner] Running {language} tests in {test_dir}...")
    
    try:
        runner = TestRunner(
            language=language,
            repo_path=str(repo_path),
            install_deps=False  # Already installed earlier in workflow
        )
        
        # Get test files
        test_files = [str(f.relative_to(repo_path)) for f in test_dir.glob("*")]
        
        if not test_files:
            return True, ""  # No tests to run
        
        result = runner.run_tests(test_files=test_files)
        
        if result.passed:
            print("[Runner] ✅ Tests PASSED")
            return True, ""
        else:
            print(f"[Runner] ❌ Tests FAILED (exit code: {result.exit_code})")
            return False, result.error_message or result.output
            
    except Exception as e:
        print(f"[Runner] Error running tests: {e}")
        return False, str(e)


def extract_error_lines(output: str, language: str) -> str:
    """Extract the most relevant error lines from test output."""
    lines = output.split("\n")
    
    # Language-specific error patterns
    patterns = {
        "javascript": ["Error:", "TypeError:", "ReferenceError:", "FAIL", "expect(", "Cannot find", "SyntaxError"],
        "typescript": ["Error:", "TypeError:", "ReferenceError:", "FAIL", "expect(", "Cannot find", "TS", "SyntaxError"],
        "go": ["FAIL", "panic:", "Error:", "undefined:", "cannot", "syntax error"],
        "python": ["Error:", "AssertionError:", "FAILED", "ImportError:", "ModuleNotFoundError:", "SyntaxError"],
    }
    
    keywords = patterns.get(language, ["Error:", "FAIL", "Exception:"])
    
    error_lines = []
    capture_next = 0
    
    for line in lines:
        # Start capturing on error keywords
        if any(kw in line for kw in keywords):
            error_lines.append(line)
            capture_next = 5  # Capture next 5 lines for context
        elif capture_next > 0:
            error_lines.append(line)
            capture_next -= 1
        
        if len(error_lines) >= 30:
            break
    
    if error_lines:
        return "\n".join(error_lines)
    
    # Return last 20 lines if no specific errors found
    return "\n".join(lines[-20:])


def main():
    parser = argparse.ArgumentParser(description="Workflow Runner for AI Contract Testing")
    parser.add_argument("--repo", required=True, help="Repository in owner/repo format")
    parser.add_argument("--pr", type=int, required=True, help="Pull Request number")
    parser.add_argument("--target-repo", required=True, help="Path to target repository")
    parser.add_argument("--max-retries", type=int, default=2, help="Maximum retry attempts")
    parser.add_argument("--output-json", help="Path to write JSON result")
    parser.add_argument("--language", help="Override detected language")
    
    args = parser.parse_args()
    
    target_path = Path(args.target_repo).resolve()
    test_dir = target_path / "tests" / "contract-tests"
    
    # Initialize result
    result = WorkflowResult()
    
    # Initialize pipeline
    config = GeneratorConfig.from_env()
    pipeline = ContractTestPipeline(generator_config=config)
    
    revision_feedback = None
    
    # Main retry loop
    for attempt in range(1, args.max_retries + 2):
        result.attempts = attempt
        
        print(f"\n{'=' * 60}")
        print(f"ATTEMPT {attempt} of {args.max_retries + 1}")
        print(f"{'=' * 60}")
        
        # Step 1: Generate tests
        print("\n[Runner] Generating tests...")
        pipeline_result = pipeline.run(
            repo=args.repo,
            pr_number=args.pr,
            force_language=args.language,
            revision_feedback=revision_feedback
        )
        
        result.language = pipeline_result.detected_language
        
        # Check if tests were generated
        if not pipeline_result.has_tests:
            if pipeline_result.skip_reason:
                result.skip_reason = pipeline_result.skip_reason
                print(f"[Runner] Skipped: {result.skip_reason}")
            elif pipeline_result.error:
                result.error = pipeline_result.error
                print(f"[Runner] Error: {result.error}")
            break
        
        result.has_tests = True
        
        # Step 2: Copy tests to target repo
        print("\n[Runner] Writing tests to target repo...")
        test_dir.mkdir(parents=True, exist_ok=True)
        
        # Clear previous tests
        for f in test_dir.glob("*"):
            f.unlink()
        
        generated_files = []
        for test in pipeline_result.generated_tests:
            test_file = test_dir / test.filename
            test_file.write_text(test.code)
            generated_files.append(test.filename)
            print(f"  Wrote: {test.filename}")
        
        result.generated_files = generated_files
        
        # Step 3: Run tests
        print("\n[Runner] Validating tests...")
        success, error_output = run_tests_for_language(
            test_dir=test_dir,
            language=result.language,
            repo_path=target_path
        )
        
        if success:
            result.tests_pass = True
            print(f"\n[Runner] ✅ Tests validated on attempt {attempt}")
            break
        
        # Tests failed
        if attempt <= args.max_retries:
            print(f"\n[Runner] Tests failed, preparing retry...")
            
            # Build feedback for AI
            revision_feedback = f"""
The generated tests FAILED when executed. Fix these errors:

{error_output}

IMPORTANT:
1. Fix ALL syntax errors
2. Use correct imports for {result.language}
3. Do NOT create fake client classes - use actual consumer functions
4. Ensure all dependencies exist
"""
        else:
            print(f"\n[Runner] ❌ Tests failed after {args.max_retries + 1} attempts")
            result.error = "Tests failed validation after all retry attempts"
    
    # Write result JSON
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.write_text(result.to_json())
        print(f"\n[Runner] Wrote result to {output_path}")
    
    # Print summary
    print(f"\n{'=' * 60}")
    print("WORKFLOW RESULT")
    print(f"{'=' * 60}")
    print(f"Tests Generated: {result.has_tests}")
    print(f"Tests Validated: {result.tests_pass}")
    print(f"Attempts: {result.attempts}")
    print(f"Language: {result.language}")
    if result.skip_reason:
        print(f"Skip Reason: {result.skip_reason}")
    if result.error:
        print(f"Error: {result.error}")
    
    # Exit with appropriate code
    sys.exit(0 if result.tests_pass or result.skip_reason else 1)


if __name__ == "__main__":
    main()
