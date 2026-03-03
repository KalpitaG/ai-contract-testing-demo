"""
Breaking Change Detection Agent
================================

Analyzes provider verification failures to distinguish between:
- Test code errors (import issues, syntax errors) — not breaking changes
- Actual breaking changes (missing fields, type mismatches, status code changes)

For breaking changes, uses AI to generate 3 fix options:
1. Provider adapts (backward compatibility)
2. Consumer updates expectations
3. API versioning

Usage:
    from src.provider.breaking_change_agent import BreakingChangeAgent

    agent = BreakingChangeAgent()
    analysis = agent.analyze(
        verification_output="...",
        pact_context=pact_ctx,
        provider_context=provider_ctx,
        provider_name="pact-provider-demo"
    )

    if analysis.is_breaking_change:
        print(agent.format_github_comment(analysis))
"""

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from langfuse import observe

from .breaking_change_prompts import (
    BREAKING_CHANGE_SYSTEM_PROMPT,
    build_fix_generation_prompt,
)
from .pact_fetcher import PactContext
from .provider_analyzer import ProviderCodeContext


# =============================================================================
# Data Classes
# =============================================================================

class BreakingChangeType(Enum):
    """Types of breaking changes detected from verification output."""
    MISSING_FIELD = "missing_field"
    TYPE_MISMATCH = "type_mismatch"
    STATUS_CODE_CHANGE = "status_code_change"
    MISSING_ENDPOINT = "missing_endpoint"
    VALUE_MISMATCH = "value_mismatch"
    STATE_HANDLER_ERROR = "state_handler_error"
    TEST_CODE_ERROR = "test_code_error"


@dataclass
class BreakingChange:
    """A single detected breaking change."""
    change_type: BreakingChangeType
    description: str
    affected_endpoint: str = ""
    affected_field: str = ""
    expected_value: str = ""
    actual_value: str = ""
    consumer: str = ""
    severity: str = "critical"  # "critical", "warning", "info"


@dataclass
class FixOption:
    """A suggested fix for a breaking change."""
    option_number: int
    title: str
    description: str
    side: str  # "provider", "consumer", "both"
    code_suggestion: str = ""
    impact: str = ""


@dataclass
class BreakingChangeAnalysis:
    """Complete analysis result from the agent."""
    is_breaking_change: bool
    is_test_code_error: bool
    changes: list
    fix_options: list
    summary: str
    raw_error: str = ""

    def has_actionable_changes(self) -> bool:
        """Whether there are changes that need human attention."""
        return self.is_breaking_change and len(self.changes) > 0


# =============================================================================
# Error Parsing Patterns
# =============================================================================

# Test code errors — these are NOT breaking changes
TEST_CODE_PATTERNS = [
    (r"Cannot find module ['\"]([^'\"]+)['\"]", "Import error: module not found"),
    (r"SyntaxError: (.+)", "Syntax error in generated test"),
    (r"TypeError: (.+) is not a function", "Type error: invalid function call"),
    (r"ReferenceError: (\w+) is not defined", "Reference error: undefined variable"),
    (r"Test suite failed to run", "Test suite failed to initialize"),
    (r"require\(\.\.\.\)\.__get__ is not a function", "Hallucinated API: __get__ does not exist"),
    (r"require\(['\"]rewire['\"]\)", "Hallucinated dependency: rewire not installed"),
]

# State handler errors — generation issues, not breaking changes
STATE_HANDLER_PATTERNS = [
    (r"State handler not found for state: ['\"](.+?)['\"]", "Missing state handler"),
    (r"MissingStateChangeMethod.*['\"](.+?)['\"]", "Missing state change method"),
    (r"No state handler found for: ['\"](.+?)['\"]", "No state handler found"),
]

