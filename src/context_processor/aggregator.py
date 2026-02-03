"""
Context Aggregator
==================
Orchestrates all context collectors and combines their outputs into a
single, unified context ready for AI consumption.

This is the main entry point for the context collection phase.
It coordinates:
- RepoAnalyzer: Detects language, finds specs, extracts tickets
- GitHubCollector: Gets PR information
- JiraCollector: Gets ticket details (if ticket found AND JIRA configured)
- OpenAPICollector: Parses relevant API specs
- PactflowCollector: Gets existing contract information

JIRA Integration:
- JIRA is OPTIONAL. If JIRA_BASE_URL is not set, JIRA collection is skipped.
- This allows the pipeline to work on personal repos without JIRA access.
"""

import os
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from langfuse import observe, get_client
import yaml
import json

from .repo_analyzer import RepoAnalyzer, RepoAnalysis, PactLibraryInfo
from src.context_collector.github_collector import GitHubCollector, GitHubContext
from src.context_collector.openapi_collector import OpenAPICollector, OpenAPIContext
from src.context_collector.pactflow_collector import PactflowCollector, PactflowContext

# Conditional import for JIRA - only if credentials are available
try:
    from src.context_collector.jira_collector import JiraCollector, JiraContext
    JIRA_AVAILABLE = True
except ImportError:
    JIRA_AVAILABLE = False
    JiraContext = None

load_dotenv()


def is_jira_configured() -> bool:
    """Check if JIRA credentials are configured in environment."""
    return all([
        os.getenv("JIRA_BASE_URL"),
        os.getenv("JIRA_EMAIL"),
        os.getenv("JIRA_API_TOKEN")
    ])


