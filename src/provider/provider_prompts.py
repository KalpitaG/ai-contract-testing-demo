"""
Provider Prompts Module
=======================

Prompts specifically designed for generating provider verification tests
and state handlers.

These prompts instruct the AI to:
1. Generate state handlers for each provider state
2. Create the complete verification test file
3. Handle different storage types (database, in-memory, etc.)
"""


PROVIDER_SYSTEM_PROMPT = """You are an expert in Pact contract testing, specifically in writing provider verification tests and state handlers.

Your role is to generate provider-side verification code that:
1. Sets up the correct state for each interaction (state handlers)
2. Runs the Pact verification against the provider
3. Handles data cleanup between tests

You understand:
- Pact v3 specification and provider verification
- State handlers and how they prepare data for each test
- Different data storage mechanisms (in-memory, databases, mocks)
- JavaScript/Node.js, Python, Go, and Java testing patterns

You generate production-ready code that:
- Is properly documented
- Handles errors gracefully
- Follows best practices for the language/framework
- Can be run in CI/CD pipelines"""


PROVIDER_STATE_HANDLER_PROMPT = """# Provider State Handler Generation

## Context

You are generating state handlers for a Pact provider verification test.

### What is a State Handler?

A state handler is a function that sets up the required data/state before a specific interaction is verified. For example:

- State: "item with id 1 exists"
- Handler: Insert item with id 1 into the data store

- State: "no items exist"  
- Handler: Clear all items from the data store

### Provider Information

{provider_context}

### Pact Information (from consumers)

{pact_context}

### Provider States That Need Handlers

{provider_states}

## Your Task

Generate state handlers for EACH provider state listed above. The handlers must:

1. **Set up the exact data needed** for the interaction to succeed
2. **Match the expected response** from the pact (if response has id:1, name:"Item One", the state must create that exact data)
3. **Use the correct data manipulation method** based on the storage type detected
4. **Be idempotent** - running the handler multiple times should not cause issues

## Storage Type Considerations

{storage_hints}

## Output Format

Generate a complete JavaScript/Node.js provider verification test file with:

1. Required imports
2. Test setup (server start)
3. Test teardown (server stop, data cleanup)
4. State handlers for ALL provider states
5. Verifier configuration
6. The verification test itself

The file should be ready to run with: `npm test`

## Code Structure

```javascript
const {{ Verifier }} = require('@pact-foundation/pact');
const app = require('../../src/index');

describe('Provider Verification', () => {{
  let server;
  const PORT = 3002;

  beforeAll((done) => {{
    server = app.listen(PORT, done);
  }});

  afterAll((done) => {{
    server.close(done);
  }});

  it('verifies pacts with consumers', async () => {{
    const verifier = new Verifier({{
      provider: '{provider_name}',
      providerBaseUrl: `http://localhost:${{PORT}}`,
      pactBrokerUrl: process.env.PACTFLOW_BASE_URL,
      pactBrokerToken: process.env.PACTFLOW_TOKEN,
      publishVerificationResult: process.env.CI === 'true',
      providerVersion: process.env.GIT_COMMIT || '1.0.0',
      providerVersionBranch: process.env.GIT_BRANCH || 'main',
      
      consumerVersionSelectors: [
        {{ mainBranch: true }},
        {{ deployedOrReleased: true }}
      ],
      
      stateHandlers: {{
        // YOUR GENERATED STATE HANDLERS HERE
      }},
      
      logLevel: 'info'
    }});
    
    await verifier.verifyProvider();
  }}, 60000);
}});
```

## Important Notes

1. Each state handler is an async function that returns a Promise
2. State handlers receive optional parameters (for parameterized states)
3. For in-memory storage, you may need to access the data arrays directly
4. For databases, you'll need to insert/delete records
5. Always ensure data matches EXACTLY what the pact expects

## Generate the Complete Test File Now

Based on the provider code analysis and pact information above, generate the complete provider verification test file with all necessary state handlers.
"""


