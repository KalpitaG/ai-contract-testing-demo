"""
Provider Module
===============

AI-powered provider verification test generation and breaking change detection.

Modules:
    pact_fetcher: Fetches pacts from Pactflow
    provider_analyzer: Analyzes provider source code
    provider_prompts: AI prompts for generation
    provider_generator: Main generator orchestration
    breaking_change_agent: Detects and analyzes contract breaking changes
"""

from .pact_fetcher import PactFetcher, fetch_pact_context
from .provider_analyzer import ProviderAnalyzer, analyze_provider
from .provider_generator import ProviderGenerator, generate_provider_tests
from .breaking_change_agent import BreakingChangeAgent, analyze_breaking_changes

__all__ = [
    'PactFetcher',
    'fetch_pact_context',
    'ProviderAnalyzer',
    'analyze_provider',
    'ProviderGenerator',
    'generate_provider_tests',
    'BreakingChangeAgent',
    'analyze_breaking_changes',
]