@dataclass
class AggregatedContext:
    """
    Combined context from all sources, ready for AI consumption.
    
    This is the final output of the context collection phase,
    containing everything the AI needs to generate contract tests.
    """
    # Repository analysis results
    repo: str
    pr_number: int
    
    # Collected contexts (may be None if not applicable)
    repo_analysis: Optional[RepoAnalysis] = None
    github_context: Optional[GitHubContext] = None
    jira_context: Optional["JiraContext"] = None  # String annotation for optional import
    openapi_contexts: list[OpenAPIContext] = field(default_factory=list)  # Multiple specs
    pactflow_context: Optional[PactflowContext] = None
    
    # Source files (consumer code, etc.)
    source_files: dict[str, str] = field(default_factory=dict)  # filename -> content
    
    # Metadata
    ticket_key: Optional[str] = None
    specs_used: list[str] = field(default_factory=list)
    collection_warnings: list[str] = field(default_factory=list)
    
    # Convenience properties (access from repo_analysis)
    @property
    def detected_language(self) -> str:
        return self.repo_analysis.detected_language if self.repo_analysis else "unknown"
    
    @property
    def language_confidence(self) -> str:
        return self.repo_analysis.language_confidence if self.repo_analysis else "low"
    
    @property
    def pact_library(self) -> Optional[PactLibraryInfo]:
        return self.repo_analysis.pact_library if self.repo_analysis else None
    
    @property
    def test_directory(self) -> str:
        return self.repo_analysis.test_directory if self.repo_analysis else "tests/pact"
    
    @property
    def test_file_naming(self) -> str:
        return self.repo_analysis.test_file_naming if self.repo_analysis else "{consumer}_{provider}_pact_test"
    
    @observe(name="context_format_for_ai")
    def format_for_ai(self) -> str:
        """
        Format all collected context into a single string for AI consumption.
        
        This is the main output that will be sent to Gemini.
        """
        sections = []
        
        # Header
        sections.append("=" * 70)
        sections.append("CONTEXT FOR AI CONTRACT TEST GENERATION")
        sections.append("=" * 70)
        sections.append("")
        
        # Repository info
        sections.append("REPOSITORY INFORMATION:")
        sections.append(f"  Repository: {self.repo}")
        sections.append(f"  PR Number: {self.pr_number}")
        sections.append(f"  Language: {self.detected_language} ({self.language_confidence} confidence)")
        sections.append(f"  Test Directory: {self.test_directory}")
        sections.append("")
        
        # Pact library info
        if self.pact_library:
            sections.append("PACT LIBRARY:")
            sections.append(f"  Package: {self.pact_library.package}")
            sections.append(f"  Test Framework: {self.pact_library.test_framework}")
            sections.append(f"  File Extension: {self.pact_library.file_extension}")
            sections.append("")
            sections.append("IMPORT STATEMENT TO USE:")
            sections.append(self.pact_library.import_statement)
            sections.append("")
            sections.append("EXAMPLE TEST STRUCTURE:")
            sections.append(self.pact_library.example_test_structure)
            sections.append("")
        
        # GitHub PR context
        if self.github_context:
            sections.append("-" * 70)
            sections.append(self.github_context.format_for_ai())
            sections.append("")
        
        # JIRA ticket context
        if self.jira_context:
            sections.append("-" * 70)
            sections.append(self.jira_context.format_for_ai())
            sections.append("")
        elif self.ticket_key:
            sections.append("-" * 70)
            sections.append(f"JIRA TICKET: {self.ticket_key}")
            sections.append("  (Could not fetch ticket details - JIRA not configured or inaccessible)")
            sections.append("")
        
        # OpenAPI spec context(s)
        if self.openapi_contexts:
            for openapi_ctx in self.openapi_contexts:
                sections.append("-" * 70)
                sections.append(openapi_ctx.format_for_ai())
                sections.append("")
        else:
            sections.append("-" * 70)
            sections.append("OPENAPI SPECIFICATION:")
            sections.append("  No OpenAPI spec found or applicable for this repository.")
            sections.append("  Generate tests based on PR changes and JIRA requirements.")
            sections.append("")
        
        # Pactflow existing contracts
        if self.pactflow_context:
            sections.append("-" * 70)
            sections.append(self.pactflow_context.format_for_ai())
            sections.append("")
        
        # Warnings
        if self.collection_warnings:
            sections.append("-" * 70)
            sections.append("WARNINGS:")
            for warning in self.collection_warnings:
                sections.append(f"  - {warning}")
            sections.append("")
        
        sections.append("=" * 70)
        sections.append("END OF CONTEXT")
        sections.append("=" * 70)
        
        return "\n".join(sections)
    
    @observe(name="context_get_token_estimate")
    def get_token_estimate(self) -> int:
        """
        Estimate the number of tokens in the formatted context.
        
        Rough estimate: ~4 characters per token for English text.
        """
        formatted = self.format_for_ai()
        return len(formatted) // 4