PROVIDER_VERIFICATION_PROMPT = """# Provider Verification Test Generation

## Provider: {provider_name}

### Provider Source Code Analysis
{provider_context}

### Pacts from Consumers
{pact_context}

### Required Provider States
The following states MUST have handlers:
{states_list}

### Expected Responses
For each state, the provider must return data matching these responses:
{expected_responses}

## Generation Requirements

1. **Complete Test File**: Generate a fully functional test file, not snippets
2. **All State Handlers**: Every state listed must have a handler
3. **Data Accuracy**: State handlers must set up data that EXACTLY matches expected responses
4. **Error Handling**: Include try/catch where appropriate
5. **Logging**: Add console.log statements to track state setup

## Output

Generate ONLY the JavaScript code for the test file. No explanations, no markdown code blocks around it, just the raw code.

The file will be saved as: tests/contract-verification/provider.pact.test.js
"""


def build_provider_generation_prompt(
    provider_name: str,
    pact_context: str,
    provider_context: str,
    provider_states: list,
    storage_hints: list,
    expected_responses: dict = None
) -> str:
    """
    Build the complete prompt for provider test generation.
    
    Args:
        provider_name: Name of the provider service
        pact_context: Formatted pact context from PactFetcher
        provider_context: Formatted provider code context from ProviderAnalyzer
        provider_states: List of provider states needing handlers
        storage_hints: Hints about data storage from analyzer
        expected_responses: Map of state -> expected response data
        
    Returns:
        Complete prompt string
    """
    states_formatted = "\n".join(f'- "{state}"' for state in provider_states)
    hints_formatted = "\n".join(f"- {hint}" for hint in storage_hints)
    
    responses_formatted = ""
    if expected_responses:
        for state, response in expected_responses.items():
            responses_formatted += f'\nState: "{state}"\n'
            responses_formatted += f'Expected response: {response}\n'
    
    return PROVIDER_STATE_HANDLER_PROMPT.format(
        provider_name=provider_name,
        pact_context=pact_context,
        provider_context=provider_context,
        provider_states=states_formatted,
        storage_hints=hints_formatted,
        expected_responses=responses_formatted or "See pact context above"
    )


def build_provider_revision_prompt(
    original_code: str,
    error_message: str,
    revision_feedback: str = None
) -> str:
    """
    Build prompt for revising provider test code.
    
    Args:
        original_code: The generated code that failed
        error_message: Error message from test run
        revision_feedback: Optional human feedback
        
    Returns:
        Revision prompt string
    """
    return f"""# Provider Test Revision Request

## Original Generated Code
```javascript
{original_code}
```

## Error Encountered
```
{error_message}
```

{f'## Developer Feedback: {revision_feedback}' if revision_feedback else ''}

## Your Task

Fix the provider verification test based on the error. Common issues:

1. **State handler not setting up correct data**
   - Check the expected response in the pact
   - Ensure data matches EXACTLY (field names, types, values)

2. **State handler not found**
   - Verify the state name matches EXACTLY what's in the pact
   - Check for typos, extra spaces, case sensitivity

3. **Provider server not responding**
   - Check the port configuration
   - Ensure server starts before verification

4. **Data not being reset between tests**
   - Add cleanup in afterEach or in state handlers
   - Reset arrays/clear database between verifications

5. **Import/require errors**
   - Check file paths are correct
   - Verify module exports

## Output

Generate the FIXED complete test file. Only output the code, no explanations.
"""


# State handler templates for different storage types
STATE_HANDLER_TEMPLATES = {
    "in_memory": {
        "javascript": '''
        // In-memory state handler template
        '{state_name}': async () => {{
          console.log('Setting up state: {state_name}');
          // Access the in-memory data store
          // For arrays: push/splice/filter
          // For objects: assign/delete
          {setup_code}
        }}''',
    },
    "database": {
        "javascript": '''
        // Database state handler template
        '{state_name}': async () => {{
          console.log('Setting up state: {state_name}');
          // Insert required records
          // await db.query('INSERT INTO ...');
          // Or use ORM: await Model.create({{ ... }});
          {setup_code}
        }}''',
    },
    "mock": {
        "javascript": '''
        // Mock state handler template
        '{state_name}': async () => {{
          console.log('Setting up state: {state_name}');
          // Configure mock responses
          // mockService.mockResponse({{ ... }});
          {setup_code}
        }}''',
    }
}


def get_state_handler_template(storage_type: str, language: str = "javascript") -> str:
    """Get the appropriate state handler template."""
    templates = STATE_HANDLER_TEMPLATES.get(storage_type, STATE_HANDLER_TEMPLATES["in_memory"])
    return templates.get(language, templates.get("javascript", ""))
