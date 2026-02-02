"""
Contract Test Generator
=======================
Generates Pact contract tests using Gemini AI.

This module:
1. Takes compressed context from the aggregator
2. Builds prompts using the templates
3. Calls Gemini with structured output
4. Returns parsed test code ready for PR creation

Langfuse Integration:
- All Gemini calls are traced
- Token usage, cost, and latency are tracked
- Enables debugging and thesis metrics collection
"""

import os
import json
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Gemini SDK (new google-genai)
from google import genai
from google.genai import types

# Langfuse for observability
from langfuse import observe, get_client

# Our modules
from .prompts import (
    SYSTEM_PROMPT,
    OUTPUT_SCHEMA,
    build_user_prompt,
    build_revision_prompt
)

# Type checking import to avoid circular dependency
if TYPE_CHECKING:
    from src.context_processor.repo_analyzer import PactLibraryInfo
    from src.context_processor.compressor import CompressedContext

load_dotenv()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class GeneratedInteraction:
    """Represents a single Pact interaction."""
    description: str
    provider_state: str
    request_method: str
    request_path: str
    request_headers: str = ""  # JSON string
    request_body: str = ""     # JSON string  
    response_status: int = 200
    response_headers: str = "" # JSON string
    response_body: str = ""    # JSON string


@dataclass
class GeneratedTest:
    """Represents a generated Pact test file."""
    filename: str
    description: str
    consumer_name: str
    provider_name: str
    interactions: list[GeneratedInteraction]
    code: str
    language: str = ""
    
    # Quality validation results (populated by OutputParser)
    quality_score: Optional[float] = None
    quality_issues: Optional[list] = None


@dataclass
class AnalysisResult:
    """Analysis of the PR changes."""
    change_type: str  # new_endpoint, modification, breaking_change, no_contract_impact
    risk_level: str   # low, medium, high
    affected_endpoints: list[str]
    summary: str
    recommendation: str
    existing_contract_impact: str = ""


@dataclass
class GenerationResult:
    """Complete result from the test generator."""
    analysis: AnalysisResult
    tests: list[GeneratedTest]
    skip_reason: Optional[str] = None
    raw_response: dict = field(default_factory=dict)
    token_usage: dict = field(default_factory=dict)
    
    @property
    def has_tests(self) -> bool:
        """Check if any tests were generated."""
        return len(self.tests) > 0 and self.analysis.change_type != "no_contract_impact"


# =============================================================================
# CONFIGURATION
# =============================================================================


@dataclass
class GeneratorConfig:
    """Configuration for the test generator."""
    model: str = ""  # Required: set GEMINI_MODEL in .env
    temperature: float = 0.2
    max_output_tokens: int = 16384
    
    def __post_init__(self):
        """Load config from environment variables."""
        if not self.model:
            self.model = os.getenv("GEMINI_MODEL")
            if not self.model:
                raise ValueError("GEMINI_MODEL environment variable is required")
        
        # Allow env vars to override defaults
        if temp := os.getenv("GEMINI_TEMPERATURE"):
            self.temperature = float(temp)
        if tokens := os.getenv("GEMINI_MAX_TOKENS"):
            self.max_output_tokens = int(tokens)
    
    @classmethod
    def from_env(cls) -> "GeneratorConfig":
        """Create config from environment variables."""
        model = os.getenv("GEMINI_MODEL")
        if not model:
            raise ValueError("GEMINI_MODEL environment variable is required")
        
        return cls(
            model=model,
            temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.2")),
            max_output_tokens=int(os.getenv("GEMINI_MAX_TOKENS", "16384"))
        )


# =============================================================================
# TEST GENERATOR
# =============================================================================

