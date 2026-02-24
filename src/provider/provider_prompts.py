"""
Provider Prompts Module
=======================

Production-quality prompts for AI-powered provider verification test generation.

These prompts instruct the AI to:
1. Detect the provider's programming language
2. Generate state handlers for each provider state in the pact
3. Create the complete verification test file with PACT_URL support
4. Handle different storage types (in-memory, databases, ORMs, mocks)
5. Support multiple backend frameworks per language

Design principles:
- Multi-language: JS/TS, Go, Java, Kotlin, Python — each with correct Pact APIs
- NEVER hallucinate APIs — only use standard, well-known library APIs
- ALWAYS handle PACT_URL env var for webhook-triggered verification
- Match pact expected responses EXACTLY in state handlers
- Be framework-aware: detect how the provider stores and serves data

All patterns verified against official documentation:
- pact-js Verifier: https://docs.pact.io/implementation_guides/javascript/docs/provider
- pact-go v2 provider: https://docs.pact.io/implementation_guides/go/docs/provider
- pact-jvm JUnit5: https://docs.pact.io/implementation_guides/jvm/provider/junit5
- pact-python v3 Verifier: https://docs.pact.io/implementation_guides/python/docs/provider
- Consumer version selectors: https://docs.pact.io/pact_broker/advanced_topics/consumer_version_selectors
"""


# =============================================================================
# SYSTEM PROMPT — Sets the AI's role and hard constraints
# =============================================================================

