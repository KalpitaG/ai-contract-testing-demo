"""
Contract Test Generation Pipeline
=================================
End-to-end pipeline that:
1. Collects context (GitHub PR, JIRA, OpenAPI, Pactflow)
2. Detects language and relevant specs (via RepoAnalyzer)
3. Compresses context for AI
4. Generates Pact contract tests using Gemini
5. Supports revision mode with error feedback

This is the main entry point for the AI-powered contract testing workflow.

Usage:
    # From command line:
    python -m src.pipeline owner/repo 123
    
    # With revision feedback (retry after test failure):
    python -m src.pipeline owner/repo 123 --revision-feedback "Tests failed: Error message..."
    
    # From code:
    from src.pipeline import ContractTestPipeline
    
    pipeline = ContractTestPipeline()
    result = pipeline.run(repo="owner/repo", pr_number=123)
    
    if result.has_tests:
        for test in result.generated_tests:
            print(test.code)
"""

import sys
import traceback
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from langfuse import observe, get_client

# Our modules
from src.context_processor.aggregator import ContextAggregator, AggregatedContext
from src.context_processor.compressor import ContextCompressor, CompressedContext
from src.test_generator.generator import (
    ContractTestGenerator,
    GenerationResult,
    GeneratorConfig
)
from src.test_generator.output_parser import OutputParser

load_dotenv()


# =============================================================================
# PIPELINE RESULT
# =============================================================================

