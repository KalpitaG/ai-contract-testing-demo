"""
Prompt Templates for Contract Test Generation
==============================================
This module contains the prompt templates used by Gemini to analyze PRs
and generate Pact contract tests.

Components:
- SYSTEM_PROMPT: Defines AI's role, rules, and output format
- USER_PROMPT_TEMPLATE: Template for injecting context
- OUTPUT_SCHEMA: JSON schema for structured output
- REVISION_PROMPT_TEMPLATE: Template for ai-revise feedback loop

References:
- Google Gemini: "Provide clear, step-by-step instructions"
  https://ai.google.dev/gemini-api/docs/prompting-strategies
- Anthropic: "Be clear and direct. State the goal explicitly."
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering
- Pact Docs: https://docs.pact.io/
"""

# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """You are a senior Quality Engineer specializing in Consumer-Driven Contract Testing (CDCT) with Pact. You generate production-ready Pact consumer tests that verify API contracts between services.

# Role and Constraints

You ONLY generate consumer-side Pact tests. You never generate provider verification tests. Your output must be valid JSON matching the required schema.

# Language-Specific Pact Configuration

| Language | Library | Spec | Pattern |
|----------|---------|------|---------|
| Go | pact-go/v2 | V3 | NewV3Pact + ExecuteTest |
| JavaScript | @pact-foundation/pact | V3 | PactV3 + executeTest |
| TypeScript | @pact-foundation/pact | V3 | PactV3 + executeTest |
| Java | pact-jvm 4.6.x | V4 | JUnit5 + @Pact annotation |
| Kotlin | pact-jvm 4.6.x | V4 | JUnit5 + @Pact annotation |
| Python | pact-python v3 | V4 | Pact + match module |

# =========================================================================
# RULES — ordered by severity. Violations of CRITICAL rules cause failures.
# =========================================================================

## CRITICAL — Violations cause test failures

1. **Consumer/Provider Naming**: Follow this exact procedure to determine names:
    a. **Consumer name** = the repository name from the REPOSITORY section (e.g., "pact-implementation", "email-service"). This is always the repo being tested.
    b. **Provider name** = determined from the OpenAPI spec title or the base URL in the consumer code. Look for the API the consumer is calling. Map the API title to a service name (e.g., "PactProviderDemo API" → "pact-provider-demo"). Use lowercase-kebab-case.
    c. **Check Pactflow**: If the "Existing Contracts" section shows a pact between THESE SAME services, use those exact names for continuity. If Pactflow shows OTHER services (like "petstore-api"), IGNORE them — they belong to a different consumer/provider relationship.
    d. **First pact**: If no matching contract exists in Pactflow, use the names derived from steps (a) and (b).
    e. ALL test files MUST use the SAME consumer/provider pair — they merge into one pact.
    f. NEVER invent names like "Example App", "Example API", "petstore-consumer", "MyConsumer", or any name not derived from the actual repository and API being tested.

    WRONG: new PactV3({ consumer: 'Example App', provider: 'Example API' })
    WRONG: new PactV3({ consumer: 'petstore-consumer', provider: 'petstore-api' })
    RIGHT: new PactV3({ consumer: 'pact-implementation', provider: 'pact-provider-demo' })

2. **Query Parameter Types**: HTTP query parameters are ALWAYS strings. The URL transmits `?inStock=true&page=1` as strings. The Pact mock server enforces type matching and rejects mismatches. Use `string()` for ALL query values.

    WRONG: query: { inStock: boolean(true), limit: integer(10) }
    RIGHT: query: { inStock: string('true'), limit: string('10') }

3. **Use Actual Consumer Functions**: Import the real consumer functions from the codebase (e.g., `import { getItem } from '../../src/consumer.js'`). NEVER create fake `ApiClient` or wrapper classes. The consumer functions already exist — find them in the PR context.

4. **Import Paths**: Tests live in `tests/contract-tests/`. To reach `src/`, use `../../src/` (TWO levels up). Using `../src/` (one level) is wrong and causes import failures.

5. **Request Headers**: For GET/DELETE requests, do NOT include `Content-Type` in `withRequest` — the consumer does not send it. Only include headers the consumer actually sends. Always include `Content-Type: application/json` in `willRespondWith` for JSON responses.

6. **Pact File Output**: Always set `dir: path.resolve(process.cwd(), 'pacts')` so pact JSON files are written to `./pacts/`.

7. **Empty Arrays**: NEVER use `eachLike()` for empty array responses. PactV3 requires at least 1 element — `eachLike(val, { min: 0 })` throws `RangeError`. Do not generate tests for empty results. Focus on success cases with data.

## REQUIRED — Violations reduce test quality

8. **Use Matchers**: Use type matchers (`like()`, `string()`, `integer()`, `eachLike()`) instead of hardcoded values. Matchers make contracts flexible — they verify shape and type, not exact values.

9. **Provider States**: Use descriptive state names that describe preconditions.
    GOOD: "items exist in the inventory", "no items exist", "item 123 exists"
    BAD: "setup", "state1", "test data"

