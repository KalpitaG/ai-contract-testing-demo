"""
Output Parser
=============
Validates and scores AI-generated Pact contract tests.

This module:
1. Checks syntax validity (language-specific)
2. Validates Pact patterns and best practices
3. Scores quality (0-10)
4. Identifies issues and suggests fixes

Why we need this:
- AI can generate syntactically invalid code
- AI can use deprecated patterns
- AI can hallucinate non-existent APIs
- We need measurable quality metrics for thesis

Usage:
    parser = OutputParser()
    result = parser.parse(generated_code, language="go")
    
    if result.is_valid:
        print(f"Quality: {result.quality_score}/10")
    else:
        for issue in result.issues:
            print(f"{issue.severity}: {issue.message}")
"""

import re
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum


# =============================================================================
# DATA CLASSES
# =============================================================================

class Severity(Enum):
    """Issue severity levels."""
    ERROR = "error"      # Will not compile/run
    WARNING = "warning"  # Works but bad practice
    INFO = "info"        # Suggestion for improvement


@dataclass
class QualityIssue:
    """A single quality issue found in the code."""
    severity: Severity
    rule: str           # Rule ID (e.g., "pact-version", "error-handling")
    message: str        # Human-readable description
    line: Optional[int] = None  # Line number if applicable
    suggestion: Optional[str] = None  # How to fix
    
    def __str__(self) -> str:
        loc = f"Line {self.line}: " if self.line else ""
        return f"[{self.severity.value.upper()}] {loc}{self.message}"