class ContextAggregator:
    """
    Orchestrates all context collectors and combines their outputs.
    
    This is the main entry point for context collection. It:
    1. Analyzes the repository structure (language, specs, ticket)
    2. Collects context from GitHub PR
    3. Collects context from JIRA (if ticket found AND JIRA is configured)
    4. Collects context from OpenAPI specs (if found)
    5. Collects context from Pactflow (existing contracts)
    6. Combines everything into a single AggregatedContext
    
    JIRA is optional - if JIRA credentials are not configured, the pipeline
    continues without JIRA context. This allows usage on personal repos.
    
    Usage:
        aggregator = ContextAggregator()
        context = aggregator.aggregate("owner/repo", pr_number=123)
        formatted = context.format_for_ai()
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize all collectors.
        
        Args:
            config_path: Optional path to detection.yaml config file
        """
        self.repo_analyzer = RepoAnalyzer(config_path)
        self.github_collector = GitHubCollector()
        self.openapi_collector = OpenAPICollector()
        self.pactflow_collector = PactflowCollector()
        
        # Initialize JIRA collector only if configured
        self.jira_collector = None
        self.jira_enabled = is_jira_configured()
        
        if self.jira_enabled and JIRA_AVAILABLE:
            try:
                from src.context_collector.jira_collector import JiraCollector
                self.jira_collector = JiraCollector()
                print("[Aggregator] JIRA integration enabled")
            except Exception as e:
                print(f"[Aggregator] JIRA integration disabled: {e}")
                self.jira_enabled = False
        else:
            print("[Aggregator] JIRA integration disabled (credentials not configured)")
    
    @observe(name="context_aggregate")
    def aggregate(
        self,
        repo: str,
        pr_number: int,
        force_ticket: Optional[str] = None,
        force_specs: Optional[list[str]] = None
    ) -> AggregatedContext:
        """
        Aggregate context from all sources.
        
        Args:
            repo: Repository name (e.g., "owner/repo")
            pr_number: Pull request number
            force_ticket: Optional JIRA ticket to use (overrides auto-detection)
            force_specs: Optional list of spec paths (overrides auto-detection)
            
        Returns:
            AggregatedContext with all collected information
        """
        print(f"[Aggregator] Starting context aggregation for {repo} PR #{pr_number}")
        warnings = []
        
        # Step 1: Analyze repository
        print("[Aggregator] Step 1/5: Analyzing repository...")
        repo_analysis = self.repo_analyzer.analyze(repo, pr_number)
        
        # Determine ticket to use
        ticket_key = force_ticket or repo_analysis.ticket_key
        
        # Determine specs to use
        specs_to_use = force_specs or (repo_analysis.relevant_specs + repo_analysis.common_specs)
        # Remove duplicates while preserving order
        specs_to_use = list(dict.fromkeys(specs_to_use))
        
        # Step 2: Collect GitHub PR context
        print("[Aggregator] Step 2/5: Collecting GitHub PR context...")
        github_context = None
        try:
            github_context = self.github_collector.collect(repo, pr_number)
        except Exception as e:
            warning = f"Failed to collect GitHub context: {e}"
            print(f"[Aggregator] Warning: {warning}")
            warnings.append(warning)
        
        # Step 3: Collect JIRA context (if ticket exists AND JIRA is configured)
        print("[Aggregator] Step 3/5: Collecting JIRA context...")
        jira_context = None
        
        if not self.jira_enabled:
            print("[Aggregator] Skipping JIRA (not configured)")
        elif not ticket_key:
            print("[Aggregator] Skipping JIRA (no ticket found in PR)")
        else:
            try:
                jira_context = self.jira_collector.collect(ticket_key)
                print(f"[Aggregator] Collected JIRA context for {ticket_key}")
            except Exception as e:
                warning = f"Failed to collect JIRA context for {ticket_key}: {e}"
                print(f"[Aggregator] Warning: {warning}")
                warnings.append(warning)
        
        # Step 4: Collect OpenAPI context(s)
        print("[Aggregator] Step 4/5: Collecting OpenAPI context...")
        openapi_contexts = []
        if specs_to_use:
            for spec_path in specs_to_use:
                try:
                    # We need to fetch the spec content from GitHub
                    openapi_ctx = self._collect_openapi_from_github(repo, spec_path)
                    if openapi_ctx:
                        openapi_contexts.append(openapi_ctx)
                except Exception as e:
                    warning = f"Failed to collect OpenAPI context for {spec_path}: {e}"
                    print(f"[Aggregator] Warning: {warning}")
                    warnings.append(warning)
        else:
            print("[Aggregator] Skipping OpenAPI (no specs found)")
        
        # Step 5: Collect Pactflow context
        print("[Aggregator] Step 5/6: Collecting Pactflow context...")
        pactflow_context = None
        try:
            pactflow_context = self.pactflow_collector.collect()
        except Exception as e:
            warning = f"Failed to collect Pactflow context: {e}"
            print(f"[Aggregator] Warning: {warning}")
            warnings.append(warning)
        
        # Step 6: Collect consumer source files
        print("[Aggregator] Step 6/6: Collecting consumer source files...")
        source_files = {}
        try:
            source_files = self._collect_source_files(repo, repo_analysis.detected_language)
        except Exception as e:
            warning = f"Failed to collect source files: {e}"
            print(f"[Aggregator] Warning: {warning}")
            warnings.append(warning)
        
        # Build aggregated context
        aggregated = AggregatedContext(
            repo=repo,
            pr_number=pr_number,
            repo_analysis=repo_analysis,
            github_context=github_context,
            jira_context=jira_context,
            openapi_contexts=openapi_contexts,
            pactflow_context=pactflow_context,
            source_files=source_files,
            ticket_key=ticket_key,
            specs_used=specs_to_use,
            collection_warnings=warnings
        )
        
        # Log metadata to Langfuse
        token_estimate = aggregated.get_token_estimate()
        try:
            get_client().update_current_span(
                metadata={
                    "repo": repo,
                    "pr_number": pr_number,
                    "language": repo_analysis.detected_language,
                    "ticket": ticket_key,
                    "jira_enabled": self.jira_enabled,
                    "specs_count": len(specs_to_use),
                    "warnings_count": len(warnings),
                    "token_estimate": token_estimate
                }
            )
        except Exception:
            pass  # Langfuse logging is best-effort, don't fail the workflow
        
        print(f"[Aggregator] Aggregation complete. Estimated tokens: {token_estimate}")
        
        return aggregated
    
    def _collect_openapi_from_github(
        self,
        repo: str,
        spec_path: str
    ) -> Optional[OpenAPIContext]:
        """
        Fetch OpenAPI spec from GitHub and parse it.
        
        Args:
            repo: Repository name
            spec_path: Path to spec file in the repository
            
        Returns:
            OpenAPIContext or None if failed
        """
        # Reuse the existing GitHub client from GitHubCollector to avoid duplicate connections
        gh_repo = self.github_collector.client.get_repo(repo)
        
        try:
            content = gh_repo.get_contents(spec_path)
            file_content = content.decoded_content.decode("utf-8")
            
            # Parse based on file extension
            if spec_path.endswith((".yaml", ".yml")):
                spec_dict = yaml.safe_load(file_content)
            else:
                spec_dict = json.loads(file_content)
            
            return self.openapi_collector.collect_from_dict(spec_dict, source=spec_path)
            
        except Exception as e:
            print(f"[Aggregator] Error fetching {spec_path}: {e}")
            return None

    def _collect_source_files(self, repo: str, language: str) -> dict[str, str]:
        """
        Collect key source files that contain consumer/API code.
        
        Args:
            repo: Repository name
            language: Detected programming language
            
        Returns:
            Dict mapping filename to content
        """
        source_files = {}
        gh_repo = self.github_collector.client.get_repo(repo)
        
        # Define patterns for consumer/API files by language
        patterns = {
            "javascript": ["src/consumer.js", "src/client.js", "src/api.js", "lib/consumer.js", "consumer.js"],
            "typescript": ["src/consumer.ts", "src/client.ts", "src/api.ts", "lib/consumer.ts", "consumer.ts"],
            "python": ["src/consumer.py", "src/client.py", "src/api.py", "consumer.py", "client.py"],
            "java": ["src/main/java/**/Consumer.java", "src/main/java/**/Client.java", "src/main/java/**/ApiClient.java"],
            "go": ["consumer.go", "client.go", "api.go", "internal/consumer/consumer.go"],
        }
        
        files_to_check = patterns.get(language, [])
        
        for file_path in files_to_check:
            try:
                # Skip glob patterns for now (would need recursive search)
                if "*" in file_path:
                    continue
                    
                content = gh_repo.get_contents(file_path)
                if content and hasattr(content, 'decoded_content'):
                    file_content = content.decoded_content.decode("utf-8")
                    source_files[file_path] = file_content
                    print(f"  [OK] Collected {file_path} ({len(file_content)} chars)")
            except Exception:
                # File doesn't exist, skip
                pass
        
        if not source_files:
            print("  [WARN] No consumer source files found")
        
        return source_files
