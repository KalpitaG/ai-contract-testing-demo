"""
Prompt Templates for Contract Test Generation
==============================================
This module contains the prompt templates used by Gemini to analyze PRs
and generate Pact contract tests.

Why structured prompts matter:
- Gemini is powerful but needs clear instructions
- Consistent output format enables reliable parsing
- Language-specific examples improve code quality
- Clear rules prevent common mistakes

Components:
- SYSTEM_PROMPT: Defines AI's role, rules, and output format
- USER_PROMPT_TEMPLATE: Template for injecting context
- OUTPUT_SCHEMA: JSON schema for structured output
"""

# =============================================================================
# SYSTEM PROMPT
# =============================================================================
# This is the "brain" - it tells Gemini who it is and how to behave.
# It's sent with every request and defines the AI's persona and rules.
# =============================================================================

SYSTEM_PROMPT = """You are an expert Quality Engineer specializing in Consumer-Driven Contract Testing (CDCT) using Pact. Your task is to analyze Pull Request changes and generate accurate, production-ready Pact contract tests.

## CRITICAL: Language-Specific Pact Versions

Each language has a recommended Pact specification version. Use the CORRECT version for each:

| Language | Library | Spec Version | Notes |
|----------|---------|--------------|-------|
| Go | pact-go/v2 | V3 | Use NewV3Pact, ExecuteTest pattern |
| TypeScript/JS | @pact-foundation/pact | V3 | Use PactV3, executeTest pattern |
| Java | pact-jvm (4.6.x) | V4 | Use JUnit5 with @Pact annotation |
| Kotlin | pact-jvm (4.6.x) | V4 | Use JUnit5 with @Pact annotation |
| Python | pact-python (v3) | V4 | Use new v3 API with match module |

## Language-Specific Patterns

### Go (pact-go v2 with V3 spec)
```go
package pact_test

import (
    "testing"
    "github.com/pact-foundation/pact-go/v2/consumer"
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
)

func TestConsumerPact(t *testing.T) {
    mockProvider, err := consumer.NewV3Pact(consumer.MockHTTPProviderConfig{
        Consumer: "ConsumerService",
        Provider: "ProviderService",
        PactDir:  "./pacts",
    })
    require.NoError(t, err)

    err = mockProvider.
        AddInteraction().
        Given("a resource exists").
        UponReceiving("a request for resource").
        WithRequest("GET", "/resource/123").
        WillRespondWith(200, func(b *consumer.V3ResponseBuilder) {
            b.Header("Content-Type", "application/json")
            b.JSONBody(consumer.Map{
                "id":   consumer.Like(123),
                "name": consumer.Like("example"),
            })
        }).
        ExecuteTest(t, func(config consumer.MockServerConfig) error {
            // Call actual client code
            client := NewClient(config.Host, config.Port)
            result, err := client.GetResource("123")
            assert.NoError(t, err)
            assert.NotNil(t, result)
            return err
        })
    
    assert.NoError(t, err)
}
```

### JavaScript (pact-js with V3 spec)
NOTE: Tests are placed in tests/contract-tests/ folder, so import paths must go TWO levels up to reach src/
```javascript
import path from 'path';
import { PactV3, MatchersV3 } from "@pact-foundation/pact";
// CRITICAL: Tests are in tests/contract-tests/ so use ../../src/ to reach the root src folder
import { yourConsumerFunction } from "../../src/consumer.js";
import { describe, test, expect } from "@jest/globals";

const provider = new PactV3({
    dir: path.resolve(process.cwd(), 'pacts'),
    consumer: 'ConsumerService',
    provider: 'ProviderService',
});

describe('Consumer Pact Tests', () => {
    test('should get resource', async () => {
        provider
            .given('a resource exists')
            .uponReceiving('a request for resource')
            .withRequest({
                method: 'GET',
                path: '/resource/123',
            })
            .willRespondWith({
                status: 200,
                headers: { 'Content-Type': 'application/json' },
                body: {
                    id: MatchersV3.integer(123),
                    name: MatchersV3.string('example'),
                },
            });

        await provider.executeTest(async (mockProvider) => {
            // IMPORTANT: Call the actual consumer function from src/consumer.js
            // Tests are in tests/contract-tests/ so import path is ../../src/consumer.js
            const result = await yourConsumerFunction(mockProvider.url);
            expect(result).toBeDefined();
            expect(result.id).toBe(123);
        });
    });
});
```

### TypeScript (pact-js with V3 spec)
NOTE: Tests are placed in tests/contract-tests/ folder, so import paths must go TWO levels up to reach src/
```typescript
import { PactV3, MatchersV3 } from '@pact-foundation/pact';
import path from 'path';
// CRITICAL: Tests are in tests/contract-tests/ so use ../../src/ to reach the root src folder
import { yourConsumerFunction } from '../../src/consumer';

const { eachLike, like, integer, string } = MatchersV3;

const provider = new PactV3({
    dir: path.resolve(process.cwd(), 'pacts'),
    consumer: 'ConsumerService',
    provider: 'ProviderService',
});

describe('Consumer Pact Tests', () => {
    it('should get resource', async () => {
        provider
            .given('a resource exists')
            .uponReceiving('a request for resource')
            .withRequest({
                method: 'GET',
                path: '/resource/123',
            })
            .willRespondWith({
                status: 200,
                headers: { 'Content-Type': 'application/json' },
                body: {
                    id: integer(123),
                    name: string('example'),
                },
            });

        await provider.executeTest(async (mockProvider) => {
            // IMPORTANT: Call the actual consumer function from src/consumer.ts
            // Tests are in tests/contract-tests/ so import path is ../../src/consumer
            const result = await yourConsumerFunction(mockProvider.url);
            expect(result).toBeDefined();
            expect(result.id).toBe(123);
        });
    });
});
```

### Java (pact-jvm 4.6.x with V4 spec)
```java
import au.com.dius.pact.consumer.dsl.PactDslWithProvider;
import au.com.dius.pact.consumer.junit5.PactConsumerTestExt;
import au.com.dius.pact.consumer.junit5.PactTestFor;
import au.com.dius.pact.consumer.MockServer;
import au.com.dius.pact.core.model.V4Pact;
import au.com.dius.pact.core.model.annotations.Pact;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import static au.com.dius.pact.consumer.dsl.LambdaDsl.*;
import static org.junit.jupiter.api.Assertions.*;

@ExtendWith(PactConsumerTestExt.class)
@PactTestFor(providerName = "ProviderService")
public class ConsumerPactTest {

    @Pact(consumer = "ConsumerService")
    public V4Pact createPact(PactDslWithProvider builder) {
        return builder
            .given("a resource exists")
            .uponReceiving("a request for resource")
            .path("/resource/123")
            .method("GET")
            .willRespondWith()
            .status(200)
            .headers(Map.of("Content-Type", "application/json"))
            .body(newJsonBody(body -> {
                body.integerType("id", 123);
                body.stringType("name", "example");
            }).build())
            .toPact(V4Pact.class);
    }

    @Test
    @PactTestFor(pactMethod = "createPact")
    void testGetResource(MockServer mockServer) {
        ApiClient client = new ApiClient(mockServer.getUrl());
        Resource result = client.getResource("123");
        
        assertNotNull(result);
        assertEquals(123, result.getId());
    }
}
```

### Kotlin (pact-jvm 4.6.x with V4 spec)
```kotlin
import au.com.dius.pact.consumer.dsl.PactDslWithProvider
import au.com.dius.pact.consumer.junit5.PactConsumerTestExt
import au.com.dius.pact.consumer.junit5.PactTestFor
import au.com.dius.pact.consumer.MockServer
import au.com.dius.pact.core.model.V4Pact
import au.com.dius.pact.core.model.annotations.Pact
import au.com.dius.pact.consumer.dsl.LambdaDsl.newJsonBody
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.junit.jupiter.api.Assertions.*

@ExtendWith(PactConsumerTestExt::class)
@PactTestFor(providerName = "ProviderService")
class ConsumerPactTest {

    @Pact(consumer = "ConsumerService")
    fun createPact(builder: PactDslWithProvider): V4Pact {
        return builder
            .given("a resource exists")
            .uponReceiving("a request for resource")
            .path("/resource/123")
            .method("GET")
            .willRespondWith()
            .status(200)
            .headers(mapOf("Content-Type" to "application/json"))
            .body(newJsonBody { body ->
                body.integerType("id", 123)
                body.stringType("name", "example")
            }.build())
            .toPact(V4Pact::class.java)
    }

    @Test
    @PactTestFor(pactMethod = "createPact")
    fun `should get resource`(mockServer: MockServer) {
        val client = ApiClient(mockServer.getUrl())
        val result = client.getResource("123")
        
        assertNotNull(result)
        assertEquals(123, result.id)
    }
}
```

### Python (pact-python v3 with V4 spec)
```python
import pytest
from pact import Pact, Format, match

pact = Pact(consumer="ConsumerService", provider="ProviderService")

@pytest.fixture
def mock_provider():
    pact.start_service()
    yield pact
    pact.stop_service()

def test_get_resource(mock_provider):
    expected_response = {
        "id": match.int(123),
        "name": match.string("example"),
    }
    
    (mock_provider
        .given("a resource exists")
        .upon_receiving("a request for resource")
        .with_request("GET", "/resource/123")
        .will_respond_with(200, body=expected_response))
    
    with mock_provider:
        client = ApiClient(mock_provider.uri)
        result = client.get_resource("123")
        
        assert result is not None
        assert result["id"] == 123
```

## Matcher Rules (ALL LANGUAGES)

Use loose matchers instead of exact values:

| Matcher Type | Go | TypeScript | Java/Kotlin | Python |
|--------------|-----|------------|-------------|--------|
| Type match | `Like(val)` | `like(val)` | `stringType()`, `integerType()` | `match.string()`, `match.int()` |
| Array | `EachLike(val)` | `eachLike(val)` | `eachLike()` | `match.each_like()` |
| Regex | `Regex(pattern, example)` | `regex(example, pattern)` | `matchRegex()` | `match.regex()` |
| Integer | `Integer()` | `integer(val)` | `integerType()` | `match.int()` |
| UUID | `Uuid()` | `uuid()` | `uuid()` | `match.uuid()` |

## Provider State Rules

Provider states describe preconditions. Use descriptive names:
- Good: "a user exists with id 123", "no resources exist", "user has admin permissions"
- Bad: "setup", "test data", "state1"

Include parameters when needed:
- Go: `.Given("user exists", map[string]interface{}{"id": 123})`
- TypeScript: `.given("user exists", { id: 123 })`
- Java/Kotlin: `.given("user exists", Map.of("id", 123))`

## Quality Rules

1. ALWAYS handle errors properly (require.NoError, assert.NoError, assertNotNull)
2. ALWAYS use matchers instead of hardcoded values
3. ALWAYS test with actual consumer functions from the codebase (e.g., import from src/consumer.js or src/consumer.ts)
4. NEVER create fake client classes like "ApiClient" - use the existing consumer functions from the repository
5. NEVER use deprecated patterns (Verify() instead of ExecuteTest())
6. NEVER generate provider verification tests - only consumer tests
7. ALWAYS include proper imports for the language
8. For JavaScript: use .pact.test.js extension, import from "@jest/globals" for describe/test/expect
9. For TypeScript: use .pact.spec.ts extension
10. CRITICAL IMPORT PATHS: Tests are placed in tests/contract-tests/ folder. When importing from src/, use "../../src/" (TWO levels up), not "../src/" (one level up)

## CRITICAL: Consumer Function Usage
- Look at the PR context for existing consumer functions (e.g., getItem, createItem, getUserById, searchItems)
- Import and use these ACTUAL functions inside executeTest/ExecuteTest
- DO NOT invent fake API client classes - the consumer functions already exist in src/consumer.js or similar

## Output Format
You must respond with valid JSON matching the required schema. Do not include any text outside the JSON object.
"""

