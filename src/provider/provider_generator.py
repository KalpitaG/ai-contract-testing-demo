"""
Provider Generator Module
=========================

Main module for AI-powered provider verification test generation.

This module:
1. Fetches pacts from the broker
2. Analyzes provider source code
3. Generates state handlers using AI
4. Outputs complete verification test file

Usage:
    from src.provider import generate_provider_tests
    
    result = generate_provider_tests(
        provider_name="ProviderService",
        provider_repo_path="/path/to/provider"
    )
    
    print(result.generated_code)
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from langfuse.decorators import observe

# Relative imports (same package)
from .pact_fetcher import PactFetcher, PactContext
from .provider_analyzer import ProviderAnalyzer, ProviderCodeContext
from .provider_prompts import (
    PROVIDER_SYSTEM_PROMPT,
    build_provider_generation_prompt,
    build_provider_revision_prompt
)

# Import Gemini client
from google import genai


@dataclass
class ProviderGenerationResult:
    """Result of provider test generation."""
    success: bool
    provider_name: str
    generated_code: str
    output_path: str
    provider_states: list
    consumers: list
    storage_type: str
    error: Optional[str] = None
    quality_score: float = 0.0
    quality_issues: list = field(default_factory=list)


class ProviderGenerator:
    """
    Generates provider verification tests with AI-powered state handlers.
    
    The generation process:
    1. Fetch pacts from broker → extract provider states
    2. Analyze provider code → understand data storage
    3. Build AI prompt with both contexts
    4. Generate state handlers and verification test
    5. Validate and output
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
            raise ValueError("Gemini API key not configured. Set GEMINI_API_KEY.")
        
        # Initialize Gemini client
        self.genai_client = genai.Client(api_key=self.gemini_api_key)
        
        # Initialize Pact fetcher
        self.pact_fetcher = PactFetcher(
            broker_url=self.pactflow_url,
            broker_token=self.pactflow_token
        )
    
    @observe(name="generate_provider_tests")
    def generate(
        self,
        provider_name: str,
        provider_repo_path: str,
        output_dir: Optional[str] = None
    ) -> ProviderGenerationResult:
        """
        Generate provider verification tests with state handlers.
        
        Args:
            provider_name: Name of the provider (must match Pactflow)
            provider_repo_path: Path to the provider repository
            output_dir: Where to write the generated file (default: provider_repo/tests/contract-verification)
            
        Returns:
            ProviderGenerationResult with generated code
        """
        print(f"\n{'='*60}")
        print(f"🚀 PROVIDER TEST GENERATION: {provider_name}")
        print(f"{'='*60}")
        
        # Step 1: Fetch pacts
        print("\n📥 Step 1: Fetching pacts from broker...")
        try:
            pact_context = self.pact_fetcher.fetch_provider_pacts(provider_name)
        except Exception as e:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code="",
                output_path="",
                provider_states=[],
                consumers=[],
                storage_type="unknown",
                error=f"Failed to fetch pacts: {e}"
            )
        
        if not pact_context.provider_states:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code="",
                output_path="",
                provider_states=[],
                consumers=pact_context.consumers,
                storage_type="unknown",
                error="No provider states found in pacts. Consumers may not have published pacts yet."
            )
        
        print(f"  ✅ Found {len(pact_context.provider_states)} provider states")
        print(f"  ✅ Consumers: {pact_context.consumers}")
        
        # Step 2: Analyze provider code
        print("\n🔍 Step 2: Analyzing provider source code...")
        try:
            provider_analyzer = ProviderAnalyzer(provider_repo_path)
            provider_context = provider_analyzer.analyze()
        except Exception as e:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code="",
                output_path="",
                provider_states=pact_context.provider_states,
                consumers=pact_context.consumers,
                storage_type="unknown",
                error=f"Failed to analyze provider code: {e}"
            )
        
        print(f"  ✅ Language: {provider_context.language}")
        print(f"  ✅ Framework: {provider_context.framework}")
        print(f"  ✅ Storage type: {provider_context.storage_type}")
        
        # Step 3: Build expected responses map
        print("\n📋 Step 3: Building expected responses...")
        expected_responses = self._build_expected_responses(pact_context)
        
        # Step 4: Generate with AI
        print("\n🤖 Step 4: Generating state handlers with AI...")
        try:
            generated_code = self._generate_with_ai(
                provider_name=provider_name,
                pact_context=pact_context,
                provider_context=provider_context,
                expected_responses=expected_responses
            )
        except Exception as e:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code="",
                output_path="",
                provider_states=pact_context.provider_states,
                consumers=pact_context.consumers,
                storage_type=provider_context.storage_type,
                error=f"AI generation failed: {e}"
            )
        
        # Step 5: Validate generated code
        print("\n✅ Step 5: Validating generated code...")
        quality_score, quality_issues = self._validate_generated_code(
            generated_code,
            pact_context.provider_states
        )
        
        # Step 6: Write output
        output_path = self._determine_output_path(provider_repo_path, output_dir)
        print(f"\n📝 Step 6: Writing to {output_path}")
        
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w') as f:
                f.write(generated_code)
            print(f"  ✅ File written successfully")
        except Exception as e:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code=generated_code,
                output_path=output_path,
                provider_states=pact_context.provider_states,
                consumers=pact_context.consumers,
                storage_type=provider_context.storage_type,
                error=f"Failed to write output file: {e}"
            )
        
        print(f"\n{'='*60}")
        print(f"✅ GENERATION COMPLETE")
        print(f"   Quality Score: {quality_score}/10")
        print(f"   Output: {output_path}")
        print(f"{'='*60}")
        
        return ProviderGenerationResult(
            success=True,
            provider_name=provider_name,
            generated_code=generated_code,
            output_path=output_path,
            provider_states=pact_context.provider_states,
            consumers=pact_context.consumers,
            storage_type=provider_context.storage_type,
            quality_score=quality_score,
            quality_issues=quality_issues
        )
    
    def _build_expected_responses(self, pact_context: PactContext) -> dict:
        """Build a map of state -> expected response data."""
        expected = {}
        
        for interaction in pact_context.interactions:
            state = interaction.provider_state
            if state and interaction.response_body:
                if state not in expected:
                    expected[state] = []
                expected[state].append({
                    "description": interaction.description,
                    "method": interaction.request_method,
                    "path": interaction.request_path,
                    "status": interaction.response_status,
                    "body": interaction.response_body
                })
        
        return expected
    
    @observe(name="ai_generate_provider_code")
    def _generate_with_ai(
        self,
        provider_name: str,
        pact_context: PactContext,
        provider_context: ProviderCodeContext,
        expected_responses: dict
    ) -> str:
        """Generate provider test code using Gemini AI."""
        
        # Build the prompt
        prompt = build_provider_generation_prompt(
            provider_name=provider_name,
            pact_context=pact_context.format_for_ai(),
            provider_context=provider_context.format_for_ai(),
            provider_states=pact_context.provider_states,
            storage_hints=provider_context.setup_hints,
            expected_responses=expected_responses
        )
        
        # Call Gemini
        response = self.genai_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                "system_instruction": PROVIDER_SYSTEM_PROMPT,
                "temperature": 0.2,  # Lower temperature for more consistent code
                "max_output_tokens": 8000
            }
        )
        
        generated_code = response.text
        
        # Clean up the response (remove markdown code blocks if present)
        generated_code = self._clean_generated_code(generated_code)
        
        return generated_code
    
    def _clean_generated_code(self, code: str) -> str:
        """Clean up AI-generated code."""
        code = code.strip()
        
        # Remove markdown code blocks
        if code.startswith("```javascript"):
            code = code[13:]
        elif code.startswith("```js"):
            code = code[5:]
        elif code.startswith("```"):
            code = code[3:]
        
        if code.endswith("```"):
            code = code[:-3]
        
        return code.strip()
    
    def _validate_generated_code(self, code: str, expected_states: list) -> tuple:
        """
        Validate the generated code.
        
        Returns:
            (quality_score, list of issues)
        """
        issues = []
        score = 10.0
        
        # Check for required imports
        if "require('@pact-foundation/pact')" not in code and "from '@pact-foundation/pact'" not in code:
            issues.append("Missing Pact import")
            score -= 1.0
        
        # Check for Verifier usage
        if "Verifier" not in code:
            issues.append("Missing Verifier class usage")
            score -= 2.0
        
        # Check for stateHandlers
        if "stateHandlers" not in code:
            issues.append("Missing stateHandlers configuration")
            score -= 3.0
        
        # Check that all expected states have handlers
        for state in expected_states:
            # Check if the state name appears in the code
            if state not in code and state.replace("'", "\\'") not in code:
                issues.append(f"Missing handler for state: '{state}'")
                score -= 0.5
        
        # Check for basic test structure
        if "describe(" not in code:
            issues.append("Missing describe block")
            score -= 0.5
        
        if "it(" not in code and "test(" not in code:
            issues.append("Missing test case")
            score -= 0.5
        
        # Check for server setup
        if "listen(" not in code:
            issues.append("Missing server start")
            score -= 0.5
        
        # Check for environment variables
        if "PACTFLOW_BASE_URL" not in code and "PACT_BROKER" not in code:
            issues.append("Missing Pactflow URL configuration")
            score -= 0.5
        
        # Ensure score doesn't go below 0
        score = max(0, score)
        
        return score, issues
    
    def _determine_output_path(self, repo_path: str, output_dir: Optional[str]) -> str:
        """Determine where to write the output file."""
        if output_dir:
            return os.path.join(output_dir, "provider.pact.test.js")
        
        return os.path.join(
            repo_path,
            "tests",
            "contract-verification",
            "provider.pact.test.js"
        )
    
    @observe(name="revise_provider_tests")
    def revise(
        self,
        provider_name: str,
        original_code: str,
        error_message: str,
        revision_feedback: str = None
    ) -> ProviderGenerationResult:
        """
        Revise provider tests based on errors.
        
        Args:
            provider_name: Name of the provider
            original_code: The code that failed
            error_message: Error from test execution
            revision_feedback: Optional human feedback
            
        Returns:
            ProviderGenerationResult with revised code
        """
        print(f"\n🔄 Revising provider tests for: {provider_name}")
        
        prompt = build_provider_revision_prompt(
            original_code=original_code,
            error_message=error_message,
            revision_feedback=revision_feedback
        )
        
        try:
            response = self.genai_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={
                    "system_instruction": PROVIDER_SYSTEM_PROMPT,
                    "temperature": 0.2,
                    "max_output_tokens": 8000
                }
            )
            
            revised_code = self._clean_generated_code(response.text)
            
            # Re-validate
            # Note: We don't have the original states here, so basic validation
            score, issues = self._validate_generated_code(revised_code, [])
            
            return ProviderGenerationResult(
                success=True,
                provider_name=provider_name,
                generated_code=revised_code,
                output_path="",  # Will be set by caller
                provider_states=[],
                consumers=[],
                storage_type="unknown",
                quality_score=score,
                quality_issues=issues
            )
            
        except Exception as e:
            return ProviderGenerationResult(
                success=False,
                provider_name=provider_name,
                generated_code=original_code,
                output_path="",
                provider_states=[],
                consumers=[],
                storage_type="unknown",
                error=f"Revision failed: {e}"
            )


# Convenience function
def generate_provider_tests(
    provider_name: str,
    provider_repo_path: str,
    output_dir: str = None
) -> ProviderGenerationResult:
    """
    Generate provider verification tests.
    
    Args:
        provider_name: Name of the provider (must match Pactflow)
        provider_repo_path: Path to the provider repository
        output_dir: Optional output directory
        
    Returns:
        ProviderGenerationResult
    """
    generator = ProviderGenerator()
    return generator.generate(provider_name, provider_repo_path, output_dir)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python -m src.provider.provider_generator <provider-name> <provider-repo-path>")
        print("Example: python -m src.provider.provider_generator ProviderService ./pact-provider-demo")
        sys.exit(1)
    
    provider_name = sys.argv[1]
    repo_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else None
    
    result = generate_provider_tests(provider_name, repo_path, output_dir)
    
    if result.success:
        print(f"\n✅ Generated: {result.output_path}")
        print(f"   Quality: {result.quality_score}/10")
        if result.quality_issues:
            print(f"   Issues: {result.quality_issues}")
    else:
        print(f"\n❌ Generation failed: {result.error}")
        sys.exit(1)
