"""
Validation Loop
===============
Orchestrates the generate → test → retry loop for contract tests.

Flow:
1. Generate tests using AI
2. Copy tests to target repo
3. Run tests
4. If pass → Done
5. If fail → Regenerate with error feedback (max retries)
6. Return final status

Usage:
    from src.github_ops.validation_loop import ValidationLoop
    
    loop = ValidationLoop(
        pipeline=pipeline,
        test_runner=runner,
        max_retries=2
    )
    
    result = loop.run(
        repo="owner/repo",
        pr_number=123,
        target_repo_path="/path/to/repo"
    )
    
    if result.tests_pass:
        print("Tests validated!")
    else:
        print(f"Tests failed after {result.attempts} attempts")
"""

import shutil
from typing import Optional, List
from dataclasses import dataclass, field
from pathlib import Path

from src.pipeline import ContractTestPipeline, PipelineResult
from src.github_ops.test_runner import TestRunner, TestResult


@dataclass
class ValidationResult:
    """Result of the validation loop."""
    tests_pass: bool
    attempts: int
    final_pipeline_result: Optional[PipelineResult] = None
    test_results: List[TestResult] = field(default_factory=list)
    generated_files: List[str] = field(default_factory=list)
    final_error: Optional[str] = None
    
    def get_status_message(self) -> str:
        """Get a human-readable status message."""
        if self.tests_pass:
            return f"✅ Tests passed on attempt {self.attempts}"
        else:
            return f"❌ Tests failed after {self.attempts} attempt(s)"


class ValidationLoop:
    """
    Orchestrates the generate → test → retry loop.
    
    The loop:
    1. Generates tests using AI pipeline
    2. Copies tests to target repo
    3. Runs tests
    4. If tests fail, regenerates with error feedback
    5. Repeats up to max_retries times
    6. Returns final status
    """
    
    def __init__(
        self,
        pipeline: ContractTestPipeline,
        max_retries: int = 2,
        test_output_dir: str = "tests/contract-tests"
    ):
        """
        Initialize the validation loop.
        
        Args:
            pipeline: The contract test generation pipeline
            max_retries: Maximum retry attempts (default: 2)
            test_output_dir: Where to put tests in target repo
        """
        self.pipeline = pipeline
        self.max_retries = max_retries
        self.test_output_dir = test_output_dir
    
    def run(
        self,
        repo: str,
        pr_number: int,
        target_repo_path: str,
        force_language: Optional[str] = None
    ) -> ValidationResult:
        """
        Run the full validation loop.
        
        Args:
            repo: Repository in "owner/repo" format
            pr_number: Pull Request number
            target_repo_path: Path to the target repository on disk
            force_language: Override detected language (optional)
            
        Returns:
            ValidationResult with final status
        """
        result = ValidationResult(tests_pass=False, attempts=0)
        target_path = Path(target_repo_path)
        test_runner = TestRunner(target_repo_path)
        
        revision_feedback = None  # No feedback on first attempt
        
        for attempt in range(1, self.max_retries + 2):  # +1 for initial, +1 for index
            result.attempts = attempt
            
            print(f"\n{'=' * 60}")
            print(f"Validation Attempt {attempt}/{self.max_retries + 1}")
            print(f"{'=' * 60}")
            
            # Step 1: Generate tests
            print("\n[Validation] Step 1: Generating tests...")
            pipeline_result = self.pipeline.run(
                repo=repo,
                pr_number=pr_number,
                force_language=force_language,
                revision_feedback=revision_feedback
            )
            result.final_pipeline_result = pipeline_result
            
            # Check if generation succeeded
            if not pipeline_result.has_tests:
                if pipeline_result.skip_reason:
                    print(f"[Validation] Skipped: {pipeline_result.skip_reason}")
                    result.final_error = pipeline_result.skip_reason
                    return result
                elif pipeline_result.error:
                    print(f"[Validation] Error: {pipeline_result.error}")
                    result.final_error = pipeline_result.error
                    return result
                else:
                    print("[Validation] No tests generated")
                    result.final_error = "No tests generated"
                    return result
            
            # Step 2: Copy tests to target repo
            print("\n[Validation] Step 2: Copying tests to target repo...")
            test_dir = target_path / self.test_output_dir
            test_dir.mkdir(parents=True, exist_ok=True)
            
            # Clear previous generated tests
            for f in test_dir.glob("*"):
                if f.is_file():
                    f.unlink()
            
            generated_files = []
            for test in pipeline_result.generated_tests:
                test_file = test_dir / test.filename
                test_file.write_text(test.code)
                generated_files.append(str(test_file.relative_to(target_path)))
                print(f"  Wrote: {generated_files[-1]}")
            
            result.generated_files = generated_files
            
            # Step 3: Run tests
            print("\n[Validation] Step 3: Running tests...")
            all_passed = True
            
            for test_file in generated_files:
                language = pipeline_result.detected_language
                test_result = test_runner.run_tests(test_file, language)
                result.test_results.append(test_result)
                
                if not test_result.success:
                    all_passed = False
                    print(f"  ❌ {test_file} - FAILED")
                    print(f"     Error: {test_result.error_summary[:200]}...")
                else:
                    print(f"  ✅ {test_file} - PASSED")
            
            # Step 4: Check results
            if all_passed:
                print("\n[Validation] All tests passed!")
                result.tests_pass = True
                return result
            
            # Tests failed - prepare for retry
            if attempt <= self.max_retries:
                print(f"\n[Validation] Tests failed. Preparing retry {attempt + 1}...")
                
                # Build feedback from failed tests
                failed_tests = [t for t in result.test_results if not t.success]
                revision_feedback = self._build_feedback(failed_tests)
                
                # Clear test results for next attempt
                result.test_results = []
            else:
                print(f"\n[Validation] Tests failed after {self.max_retries + 1} attempts")
                result.final_error = "Tests failed validation after all retry attempts"
        
        return result
    
    def _build_feedback(self, failed_tests: List[TestResult]) -> str:
        """
        Build feedback string from failed test results.
        
        Args:
            failed_tests: List of TestResult objects that failed
            
        Returns:
            Formatted feedback for AI
        """
        feedback_parts = ["The generated tests failed when executed. Please fix these issues:\n"]
        
        for i, test in enumerate(failed_tests, 1):
            feedback_parts.append(f"\n### Test {i}: {test.test_file}")
            feedback_parts.append(test.get_error_for_ai())
        
        feedback_parts.append("\n\nIMPORTANT: Make sure to:")
        feedback_parts.append("1. Fix all syntax errors")
        feedback_parts.append("2. Use correct imports for the language")
        feedback_parts.append("3. Import actual consumer functions from the codebase")
        feedback_parts.append("4. Do NOT create fake/mock client classes")
        feedback_parts.append("5. Make sure all dependencies are available")
        
        return "\n".join(feedback_parts)