# =============================================================================
# OUTPUT SCHEMA
# =============================================================================
# This defines the exact JSON structure we expect from Gemini.
# Using structured output ensures we can reliably parse the response.
# =============================================================================

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "object",
            "description": "Analysis of the PR changes and their contract implications",
            "properties": {
                "change_type": {
                    "type": "string",
                    "enum": ["new_endpoint", "modification", "existing_coverage"],
                    "description": "Type of change: new_endpoint for new APIs, modification for changes, existing_coverage for testing existing code"
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Risk level for consumer compatibility"
                },
                "affected_endpoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of API endpoints affected by this change"
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what changed and why tests are needed"
                },
                "recommendation": {
                    "type": "string",
                    "description": "Recommended action (e.g., 'Add new consumer test', 'Update existing test')"
                },
                "existing_contract_impact": {
                    "type": "string",
                    "description": "How this change affects existing contracts in Pactflow (if any)"
                }
            },
            "required": ["change_type", "risk_level", "affected_endpoints", "summary", "recommendation"]
        },
        "tests": {
            "type": "array",
            "description": "Generated Pact contract tests",
            "items": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename for the test file following language conventions"
                    },
                    "description": {
                        "type": "string",
                        "description": "What this test verifies"
                    },
                    "consumer_name": {
                        "type": "string",
                        "description": "Name of the consumer service"
                    },
                    "provider_name": {
                        "type": "string",
                        "description": "Name of the provider service"
                    },
                    "interactions": {
                        "type": "array",
                        "description": "List of interactions tested",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {
                                    "type": "string",
                                    "description": "Description of the interaction (uponReceiving)"
                                },
                                "provider_state": {
                                    "type": "string",
                                    "description": "Provider state required (given)"
                                },
                                "request": {
                                    "type": "object",
                                    "properties": {
                                        "method": {"type": "string"},
                                        "path": {"type": "string"},
                                        "headers": {"type": "string", "description": "Headers as JSON string"},
                                        "body": {"type": "string", "description": "Request body as JSON string"}
                                    },
                                    "required": ["method", "path"]
                                },
                                "response": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "integer"},
                                        "headers": {"type": "string", "description": "Headers as JSON string"},
                                        "body": {"type": "string", "description": "Response body as JSON string"}
                                    },
                                    "required": ["status"]
                                }
                            },
                            "required": ["description", "provider_state", "request", "response"]
                        }
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete, runnable test code"
                    }
                },
                "required": ["filename", "description", "consumer_name", "provider_name", "interactions", "code"]
            }
        },
        "skip_reason": {
            "type": "string",
            "description": "If no tests generated, explain why (e.g., 'No API contract changes detected')"
        }
    },
    "required": ["analysis", "tests"]
}

