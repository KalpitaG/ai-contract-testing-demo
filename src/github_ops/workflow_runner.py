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
    is_revision: bool = False
    stopped_early: bool = False  # True if same error detected
    
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


def errors_are_similar(error1: str, error2: str, threshold: float = 0.6) -> bool:
    """
    Check if two errors are similar enough to stop retrying.
    
    Uses word overlap comparison. If errors share >60% of keywords,
    they're considered the same error that AI couldn't fix.
    
    Args:
        error1: First error message
        error2: Second error message  
        threshold: Similarity threshold (0.0 to 1.0)
        
    Returns:
        True if errors are similar enough to stop retrying
    """
    if not error1 or not error2:
        return False
    
    # Common words to ignore
    common_words = {
        'the', 'a', 'an', 'is', 'at', 'in', 'on', 'for', 'to', 'of', 'and', 'or',
        'it', 'be', 'as', 'was', 'with', 'that', 'this', 'from', 'by', 'are',
        'not', 'but', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'can', 'cannot', 'been', 'being',
        'test', 'tests', 'error', 'failed', 'expected', 'received', 'line'
    }
    
    def extract_keywords(text: str) -> set:
        """Extract meaningful keywords from error text."""
        words = set(text.lower().split())
        return {w for w in words 
                if w not in common_words 
                and len(w) > 2 
                and not w.isdigit()}
    
    words1 = extract_keywords(error1)
    words2 = extract_keywords(error2)
    
    if not words1 or not words2:
        return False
    
    # Calculate Jaccard similarity
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    similarity = intersection / union if union > 0 else 0
    
    print(f"[Runner] Error similarity: {similarity:.1%} (threshold: {threshold:.0%})")
    
    return similarity >= threshold


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
    parser.add_argument("--revision-feedback", help="Developer feedback for revision mode")
    
    args = parser.parse_args()
    
    try:
        _run_main(args)
    except Exception as e:
        print(f"\n[Runner] FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Still exit 0 so workflow can continue and report the error
        sys.exit(0)


def _run_main(args):
    """Main logic wrapped for error handling."""
    target_path = Path(args.target_repo).resolve()
    test_dir = target_path / "tests" / "contract-tests"
    
    # Initialize result
    result = WorkflowResult()
    result.is_revision = args.revision_feedback is not None
    
    # Collect existing tests for revision mode
    existing_tests = {}
    if result.is_revision:
        print("\n" + "=" * 60)
        print("REVISION MODE")
        print("=" * 60)
        feedback_preview = args.revision_feedback[:200] + "..." if len(args.revision_feedback) > 200 else args.revision_feedback
        print(f"Developer feedback: {feedback_preview}")
        
        # Only collect tests that were generated in THIS PR (not old tests from main)
        # We do this by checking git status for new/modified files in tests/contract-tests/
        if test_dir.exists():
            print("\n[Runner] Collecting tests generated for this PR...")
            import subprocess
            try:
                # Get list of new/modified test files in this branch
                git_result = subprocess.run(
                    ["git", "diff", "--name-only", "origin/main", "--", "tests/contract-tests/"],
                    cwd=target_path,
                    capture_output=True,
                    text=True
                )
                changed_test_files = [f.split("/")[-1] for f in git_result.stdout.strip().split("\n") if f.endswith(".test.js") or f.endswith(".test.ts")]
                
                if changed_test_files:
                    for filename in changed_test_files:
                        test_file = test_dir / filename
                        if test_file.exists():
                            try:
                                content = test_file.read_text()
                                existing_tests[filename] = content
                                print(f"  [OK] {filename} ({len(content)} chars)")
                            except Exception as e:
                                print(f"  [WARN] Failed to read {filename}: {e}")
                    print(f"  Found {len(existing_tests)} tests from this PR")
                else:
                    print("  [WARN] No changed test files found in this PR")
            except Exception as e:
                print(f"  [WARN] Git diff failed: {e}, falling back to all tests")
                # Fallback: collect all (but limit to 5 most recent)
                test_files = sorted(test_dir.glob("*.test.js"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
                for test_file in test_files:
                    try:
                        content = test_file.read_text()
                        existing_tests[test_file.name] = content
                        print(f"  [OK] {test_file.name} ({len(content)} chars)")
                    except Exception:
                        pass
    
    # Initialize pipeline
    config = GeneratorConfig.from_env()
    pipeline = ContractTestPipeline(generator_config=config)
    
    # Track errors for same-error detection
    previous_error = None
    revision_feedback = args.revision_feedback  # Start with developer feedback (if any)
    
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
            revision_feedback=revision_feedback,
            existing_tests=existing_tests if result.is_revision else None
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

        # Clean test directory before writing new files.
        # AI may generate different filenames between attempts (e.g.,
        # "items.pact.test.js" in attempt 1, "items-api.pact.test.js" in attempt 2).
        # Without cleanup, stale files accumulate and cause duplicate interactions.
        if attempt > 1:
            print("  Cleaning stale test files from previous attempt...")
            for old_file in test_dir.glob("*.test.js"):
                print(f"    Removing: {old_file.name}")
                old_file.unlink()
        
        generated_files = []
        for test in pipeline_result.generated_tests:
            # Extract just the filename (AI may return full path like "tests/contract-tests/name.js")
            filename = Path(test.filename).name
            test_file = test_dir / filename
            if test_file.exists():
                print(f"  Overwriting: {filename}")
            else:
                print(f"  Creating: {filename}")
            test_file.write_text(test.code)
            generated_files.append(filename)
        
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
        
        # Step 4: Check if same error as before (stop early)
        if previous_error and errors_are_similar(previous_error, error_output):
            print(f"\n[Runner] ⚠️ Same error repeated - AI cannot fix this automatically")
            result.stopped_early = True
            result.error = "Same error occurred twice - stopping early"
            break
        
        previous_error = error_output
        
        # Step 5: Prepare for retry (if attempts remaining)
        if attempt <= args.max_retries:
            print(f"\n[Runner] Tests failed, preparing retry...")
            
            # Build feedback for AI
            revision_feedback = (
                f"The generated tests FAILED when executed. Fix these errors:\n\n"
                f"{error_output[:4000]}\n\n"
                f"IMPORTANT:\n"
                f"1. Fix ALL syntax errors\n"
                f"2. Use correct imports for {result.language}\n"
                f"3. Do NOT create fake client classes - use actual consumer functions\n"
                f"4. Ensure all dependencies exist\n"
                f"5. Query parameters must use string() matcher, never boolean() or integer()\n"
                f"6. Consumer and provider names must match Pactflow exactly"
            )
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
    print(f"Mode: {'REVISION' if result.is_revision else 'INITIAL GENERATION'}")
    print(f"Tests Generated: {result.has_tests}")
    print(f"Tests Validated: {result.tests_pass}")
    print(f"Attempts: {result.attempts}")
    print(f"Language: {result.language}")
    if result.stopped_early:
        print(f"Stopped Early: Yes (same error repeated)")
    if result.skip_reason:
        print(f"Skip Reason: {result.skip_reason}")
    if result.error:
        print(f"Error: {result.error}")
    print(f"Generated Files: {result.generated_files}")
    
    # Exit code: always 0 so workflow continues to create PR
    # The workflow uses the JSON output to determine status
    sys.exit(0)


if __name__ == "__main__":
    main()