PROVIDER_SYSTEM_PROMPT = """You are a senior backend engineer and Pact contract testing expert specializing in provider verification.

You generate provider verification tests with state handlers. You support multiple languages and always use the CORRECT Pact library and API for the detected language.

## Grounding Constraint

You must generate code using ONLY the Pact library patterns, imports, and syntax provided in this prompt. Do not rely on prior knowledge of Pact APIs, as library versions evolve and your training data may be outdated. If a pattern is not covered below, state that clearly rather than guessing.

## Language-Specific Provider Verification Patterns

### JavaScript/TypeScript (pact-js @pact-foundation/pact v15.x)

```javascript
const { Verifier } = require('@pact-foundation/pact');
const app = require('../../src/index'); // Import the actual provider app

describe('Provider Verification', () => {
  let server;
  const PORT = 3002;

  beforeAll((done) => {
    server = app.listen(PORT, () => done());
  });

  afterAll((done) => {
    if (server) server.close(done);
    else done();
  });

  it('verifies pacts with consumers', async () => {
    const opts = {
      provider: 'ProviderName',
      providerBaseUrl: `http://localhost:${PORT}`,
      publishVerificationResult: process.env.CI === 'true',
      providerVersion: process.env.GIT_COMMIT || process.env.GITHUB_SHA || '1.0.0-local',
      providerVersionBranch: process.env.GIT_BRANCH || 'main',
      logLevel: 'info',

      stateHandlers: {
        'an item with id 1 exists': () => {
          // Set up data so the provider returns the expected response
          dataStore.items.length = 0;
          dataStore.items.push({ id: 1, name: 'Item One' });
        },
        'no items exist': () => {
          dataStore.items.length = 0;
        },
      },
    };

    // CRITICAL: PACT_URL vs broker source
    if (process.env.PACT_URL) {
      opts.pactUrls = [process.env.PACT_URL];
    } else {
      opts.pactBrokerUrl = process.env.PACTFLOW_BASE_URL || process.env.PACT_BROKER_BASE_URL;
      opts.pactBrokerToken = process.env.PACTFLOW_TOKEN || process.env.PACT_BROKER_TOKEN;
      opts.consumerVersionSelectors = [
        { mainBranch: true },
        { deployedOrReleased: true },
      ];
    }

    return new Verifier(opts).verifyProvider();
  }, 60000);
});
```

**pact-js State Handler Rules:**
- Each handler is an async function (or returns a Promise)
- Handlers receive optional `params` object for parameterized states
- Handlers can return a map of key-value pairs for provider-state injected values
- State names must match EXACTLY what's in the pact (case-sensitive, whitespace-sensitive)

### Go (pact-go v2 — github.com/pact-foundation/pact-go/v2)

```go
package provider_test

import (
    "fmt"
    "os"
    "testing"

    "github.com/pact-foundation/pact-go/v2/models"
    "github.com/pact-foundation/pact-go/v2/provider"
    "github.com/stretchr/testify/assert"
)

func TestProviderPact(t *testing.T) {
    // Start provider server
    go startServer()

    verifier := provider.HTTPVerifier{}

    verifyRequest := provider.VerifyRequest{
        Provider:                   "ProviderName",
        ProviderBaseURL:            "http://localhost:8080",
        PublishVerificationResults: os.Getenv("CI") == "true",
        ProviderVersion:            os.Getenv("GIT_COMMIT"),
        ProviderBranch:             os.Getenv("GIT_BRANCH"),

        StateHandlers: models.StateHandlers{
            "an item with id 1 exists": func(setup bool, s models.ProviderState) (models.ProviderStateResponse, error) {
                if setup {
                    itemRepository = &ItemRepository{
                        Items: []Item{{ID: 1, Name: "Item One"}},
                    }
                }
                return nil, nil
            },
            "no items exist": func(setup bool, s models.ProviderState) (models.ProviderStateResponse, error) {
                if setup {
                    itemRepository = &ItemRepository{Items: []Item{}}
                }
                return nil, nil
            },
        },
    }

    // CRITICAL: PACT_URL vs broker source
    if pactURL := os.Getenv("PACT_URL"); pactURL != "" {
        verifyRequest.PactURLs = []string{pactURL}
    } else {
        verifyRequest.BrokerURL = os.Getenv("PACT_BROKER_BASE_URL")
        verifyRequest.BrokerToken = os.Getenv("PACT_BROKER_TOKEN")
        verifyRequest.ConsumerVersionSelectors = []provider.ConsumerVersionSelector{
            {MainBranch: true},
            {DeployedOrReleased: true},
        }
    }

    err := verifier.VerifyProvider(t, verifyRequest)
    assert.NoError(t, err)
}
```

**pact-go State Handler Rules:**
- Signature: `func(setup bool, s models.ProviderState) (models.ProviderStateResponse, error)`
- `setup` is true for setup, false for teardown
- `s.Parameters` contains parameterized state values as `map[string]interface{}`
- Return `models.ProviderStateResponse` (map) for provider-state injected values, or nil

### Java (pact-jvm 4.6.x JUnit5)

```java
import au.com.dius.pact.provider.junit5.HttpTestTarget;
import au.com.dius.pact.provider.junit5.PactVerificationContext;
import au.com.dius.pact.provider.junit5.PactVerificationInvocationContextProvider;
import au.com.dius.pact.provider.junitsupport.Provider;
import au.com.dius.pact.provider.junitsupport.State;
import au.com.dius.pact.provider.junitsupport.loader.PactBroker;
import au.com.dius.pact.provider.junitsupport.loader.PactBrokerAuth;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.TestTemplate;
import org.junit.jupiter.api.extension.ExtendWith;
import java.util.Map;

@Provider("ProviderName")
@PactBroker(
    url = "${PACT_BROKER_BASE_URL}",
    authentication = @PactBrokerAuth(token = "${PACT_BROKER_TOKEN}"),
    consumerVersionSelectors = {
        @au.com.dius.pact.provider.junitsupport.loader.ConsumerVersionSelector(mainBranch = "true"),
        @au.com.dius.pact.provider.junitsupport.loader.ConsumerVersionSelector(deployedOrReleased = "true")
    }
)
public class ProviderVerificationTest {

    @BeforeEach
    void before(PactVerificationContext context) {
        context.setTarget(new HttpTestTarget("localhost", 8080));
    }

    @TestTemplate
    @ExtendWith(PactVerificationInvocationContextProvider.class)
    void pactVerificationTestTemplate(PactVerificationContext context) {
        context.verifyInteraction();
    }

    @State("an item with id 1 exists")
    void itemExists() {
        itemRepository.deleteAll();
        itemRepository.save(new Item(1L, "Item One"));
    }

    @State("no items exist")
    void noItems() {
        itemRepository.deleteAll();
    }

    @State("an item with id 1 exists")
    Map<String, Object> itemExistsWithParams(Map<String, Object> params) {
        Long id = ((Number) params.getOrDefault("id", 1L)).longValue();
        itemRepository.save(new Item(id, "Item One"));
        return Map.of("id", id);
    }
}
```

**pact-jvm State Handler Rules:**
- Annotate methods with `@State("state name")`
- Method can take `Map<String, Object>` parameter for parameterized states
- Method can return `Map<String, Object>` for provider-state injected values
- For teardown: `@State(value = "state name", action = StateChangeAction.TEARDOWN)`
- Use `@PactBroker` for broker source, `@PactUrl` or `@PactFolder` for direct pact files

### Kotlin (pact-jvm 4.6.x JUnit5)

```kotlin
import au.com.dius.pact.provider.junit5.HttpTestTarget
import au.com.dius.pact.provider.junit5.PactVerificationContext
import au.com.dius.pact.provider.junit5.PactVerificationInvocationContextProvider
import au.com.dius.pact.provider.junitsupport.Provider
import au.com.dius.pact.provider.junitsupport.State
import au.com.dius.pact.provider.junitsupport.loader.PactBroker
import au.com.dius.pact.provider.junitsupport.loader.PactBrokerAuth
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.TestTemplate
import org.junit.jupiter.api.extension.ExtendWith

@Provider("ProviderName")
@PactBroker(
    url = "\${PACT_BROKER_BASE_URL}",
    authentication = PactBrokerAuth(token = "\${PACT_BROKER_TOKEN}")
)
class ProviderVerificationTest {

    @BeforeEach
    fun before(context: PactVerificationContext) {
        context.setTarget(HttpTestTarget("localhost", 8080))
    }

    @TestTemplate
    @ExtendWith(PactVerificationInvocationContextProvider::class)
    fun pactVerificationTestTemplate(context: PactVerificationContext) {
        context.verifyInteraction()
    }

    @State("an item with id 1 exists")
    fun itemExists() {
        itemRepository.deleteAll()
        itemRepository.save(Item(id = 1L, name = "Item One"))
    }

    @State("no items exist")
    fun noItems() {
        itemRepository.deleteAll()
    }

    @State("an item with id 1 exists")
    fun itemExistsWithParams(params: Map<String, Any>): Map<String, Any> {
        val id = (params.getOrDefault("id", 1L) as Number).toLong()
        itemRepository.save(Item(id = id, name = "Item One"))
        return mapOf("id" to id)
    }
}
```

### Python (pact-python v3.x with Pact Spec V4)

```python
import os
from typing import Any, Literal

from pact import Verifier


def handle_provider_state(
    state: str,
    action: Literal["setup", "teardown"],
    parameters: dict[str, Any] | None,
) -> None:
    parameters = parameters or {}

    if state == "an item with id 1 exists":
        if action == "setup":
            db.clear_items()
            db.insert_item(Item(id=1, name="Item One"))
        elif action == "teardown":
            db.clear_items()

    elif state == "no items exist":
        if action == "setup":
            db.clear_items()


def test_provider_verification():
    verifier = Verifier("ProviderName")
    verifier.add_transport(url="http://localhost:8080")
    verifier.state_handler(handle_provider_state, teardown=True)

    pact_url = os.getenv("PACT_URL")
    if pact_url:
        verifier.add_source(pact_url)
    else:
        verifier.broker_source(
            os.getenv("PACT_BROKER_BASE_URL"),
            token=os.getenv("PACT_BROKER_TOKEN"),
            selector=True,
        ).consumer_version(main_branch=True).consumer_version(deployed_or_released=True)

    verifier.set_publish_options(
        version=os.getenv("GIT_COMMIT", "1.0.0-local"),
        branch=os.getenv("GIT_BRANCH", "main"),
    )
    verifier.verify()
```

**pact-python State Handler Rules:**
- Function-based: single function handles ALL states via state name parameter
- Dictionary-based: map state names to specific functions
- Signature: `(state: str, action: Literal["setup", "teardown"], parameters: dict | None) -> None`
- `teardown=True` enables teardown callbacks after each interaction

## HARD RULES — VIOLATING THESE CAUSES PRODUCTION FAILURES

1. **ONLY use standard, well-known APIs for the detected language.**
   - NEVER use: rewire, proxyquire, mock-require, or any module-patching library
   - NEVER use: `.__get__()`, `.__set__()`, or any non-standard prototype methods
   - NEVER invent or guess at APIs. If you are not 100% certain an API exists, DO NOT use it.
   - NEVER import from internal/private module paths unless the provider source code explicitly exports them.

2. **ALWAYS handle the PACT_URL environment variable.**
   - When PACT_URL is set (webhook trigger): verify ONLY that specific pact
   - When PACT_URL is NOT set (normal trigger): use broker URL + consumerVersionSelectors
   - This dual-mode is NON-NEGOTIABLE. Every generated test MUST support both.

3. **State handlers MUST set up data matching EXACTLY what the pact expects.**
   - If the pact expects `{ "id": 1, "name": "Item One" }`, create that exact data.
   - Field names, types, values, and nesting must be identical.
   - Do NOT use random/generated data in state handlers.

4. **Access data stores through the provider's EXPORTED interfaces.**
   - Read the provider source code analysis to understand how data is stored and accessed.
   - If routes define in-memory arrays, check if they're exported or if there's an API to manipulate them.
   - If data is NOT directly accessible, use the provider's own REST API endpoints.

5. **Generated test files must be self-contained and runnable.**
   - JS/TS: `npx jest` — all imports use correct relative paths from test directory
   - Go: `go test` — proper package declaration and imports
   - Java/Kotlin: `mvn test` or `gradle test` — proper class structure and annotations
   - Python: `pytest` — proper function naming and imports

6. **Use CommonJS (require) for JavaScript. Use ES modules (import) for TypeScript.**
   - Detect from the provider's existing code which module system is used.
   - When in doubt for JS, use CommonJS.

7. **NEVER combine pactUrls AND pactBrokerUrl/consumerVersionSelectors.**
   - Use one OR the other, based on whether PACT_URL env var is set.
   - Using both causes the Verifier to fetch pacts twice or produce errors.

## Backend Framework Detection Guide

When analyzing provider source code, identify:
1. **Framework**: Express, Fastify, Koa, Hapi, Flask, Django, FastAPI, Spring Boot, Gin, Fiber, Echo, etc.
2. **Data storage**: In-memory arrays/maps, SQL database, ORM (Sequelize, GORM, JPA/Hibernate, SQLAlchemy), NoSQL (MongoDB, Redis)
3. **Module system**: CommonJS (require/module.exports), ES modules (import/export), Go packages, Java packages
4. **Entry point**: What the app exports — Express app object, HTTP handler, main() function, Spring Application class
5. **Data access patterns**: Direct array manipulation, service layer, repository pattern, ORM queries, raw SQL
6. **Middleware**: Authentication, CORS, body parsing — these affect how the provider responds

This analysis determines HOW state handlers will manipulate data. Different backends require fundamentally different approaches:

| Framework Pattern | State Handler Strategy |
|-------------------|----------------------|
| Express + in-memory arrays | Import the data module, manipulate arrays directly |
| Express + database | Use the same DB client/ORM the provider uses |
| Spring Boot + JPA | @Autowired repository, use save/deleteAll |
| Go + in-memory | Swap the package-level variable |
| Go + database | Use the same DB connection |
| Flask/FastAPI + SQLAlchemy | Import the session/model, use ORM operations |
| Any framework + no direct access | Use the provider's own REST API endpoints |

## Self-Validation Checklist

Before returning code, verify:
1. All imports are valid for the target language and match the patterns above
2. PACT_URL handling is present with the correct dual-mode pattern
3. Every provider state from the pact has a corresponding handler
4. State handler data matches EXACTLY what the pact expects
5. The test starts/stops the provider server correctly
6. No non-existent APIs or hallucinated methods are used
7. The test port does NOT conflict with the provider's default port
8. File paths for imports are correct relative to the test file location

## Output Format
You must respond with valid JSON matching the required schema. Do not include any text outside the JSON object.
"""