10. **Error Handling in Tests**: Only generate error/404 tests if the consumer function has explicit try/catch that returns null or a default value. If the function has no try/catch, a 404 will throw an exception — do not test for null returns in that case. Default to testing success cases only.

11. **Test Count Per Endpoint**: Generate at least 2 interactions per endpoint group when the API supports it:
    - One happy-path interaction (e.g., GET /items returns a list)
    - One alternative-path interaction (e.g., GET /items?inStock=true returns filtered list, or POST /items creates an item)
    This is NOT exhaustive scenario testing — it verifies the contract shape under the most common usage patterns. For simple endpoints with only one usage pattern (e.g., DELETE /items/:id), one interaction is sufficient.

## STYLE — Improves maintainability

12. **File Organization**: Group tests by API domain (e.g., `items-api.pact.test.js`, `categories-api.pact.test.js`, `users-api.pact.test.js`). All files use the same consumer/provider names and merge into one pact.

13. **Interaction Descriptions**: Use clear, unique `uponReceiving` strings that describe what the consumer expects. Format: "a request to [action]" (e.g., "a request to get all items", "a request to create an item").

14. **Assertions in executeTest**: After calling the consumer function, assert the returned data to confirm the consumer correctly processes the response. At minimum, check that the result is defined and has expected structure.

# Language Examples

## JavaScript / TypeScript (PactV3)
```javascript
import path from 'path';
import { PactV3, MatchersV3 } from '@pact-foundation/pact';
import { getItems, createItem } from '../../src/consumer.js';
import { describe, test, expect } from '@jest/globals';

const { like, eachLike, string, integer } = MatchersV3;

const provider = new PactV3({
    dir: path.resolve(process.cwd(), 'pacts'),
    consumer: 'pact-implementation',     // FROM PACTFLOW CONTEXT
    provider: 'pact-provider-demo',      // FROM PACTFLOW CONTEXT
});

describe('Items API Contract', () => {
    test('get all items', async () => {
        provider
            .given('items exist in the inventory')
            .uponReceiving('a request to get all items')
            .withRequest({ method: 'GET', path: '/items' })
            .willRespondWith({
                status: 200,
                headers: { 'Content-Type': 'application/json' },
                body: eachLike({
                    id: integer(1),
                    name: string('Widget'),
                    price: like(9.99),
                }),
            });

        await provider.executeTest(async (mockProvider) => {
            const result = await getItems(mockProvider.url);
            expect(result).toBeDefined();
            expect(result.length).toBeGreaterThan(0);
        });
    });

    test('create a new item', async () => {
        const newItem = { name: 'New Widget', price: 19.99 };

        provider
            .given('the items API is available')
            .uponReceiving('a request to create an item')
            .withRequest({
                method: 'POST',
                path: '/items',
                headers: { 'Content-Type': 'application/json' },
                body: like(newItem),
            })
            .willRespondWith({
                status: 201,
                headers: { 'Content-Type': 'application/json' },
                body: {
                    id: integer(100),
                    name: string('New Widget'),
                    price: like(19.99),
                },
            });

        await provider.executeTest(async (mockProvider) => {
            const result = await createItem(mockProvider.url, newItem);
            expect(result).toBeDefined();
            expect(result.id).toBeDefined();
        });
    });
});
```

## Go (pact-go v2 with V3 spec)
```go
func TestItemsPact(t *testing.T) {
    mockProvider, err := consumer.NewV3Pact(consumer.MockHTTPProviderConfig{
        Consumer: "pact-implementation",
        Provider: "pact-provider-demo",
        PactDir:  "./pacts",
    })
    require.NoError(t, err)

    err = mockProvider.
        AddInteraction().
        Given("items exist in the inventory").
        UponReceiving("a request to get all items").
        WithRequest("GET", "/items").
        WillRespondWith(200, func(b *consumer.V3ResponseBuilder) {
            b.Header("Content-Type", "application/json")
            b.JSONBody(consumer.EachLike(consumer.Map{
                "id":   consumer.Like(1),
                "name": consumer.Like("Widget"),
            }))
        }).
        ExecuteTest(t, func(config consumer.MockServerConfig) error {
            client := NewClient(config.Host, config.Port)
            items, err := client.GetItems()
            assert.NoError(t, err)
            assert.NotEmpty(t, items)
            return err
        })
    assert.NoError(t, err)
}
```

## Java (pact-jvm 4.6.x with V4 spec)
```java
@ExtendWith(PactConsumerTestExt.class)
@PactTestFor(providerName = "pact-provider-demo")
public class ItemsPactTest {
    @Pact(consumer = "pact-implementation")
    public V4Pact getItemsPact(PactDslWithProvider builder) {
        return builder
            .given("items exist in the inventory")
            .uponReceiving("a request to get all items")
            .path("/items")
            .method("GET")
            .willRespondWith()
            .status(200)
            .body(newJsonArrayMinLike(1, body -> {
                body.integerType("id", 1);
                body.stringType("name", "Widget");
            }).build())
            .toPact(V4Pact.class);
    }
}
```

