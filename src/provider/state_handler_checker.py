"""
State Handler Existence Checker
================================

Checks if a provider repo already has committed state handler files
and extracts which pact states are covered.

This enables:
- Skipping AI generation when all states are already handled
- Incremental generation (only add missing handlers)
- Detecting when committed handlers become stale

Usage:
    from src.provider.state_handler_checker import StateHandlerChecker

    checker = StateHandlerChecker()
    info = checker.check(
        provider_repo_path="/path/to/provider",
        pact_states=["item 1 exists", "no items exist"],
        language="javascript"
    )

    if info.all_states_covered:
        print("Skip AI generation — use existing handlers")
    elif info.exists:
        print(f"Missing states: {info.missing_states}")
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ExistingHandlerInfo:
    """Result of checking for existing state handlers."""
    exists: bool
    file_path: str = ""
    covered_states: list = field(default_factory=list)
    missing_states: list = field(default_factory=list)
    all_states_covered: bool = False
    language: str = ""
    raw_content: str = ""


# =============================================================================
# Handler File Patterns (per language)
# =============================================================================

# Where to look for handler files, and what extension they use
HANDLER_FILE_CONFIG = {
    "javascript": {
        "directory": "tests/contract-verification",
        "filename": "provider.pact.test.js",
    },
    "typescript": {
        "directory": "tests/contract-verification",
        "filename": "provider.pact.test.ts",
    },
    "go": {
        "directory": "tests/contract-verification",
        "filename": "provider_test.go",
    },
    "python": {
        "directory": "tests/contract-verification",
        "filename": "test_provider.py",
    },
    "java": {
        "directory": "tests/contract-verification",
        "filename": "ProviderPactTest.java",
    },
    "kotlin": {
        "directory": "tests/contract-verification",
        "filename": "ProviderPactTest.kt",
    },
}


# =============================================================================
# State Extraction Patterns (per language)
# =============================================================================

# Regex patterns to extract state handler names from code
# Each language has different Pact API patterns for declaring state handlers

STATE_PATTERNS = {
    "javascript": [
        # stateHandlers: { 'state name': () => { ... } }
        # Matches single-quoted, double-quoted, or backtick-quoted keys
        re.compile(r"""['"`]([^'"`]+)['"`]\s*:\s*(?:async\s*)?\("""),
    ],
    "typescript": [
        re.compile(r"""['"`]([^'"`]+)['"`]\s*:\s*(?:async\s*)?\("""),
    ],
    "go": [
        # StateHandlers: models.StateHandlers{ "state name": func(...) { ... } }
        re.compile(r'"([^"]+)"\s*:\s*func'),
    ],
    "python": [
        # @state_handler('state name') or state_handler={'state name': ...}
        re.compile(r"""state_handler\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
        re.compile(r"""['"]([^'"]+)['"]\s*:\s*(?:lambda|def)"""),
    ],
    "java": [
        # @State("state name")
        re.compile(r'@State\(\s*"([^"]+)"\s*\)'),
    ],
    "kotlin": [
        # @State("state name")
        re.compile(r'@State\(\s*"([^"]+)"\s*\)'),
    ],
}


# =============================================================================
# State Handler Checker
# =============================================================================

class StateHandlerChecker:
    """
    Checks for existing state handler files in a provider repository.

    Looks for committed handler files in the expected location, extracts
    which pact states are already covered, and reports missing states.
    """

    def check(
        self,
        provider_repo_path: str,
        pact_states: list,
        language: str
    ) -> ExistingHandlerInfo:
        """
        Check if provider repo has existing state handlers.

        Args:
            provider_repo_path: Path to the checked-out provider repo
            pact_states: List of state names from the pact
            language: Provider's programming language

        Returns:
            ExistingHandlerInfo with coverage details
        """
        # Find the handler file
        file_path = self._find_handler_file(provider_repo_path, language)

        if not file_path:
            return ExistingHandlerInfo(
                exists=False,
                language=language,
                missing_states=list(pact_states),
            )

        # Read the file content
        try:
            with open(file_path, 'r') as f:
                content = f.read()
        except (IOError, OSError) as e:
            print(f"  Warning: Could not read handler file {file_path}: {e}")
            return ExistingHandlerInfo(
                exists=False,
                language=language,
                missing_states=list(pact_states),
            )

        # Extract covered states
        covered_states = self._extract_states_from_content(content, language)

        # Calculate missing states (case-insensitive comparison)
        covered_lower = {s.lower() for s in covered_states}
        missing_states = [s for s in pact_states if s.lower() not in covered_lower]
        all_covered = len(missing_states) == 0

        # Make the path relative for cleaner output
        rel_path = os.path.relpath(file_path, provider_repo_path)

        print(f"  Existing handler file: {rel_path}")
        print(f"  Covered states: {len(covered_states)}/{len(pact_states)}")
        if missing_states:
            print(f"  Missing states: {missing_states}")
        else:
            print(f"  All states covered!")

        return ExistingHandlerInfo(
            exists=True,
            file_path=file_path,
            covered_states=covered_states,
            missing_states=missing_states,
            all_states_covered=all_covered,
            language=language,
            raw_content=content,
        )

    def _find_handler_file(
        self, repo_path: str, language: str
    ) -> Optional[str]:
        """Find the handler file in the provider repo."""
        config = HANDLER_FILE_CONFIG.get(language)
        if not config:
            # Unknown language — try common patterns
            for lang_config in HANDLER_FILE_CONFIG.values():
                path = os.path.join(
                    repo_path,
                    lang_config["directory"],
                    lang_config["filename"]
                )
                if os.path.isfile(path):
                    return path
            return None

        path = os.path.join(repo_path, config["directory"], config["filename"])
        if os.path.isfile(path):
            return path

        # Fallback: look for any file in the directory
        dir_path = os.path.join(repo_path, config["directory"])
        if os.path.isdir(dir_path):
            for fname in os.listdir(dir_path):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath) and not fname.startswith('.'):
                    return fpath

        return None

    def _extract_states_from_content(
        self, content: str, language: str
    ) -> list:
        """Extract state handler names from file content."""
        patterns = STATE_PATTERNS.get(language, [])
        if not patterns:
            # Fallback for unknown languages: try all patterns
            for lang_patterns in STATE_PATTERNS.values():
                patterns.extend(lang_patterns)

        states = set()
        for pattern in patterns:
            matches = pattern.findall(content)
            states.update(matches)

        # Filter out likely false positives (very short or generic names)
        states = {s for s in states if len(s) > 2}

        return sorted(states)