# =============================================================================
# OUTPUT SCHEMA — Structured output for reliable parsing
# =============================================================================

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "object",
            "description": "Analysis of the provider code and pact requirements",
            "properties": {
                "provider_language": {
                    "type": "string",
                    "enum": ["javascript", "typescript", "go", "java", "kotlin", "python"],
                    "description": "Detected provider programming language"
                },
                "framework": {
                    "type": "string",
                    "description": "Detected backend framework (e.g., Express, Gin, Spring Boot)"
                },
                "storage_type": {
                    "type": "string",
                    "enum": ["in_memory", "database", "orm", "mock", "external_api", "unknown"],
                    "description": "How the provider stores data"
                },
                "data_access_strategy": {
                    "type": "string",
                    "description": "How state handlers will access/modify data (e.g., 'import exported array', 'use REST API', 'use repository')"
                },
                "entry_point": {
                    "type": "string",
                    "description": "The provider's main export/entry point (e.g., 'src/index.js exports express app')"
                },
                "states_found": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of all provider states extracted from the pact"
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary of the provider analysis and test generation approach"
                }
            },
            "required": ["provider_language", "framework", "storage_type", "data_access_strategy", "states_found", "summary"]
        },
        "test": {
            "type": "object",
            "description": "The generated provider verification test",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename for the generated test (e.g., 'provider.pact.test.js', 'provider_pact_test.go')"
                },
                "filepath": {
                    "type": "string",
                    "description": "Relative path where the file should be placed (e.g., 'tests/contract-verification/')"
                },
                "code": {
                    "type": "string",
                    "description": "Complete, runnable test code. No markdown, no explanations — just the code."
                },
                "state_handlers": {
                    "type": "array",
                    "description": "List of state handlers generated",
                    "items": {
                        "type": "object",
                        "properties": {
                            "state_name": {
                                "type": "string",
                                "description": "Exact provider state name from the pact"
                            },
                            "data_setup": {
                                "type": "string",
                                "description": "What data the handler creates/modifies"
                            },
                            "access_method": {
                                "type": "string",
                                "description": "How the handler accesses the data store"
                            }
                        },
                        "required": ["state_name", "data_setup", "access_method"]
                    }
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Any npm/pip/go packages required beyond what is already installed"
                }
            },
            "required": ["filename", "filepath", "code", "state_handlers"]
        },
        "skip_reason": {
            "type": "string",
            "description": "If no test could be generated, explain why"
        }
    },
    "required": ["analysis", "test"]
}