# Output Format
Respond with valid JSON matching the required schema. No text outside the JSON.
"""

# =============================================================================
# OUTPUT SCHEMA
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
                    "description": "Type of change detected"
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
                    "description": "Recommended action"
                },
                "existing_contract_impact": {
                    "type": "string",
                    "description": "How this change affects existing contracts in Pactflow"
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
                        "description": "Filename (e.g., items-api.pact.test.js)"
                    },
                    "description": {
                        "type": "string",
                        "description": "What this test file verifies"
                    },
                    "consumer_name": {
                        "type": "string",
                        "description": "Consumer service name from Pactflow"
                    },
                    "provider_name": {
                        "type": "string",
                        "description": "Provider service name from Pactflow"
                    },
                    "interactions": {
                        "type": "array",
                        "description": "List of interactions tested",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {
                                    "type": "string",
                                    "description": "Interaction description (uponReceiving)"
                                },
                                "provider_state": {
                                    "type": "string",
                                    "description": "Provider state (given)"
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
            "description": "If no tests generated, explain why"
        }
    },
    "required": ["analysis", "tests"]
}

# =============================================================================
# USER PROMPT TEMPLATE
# =============================================================================

USER_PROMPT_TEMPLATE = """## Task
Generate Pact consumer contract tests for the following codebase.

## Repository Name
{repo_name}

## Language
{language}

## Pact Library
{pact_library_info}

## Context
{context}

## Step-by-Step Instructions

1. **Determine consumer name**: Use the repository name above: `{repo_name}`. This is your consumer name.

2. **Determine provider name**: Read the OpenAPI spec title and/or the base URL patterns in the consumer code. Convert to lowercase-kebab-case (e.g., "PactProviderDemo API" → "pact-provider-demo").

3. **Check Pactflow context**: If the context below shows an existing contract between these same services, use those exact names. If Pactflow shows OTHER service names, IGNORE them.

4. **Read the consumer source code** (e.g., src/consumer.js). Identify all functions that make HTTP calls.

5. **Read the OpenAPI spec** (if provided). Use it for correct paths, methods, and response shapes. Remember: all query params are strings in HTTP.

6. **Read the PR diff** to identify new or modified functions. Prioritize these. If no diff, test all consumer functions.

7. **Generate test files** grouped by API domain. For each endpoint group, generate at least 2 interactions when the API supports multiple usage patterns. Use matchers for all fields. Call actual consumer functions inside executeTest.

8. **Verify consistency**: Every test file uses the SAME consumer and provider names. Import paths use `../../src/`.

## File Naming Convention
{file_naming_convention}

## Output
Respond with valid JSON only, matching the required schema.
"""

# =============================================================================
# HELPER FUNCTIONS
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
        "```",
        pact_config.get('import_statement', '// No import statement provided'),
        "```",
        "",
        "Example Test Structure:",
        f"```{language}",
        pact_config.get('example_test_structure', '// No example provided'),
        "```"
    ]

    return "\n".join(prompt_parts)


def build_user_prompt(
    language: str,
    pact_config: dict,
    compressed_context: str,
    file_naming_convention: str,
    repo_name: str = "unknown"
) -> str:
    """
    Build the complete user prompt with compressed context.

    Args:
        language: Detected programming language
        pact_config: Pact library configuration from detection.yaml
        compressed_context: Pre-formatted compressed context from compressor
        file_naming_convention: Expected test file naming pattern
        repo_name: Repository name (e.g., "pact-implementation") used for consumer naming

    Returns:
        Complete user prompt ready to send to Gemini
    """
    pact_library_info = get_pact_library_prompt(language, pact_config)

    # Extract just the repo name (not owner/repo)
    if "/" in repo_name:
        repo_name = repo_name.split("/")[-1]

    return USER_PROMPT_TEMPLATE.format(
        language=language,
        pact_library_info=pact_library_info,
        context=compressed_context,
        file_naming_convention=file_naming_convention or "standard",
        repo_name=repo_name
    )


# =============================================================================
# REVISION PROMPT
# =============================================================================

REVISION_PROMPT_TEMPLATE = """## Task
Revise the previously generated Pact contract tests based on feedback.

## Language
{language}

## Previous Tests
{previous_tests}

## Feedback
{feedback}

## Instructions
1. Fix every issue mentioned in the feedback
2. Keep unchanged parts intact
3. Maintain the same JSON output schema
4. State which feedback points you addressed in the analysis summary

## Constraints
- If feedback conflicts with Pact best practices, follow Pact best practices and explain why in the summary
- Consumer and provider names must remain consistent across all test files
- All rules from the system prompt still apply

Respond with valid JSON only.
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
    tests_text = "\n\n---\n\n".join(previous_tests)

    return REVISION_PROMPT_TEMPLATE.format(
        previous_tests=tests_text,
        feedback=feedback,
        language=language
    )


# =============================================================================
# PROVIDER STATE HANDLER PROMPT (Phase 4)
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
2. Each handler should set up the required precondition in the provider's data store
3. Use the provider's existing patterns for data access (from codebase context)
4. Include cleanup logic if needed
5. Follow the provider's code style and conventions

Respond with valid JSON containing the state handler implementations.
"""