# Breaking change patterns — actual contract mismatches
BREAKING_CHANGE_PATTERNS = [
    # Missing fields in response
    (
        r"Actual map is missing the following keys: (.+?)(?:\n|$)",
        BreakingChangeType.MISSING_FIELD,
        "Response is missing expected fields"
    ),
    (
        r"Missing the following keys: (.+?)(?:\n|$)",
        BreakingChangeType.MISSING_FIELD,
        "Response is missing expected fields"
    ),
    # Type mismatches
    (
        r"Expected type: (\w+).*?Actual type: (\w+)",
        BreakingChangeType.TYPE_MISMATCH,
        "Field type changed"
    ),
    (
        r"Type mismatch for key ['\"](\w+)['\"]",
        BreakingChangeType.TYPE_MISMATCH,
        "Field type mismatch"
    ),
    # Status code changes
    (
        r"Expected status code: (\d+).*?Actual status code: (\d+)",
        BreakingChangeType.STATUS_CODE_CHANGE,
        "HTTP status code changed"
    ),
    (
        r"expected (\d+) but got (\d+)",
        BreakingChangeType.STATUS_CODE_CHANGE,
        "HTTP status code mismatch"
    ),
    # Missing endpoints
    (
        r"Request to (/\S+) returned 404",
        BreakingChangeType.MISSING_ENDPOINT,
        "Endpoint not found"
    ),
    (
        r"Cannot (?:GET|POST|PUT|PATCH|DELETE) (/\S+)",
        BreakingChangeType.MISSING_ENDPOINT,
        "Endpoint does not exist"
    ),
    # Value mismatches
    (
        r"Expected:.*?\"(\w+)\":\s*(.+?)[\n,}].*?Actual:.*?\"(\w+)\":\s*(.+?)[\n,}]",
        BreakingChangeType.VALUE_MISMATCH,
        "Response value does not match expectation"
    ),
]

# Endpoint extraction patterns
# 1. Pact-js verifier: "Sending request HTTP Request ( method: GET, path: /items/1, ...)"
# 2. Pact interaction: "Interaction|Verifying ... GET /items/1"
ENDPOINT_PATTERNS = [
    re.compile(
        r"Sending request HTTP Request \( method: (GET|POST|PUT|PATCH|DELETE), path: (/\S+?),",
        re.IGNORECASE
    ),
    re.compile(
        r"(?:Interaction|Verifying).*?(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
        re.IGNORECASE
    ),
]


# =============================================================================
# Breaking Change Agent
# =============================================================================