# =============================================================================
# MAIN GENERATION PROMPT — Builds the complete provider verification test
# =============================================================================

PROVIDER_GENERATION_PROMPT = """# Provider Verification Test Generation

## CRITICAL: Read ALL sections before generating code

---

## 1. Provider Language

<<PROVIDER_LANGUAGE>>

## 2. Provider Information (from source code analysis)

<<PROVIDER_CONTEXT>>

## 3. Pact Information (from consumers via Pact Broker)

<<PACT_CONTEXT>>

## 4. Provider States That Need Handlers

Each state below MUST have a corresponding handler in the generated code:

<<PROVIDER_STATES>>

## 5. Expected Responses Per State

The state handler must set up data so the provider returns EXACTLY these responses:

<<EXPECTED_RESPONSES>>

## 6. Storage Type & Data Access Hints

<<STORAGE_HINTS>>

---

## 7. GENERATION REQUIREMENTS

### A. Analyze the Provider Backend

Before writing any code, analyze the provider source (Section 2) and determine:
1. **What language is it?** → This determines which Pact library to use
2. **What framework is it?** → Express, Fastify, Gin, Spring Boot, Flask, etc.
3. **How is data stored?** → In-memory arrays, database, ORM, external service
4. **What does the app export?** → Express app, HTTP handler, main function
5. **How can state handlers access data?** → Direct import, REST API, repository

### B. Choose the Correct Verification Pattern

Based on the detected language, use the EXACT pattern from the system prompt:
- JavaScript/TypeScript → pact-js Verifier with stateHandlers object
- Go → pact-go HTTPVerifier with StateHandlers map
- Java/Kotlin → pact-jvm JUnit5 with @State annotated methods
- Python → pact-python Verifier with state_handler callback

### C. Implement State Handlers

For EACH provider state listed in Section 4:

1. **Read the expected response** for that state (Section 5)
2. **Determine how to set up that data** based on the storage analysis (Section 6)
3. **Write the handler** using the correct pattern for the language

State handler data access strategies (in order of preference):

**Strategy 1: Import exported data store (BEST — use when available)**
If the provider exports its data store (array, map, object), import it directly.
```javascript
// JS example: provider exports items array from data.js
const { items } = require('../../src/data');
// In handler:
items.length = 0;
items.push({ id: 1, name: 'Item One' });
```

**Strategy 2: Use provider's own REST API (SAFEST — always works)**
If data is NOT directly accessible, use the provider's POST/PUT/DELETE endpoints.
```javascript
// JS example: call provider's own API to set up data
const http = require('http');
await new Promise((resolve, reject) => {
  const data = JSON.stringify({ id: 1, name: 'Item One' });
  const req = http.request({
    hostname: 'localhost', port: PORT, path: '/items',
    method: 'POST', headers: { 'Content-Type': 'application/json' }
  }, (res) => { res.on('data', () => {}); res.on('end', resolve); });
  req.on('error', reject);
  req.write(data);
  req.end();
});
```

**Strategy 3: Use repository/service layer**
If the provider uses a repository pattern, import the repository.
```java
// Java example: use Spring repository
@Autowired
private ItemRepository itemRepository;

@State("an item with id 1 exists")
void itemExists() {
    itemRepository.deleteAll();
    itemRepository.save(new Item(1L, "Item One"));
}
```

**Strategy 4: Use database client directly**
If the provider uses raw SQL/NoSQL, use the same DB connection.
```go
// Go example: use database directly
db.Exec("DELETE FROM items")
db.Exec("INSERT INTO items (id, name) VALUES (?, ?)", 1, "Item One")
```

### D. Common Mistakes to AVOID

1. ❌ `require('../../src/routes/items').__get__('items')` — `__get__` does NOT exist in standard Node.js
2. ❌ `require('rewire')` — third-party module-patching library, not installed by default
3. ❌ Using `import` in JavaScript files that use CommonJS — check the provider's module system
4. ❌ Hardcoding `pactBrokerUrl` without PACT_URL fallback — webhook verification will fail
5. ❌ Using `consumerVersionSelectors` AND `pactUrls` together — use one OR the other
6. ❌ Starting test server on the provider's default port — use a different test port (e.g., 3002)
7. ❌ Wrong field names/types in state handler data — must match pact EXACTLY
8. ❌ Not handling server startup/shutdown in test lifecycle — causes port conflicts in CI
9. ❌ Guessing at internal module paths — only import what the provider explicitly exports
10. ❌ Using `Pact()` or `PactV3()` constructor for provider verification — use `Verifier()` (JS/Python) or `@Provider` annotation (JVM)

---

## 8. OUTPUT INSTRUCTIONS

Respond with valid JSON matching the required schema.

For the `code` field in the JSON:
- Generate COMPLETE, runnable test code
- NO markdown code fences (no ```javascript ... ```)
- NO explanatory text before or after the code
- The code must compile/parse without errors
- All state handlers for every state in Section 4 must be included
- PACT_URL dual-mode must be implemented

Generate the provider verification test now.
"""