# =============================================================================
# Standalone Function for Workflow
# =============================================================================

def run_validation_loop(
    repo: str,
    pr_number: int,
    target_repo_path: str,
    max_retries: int = 2,
    force_language: Optional[str] = None,
    generator_config = None
) -> ValidationResult:
    """
    Convenience function to run the validation loop.
    
    This is the main entry point for the GitHub Actions workflow.
    
    Args:
        repo: Repository in "owner/repo" format
        pr_number: Pull Request number
        target_repo_path: Path to the target repository
        max_retries: Maximum retry attempts
        force_language: Override detected language
        generator_config: Optional generator configuration
        
    Returns:
        ValidationResult with final status
    """
    from src.pipeline import ContractTestPipeline
    from src.test_generator.generator import GeneratorConfig
    
    # Create pipeline
    config = generator_config or GeneratorConfig.from_env()
    pipeline = ContractTestPipeline(generator_config=config)
    
    # Create and run validation loop
    loop = ValidationLoop(pipeline=pipeline, max_retries=max_retries)
    
    return loop.run(
        repo=repo,
        pr_number=pr_number,
        target_repo_path=target_repo_path,
        force_language=force_language
    )


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run validation loop")
    parser.add_argument("repo", help="Repository in owner/repo format")
    parser.add_argument("pr", type=int, help="Pull Request number")
    parser.add_argument("target_path", help="Path to target repository")
    parser.add_argument("--max-retries", type=int, default=2, help="Max retry attempts")
    parser.add_argument("--language", help="Override detected language")
    
    args = parser.parse_args()
    
    result = run_validation_loop(
        repo=args.repo,
        pr_number=args.pr,
        target_repo_path=args.target_path,
        max_retries=args.max_retries,
        force_language=args.language
    )
    
    print(f"\n{result.get_status_message()}")
    print(f"Generated files: {result.generated_files}")