class BreakingChangeAgent:
    """
    Analyzes provider verification failures and generates fix options.

    The agent operates in two phases:
    1. Parse & Classify (regex-based, deterministic, fast)
    2. Generate Fixes (AI-powered, only for actual breaking changes)
    """

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        pactflow_url: Optional[str] = None,
        pactflow_token: Optional[str] = None
    ):
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.pactflow_url = pactflow_url or os.getenv("PACTFLOW_BASE_URL")
        self.pactflow_token = pactflow_token or os.getenv("PACTFLOW_TOKEN")

        if not self.gemini_api_key:
            raise ValueError("Gemini API key required. Set GEMINI_API_KEY.")

        from google import genai
        self.genai_client = genai.Client(api_key=self.gemini_api_key)

    @observe(name="analyze_breaking_changes")
    def analyze(
        self,
        verification_output: str,
        pact_context: PactContext,
        provider_context: ProviderCodeContext,
        provider_name: str
    ) -> BreakingChangeAnalysis:
        """
        Analyze verification output for breaking changes.

        Args:
            verification_output: Full stdout/stderr from verification run
            pact_context: Pact context with consumer expectations
            provider_context: Provider code analysis
            provider_name: Provider service name

        Returns:
            BreakingChangeAnalysis with classification and fix options
        """
        print(f"\n{'='*60}")
        print(f"BREAKING CHANGE ANALYSIS: {provider_name}")
        print(f"{'='*60}")

        # Phase 1: Parse and classify errors
        print("\n Phase 1: Parsing verification output...")
        test_code_errors = self._detect_test_code_errors(verification_output)
        state_handler_errors = self._detect_state_handler_errors(verification_output)
        breaking_changes = self._detect_breaking_changes(
            verification_output, pact_context
        )

        print(f"  Test code errors: {len(test_code_errors)}")
        print(f"  State handler errors: {len(state_handler_errors)}")
        print(f"  Breaking changes: {len(breaking_changes)}")

        # If only test code errors, not a breaking change
        if test_code_errors and not breaking_changes:
            print("\n  Result: Test code error (not a breaking change)")
            return BreakingChangeAnalysis(
                is_breaking_change=False,
                is_test_code_error=True,
                changes=test_code_errors,
                fix_options=[],
                summary="Verification failed due to test code errors, not a contract breaking change.",
                raw_error=verification_output[:2000]
            )

        # If only state handler errors, generation issue
        if state_handler_errors and not breaking_changes:
            print("\n  Result: State handler generation issue")
            return BreakingChangeAnalysis(
                is_breaking_change=False,
                is_test_code_error=True,
                changes=state_handler_errors,
                fix_options=[],
                summary="Verification failed due to missing or incorrect state handlers. "
                        "This is an AI generation issue, not a contract breaking change.",
                raw_error=verification_output[:2000]
            )

        # If no errors detected at all, return unknown
        if not breaking_changes:
            print("\n  Result: Could not classify failure")
            return BreakingChangeAnalysis(
                is_breaking_change=False,
                is_test_code_error=False,
                changes=[],
                fix_options=[],
                summary="Verification failed but the error could not be automatically classified. "
                        "Check the workflow logs for details.",
                raw_error=verification_output[:2000]
            )

        # Phase 2: Generate fix options using AI
        print("\n Phase 2: Generating fix options with AI...")
        fix_options = self._generate_fix_options(
            breaking_changes, pact_context, provider_context
        )

        consumers = ", ".join(pact_context.consumers) if pact_context.consumers else "unknown"
        change_types = set(c.change_type.value for c in breaking_changes)
        summary = (
            f"Detected {len(breaking_changes)} breaking change(s) "
            f"({', '.join(change_types)}) affecting consumer(s): {consumers}."
        )

        print(f"\n  Result: {summary}")
        print(f"  Fix options generated: {len(fix_options)}")

        return BreakingChangeAnalysis(
            is_breaking_change=True,
            is_test_code_error=False,
            changes=breaking_changes,
            fix_options=fix_options,
            summary=summary,
            raw_error=verification_output[:2000]
        )

    # =========================================================================
    # Phase 1: Regex-based parsing (no AI)
    # =========================================================================

    def _detect_test_code_errors(self, output: str) -> list:
        """Detect test code errors (not breaking changes)."""
        errors = []
        for pattern, description in TEST_CODE_PATTERNS:
            matches = re.findall(pattern, output)
            if matches:
                detail = matches[0] if isinstance(matches[0], str) else str(matches[0])
                errors.append(BreakingChange(
                    change_type=BreakingChangeType.TEST_CODE_ERROR,
                    description=f"{description}: {detail}",
                    severity="info"
                ))
        return errors

    def _detect_state_handler_errors(self, output: str) -> list:
        """Detect state handler errors (generation issues)."""
        errors = []
        for pattern, description in STATE_HANDLER_PATTERNS:
            matches = re.findall(pattern, output)
            for match in matches:
                errors.append(BreakingChange(
                    change_type=BreakingChangeType.STATE_HANDLER_ERROR,
                    description=f"{description}: \"{match}\"",
                    severity="warning"
                ))
        return errors

    def _detect_breaking_changes(
        self, output: str, pact_context: PactContext
    ) -> list:
        """Detect actual breaking changes from verification output."""
        changes = []
        consumers = ", ".join(pact_context.consumers) if pact_context.consumers else "unknown"

        # Extract current endpoint context from output
        current_endpoint = self._extract_current_endpoint(output)

        for pattern, change_type, description in BREAKING_CHANGE_PATTERNS:
            matches = re.finditer(pattern, output, re.DOTALL | re.IGNORECASE)
            for match in matches:
                groups = match.groups()

                change = BreakingChange(
                    change_type=change_type,
                    description=description,
                    consumer=consumers,
                    affected_endpoint=current_endpoint,
                )

                if change_type == BreakingChangeType.MISSING_FIELD:
                    fields = groups[0].strip() if groups else ""
                    change.affected_field = fields
                    change.description = f"Response missing fields: {fields}"

                elif change_type == BreakingChangeType.TYPE_MISMATCH:
                    if len(groups) >= 2:
                        change.expected_value = groups[0]
                        change.actual_value = groups[1]
                        change.description = (
                            f"Type mismatch: expected {groups[0]}, got {groups[1]}"
                        )
                    elif len(groups) == 1:
                        change.affected_field = groups[0]
                        change.description = f"Type mismatch for field '{groups[0]}'"

                elif change_type == BreakingChangeType.STATUS_CODE_CHANGE:
                    if len(groups) >= 2:
                        change.expected_value = groups[0]
                        change.actual_value = groups[1]
                        change.description = (
                            f"Expected HTTP {groups[0]} but got {groups[1]}"
                        )

                elif change_type == BreakingChangeType.MISSING_ENDPOINT:
                    if groups:
                        change.affected_endpoint = groups[0]
                        change.description = f"Endpoint {groups[0]} not found (404)"

                elif change_type == BreakingChangeType.VALUE_MISMATCH:
                    if len(groups) >= 4:
                        change.affected_field = groups[0]
                        change.expected_value = groups[1]
                        change.actual_value = groups[3]

                changes.append(change)

        # Deduplicate changes with same type and endpoint
        return self._deduplicate_changes(changes)

    def _extract_current_endpoint(self, output: str) -> str:
        """Extract the affected endpoint from verification output context.

        Searches for Sending request lines near the failure, falling back to
        interaction description lines.
        """
        # Find the last endpoint mentioned before the failure section
        # Pact verifier logs requests in order; the last one before "Failures:" is usually it
        failure_pos = output.find("Failures:")
        search_text = output[:failure_pos] if failure_pos > 0 else output

        # Try each pattern, prefer the last match (closest to the failure)
        for pattern in ENDPOINT_PATTERNS:
            matches = list(pattern.finditer(search_text))
            if matches:
                m = matches[-1]  # Last match = closest to failure
                return f"{m.group(1).upper()} {m.group(2)}"
        return ""

    def _deduplicate_changes(self, changes: list) -> list:
        """Remove duplicate breaking changes."""
        seen = set()
        unique = []
        for change in changes:
            key = (change.change_type, change.affected_endpoint, change.affected_field)
            if key not in seen:
                seen.add(key)
                unique.append(change)
        return unique

    # =========================================================================
    # Phase 2: AI-powered fix generation
    # =========================================================================

    @observe(name="generate_fix_options")
    def _generate_fix_options(
        self,
        changes: list,
        pact_context: PactContext,
        provider_context: ProviderCodeContext
    ) -> list:
        """Use AI to generate fix options for breaking changes."""

        # Format breaking changes for the prompt
        changes_text = self._format_changes_for_prompt(changes)

        prompt = build_fix_generation_prompt(
            breaking_changes_formatted=changes_text,
            provider_context=provider_context.format_for_ai(),
            pact_context=pact_context.format_for_ai(),
            provider_language=provider_context.language
        )

        try:
            response = self.genai_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={
                    "system_instruction": BREAKING_CHANGE_SYSTEM_PROMPT,
                    "temperature": 0.3,
                    "max_output_tokens": 4000
                }
            )

            return self._parse_fix_options(response.text)

        except Exception as e:
            print(f"  AI fix generation failed: {e}")
            return self._fallback_fix_options(changes)

    def _format_changes_for_prompt(self, changes: list) -> str:
        """Format breaking changes as text for the AI prompt."""
        lines = []
        for i, change in enumerate(changes, 1):
            lines.append(f"### Change {i}: {change.change_type.value}")
            lines.append(f"- Description: {change.description}")
            if change.affected_endpoint:
                lines.append(f"- Endpoint: {change.affected_endpoint}")
            if change.affected_field:
                lines.append(f"- Field(s): {change.affected_field}")
            if change.expected_value:
                lines.append(f"- Expected: {change.expected_value}")
            if change.actual_value:
                lines.append(f"- Actual: {change.actual_value}")
            if change.consumer:
                lines.append(f"- Consumer: {change.consumer}")
            lines.append("")
        return "\n".join(lines)

    def _parse_fix_options(self, response_text: str) -> list:
        """Parse AI response into FixOption objects."""
        text = response_text.strip()

        # Remove markdown code fences
        for prefix in ["```json", "```"]:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"  Could not parse AI response as JSON")
            return []

        # Handle different response shapes from Gemini
        # Expected: {"fix_groups": [...]}
        # Sometimes returned: [{"options": [...]}] (list at top level)
        # Sometimes returned: [{"option_number": 1, ...}] (flat list of options)
        if isinstance(data, list):
            # Check if it's a list of groups or a flat list of options
            if data and "options" in data[0]:
                fix_groups = data
            else:
                fix_groups = [{"options": data}]
        elif isinstance(data, dict):
            fix_groups = data.get("fix_groups", [])
        else:
            return []

        options = []
        for group in fix_groups:
            group_options = group.get("options", []) if isinstance(group, dict) else []
            for opt in group_options:
                options.append(FixOption(
                    option_number=opt.get("option_number", 0),
                    title=opt.get("title", ""),
                    description=opt.get("description", ""),
                    side=opt.get("side", "unknown"),
                    code_suggestion=opt.get("code_suggestion", ""),
                    impact=opt.get("impact", "")
                ))

        # Ensure sequential numbering (AI sometimes returns all zeros)
        for i, option in enumerate(options, 1):
            option.option_number = i

        return options

    def _fallback_fix_options(self, changes: list) -> list:
        """Generate basic fix options when AI call fails."""
        change_desc = "; ".join(c.description for c in changes[:3])
        return [
            FixOption(
                option_number=1,
                title="Provider adapts to satisfy consumer expectations",
                description=f"Update the provider to return the expected data. Issues: {change_desc}",
                side="provider",
                code_suggestion="Review the provider's route handlers and ensure response format matches the pact.",
                impact="Backward compatible — consumer does not need to change."
            ),
            FixOption(
                option_number=2,
                title="Consumer updates expectations",
                description="Update the consumer's pact test to match the provider's current behavior.",
                side="consumer",
                code_suggestion="Review the consumer's pact test and update matchers/expectations.",
                impact="Consumer test changes required — coordinate with consumer team."
            ),
            FixOption(
                option_number=3,
                title="Version the API",
                description="Create a new API version with the updated schema. Keep old version for existing consumers.",
                side="both",
                code_suggestion="Create /v2/ endpoints with the new schema. Deprecate /v1/ over time.",
                impact="Both teams need to coordinate on migration timeline."
            ),
        ]

    # =========================================================================
    # GitHub Comment Formatting
    # =========================================================================

    def format_github_comment(self, analysis: BreakingChangeAnalysis) -> str:
        """Format the analysis as a GitHub-flavored markdown comment."""

        if analysis.is_test_code_error and not analysis.is_breaking_change:
            return self._format_test_error_comment(analysis)

        if not analysis.is_breaking_change:
            return self._format_unknown_error_comment(analysis)

        return self._format_breaking_change_comment(analysis)

    def _format_breaking_change_comment(self, analysis: BreakingChangeAnalysis) -> str:
        """Format a breaking change analysis comment."""
        lines = []
        lines.append("## Breaking Change Detected\n")
        lines.append(f"**Summary:** {analysis.summary}\n")

        # Changes table
        lines.append("### What broke:\n")
        lines.append("| # | Type | Endpoint | Details |")
        lines.append("|---|------|----------|---------|")
        for i, change in enumerate(analysis.changes, 1):
            change_type = change.change_type.value.replace("_", " ").title()
            endpoint = change.affected_endpoint or "N/A"
            lines.append(f"| {i} | {change_type} | {endpoint} | {change.description} |")

        lines.append("")

        # Fix options
        if analysis.fix_options:
            lines.append("### Fix Options:\n")

            for option in analysis.fix_options:
                recommended = " (Recommended)" if option.option_number == 1 else ""
                lines.append("")
                lines.append("<details>")
                lines.append(
                    f"<summary><strong>Option {option.option_number}: "
                    f"{option.title}{recommended}</strong></summary>"
                )
                lines.append("")
                lines.append(f"**Who changes:** {option.side.title()}")
                lines.append(f"**Impact:** {option.impact}")
                lines.append("")

                if option.code_suggestion:
                    lines.append("```")
                    lines.append(option.code_suggestion)
                    lines.append("```")
                    lines.append("")

                lines.append(option.description)
                lines.append("")
                lines.append("</details>")

        lines.append("---")
        lines.append("*AI Contract Testing — Breaking Change Agent*")

        return "\n".join(lines)

    def _format_test_error_comment(self, analysis: BreakingChangeAnalysis) -> str:
        """Format a test code error comment."""
        lines = []
        lines.append("## Verification Failed — Test Code Error\n")
        lines.append("This is **not** a breaking change. "
                      "The AI-generated test code has issues:\n")

        for change in analysis.changes[:5]:
            lines.append(f"- **{change.description}**")

        lines.append("")
        lines.append("This will be addressed in the next generation attempt.\n")
        lines.append("---")
        lines.append("*AI Contract Testing — Breaking Change Agent*")

        return "\n".join(lines)

    def _format_unknown_error_comment(self, analysis: BreakingChangeAnalysis) -> str:
        """Format an unknown error comment."""
        lines = []
        lines.append("## Verification Failed — Unclassified Error\n")
        lines.append(f"{analysis.summary}\n")

        if analysis.raw_error:
            lines.append("<details>")
            lines.append("<summary>Raw error output</summary>\n")
            lines.append("```")
            lines.append(analysis.raw_error[:1500])
            lines.append("```")
            lines.append("</details>\n")

        lines.append("---")
        lines.append("*AI Contract Testing — Breaking Change Agent*")

        return "\n".join(lines)


# =============================================================================
# Convenience function
# =============================================================================

def analyze_breaking_changes(
    verification_output: str,
    pact_context: PactContext,
    provider_context: ProviderCodeContext,
    provider_name: str
) -> BreakingChangeAnalysis:
    """Analyze verification output for breaking changes."""
    agent = BreakingChangeAgent()
    return agent.analyze(
        verification_output=verification_output,
        pact_context=pact_context,
        provider_context=provider_context,
        provider_name=provider_name
    )
