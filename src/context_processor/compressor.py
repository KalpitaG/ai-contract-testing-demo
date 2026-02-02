"""
Context Compressor
==================
Reduces the token count of aggregated context before sending to AI.

Why compression matters:
- Gemini has token limits (input + output combined)
- Larger context = higher API costs
- Smaller, focused context = better AI responses

Compression strategies:
1. Remove redundant information
2. Summarize verbose sections
3. Keep only relevant fields
4. Deduplicate across sources
"""

import os
import re
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from langfuse import observe, get_client

from .aggregator import AggregatedContext

load_dotenv()


@dataclass
class CompressionStats:
    """Statistics about the compression performed."""
    original_tokens: int
    compressed_tokens: int
    reduction_percent: float
    strategies_applied: list[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        return (
            f"Compression: {self.original_tokens} -> {self.compressed_tokens} tokens "
            f"({self.reduction_percent:.1f}% reduction)"
        )


@dataclass
class CompressedContext:
    """Compressed context ready for AI consumption."""
    compressed_text: str
    stats: CompressionStats
    
    # Reference to original context (single source of truth)
    original_context: Optional[AggregatedContext] = None
    
    # Convenience properties
    @property
    def repo(self) -> str:
        return self.original_context.repo if self.original_context else ""
    
    @property
    def pr_number(self) -> int:
        return self.original_context.pr_number if self.original_context else 0
    
    @property
    def language(self) -> str:
        return self.original_context.detected_language if self.original_context else "unknown"
    
    @property
    def pact_library_info(self) -> str:
        if self.original_context and self.original_context.pact_library:
            return self.original_context.pact_library.package
        return "unknown"
    
    @property
    def test_directory(self) -> str:
        return self.original_context.test_directory if self.original_context else "tests/pact"
    
    def get_token_estimate(self) -> int:
        """Estimate token count (~4 chars per token)."""
        return len(self.compressed_text) // 4


class ContextCompressor:
    """
    Compresses aggregated context to reduce token count.
    
    Target: ~80% reduction while preserving essential information.
    
    Strategies:
    1. Remove raw API responses (keep formatted only)
    2. Truncate long descriptions
    3. Remove duplicate information across sources
    4. Strip unnecessary whitespace and formatting
    5. Summarize file changes (group by type)
    6. Keep only relevant OpenAPI endpoints
    
    Usage:
        compressor = ContextCompressor()
        compressed = compressor.compress(aggregated_context)
        print(compressed.stats)  # Shows reduction stats
    """
    
    def __init__(
        self,
        max_description_length: int = 500,
        max_file_list: int = 20,
        max_comments: int = 5,
        max_endpoints_per_spec: int = 10
    ):
        """
        Initialize compressor with limits.
        
        Args:
            max_description_length: Max chars for descriptions
            max_file_list: Max number of files to list
            max_comments: Max number of PR/JIRA comments to include
            max_endpoints_per_spec: Max endpoints per OpenAPI spec
        """
        self.max_description_length = max_description_length
        self.max_file_list = max_file_list
        self.max_comments = max_comments
        self.max_endpoints_per_spec = max_endpoints_per_spec
    
    @observe(name="context_compress")
    def compress(self, context: AggregatedContext) -> CompressedContext:
        """
        Compress aggregated context.
        
        Args:
            context: The full aggregated context
            
        Returns:
            CompressedContext with reduced token count
        """
        print("[Compressor] Starting compression...")
        strategies_applied = []
        
        # Calculate original size
        original_text = context.format_for_ai()
        original_tokens = len(original_text) // 4
        print(f"[Compressor] Original size: ~{original_tokens} tokens")
        
        # Build compressed sections
        sections = []
        
        # Header (minimal)
        sections.append(self._compress_header(context))
        strategies_applied.append("minimal_header")
        
        # Pact library info (keep full - AI needs this)
        sections.append(self._format_pact_library(context))
        
        # GitHub PR context (compress)
        if context.github_context:
            sections.append(self._compress_github(context.github_context))
            strategies_applied.append("github_compression")
        
        # JIRA context (compress)
        if context.jira_context:
            sections.append(self._compress_jira(context.jira_context))
            strategies_applied.append("jira_compression")
        
        # OpenAPI context (compress significantly)
        if context.openapi_contexts:
            sections.append(self._compress_openapi_multiple(context.openapi_contexts))
            strategies_applied.append("openapi_compression")
        
        # Pactflow context (compress)
        if context.pactflow_context:
            sections.append(self._compress_pactflow(context.pactflow_context))
            strategies_applied.append("pactflow_compression")
        
        # Combine all sections
        compressed_text = "\n\n".join(sections)
        
        # Final cleanup
        compressed_text = self._final_cleanup(compressed_text)
        strategies_applied.append("whitespace_cleanup")
        
        # Calculate stats
        compressed_tokens = len(compressed_text) // 4
        reduction = ((original_tokens - compressed_tokens) / original_tokens) * 100 if original_tokens > 0 else 0
        
        print(f"[Compressor] Compressed size: ~{compressed_tokens} tokens")
        print(f"[Compressor] Reduction: {reduction:.1f}%")
        
        stats = CompressionStats(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction_percent=reduction,
            strategies_applied=strategies_applied
        )
        
        # Log to Langfuse
        try:
            get_client().update_current_span(
                metadata={
                    "original_tokens": original_tokens,
                    "compressed_tokens": compressed_tokens,
                    "reduction_percent": reduction,
                    "strategies": strategies_applied
                }
            )
        except Exception:
            pass  # Langfuse logging is best-effort, don't fail the workflow
        
        return CompressedContext(
            compressed_text=compressed_text,
            stats=stats,
            original_context=context
        )
    
    def _compress_header(self, context: AggregatedContext) -> str:
        """Create minimal header."""
        return f"""=== CONTRACT TEST GENERATION CONTEXT ===
Repository: {context.repo}
PR: #{context.pr_number}
Language: {context.detected_language}
Test Directory: {context.test_directory}"""
    
    def _format_pact_library(self, context: AggregatedContext) -> str:
        """Format Pact library info (keep full - AI needs exact syntax)."""
        if not context.pact_library:
            return "Pact Library: Use appropriate library for detected language"
        
        lib = context.pact_library
        return f"""=== PACT LIBRARY ===
Package: {lib.package}
Framework: {lib.test_framework}
File Extension: {lib.file_extension}

Import:
{lib.import_statement}

Example Structure:
{lib.example_test_structure}"""
    
    def _compress_github(self, github_ctx) -> str:
        """Compress GitHub PR context."""
        lines = ["=== PULL REQUEST ==="]
        
        # Essential info only
        lines.append(f"Title: {github_ctx.title}")
        lines.append(f"Author: {github_ctx.author}")
        lines.append(f"Branch: {github_ctx.source_branch} -> {github_ctx.target_branch}")
        
        # Truncate description
        if github_ctx.description:
            desc = github_ctx.description[:self.max_description_length]
            if len(github_ctx.description) > self.max_description_length:
                desc += "..."
            lines.append(f"\nDescription:\n{desc}")
        
        # Summarize files by type instead of listing all
        if github_ctx.changed_files:
            file_summary = self._summarize_files(github_ctx.changed_files)
            lines.append(f"\nFiles Changed ({len(github_ctx.changed_files)} total):")
            lines.append(file_summary)
        
        # Limited comments
        if github_ctx.review_comments:
            lines.append(f"\nRecent Comments ({min(len(github_ctx.review_comments), self.max_comments)} shown):")
            for comment in github_ctx.review_comments[:self.max_comments]:
                lines.append(f"  - {comment.get('author', 'unknown')}: {comment.get('body', '')[:100]}")
        
        return "\n".join(lines)
    
    def _summarize_files(self, files: list) -> str:
        """Summarize file changes by category."""
        categories = {
            "api": [],
            "tests": [],
            "config": [],
            "source": [],
            "other": []
        }
        
        for file in files:
            filename = file if isinstance(file, str) else file.get('filename', str(file))
            
            if any(x in filename.lower() for x in ['openapi', 'swagger', 'api/specs']):
                categories["api"].append(filename)
            elif any(x in filename.lower() for x in ['test', 'spec', '_test.', '.test.']):
                categories["tests"].append(filename)
            elif any(x in filename.lower() for x in ['config', '.yaml', '.yml', '.json', '.toml']):
                categories["config"].append(filename)
            elif any(x in filename.lower() for x in ['.go', '.ts', '.js', '.py', '.java', '.kt']):
                categories["source"].append(filename)
            else:
                categories["other"].append(filename)
        
        summary = []
        for category, files in categories.items():
            if files:
                # Show first few files, then count
                shown = files[:3]
                remaining = len(files) - 3
                file_str = ", ".join(shown)
                if remaining > 0:
                    file_str += f" (+{remaining} more)"
                summary.append(f"  {category.upper()}: {file_str}")
        
        return "\n".join(summary) if summary else "  No files"
    
    def _compress_jira(self, jira_ctx) -> str:
        """Compress JIRA ticket context."""
        lines = ["=== JIRA TICKET ==="]
        
        lines.append(f"Key: {jira_ctx.ticket_key}")
        lines.append(f"Summary: {jira_ctx.summary}")
        lines.append(f"Type: {jira_ctx.issue_type}")
        lines.append(f"Status: {jira_ctx.status}")
        
        # Truncate description
        if jira_ctx.description:
            desc = jira_ctx.description[:self.max_description_length]
            if len(jira_ctx.description) > self.max_description_length:
                desc += "..."
            lines.append(f"\nDescription:\n{desc}")
        
        # Acceptance criteria (important for test generation)
        if jira_ctx.acceptance_criteria:
            ac = jira_ctx.acceptance_criteria[:800]  # Keep more of AC
            if len(jira_ctx.acceptance_criteria) > 800:
                ac += "..."
            lines.append(f"\nAcceptance Criteria:\n{ac}")
        
        return "\n".join(lines)
    
    def _compress_openapi(self, openapi_ctx) -> str:
        """Compress single OpenAPI spec context."""
        lines = ["=== OPENAPI SPECIFICATION ==="]
        
        lines.append(f"Title: {openapi_ctx.title}")
        lines.append(f"Version: {openapi_ctx.version}")
        if openapi_ctx.base_url:
            lines.append(f"Base URL: {openapi_ctx.base_url}")
        
        # Compress endpoints
        endpoints = openapi_ctx.endpoints[:self.max_endpoints_per_spec]
        lines.append(f"\nEndpoints ({len(openapi_ctx.endpoints)} total, showing {len(endpoints)}):")
        
        for ep in endpoints:
            lines.append(f"\n  {ep.method.upper()} {ep.path}")
            if ep.summary:
                lines.append(f"    Summary: {ep.summary}")
            
            # Minimal parameter info
            if ep.parameters:
                param_names = [p.get('name') or 'unknown' for p in ep.parameters[:5] if p]
                if param_names:
                    lines.append(f"    Params: {', '.join(param_names)}")
            
            # Response codes only
            if ep.responses:
                codes = list(ep.responses.keys())[:4]
                lines.append(f"    Responses: {', '.join(str(c) for c in codes)}")
        
        # Schemas (names only, not full definitions)
        if openapi_ctx.schemas:
            schema_names = list(openapi_ctx.schemas.keys())[:10]
            lines.append(f"\nSchemas: {', '.join(schema_names)}")
            if len(openapi_ctx.schemas) > 10:
                lines.append(f"  (+{len(openapi_ctx.schemas) - 10} more)")
        
        return "\n".join(lines)
    
    def _compress_openapi_multiple(self, openapi_contexts: list) -> str:
        """Compress multiple OpenAPI specs."""
        sections = []
        
        for i, ctx in enumerate(openapi_contexts):
            if i >= 3:  # Max 3 specs
                sections.append(f"\n(+{len(openapi_contexts) - 3} more specs not shown)")
                break
            sections.append(self._compress_openapi(ctx))
        
        return "\n\n".join(sections)
    
    def _compress_pactflow(self, pactflow_ctx) -> str:
        """Compress Pactflow context."""
        lines = ["=== EXISTING CONTRACTS (Pactflow) ==="]
        
        if not pactflow_ctx.contracts:
            lines.append("No existing contracts found.")
            return "\n".join(lines)
        
        lines.append(f"Total Contracts: {len(pactflow_ctx.contracts)}")
        
        # Group by consumer
        by_consumer = {}
        for contract in pactflow_ctx.contracts:
            consumer = contract.consumer
            if consumer not in by_consumer:
                by_consumer[consumer] = []
            by_consumer[consumer].append(contract)
        
        for consumer, contracts in by_consumer.items():
            providers = [c.provider for c in contracts]
            lines.append(f"\n  {consumer} -> {', '.join(providers)}")
        
        return "\n".join(lines)
    
    def _final_cleanup(self, text: str) -> str:
        """Final cleanup of compressed text."""
        # Remove multiple blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Remove trailing whitespace on lines
        text = '\n'.join(line.rstrip() for line in text.split('\n'))
        
        # Remove leading/trailing whitespace
        text = text.strip()
        
        return text