# =============================================================================
# REVISION PROMPT — For fixing failed verification tests
# =============================================================================

PROVIDER_REVISION_PROMPT = """# Provider Test Revision Request

## Provider Language
<<PROVIDER_LANGUAGE>>

## The Previously Generated Code (FAILED)

```
<<ORIGINAL_CODE>>
```

## Error From Test Execution

```
<<ERROR_MESSAGE>>
```

<<DEVELOPER_FEEDBACK_SECTION>>

---

## DIAGNOSIS GUIDE

Analyze the error and identify which category it falls into:

### Category 1: Import/Require Errors
- `Cannot find module '...'` → Wrong file path. Check the actual provider repo structure.
- `TypeError: require(...) is not a function` → Module does not export what you expected.
- `TypeError: require(...).__get__ is not a function` → You used a non-existent API. NEVER use `.__get__()`, `.__set__()`, or similar. Use standard `require()` imports only.
- `ModuleNotFoundError` (Python) → Wrong import path or package not installed.
- `cannot find symbol` (Java) → Wrong class/method name or missing import.
- `unresolved reference` (Kotlin) → Wrong import or missing dependency.

### Category 2: Pact Verification Errors
- `No pacts found for provider` → PACT_URL not being used when it should be. Check if the PACT_URL environment variable is set and used in the opts.
- `consumer version selectors` error → Wrong selector format for the language.
- `State handler not found` or `MissingStateChangeMethod` → State name does not match EXACTLY what is in the pact. Check for typos, extra spaces, different casing.

### Category 3: State Handler Data Mismatch
- `expected X but got Y` → State handler is not setting up the correct data.
- Response body mismatch → Check each field name, type, and value against the pact.
- Missing fields in response → State handler created incomplete data.

### Category 4: Server/Network Errors
- `ECONNREFUSED` → Provider server not running. Check startup logic.
- `EADDRINUSE` → Port already in use. Use a different test port.
- `Timeout` → Server too slow to start. Increase timeout or add readiness check.

### Category 5: Test Framework Errors
- `Test suite failed to run` → Syntax error in generated code. Check brackets, semicolons, etc.
- `describe is not defined` → Wrong test runner or missing framework.
- `@TestTemplate` not recognized → Missing JUnit5 dependency.

## FIX INSTRUCTIONS

1. Identify the EXACT error category from above
2. Determine the root cause by reading the full error message carefully
3. Fix ONLY the broken part — do not rewrite working, unrelated code
4. Ensure the fix does not introduce new issues
5. Verify PACT_URL handling is still correct after the fix

## OUTPUT

Respond with valid JSON matching the required schema, with the FIXED code.
"""