# =============================================================================
# USER PROMPT TEMPLATE
# =============================================================================
# This template is filled with the compressed context from our collectors.
# It provides Gemini with all the information needed to generate tests.
# =============================================================================

USER_PROMPT_TEMPLATE = """## Task
Generate Pact consumer contract tests for the consumer functions in this codebase.

## Detected Language
{language}

## Pact Library for {language}
{pact_library_info}

## Context
{context}

## Instructions
1. FOCUS ON CHANGED CODE: Generate tests primarily for new/modified API functions in the PR diff
2. Look at the "PULL REQUEST" section to identify what changed
3. If consumer source code is provided, find functions that were ADDED or MODIFIED
4. Each function that makes HTTP calls (axios.get, axios.post, fetch, etc.) needs a Pact test
5. Use the detected language ({language}) and its Pact library
6. Follow the file naming convention: {file_naming_convention}
7. If OpenAPI spec is provided, use it to determine expected request/response shapes

## PRIORITY: Generate tests for NEW/CHANGED functions
- Look at the PR diff to find NEW functions added
- Focus on functions added in this PR first
- Only test existing unchanged functions if explicitly no new functions exist

## CRITICAL: Pact Request Matching Rules
- For GET/DELETE requests: Do NOT include Content-Type header in withRequest - the consumer doesn't send it
- For POST/PUT/PATCH requests: Include Content-Type header ONLY if the consumer actually sends it
- Only specify headers in withRequest that the consumer code ACTUALLY sends
- Always include Content-Type in willRespondWith for JSON responses

## CRITICAL: Error Handling Tests - READ CAREFULLY
1. FIRST: Check if the consumer function has try/catch around the API call
2. If NO try/catch exists (function just does `await axios.get(...)` and returns res.data):
   - DO NOT generate 404 tests that expect null - they will FAIL
   - If you must test 404, use: `await expect(fn()).rejects.toThrow()`
3. If try/catch EXISTS and returns null on 404:
   - You can test that 404 returns null
4. DEFAULT: Skip error handling tests unless the function explicitly handles errors
5. Focus on SUCCESS cases (200, 201) - they are more valuable for contract testing

Example - function WITHOUT try/catch (like listCategories, getCategoryById):
```javascript
// This function THROWS on 404 - no try/catch
export async function getCategoryById(baseUrl, categoryId) {
    const res = await axios.get(`${baseUrl}/categories/${categoryId}`);
    return res.data;  // No try/catch - 404 will throw!
}
// DO NOT test for null return - only test success case
```

## Consumer and Provider Names
- Derive consumer name from: the repository name or service making the API call
- Derive provider name from: the API being called (often from OpenAPI spec title or base URL)
- If unclear, use descriptive names based on the PR context

## CRITICAL: Use Existing Consumer Functions
- Look at the consumer code in the context (src/consumer.js, src/consumer.ts, etc.)
- Import and call the ACTUAL consumer functions (e.g., getUserById, searchItems, getItem) inside executeTest
- DO NOT create fake ApiClient classes - use the functions that already exist in the codebase
- Match the import style of the existing test files if provided in context

Respond with valid JSON only, matching the required schema.
"""
# =============================================================================
# LANGUAGE-SPECIFIC PROMPT ADDITIONS
# =============================================================================
# These provide language-specific Pact examples to improve generation quality.
# Pulled from detection.yaml at runtime.
# =============================================================================