class ContractTestGenerator:
    """
    Generates Pact contract tests using Gemini AI.
    
    Usage:
        generator = ContractTestGenerator()
        result = generator.generate(
            compressed_context=compressed,
            language="go",
            pact_library=repo_analysis.pact_library
        )
        
        if result.has_tests:
            for test in result.tests:
                print(f"Generated: {test.filename}")
                print(test.code)
    """
    
    def __init__(self, config: Optional[GeneratorConfig] = None):
        """
        Initialize the generator.
        
        Args:
            config: Generator configuration. If None, loads from environment.
        """
        self.config = config or GeneratorConfig.from_env()
        
        # Initialize Gemini client
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")
        
        self.client = genai.Client(api_key=api_key)
        
        print(f"[Generator] Initialized with model: {self.config.model}")
        print(f"[Generator] Temperature: {self.config.temperature}")
    
    @observe(name="contract_test_generate")
    def generate(
        self,
        compressed_context: "CompressedContext",
        language: str,
        pact_library: Optional["PactLibraryInfo"],
        file_naming_convention: str
    ) -> GenerationResult:
        """
        Generate Pact contract tests from compressed context.
        
        Args:
            compressed_context: CompressedContext object from compressor
            language: Detected programming language
            pact_library: PactLibraryInfo object from repo_analyzer (or None)
            file_naming_convention: Test file naming pattern
            
        Returns:
            GenerationResult containing analysis and generated tests
        """
        # Convert PactLibraryInfo object to dict for prompt builder
        pact_config = {}
        if pact_library:
            pact_config = {
                "package": pact_library.package,
                "import_statement": pact_library.import_statement,
                "test_framework": pact_library.test_framework,
                "file_extension": pact_library.file_extension,
                "file_naming": pact_library.file_naming,
                "example_test_structure": pact_library.example_test_structure
            }
        
        # Build the user prompt with compressed context
        user_prompt = build_user_prompt(
            language=language,
            pact_config=pact_config,
            compressed_context=compressed_context.compressed_text,
            file_naming_convention=file_naming_convention
        )
        
        # Log input to Langfuse
        try:
            get_client().update_current_span(
                input={
                    "language": language,
                    "compressed_tokens": compressed_context.stats.compressed_tokens,
                    "prompt_length": len(user_prompt)
                }
            )
        except Exception:
            pass  # Langfuse logging is optional
        
        # Call Gemini
        response = self._call_gemini(user_prompt)
        
        # Parse the response
        result = self._parse_response(response, language)
        
        # Log output to Langfuse
        try:
            get_client().update_current_span(
                output={
                    "change_type": result.analysis.change_type,
                    "tests_generated": len(result.tests),
                    "risk_level": result.analysis.risk_level
                },
                metadata={
                    "token_usage": result.token_usage,
                    "model": self.config.model
                }
            )
        except Exception:
            pass
        
        return result
    
    @observe(name="contract_test_revise")
    def revise(
        self,
        previous_result: GenerationResult,
        feedback: str,
        language: str
    ) -> GenerationResult:
        """
        Revise previously generated tests based on developer feedback.
        
        This is called when a developer comments "ai-revise" on the PR.
        
        Args:
            previous_result: The previous generation result
            feedback: Developer's feedback/comments
            language: Programming language for tests
            
        Returns:
            Revised GenerationResult
        """
        # Build revision prompt
        revision_prompt = build_revision_prompt(
            previous_tests=[t.code for t in previous_result.tests],
            feedback=feedback,
            language=language
        )
        
        # Log to Langfuse
        try:
            get_client().update_current_span(
                input={
                    "feedback_length": len(feedback),
                    "previous_tests_count": len(previous_result.tests)
                }
            )
        except Exception:
            pass
        
        # Call Gemini
        response = self._call_gemini(revision_prompt)
        
        # Parse and return
        return self._parse_response(response, language)
    
    def _call_gemini(self, user_prompt: str) -> types.GenerateContentResponse:
        """
        Make the actual Gemini API call.
        
        Args:
            user_prompt: The user prompt to send
            
        Returns:
            Gemini response object
        """
        print(f"[Generator] Calling Gemini ({self.config.model})...")
        
        # Configure generation settings
        generation_config = types.GenerateContentConfig(
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
            response_mime_type="application/json",
            response_schema=OUTPUT_SCHEMA
        )
        
        # Make the API call
        response = self.client.models.generate_content(
            model=self.config.model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(text=SYSTEM_PROMPT),
                        types.Part(text=user_prompt)
                    ]
                )
            ],
            config=generation_config
        )
        
        print(f"[Generator] Response received")
        
        return response
    
    def _parse_response(
        self,
        response: types.GenerateContentResponse,
        language: str
    ) -> GenerationResult:
        """
        Parse Gemini's response into structured GenerationResult.
        
        Args:
            response: Gemini response object
            language: Programming language for tests
            
        Returns:
            Parsed GenerationResult
        """
        # Extract token usage
        token_usage = {}
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            token_usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count
            }
            print(f"[Generator] Tokens - Prompt: {token_usage.get('prompt_tokens', 'N/A')}, "
                  f"Completion: {token_usage.get('completion_tokens', 'N/A')}")
        
        # Parse JSON response
        try:
            raw_response = json.loads(response.text)
        except json.JSONDecodeError as e:
            print(f"[Generator] ERROR: Failed to parse JSON response: {e}")
            print(f"[Generator] Raw response: {response.text[:500]}...")
            # Return empty result on parse failure
            return GenerationResult(
                analysis=AnalysisResult(
                    change_type="no_contract_impact",
                    risk_level="low",
                    affected_endpoints=[],
                    summary="Failed to parse AI response",
                    recommendation="Manual review required"
                ),
                tests=[],
                skip_reason=f"JSON parse error: {str(e)}",
                raw_response={},
                token_usage=token_usage
            )
        
        # Parse analysis
        analysis_data = raw_response.get("analysis", {})
        analysis = AnalysisResult(
            change_type=analysis_data.get("change_type", "no_contract_impact"),
            risk_level=analysis_data.get("risk_level", "low"),
            affected_endpoints=analysis_data.get("affected_endpoints", []),
            summary=analysis_data.get("summary", ""),
            recommendation=analysis_data.get("recommendation", ""),
            existing_contract_impact=analysis_data.get("existing_contract_impact", "")
        )
        
        # Parse tests
        tests = []
        for test_data in raw_response.get("tests", []):
            interactions = []
            for interaction_data in test_data.get("interactions", []):
                request_data = interaction_data.get("request", {})
                response_data = interaction_data.get("response", {})
                
                interactions.append(GeneratedInteraction(
                    description=interaction_data.get("description", ""),
                    provider_state=interaction_data.get("provider_state", ""),
                    request_method=request_data.get("method", "GET"),
                    request_path=request_data.get("path", "/"),
                    request_headers=request_data.get("headers", ""),
                    request_body=request_data.get("body", ""),
                    response_status=response_data.get("status", 200),
                    response_headers=response_data.get("headers", ""),
                    response_body=response_data.get("body", "")
                ))
            
            tests.append(GeneratedTest(
                filename=test_data.get("filename", "pact_test"),
                description=test_data.get("description", ""),
                consumer_name=test_data.get("consumer_name", ""),
                provider_name=test_data.get("provider_name", ""),
                interactions=interactions,
                code=test_data.get("code", ""),
                language=language
            ))
        
        return GenerationResult(
            analysis=analysis,
            tests=tests,
            skip_reason=raw_response.get("skip_reason"),
            raw_response=raw_response,
            token_usage=token_usage
        )
    
    def count_tokens(self, text: str) -> int:
        """
        Count tokens for a given text using Gemini's tokenizer.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Token count
        """
        response = self.client.models.count_tokens(
            model=self.config.model,
            contents=text
        )
        return response.total_tokens
