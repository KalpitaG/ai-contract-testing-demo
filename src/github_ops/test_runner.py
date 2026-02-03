"""
Test Runner
===========
Runs generated contract tests to validate they work before creating PR.

This module:
1. Detects the test runner based on language (npm/go/pytest)
2. Installs dependencies if needed
3. Runs the generated tests
4. Captures output (pass/fail + error messages)
5. Returns structured result for retry logic

Usage:
    runner = TestRunner(language="javascript", repo_path="/path/to/repo")
    result = runner.run_tests(test_files=["tests/generated/user.pact.test.js"])
    
    if result.passed:
        print("Tests passed!")
    else:
        print(f"Tests failed: {result.error_message}")
"""

import os
import subprocess
import shutil
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class TestResult:
    """Result of running tests."""
    passed: bool
    output: str
    error_message: Optional[str] = None
    exit_code: int = 0
    command_used: str = ""
    
    def format_for_ai_retry(self) -> str:
        """Format the error for AI to understand and fix."""
        if self.passed:
            return "Tests passed successfully."
        
        return f"""## Test Execution Failed

**Command:** `{self.command_used}`
**Exit Code:** {self.exit_code}

### Error Output:
```
{self.error_message or self.output}
```

### Instructions for Fixing:
1. Analyze the error message above
2. Identify what went wrong (syntax error, import error, assertion error, etc.)
3. Generate corrected test code that fixes these specific issues
4. Make sure all imports are correct for the language
5. Ensure the test can actually run
"""


class TestRunner:
    """
    Runs generated tests to validate them before PR creation.
    
    Supports:
    - JavaScript/TypeScript: npm test / jest
    - Go: go test
    - Python: pytest
    - Java/Kotlin: gradle test / mvn test
    """
    
    # Test commands by language
    # Uses project's native test command to respect their configuration
    TEST_COMMANDS = {
        "javascript": {
            "install": "npm install",
            "test": "npm test -- --testPathPattern={test_path} --passWithNoTests",
            "test_single": "npm test -- {test_file} --passWithNoTests",
            "check_tool": "npm",
        },
        "typescript": {
            "install": "npm install",
            "test": "npm test -- --testPathPattern={test_path} --passWithNoTests",
            "test_single": "npm test -- {test_file} --passWithNoTests",
            "check_tool": "npm",
        },
        "go": {
            "install": "go mod download",
            "test": "go test -v {test_path}/...",
            "test_single": "go test -v {test_file}",
            "check_tool": "go",
        },
        "python": {
            "install": "pip install -r requirements.txt",
            "test": "pytest {test_path} -v",
            "test_single": "pytest {test_file} -v",
            "check_tool": "pytest",
        },
        "java": {
            "install": "./gradlew build -x test || mvn compile -DskipTests",
            "test": "./gradlew test --tests '*Pact*' || mvn test -Dtest='*Pact*'",
            "test_single": "./gradlew test --tests {test_class} || mvn test -Dtest={test_class}",
            "check_tool": "gradle",
        },
        "kotlin": {
            "install": "./gradlew build -x test",
            "test": "./gradlew test --tests '*Pact*'",
            "test_single": "./gradlew test --tests {test_class}",
            "check_tool": "gradle",
        },
    }
    
    def __init__(
        self,
        language: str,
        repo_path: str,
        timeout: int = 120,
        install_deps: bool = True
    ):
        """
        Initialize the test runner.
        
        Args:
            language: Programming language (javascript, typescript, go, python, java, kotlin)
            repo_path: Path to the repository root
            timeout: Timeout for test execution in seconds
            install_deps: Whether to install dependencies before running tests
        """
        self.language = language.lower()
        self.repo_path = Path(repo_path)
        self.timeout = timeout
        self.install_deps = install_deps
        
        if self.language not in self.TEST_COMMANDS:
            raise ValueError(f"Unsupported language: {language}. Supported: {list(self.TEST_COMMANDS.keys())}")
        
        self.commands = self.TEST_COMMANDS[self.language]
    
    def run_tests(
        self,
        test_files: list[str],
        test_path: str = "tests/generated"
    ) -> TestResult:
        """
        Run the generated tests.
        
        Args:
            test_files: List of test file paths relative to repo root
            test_path: Directory containing tests (for running all)
            
        Returns:
            TestResult with pass/fail status and output
        """
        print(f"[TestRunner] Running tests for {self.language} in {self.repo_path}")
        
        # Step 1: Install dependencies if needed
        if self.install_deps:
            install_result = self._install_dependencies()
            if not install_result.passed:
                print(f"[TestRunner] Dependency installation failed")
                return install_result
        
        # Step 2: Run the tests
        if len(test_files) == 1:
            # Run single test file
            return self._run_single_test(test_files[0])
        else:
            # Run all tests in path
            return self._run_test_path(test_path)
    
    def _install_dependencies(self) -> TestResult:
        """Install dependencies for the project."""
        install_cmd = self.commands["install"]
        print(f"[TestRunner] Installing dependencies: {install_cmd}")
        
        try:
            result = subprocess.run(
                install_cmd,
                shell=True,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if result.returncode != 0:
                return TestResult(
                    passed=False,
                    output=result.stdout,
                    error_message=result.stderr or result.stdout,
                    exit_code=result.returncode,
                    command_used=install_cmd
                )
            
            print("[TestRunner] Dependencies installed successfully")
            return TestResult(passed=True, output=result.stdout, command_used=install_cmd)
            
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output="",
                error_message=f"Dependency installation timed out after {self.timeout}s",
                exit_code=-1,
                command_used=install_cmd
            )
        except Exception as e:
            return TestResult(
                passed=False,
                output="",
                error_message=str(e),
                exit_code=-1,
                command_used=install_cmd
            )
    
    def _run_single_test(self, test_file: str) -> TestResult:
        """Run a single test file."""
        # Format command with test file
        cmd_template = self.commands["test_single"]
        
        # Handle different languages
        if self.language in ["java", "kotlin"]:
            # Extract class name from file path
            test_class = Path(test_file).stem
            cmd = cmd_template.format(test_class=test_class)
        else:
            cmd = cmd_template.format(test_file=test_file)
        
        return self._execute_test_command(cmd)
    
    def _run_test_path(self, test_path: str) -> TestResult:
        """Run all tests in a directory."""
        cmd_template = self.commands["test"]
        cmd = cmd_template.format(test_path=test_path)
        return self._execute_test_command(cmd)
    
    def _execute_test_command(self, cmd: str) -> TestResult:
        """Execute a test command and capture results."""
        print(f"[TestRunner] Executing: {cmd}")
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            combined_output = f"{result.stdout}\n{result.stderr}".strip()
            
            if result.returncode == 0:
                print("[TestRunner] Tests PASSED")
                return TestResult(
                    passed=True,
                    output=combined_output,
                    exit_code=0,
                    command_used=cmd
                )
            else:
                print(f"[TestRunner] Tests FAILED (exit code: {result.returncode})")
                return TestResult(
                    passed=False,
                    output=combined_output,
                    error_message=self._extract_error_message(combined_output),
                    exit_code=result.returncode,
                    command_used=cmd
                )
                
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output="",
                error_message=f"Test execution timed out after {self.timeout}s",
                exit_code=-1,
                command_used=cmd
            )
        except Exception as e:
            return TestResult(
                passed=False,
                output="",
                error_message=str(e),
                exit_code=-1,
                command_used=cmd
            )
    
    def _extract_error_message(self, output: str) -> str:
        """
        Extract the most relevant error message from test output.
        
        Tries to find the actual error rather than returning the whole log.
        """
        lines = output.split('\n')
        error_lines = []
        capture = False
        
        # Keywords that indicate error sections
        error_indicators = [
            'error:', 'Error:', 'ERROR',
            'failed', 'Failed', 'FAILED',
            'exception', 'Exception',
            'TypeError', 'ReferenceError', 'SyntaxError',
            'AssertionError', 'expect(',
            'Cannot find', 'not found', 'undefined',
            'FAIL ', '✕', '✗',
        ]
        
        for line in lines:
            # Start capturing on error indicators
            if any(indicator in line for indicator in error_indicators):
                capture = True
            
            if capture:
                error_lines.append(line)
                
                # Stop after getting enough context (30 lines max)
                if len(error_lines) >= 30:
                    break
        
        if error_lines:
            return '\n'.join(error_lines)
        
        # If no specific error found, return last 20 lines
        return '\n'.join(lines[-20:]) if len(lines) > 20 else output
    
    def check_tool_available(self) -> bool:
        """Check if the required test tool is available."""
        tool = self.commands["check_tool"]
        return shutil.which(tool) is not None