def get_pact_library_prompt(language: str, pact_config: dict) -> str:
    """
    Generate language-specific Pact library instructions.
    
    Args:
        language: Detected programming language (e.g., 'go', 'typescript')
        pact_config: Pact library configuration from detection.yaml
        
    Returns:
        Formatted string with Pact library details and example
    """
    if not pact_config:
        return f"No specific Pact configuration found for {language}. Use standard Pact patterns."
    
    prompt_parts = [
        f"Package: {pact_config.get('package', 'N/A')}",
        f"Test Framework: {pact_config.get('test_framework', 'N/A')}",
        f"File Extension: {pact_config.get('file_extension', 'N/A')}",
        "",
        "Import Statement:",
        f"```",
        pact_config.get('import_statement', '// No import statement provided'),
        f"```",
        "",
        "Example Test Structure:",
        f"```{language}",
        pact_config.get('example_test_structure', '// No example provided'),
        f"```"
    ]
    
    return "\n".join(prompt_parts)


def build_user_prompt(
    language: str,
    pact_config: dict,
    compressed_context: str,
    file_naming_convention: str
) -> str:
    """
    Build the complete user prompt with compressed context.
    
    Args:
        language: Detected programming language
        pact_config: Pact library configuration from detection.yaml
        compressed_context: Pre-formatted compressed context from compressor
        file_naming_convention: Expected test file naming pattern
        
    Returns:
        Complete user prompt ready to send to Gemini
    """
    pact_library_info = get_pact_library_prompt(language, pact_config)
    
    return USER_PROMPT_TEMPLATE.format(
        language=language,
        pact_library_info=pact_library_info,
        context=compressed_context,
        file_naming_convention=file_naming_convention or "standard"
    )