@dataclass
class PipelineResult:
    """Complete result from the pipeline."""
    # Context collection
    aggregated_context: Optional[AggregatedContext] = None
    compressed_context: Optional[CompressedContext] = None
    
    # Language detection
    detected_language: str = "unknown"
    detected_confidence: str = "low"
    
    # AI generation
    generation_result: Optional[GenerationResult] = None
    
    # Pipeline status
    success: bool = False
    error: Optional[str] = None
    skip_reason: Optional[str] = None
    
    # Revision tracking
    is_revision: bool = False
    revision_feedback: Optional[str] = None
    
    @property
    def has_tests(self) -> bool:
        """Check if tests were generated."""
        return (
            self.generation_result is not None 
            and self.generation_result.has_tests
        )
    
    @property
    def generated_tests(self):
        """Get generated tests (if any)."""
        if self.generation_result:
            return self.generation_result.tests
        return []
    
    @property
    def analysis(self):
        """Get analysis result (if any)."""
        if self.generation_result:
            return self.generation_result.analysis
        return None
    
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = ["=" * 60, "Pipeline Result Summary", "=" * 60]
        
        if self.is_revision:
            lines.append("Mode: REVISION (retry with feedback)")
        
        if self.error:
            lines.append(f"Status: FAILED")
            lines.append(f"Error: {self.error}")
            return "\n".join(lines)
        
        if self.skip_reason:
            lines.append(f"Status: SKIPPED")
            lines.append(f"Reason: {self.skip_reason}")
            return "\n".join(lines)
        
        lines.append(f"Status: {'SUCCESS' if self.success else 'INCOMPLETE'}")
        lines.append(f"Language: {self.detected_language} ({self.detected_confidence} confidence)")
        
        if self.compressed_context:
            stats = self.compressed_context.stats
            lines.append(f"Context: {stats.original_tokens} -> {stats.compressed_tokens} tokens "
                        f"({stats.reduction_percent:.1f}% reduction)")
        
        if self.generation_result:
            analysis = self.generation_result.analysis
            lines.append(f"Change Type: {analysis.change_type}")
            lines.append(f"Risk Level: {analysis.risk_level}")
            lines.append(f"Affected Endpoints: {', '.join(analysis.affected_endpoints) or 'None'}")
            lines.append(f"Tests Generated: {len(self.generation_result.tests)}")
            
            if self.generation_result.token_usage:
                usage = self.generation_result.token_usage
                lines.append(f"AI Tokens Used: {usage.get('total_tokens', 'N/A')}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


# =============================================================================
# PIPELINE
# =============================================================================

class ContractTestPipeline:
    """
    End-to-end pipeline for AI-powered contract test generation.
    
    The pipeline:
    1. Aggregates context from multiple sources (GitHub, JIRA, OpenAPI, Pactflow)
    2. Detects repository language and finds relevant OpenAPI specs
    3. Compresses context to fit AI token limits
    4. Calls Gemini to analyze changes and generate tests
    5. Supports revision mode for retrying with error feedback
    
    Each step is traced in Langfuse for observability.
    """
    
    def __init__(self, generator_config: Optional[GeneratorConfig] = None):
        """
        Initialize the pipeline.
        
        Args:
            generator_config: Optional Gemini configuration
        """
        self.aggregator = ContextAggregator()
        self.compressor = ContextCompressor()
        self.generator = ContractTestGenerator(config=generator_config)
        self.output_parser = OutputParser()
        
        print("[Pipeline] Initialized")
    
    @observe(name="contract_test_pipeline")
    def run(
        self,
        repo: str,
        pr_number: int,
        force_language: Optional[str] = None,
        revision_feedback: Optional[str] = None,
        existing_tests: Optional[dict[str, str]] = None
    ) -> PipelineResult:
        """
        Run the full pipeline for a Pull Request.
        
        Args:
            repo: Repository in "owner/repo" format
            pr_number: Pull Request number
            force_language: Override auto-detected language (optional)
            revision_feedback: Error feedback for revision mode (optional)
            existing_tests: Dict of existing test files (filename -> content) for revision mode
            
        Returns:
            PipelineResult with all outputs
        """
        result = PipelineResult()
        result.is_revision = revision_feedback is not None
        result.revision_feedback = revision_feedback
        
        mode_str = "REVISION MODE" if result.is_revision else "GENERATION MODE"
        print(f"\n{'=' * 60}")
        print(f"Running Pipeline: {repo} PR #{pr_number} [{mode_str}]")
        print(f"{'=' * 60}")
        
        if revision_feedback:
            print(f"\n[Revision] Feedback received:")
            print(f"  {revision_feedback[:200]}..." if len(revision_feedback) > 200 else f"  {revision_feedback}")
        
        # Step 1: Aggregate context
        print("\n[Step 1/5] Collecting and aggregating context...")
        try:
            aggregated = self.aggregator.aggregate(repo, pr_number)
            result.aggregated_context = aggregated
            
            # Extract language detection results
            result.detected_language = aggregated.detected_language
            result.detected_confidence = aggregated.language_confidence
            
            # Override language if specified
            if force_language:
                result.detected_language = force_language
                print(f"  Language overridden to: {force_language}")
            
            print(f"  Detected language: {result.detected_language}")
            print(f"  Token estimate: {aggregated.get_token_estimate()}")
            
        except Exception as e:
            result.error = f"Context aggregation failed: {str(e)}"
            print(f"  ERROR: {result.error}")
            traceback.print_exc()
            return result
        
        # Step 2: Check if PR has API changes (skip in revision mode)
        # NOTE: Disabled strict check - let AI decide based on full context
        # The AI has better understanding of what constitutes API changes
        print("\n[Step 2/5] Checking for API changes...")
        # Always proceed - AI will set change_type="no_contract_impact" if truly no changes
        print("  Proceeding with generation (AI will analyze for contract changes)")
        
        # Step 3: Compress context
        print("\n[Step 3/5] Compressing context...")
        try:
            compressed = self.compressor.compress(aggregated)
            result.compressed_context = compressed
            
            stats = compressed.stats
            print(f"  Original: {stats.original_tokens} tokens")
            print(f"  Compressed: {stats.compressed_tokens} tokens")
            print(f"  Reduction: {stats.reduction_percent:.1f}%")
            
        except Exception as e:
            result.error = f"Context compression failed: {str(e)}"
            print(f"  ERROR: {result.error}")
            traceback.print_exc()
            return result
        
        # Step 4: Generate tests (or regenerate with feedback)
        step_name = "Revising" if result.is_revision else "Generating"
        print(f"\n[Step 4/5] {step_name} contract tests with AI...")
        try:
            # Get file naming convention from pact library (default to snake_case)
            pact_library = aggregated.pact_library
            file_naming = pact_library.file_naming if pact_library else "snake_case"
            
            generation = self.generator.generate(
                compressed_context=compressed,
                language=result.detected_language,
                pact_library=pact_library,
                file_naming_convention=file_naming,
                revision_feedback=revision_feedback,  # Pass feedback for revision
                existing_tests=existing_tests  # Pass existing tests for revision mode
            )
            result.generation_result = generation
            
            print(f"  Change type: {generation.analysis.change_type}")
            print(f"  Risk level: {generation.analysis.risk_level}")
            print(f"  Tests generated: {len(generation.tests)}")
            
            if generation.skip_reason:
                result.skip_reason = generation.skip_reason
                print(f"  Skip reason: {generation.skip_reason}")
            
        except Exception as e:
            result.error = f"Test generation failed: {str(e)}"
            print(f"  ERROR: {result.error}")
            traceback.print_exc()
            return result
        
        # Step 5: Validate generated tests
        print("\n[Step 5/5] Validating generated tests...")
        if result.generation_result and result.generation_result.tests:
            for test in result.generation_result.tests:
                parsed = self.output_parser.parse(test.code, result.detected_language)
                print(f"  {test.filename}: {parsed.quality_score:.1f}/10")
                
                # Attach parsed result to test (optional)
                test.quality_score = parsed.quality_score
                test.quality_issues = parsed.issues
                
                # Log issues
                for issue in parsed.issues:
                    if issue.severity.value == "error":
                        print(f"    [ERROR] {issue.message}")
                    elif issue.severity.value == "warning":
                        print(f"    [WARN] {issue.message}")
        
        # Log to Langfuse
        try:
            get_client().update_current_span(
                output={
                    "success": True,
                    "tests_generated": len(result.generated_tests),
                    "change_type": result.analysis.change_type if result.analysis else None,
                    "is_revision": result.is_revision
                },
                metadata={
                    "repo": repo,
                    "pr_number": pr_number,
                    "language": result.detected_language
                }
            )
        except Exception:
            pass
        
        result.success = True
        return result
    
    def _has_api_changes(self, context: AggregatedContext) -> bool:
        """
        Determine if the PR contains API-related changes.

        Policy:
        - Return False (skip) only when we have strong evidence the PR is non-API (docs/ci-only etc.).
        - Return True when we see API/contract signals.
        - Default to True when uncertain (fail-open).
        """
        gh = context.github_context

        # 1) Strong signal: OpenAPI spec was found/used
        if context.openapi_contexts:
            return True

        # If we don't have GitHub context, we can't be confident -> proceed
        if not gh or not getattr(gh, "changed_files", None):
            return True

        changed_files = gh.changed_files or []
        filenames = [(f.get("filename") or "").lower() for f in changed_files if isinstance(f, dict)]
        print("[API Check] changed files (first 30):", filenames[:30])
        
        # If we can't see filenames, don't skip
        if not filenames:
            return True

        # 2) Strong "non-API-only" signal: ALL changed files are in doc/ci/config buckets
        non_api_prefixes = (
            ".github/", "docs/", "doc/", ".vscode/", ".idea/",
        )
        non_api_exact = (
            "readme.md", "changelog.md", "license", "license.md", "contributing.md",
            ".gitignore", ".gitattributes",
            "docker-compose.yml", "docker-compose.yaml",
        )
        non_api_exts = (
            ".md", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        )
        non_api_ci_exts = (".yml", ".yaml")  # BUT only safe if they are CI-only paths (handled below)

        def is_confidently_non_api_file(path: str) -> bool:
            p = path.lstrip("./")
            base = p.split("/")[-1]

            if base in non_api_exact:
                return True
            if p.startswith(non_api_prefixes):
                return True
            if base.endswith(non_api_exts):
                return True

            # YAML can be risky (could be OpenAPI), so only treat as non-API if it's clearly CI/config
            if base.endswith(non_api_ci_exts) and (
                p.startswith(".github/") or "ci" in p or "pipeline" in p or "workflows" in p
            ):
                return True

            return False

        # If every changed file is confidently non-API, skip
        if all(is_confidently_non_api_file(p) for p in filenames):
            return False

        # 3) Positive signals from filenames (API-ish)
        api_filename_signals = (
            "openapi", "swagger", "api-spec", "oas",
            "route", "router", "controller", "handler",
            "schema", "dto", "model",
            "endpoint", "contract",
            "rest", "http",
            "pact", "pactflow",
            "consumer", "provider", "client", "api",
            "service", "adapter", "gateway",
        )
        if any(any(sig in p for sig in api_filename_signals) for p in filenames):
            return True

        # 4) Positive signals from PR text (weak but useful)
        title = (getattr(gh, "title", "") or "").lower()
        desc = (getattr(gh, "description", "") or "").lower()
        text = f"{title} {desc}".strip()

        api_text_signals = ("api", "endpoint", "contract", "schema", "request", "response", "rest", "http", "openapi", "swagger", "pact")
        if text and any(sig in text for sig in api_text_signals):
            return True

        # 5) Default: uncertain -> proceed (do not skip)
        return True


# =============================================================================
# CLI RUNNER
# =============================================================================

def main() -> int:
    """Run the pipeline from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate Pact contract tests for a Pull Request"
    )
    parser.add_argument("repo", help="Repository in owner/repo format")
    parser.add_argument("pr", type=int, help="Pull Request number")
    parser.add_argument("--language", help="Override detected language")
    parser.add_argument("--model", default=None, help="Gemini model to use (default: from GEMINI_MODEL env var)")
    parser.add_argument("--output-dir", help="Directory to write generated tests")
    parser.add_argument(
        "--revision-feedback", 
        help="Error feedback for revision mode (retrying after test failure)"
    )
    
    args = parser.parse_args()
    
    # Configure generator (uses GEMINI_MODEL env var if --model not specified)
    config = GeneratorConfig(model=args.model) if args.model else GeneratorConfig.from_env()
    
    # Run pipeline
    pipeline = ContractTestPipeline(generator_config=config)
    result = pipeline.run(
        repo=args.repo,
        pr_number=args.pr,
        force_language=args.language,
        revision_feedback=args.revision_feedback
    )
    
    # Print summary
    print("\n" + result.summary())
    
    # Write tests to files if output dir specified
    if args.output_dir and result.has_tests:
        output_path = Path(args.output_dir)
        # Clear previous generated tests
        if output_path.exists():
            for f in output_path.glob("*"):
                f.unlink()
        output_path.mkdir(parents=True, exist_ok=True)
        
        for test in result.generated_tests:
            test_file = output_path / test.filename
            test_file.write_text(test.code)
            print(f"Wrote: {test_file}")
    
    # Print generated code to stdout
    if result.has_tests:
        print("\n" + "=" * 60)
        print("GENERATED TESTS")
        print("=" * 60)
        for test in result.generated_tests:
            print(f"\n--- {test.filename} ---")
            print(test.code)
    
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