# =============================================================================
# Convenience Functions
# =============================================================================

def validate_generated_tests(
    language: str,
    repo_path: str,
    test_files: list[str],
    max_retries: int = 2
) -> tuple[bool, str, int]:
    """
    Validate generated tests by running them.
    
    Args:
        language: Programming language
        repo_path: Path to repository
        test_files: List of generated test file paths
        max_retries: Maximum retry attempts (not used here, just returns result)
        
    Returns:
        Tuple of (passed: bool, error_message: str, exit_code: int)
    """
    try:
        runner = TestRunner(language=language, repo_path=repo_path)
        result = runner.run_tests(test_files=test_files)
        return (result.passed, result.error_message or "", result.exit_code)
    except Exception as e:
        return (False, str(e), -1)


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test Runner for Generated Contract Tests")
    parser.add_argument("--language", "-l", required=True, help="Programming language")
    parser.add_argument("--repo", "-r", required=True, help="Path to repository")
    parser.add_argument("--test-file", "-t", required=True, help="Test file to run")
    parser.add_argument("--no-install", action="store_true", help="Skip dependency installation")
    
    args = parser.parse_args()
    
    runner = TestRunner(
        language=args.language,
        repo_path=args.repo,
        install_deps=not args.no_install
    )
    
    result = runner.run_tests(test_files=[args.test_file])
    
    print("\n" + "=" * 60)
    print("TEST RESULT")
    print("=" * 60)
    print(f"Passed: {result.passed}")
    print(f"Exit Code: {result.exit_code}")
    print(f"Command: {result.command_used}")
    
    if not result.passed:
        print(f"\nError:\n{result.error_message}")
        print("\n" + "=" * 60)
        print("AI RETRY PROMPT:")
        print("=" * 60)
        print(result.format_for_ai_retry())