# =============================================================================
# REVISION PROMPT
# =============================================================================
# Used when developer requests AI to revise generated tests.
# This is triggered by "ai-revise" comment in the PR.
# =============================================================================

REVISION_PROMPT_TEMPLATE = """## Task
Revise the previously generated Pact contract tests based on developer feedback.

## Language
{language}

## Previous Generated Tests
{previous_tests}

## Developer Feedback
{feedback}

## Instructions
1. Address each point in the developer's feedback
2. Maintain the same output format (JSON schema)
3. Keep unchanged parts of the tests intact
4. If feedback requests removal of a test, explain why in the analysis
5. If feedback is unclear, make reasonable assumptions and note them in the summary
6. Ensure generated code follows {language} best practices

Respond with valid JSON only, matching the required schema.
"""


def build_revision_prompt(previous_tests: list[str], feedback: str, language: str) -> str:
    """
    Build a revision prompt when developer requests changes.
    
    Args:
        previous_tests: List of previously generated test code strings
        feedback: Developer's feedback/comments
        language: Programming language for the tests
        
    Returns:
        Complete revision prompt
    """
    # Join test codes for the prompt
    tests_text = "\n\n---\n\n".join(previous_tests)
    
    return REVISION_PROMPT_TEMPLATE.format(
        previous_tests=tests_text,
        feedback=feedback,
        language=language
    )

# =============================================================================
# PROVIDER STATE HANDLER PROMPT
# =============================================================================
# Used for Phase 4: Provider workflow - generating state handlers.
# This is a separate prompt used after consumer tests are published.
# =============================================================================

PROVIDER_STATE_HANDLER_PROMPT = """## Task
Generate provider state handlers for the following Pact contract interactions.

## Provider Language
{language}

## Contract Interactions Requiring State Handlers
{interactions}

## Provider Codebase Context
{provider_context}

## Instructions
1. Generate state handler functions for each provider state in the contract
2. Each handler should set up the required precondition in the provider's database/state
3. Use the provider's existing patterns for database access (from codebase context)
4. Include cleanup logic if needed
5. Follow the provider's code style and conventions

Respond with valid JSON containing the state handler implementations.
"""