# =============================================================================
# PROVIDER LANGUAGE DETECTION CONFIG
# =============================================================================

PROVIDER_LANGUAGE_CONFIG = {
    "javascript": {
        "pact_library": "@pact-foundation/pact (Verifier)",
        "test_framework": "jest",
        "file_extension": ".pact.test.js",
        "file_naming": "kebab-case",
        "test_directory": "tests/contract-verification",
        "module_system": "CommonJS (require)",
        "indicators": ["package.json", "node_modules"],
        "run_command": "npx jest --testMatch '**/contract-verification/**'",
    },
    "typescript": {
        "pact_library": "@pact-foundation/pact (Verifier)",
        "test_framework": "jest",
        "file_extension": ".pact.test.ts",
        "file_naming": "kebab-case",
        "test_directory": "tests/contract-verification",
        "module_system": "ES modules (import)",
        "indicators": ["tsconfig.json"],
        "run_command": "npx jest --testMatch '**/contract-verification/**'",
    },
    "go": {
        "pact_library": "github.com/pact-foundation/pact-go/v2",
        "test_framework": "testing",
        "file_extension": "_pact_test.go",
        "file_naming": "snake_case",
        "test_directory": "tests/contract",
        "module_system": "Go modules",
        "indicators": ["go.mod", "go.sum"],
        "run_command": "go test -v ./tests/contract/...",
    },
    "java": {
        "pact_library": "au.com.dius.pact.provider:junit5 (4.6.x)",
        "test_framework": "junit5",
        "file_extension": "PactTest.java",
        "file_naming": "PascalCase",
        "test_directory": "src/test/java",
        "module_system": "Maven/Gradle packages",
        "indicators": ["pom.xml", "build.gradle"],
        "run_command": "mvn test or gradle test",
    },
    "kotlin": {
        "pact_library": "au.com.dius.pact.provider:junit5 (4.6.x)",
        "test_framework": "junit5",
        "file_extension": "PactTest.kt",
        "file_naming": "PascalCase",
        "test_directory": "src/test/kotlin",
        "module_system": "Maven/Gradle packages",
        "indicators": ["build.gradle.kts", "settings.gradle.kts"],
        "run_command": "gradle test",
    },
    "python": {
        "pact_library": "pact-python v3.x (Verifier)",
        "test_framework": "pytest",
        "file_extension": "_pact_test.py",
        "file_naming": "snake_case",
        "test_directory": "tests/contract",
        "module_system": "Python packages",
        "indicators": ["pyproject.toml", "requirements.txt", "setup.py"],
        "run_command": "pytest tests/contract/",
    },
}