@dataclass
class ParsedOutput:
    """Complete result from output parsing."""
    code: str
    language: str
    
    # Validation results
    is_valid_syntax: bool = True
    syntax_error: Optional[str] = None
    
    # Quality assessment
    quality_score: float = 0.0  # 0-10
    issues: list[QualityIssue] = field(default_factory=list)
    
    # Metadata
    line_count: int = 0
    has_imports: bool = False
    has_test_function: bool = False
    has_pact_setup: bool = False
    has_interactions: bool = False
    has_matchers: bool = False
    has_error_handling: bool = False
    uses_correct_version: bool = False
    uses_execute_pattern: bool = False
    
    @property
    def is_valid(self) -> bool:
        """Check if code is valid (no errors)."""
        return self.is_valid_syntax and not any(
            i.severity == Severity.ERROR for i in self.issues
        )
    
    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)
    
    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)
    
    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.INFO)
    
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 60,
            "OUTPUT PARSER RESULT",
            "=" * 60,
            f"Language: {self.language}",
            f"Lines of code: {self.line_count}",
            f"Syntax valid: {'[OK]' if self.is_valid_syntax else '[FAIL]'}",
            f"Quality score: {self.quality_score:.1f}/10",
            "",
            "Checklist:",
            f"  {'[OK]' if self.has_imports else '[FAIL]'} Has correct imports",
            f"  {'[OK]' if self.has_test_function else '[FAIL]'} Has test function",
            f"  {'[OK]' if self.has_pact_setup else '[FAIL]'} Has Pact setup",
            f"  {'[OK]' if self.has_interactions else '[FAIL]'} Has interactions",
            f"  {'[OK]' if self.has_matchers else '[FAIL]'} Uses matchers",
            f"  {'[OK]' if self.has_error_handling else '[FAIL]'} Has error handling",
            f"  {'[OK]' if self.uses_correct_version else '[FAIL]'} Uses correct Pact version",
            f"  {'[OK]' if self.uses_execute_pattern else '[FAIL]'} Uses ExecuteTest pattern",
            "",
            f"Issues: {self.error_count} errors, {self.warning_count} warnings, {self.info_count} info",
        ]
        
        if self.issues:
            lines.append("")
            lines.append("Issues found:")
            for issue in self.issues:
                lines.append(f"  {issue}")
                if issue.suggestion:
                    lines.append(f"    â†’ {issue.suggestion}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


# =============================================================================
# LANGUAGE-SPECIFIC RULES
# =============================================================================

# Go patterns
GO_RULES = {
    "imports": {
        "correct": [
            r'github\.com/pact-foundation/pact-go/v2',
            r'github\.com/stretchr/testify',
        ],
        "deprecated": [
            r'github\.com/pact-foundation/pact-go"[^/v2]',  # v1
        ],
    },
    "pact_setup": {
        "correct": r'consumer\.NewV3Pact\(',
        "deprecated": [
            r'consumer\.NewPact\(',  # v1 style
            r'dsl\.Pact\{',          # Old DSL
        ],
    },
    "interactions": {
        "pattern": r'\.AddInteraction\(\)',
    },
    "matchers": {
        "patterns": [
            r'consumer\.Like\(',
            r'consumer\.EachLike\(',
            r'consumer\.Regex\(',
            r'consumer\.Integer\(',
            r'consumer\.String\(',
            r'consumer\.Map\{',
        ],
    },
    "error_handling": {
        "patterns": [
            r'require\.NoError\(',
            r'assert\.NoError\(',
            r'if err != nil',
        ],
    },
    "execute_pattern": {
        "correct": r'\.ExecuteTest\(t,',
        "deprecated": [
            r'\.Verify\(',
            r'pact\.Verify\(',
        ],
    },
    "test_function": {
        "pattern": r'func Test\w+\(t \*testing\.T\)',
    },
}

# TypeScript patterns
TYPESCRIPT_RULES = {
    "imports": {
        "correct": [
            r'from\s+["\']@pact-foundation/pact["\']',
            r'PactV3',
            r'MatchersV3',
        ],
        "deprecated": [
            r'from\s+["\']pact["\']',  # Old package
        ],
    },
    "pact_setup": {
        "correct": r'new\s+PactV3\s*\(',
        "deprecated": [
            r'new\s+Pact\s*\(',  # v2 style
        ],
    },
    "interactions": {
        "pattern": r'\.(given|uponReceiving)\(',
    },
    "matchers": {
        "patterns": [
            r'like\(',
            r'eachLike\(',
            r'regex\(',
            r'integer\(',
            r'string\(',
            r'MatchersV3\.',
        ],
    },
    "error_handling": {
        "patterns": [
            r'expect\(.+\)\.not\.toThrow',
            r'\.rejects\.',
            r'try\s*\{',
            r'catch\s*\(',
            r'await\s+expect\(',
        ],
    },
    "execute_pattern": {
        "correct": r'\.executeTest\s*\(',
        "deprecated": [
            r'\.verify\s*\(',
        ],
    },
    "test_function": {
        "pattern": r'(it|test|describe)\s*\(',
    },
}

# Java patterns
JAVA_RULES = {
    "imports": {
        "correct": [
            r'au\.com\.dius\.pact\.consumer',
            r'au\.com\.dius\.pact\.consumer\.junit5',
        ],
        "deprecated": [],
    },
    "pact_setup": {
        "correct": r'@Pact\s*\(',
        "deprecated": [],
    },
    "interactions": {
        "pattern": r'\.(given|uponReceiving)\(',
    },
    "matchers": {
        "patterns": [
            r'LambdaDsl\.',
            r'PactDslJsonBody',
            r'newJsonBody\(',
            r'stringType\(',
            r'integerType\(',
        ],
    },
    "error_handling": {
        "patterns": [
            r'assertNotNull\(',
            r'assertEquals\(',
            r'assertThat\(',
            r'@Test',
        ],
    },
    "execute_pattern": {
        "correct": r'@PactTestFor\(',
        "deprecated": [],
    },
    "test_function": {
        "pattern": r'@Test\s*\n\s*(public\s+)?void\s+\w+\(',
    },
}

# Kotlin patterns  
KOTLIN_RULES = {
    "imports": {
        "correct": [
            r'au\.com\.dius\.pact\.consumer',
            r'au\.com\.dius\.pact\.consumer\.junit5',
        ],
        "deprecated": [],
    },
    "pact_setup": {
        "correct": r'@Pact\s*\(',
        "deprecated": [],
    },
    "interactions": {
        "pattern": r'\.(given|uponReceiving)\(',
    },
    "matchers": {
        "patterns": [
            r'LambdaDsl\.',
            r'PactDslJsonBody',
            r'newJsonBody\s*\{',
            r'stringType\(',
            r'integerType\(',
        ],
    },
    "error_handling": {
        "patterns": [
            r'assertNotNull\(',
            r'assertEquals\(',
            r'shouldBe\s',
            r'@Test',
        ],
    },
    "execute_pattern": {
        "correct": r'@PactTestFor\(',
        "deprecated": [],
    },
    "test_function": {
        "pattern": r'@Test\s*\n\s*fun\s+\w+\(',
    },
}

# Python patterns
PYTHON_RULES = {
    "imports": {
        "correct": [
            r'from\s+pact\s+import',
            r'from\s+pact\.v3',
            r'import\s+pact',
        ],
        "deprecated": [],
    },
    "pact_setup": {
        "correct": r'Pact\s*\(',
        "deprecated": [],
    },
    "interactions": {
        "pattern": r'\.(given|upon_receiving)\(',
    },
    "matchers": {
        "patterns": [
            r'Like\(',
            r'EachLike\(',
            r'Regex\(',
            r'match\.',
            r'Format\.',
        ],
    },
    "error_handling": {
        "patterns": [
            r'assert\s+',
            r'pytest\.raises\(',
            r'with\s+pact:',
        ],
    },
    "execute_pattern": {
        "correct": r'with\s+pact',  # Context manager pattern
        "deprecated": [],
    },
    "test_function": {
        "pattern": r'def\s+test_\w+\(',
    },
}

LANGUAGE_RULES = {
    "go": GO_RULES,
    "typescript": TYPESCRIPT_RULES,
    "javascript": TYPESCRIPT_RULES,  # Same as TS
    "java": JAVA_RULES,
    "kotlin": KOTLIN_RULES,
    "python": PYTHON_RULES,
}


# =============================================================================
# OUTPUT PARSER
# =============================================================================

class OutputParser:
    """
    Validates and scores AI-generated Pact contract tests.
    
    Usage:
        parser = OutputParser()
        result = parser.parse(code, language="go")
        
        print(result.summary())
        print(f"Valid: {result.is_valid}")
        print(f"Score: {result.quality_score}/10")
    """
    
    def __init__(self, strict_mode: bool = False):
        """
        Initialize the parser.
        
        Args:
            strict_mode: If True, treat warnings as errors
        """
        self.strict_mode = strict_mode
    
    def parse(self, code: str, language: str) -> ParsedOutput:
        """
        Parse and validate generated code.
        
        Args:
            code: The generated test code
            language: Programming language (go, typescript, java, kotlin, python)
            
        Returns:
            ParsedOutput with validation results and quality score
        """
        language = language.lower()
        
        # Initialize result
        result = ParsedOutput(
            code=code,
            language=language,
            line_count=len(code.strip().split('\n'))
        )
        
        # Get language-specific rules
        rules = LANGUAGE_RULES.get(language)
        if not rules:
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="unknown-language",
                message=f"Unknown language '{language}', skipping pattern checks"
            ))
            result.quality_score = 5.0  # Neutral score
            return result
        
        # Run all checks
        self._check_minimum_code(result)
        self._check_imports(result, rules)
        self._check_pact_setup(result, rules)
        self._check_interactions(result, rules)
        self._check_matchers(result, rules)
        self._check_error_handling(result, rules)
        self._check_execute_pattern(result, rules)
        self._check_test_function(result, rules)
        self._check_syntax(result)
        self._check_common_issues(result)
        
        # Calculate quality score
        result.quality_score = self._calculate_score(result)
        
        return result
    
    def _check_minimum_code(self, result: ParsedOutput) -> None:
        """Check if code meets minimum requirements."""
        if result.line_count < 10:
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="minimum-code",
                message=f"Code too short ({result.line_count} lines). Expected at least 10 lines.",
                suggestion="Ensure complete test is generated, not just a stub"
            ))
        
        if not result.code.strip():
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="empty-code",
                message="Generated code is empty"
            ))
    
    def _check_imports(self, result: ParsedOutput, rules: dict) -> None:
        """Check import statements."""
        import_rules = rules.get("imports", {})
        
        # Check for correct imports
        correct_imports = import_rules.get("correct", [])
        has_correct = any(
            re.search(pattern, result.code) 
            for pattern in correct_imports
        )
        result.has_imports = has_correct
        
        if not has_correct and correct_imports:
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="missing-imports",
                message="Missing required Pact imports",
                suggestion=f"Add import for Pact library"
            ))
        
        # Check for deprecated imports
        deprecated_imports = import_rules.get("deprecated", [])
        for pattern in deprecated_imports:
            if re.search(pattern, result.code):
                result.issues.append(QualityIssue(
                    severity=Severity.ERROR,
                    rule="deprecated-import",
                    message="Using deprecated Pact import (v1 style)",
                    suggestion="Update to Pact v2/v3 imports"
                ))
    
    def _check_pact_setup(self, result: ParsedOutput, rules: dict) -> None:
        """Check Pact setup/initialization."""
        setup_rules = rules.get("pact_setup", {})
        
        correct_pattern = setup_rules.get("correct")
        if correct_pattern and re.search(correct_pattern, result.code):
            result.has_pact_setup = True
            result.uses_correct_version = True
        else:
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="missing-pact-setup",
                message="Missing or incorrect Pact setup",
                suggestion="Use correct Pact initialization (V3 for Go/TS, V4 for Java/Kotlin)"
            ))
        
        # Check for deprecated setup
        deprecated = setup_rules.get("deprecated", [])
        for pattern in deprecated:
            if re.search(pattern, result.code):
                result.uses_correct_version = False
                result.issues.append(QualityIssue(
                    severity=Severity.ERROR,
                    rule="deprecated-pact-setup",
                    message="Using deprecated Pact setup pattern",
                    suggestion="Update to current Pact version pattern"
                ))
    
    def _check_interactions(self, result: ParsedOutput, rules: dict) -> None:
        """Check for Pact interactions."""
        interaction_rules = rules.get("interactions", {})
        pattern = interaction_rules.get("pattern")
        
        if pattern and re.search(pattern, result.code):
            result.has_interactions = True
        else:
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="missing-interactions",
                message="No Pact interactions found",
                suggestion="Add at least one interaction with given/uponReceiving"
            ))
    
    def _check_matchers(self, result: ParsedOutput, rules: dict) -> None:
        """Check for Pact matchers."""
        matcher_rules = rules.get("matchers", {})
        patterns = matcher_rules.get("patterns", [])
        
        has_matchers = any(
            re.search(pattern, result.code)
            for pattern in patterns
        )
        result.has_matchers = has_matchers
        
        if not has_matchers:
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="no-matchers",
                message="No Pact matchers found. Using hardcoded values.",
                suggestion="Use Like(), EachLike(), Regex() etc. for flexible matching"
            ))
    
    def _check_error_handling(self, result: ParsedOutput, rules: dict) -> None:
        """Check for error handling."""
        error_rules = rules.get("error_handling", {})
        patterns = error_rules.get("patterns", [])
        
        has_error_handling = any(
            re.search(pattern, result.code)
            for pattern in patterns
        )
        result.has_error_handling = has_error_handling
        
        if not has_error_handling:
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="no-error-handling",
                message="No error handling found",
                suggestion="Add assertions or error checks (require.NoError, expect, assert)"
            ))
    
    def _check_execute_pattern(self, result: ParsedOutput, rules: dict) -> None:
        """Check for correct execution pattern."""
        execute_rules = rules.get("execute_pattern", {})
        
        correct_pattern = execute_rules.get("correct")
        if correct_pattern and re.search(correct_pattern, result.code):
            result.uses_execute_pattern = True
        
        # Check for deprecated patterns
        deprecated = execute_rules.get("deprecated", [])
        for pattern in deprecated:
            if re.search(pattern, result.code):
                result.uses_execute_pattern = False
                result.issues.append(QualityIssue(
                    severity=Severity.ERROR,
                    rule="deprecated-execute-pattern",
                    message="Using deprecated execution pattern (Verify instead of ExecuteTest)",
                    suggestion="Replace Verify() with ExecuteTest()"
                ))
    
    def _check_test_function(self, result: ParsedOutput, rules: dict) -> None:
        """Check for test function definition."""
        test_rules = rules.get("test_function", {})
        pattern = test_rules.get("pattern")
        
        if pattern and re.search(pattern, result.code):
            result.has_test_function = True
        else:
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="missing-test-function",
                message="No test function found",
                suggestion="Wrap code in proper test function"
            ))
    
    def _check_syntax(self, result: ParsedOutput) -> None:
        """
        Check syntax validity.
        
        Note: Full syntax checking would require language-specific parsers.
        This does basic checks that catch common issues.
        """
        code = result.code
        
        # Check balanced braces/brackets
        if code.count('{') != code.count('}'):
            result.is_valid_syntax = False
            result.syntax_error = "Unbalanced curly braces {}"
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="syntax-braces",
                message="Unbalanced curly braces",
                suggestion="Check for missing { or }"
            ))
        
        if code.count('(') != code.count(')'):
            result.is_valid_syntax = False
            result.syntax_error = "Unbalanced parentheses ()"
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="syntax-parens",
                message="Unbalanced parentheses",
                suggestion="Check for missing ( or )"
            ))
        
        if code.count('[') != code.count(']'):
            result.is_valid_syntax = False
            result.syntax_error = "Unbalanced brackets []"
            result.issues.append(QualityIssue(
                severity=Severity.ERROR,
                rule="syntax-brackets",
                message="Unbalanced brackets",
                suggestion="Check for missing [ or ]"
            ))
    
    def _check_common_issues(self, result: ParsedOutput) -> None:
        """Check for common issues across all languages."""
        code = result.code
        
        # Check for TODO/FIXME comments
        if re.search(r'//\s*(TODO|FIXME|XXX)', code, re.IGNORECASE):
            result.issues.append(QualityIssue(
                severity=Severity.INFO,
                rule="todo-comments",
                message="Contains TODO/FIXME comments",
                suggestion="Complete or remove placeholder comments"
            ))
        
        # Check for excessive commented code
        comment_lines = len(re.findall(r'^\s*(//|#|/\*|\*)', code, re.MULTILINE))
        total_lines = result.line_count
        if total_lines > 0 and comment_lines / total_lines > 0.3:
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="excessive-comments",
                message=f"High comment ratio ({comment_lines}/{total_lines} lines)",
                suggestion="Remove commented-out code blocks"
            ))
        
        # Check for hardcoded IDs/values that should be matchers
        hardcoded_patterns = [
            (r'["\']123["\']', "Hardcoded ID '123'"),
            (r'["\']test["\']', "Hardcoded value 'test'"),
            (r'["\']example["\']', "Hardcoded value 'example'"),
        ]
        for pattern, message in hardcoded_patterns:
            if re.search(pattern, code):
                result.issues.append(QualityIssue(
                    severity=Severity.INFO,
                    rule="hardcoded-values",
                    message=message,
                    suggestion="Consider using matchers for flexibility"
                ))
                break  # Only report once
        
        # Check for query params in path (should use WithQuery)
        if re.search(r'WithRequest\([^)]*\?[^)]*\)', code):
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="query-in-path",
                message="Query parameters embedded in path string",
                suggestion="Use WithQuery() or .query() method instead"
            ))
        
        # Check for empty ExecuteTest callback
        if re.search(r'ExecuteTest\([^{]*\{\s*return\s+nil\s*\}', code):
            result.issues.append(QualityIssue(
                severity=Severity.WARNING,
                rule="empty-callback",
                message="ExecuteTest callback is empty (just returns nil)",
                suggestion="Add actual client call and assertions"
            ))
    
    def _calculate_score(self, result: ParsedOutput) -> float:
        """
        Calculate quality score (0-10).
        
        Scoring breakdown:
        - Base: 5 points
        - Syntax valid: +1
        - Has imports: +0.5
        - Has test function: +0.5
        - Has Pact setup: +1
        - Has interactions: +1
        - Uses matchers: +0.5
        - Has error handling: +0.5
        - Correct version: +0.5
        - Execute pattern: +0.5
        - Deductions for issues
        """
        score = 5.0  # Base score
        
        # Positive points
        if result.is_valid_syntax:
            score += 1.0
        if result.has_imports:
            score += 0.5
        if result.has_test_function:
            score += 0.5
        if result.has_pact_setup:
            score += 1.0
        if result.has_interactions:
            score += 1.0
        if result.has_matchers:
            score += 0.5
        if result.has_error_handling:
            score += 0.5
        if result.uses_correct_version:
            score += 0.5
        if result.uses_execute_pattern:
            score += 0.5
        
        # Deductions for issues
        for issue in result.issues:
            if issue.severity == Severity.ERROR:
                score -= 1.0
            elif issue.severity == Severity.WARNING:
                score -= 0.3
            # INFO doesn't affect score
        
        # Clamp to 0-10
        return max(0.0, min(10.0, score))
    
    def validate_syntax_with_compiler(
        self, 
        code: str, 
        language: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate syntax using actual compiler/interpreter.
        
        Note: Requires language tools to be installed.
        
        Args:
            code: The code to validate
            language: Programming language
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        language = language.lower()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            if language == "go":
                return self._validate_go(code, tmpdir)
            elif language in ("typescript", "javascript"):
                return self._validate_typescript(code, tmpdir)
            elif language == "python":
                return self._validate_python(code, tmpdir)
            else:
                return True, None  # Can't validate, assume OK
    
    def _validate_go(self, code: str, tmpdir: str) -> tuple[bool, Optional[str]]:
        """Validate Go syntax."""
        filepath = Path(tmpdir) / "test_pact_test.go"
        filepath.write_text(code)
        
        try:
            result = subprocess.run(
                ["go", "build", "-n", str(filepath)],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, None
        except FileNotFoundError:
            return True, None  # Go not installed
        except subprocess.TimeoutExpired:
            return False, "Compilation timed out"
    
    def _validate_typescript(self, code: str, tmpdir: str) -> tuple[bool, Optional[str]]:
        """Validate TypeScript syntax."""
        filepath = Path(tmpdir) / "test.pact.spec.ts"
        filepath.write_text(code)
        
        try:
            result = subprocess.run(
                ["npx", "tsc", "--noEmit", str(filepath)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, None
        except FileNotFoundError:
            return True, None  # TypeScript not installed
        except subprocess.TimeoutExpired:
            return False, "Compilation timed out"
    
    def _validate_python(self, code: str, tmpdir: str) -> tuple[bool, Optional[str]]:
        """Validate Python syntax."""
        filepath = Path(tmpdir) / "test_pact.py"
        filepath.write_text(code)
        
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", str(filepath)],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return False, result.stderr
            return True, None
        except FileNotFoundError:
            return True, None
        except subprocess.TimeoutExpired:
            return False, "Compilation timed out"