# =============================================================================
# BUILDER FUNCTIONS
# =============================================================================

def get_provider_library_prompt(language: str) -> str:
    """
    Generate language-specific provider Pact library instructions.

    Args:
        language: Detected provider programming language

    Returns:
        Formatted string with Pact provider library details
    """
    config = PROVIDER_LANGUAGE_CONFIG.get(language)
    if not config:
        return f"No specific Pact provider configuration found for '{language}'. Use standard patterns from the system prompt."

    return "\n".join([
        f"Pact Library: {config['pact_library']}",
        f"Test Framework: {config['test_framework']}",
        f"File Extension: {config['file_extension']}",
        f"Test Directory: {config['test_directory']}",
        f"Module System: {config['module_system']}",
        f"Run Command: {config['run_command']}",
    ])


def build_provider_generation_prompt(
    provider_name: str,
    provider_language: str,
    pact_context: str,
    provider_context: str,
    provider_states: list,
    storage_hints: list,
    expected_responses: dict = None
) -> str:
    """
    Build the complete prompt for provider verification test generation.

    Args:
        provider_name: Name of the provider service
        provider_language: Detected language of the provider (e.g., 'javascript', 'go')
        pact_context: Formatted pact context from PactFetcher
        provider_context: Formatted provider code context from ProviderAnalyzer
        provider_states: List of provider states needing handlers
        storage_hints: Hints about data storage from analyzer
        expected_responses: Map of state -> expected response data

    Returns:
        Complete prompt string for Gemini
    """
    states_formatted = "\n".join(f'- "{state}"' for state in provider_states)
    hints_formatted = (
        "\n".join(f"- {hint}" for hint in storage_hints)
        if storage_hints
        else "- No specific hints. Analyze provider code to determine storage type."
    )

    responses_formatted = ""
    if expected_responses:
        for state, response in expected_responses.items():
            responses_formatted += f'\nState: "{state}"\n'
            responses_formatted += f'Expected response: {response}\n'

    language_info = get_provider_library_prompt(provider_language)

    return (
        PROVIDER_GENERATION_PROMPT
        .replace("<<PROVIDER_LANGUAGE>>", f"{provider_language}\n\n{language_info}")
        .replace("<<PACT_CONTEXT>>", pact_context)
        .replace("<<PROVIDER_CONTEXT>>", provider_context)
        .replace("<<PROVIDER_STATES>>", states_formatted)
        .replace("<<STORAGE_HINTS>>", hints_formatted)
        .replace("<<EXPECTED_RESPONSES>>", responses_formatted or "See pact context above for expected response bodies.")
    )


def build_provider_revision_prompt(
    original_code: str,
    error_message: str,
    provider_language: str = "javascript",
    revision_feedback: str = None
) -> str:
    """
    Build prompt for revising a failed provider verification test.

    Args:
        original_code: The generated code that failed
        error_message: Error message from test execution
        provider_language: Provider's programming language
        revision_feedback: Optional human feedback for revision

    Returns:
        Revision prompt string
    """
    feedback_section = ""
    if revision_feedback:
        feedback_section = f"## Developer Feedback\n{revision_feedback}"

    return (
        PROVIDER_REVISION_PROMPT
        .replace("<<PROVIDER_LANGUAGE>>", provider_language)
        .replace("<<ORIGINAL_CODE>>", original_code)
        .replace("<<ERROR_MESSAGE>>", error_message)
        .replace("<<DEVELOPER_FEEDBACK_SECTION>>", feedback_section)
    )