"""
Breaking Change Prompts Module
===============================

AI prompts for the Breaking Change Detection Agent.

The agent analyzes provider verification failures and generates
fix options when actual breaking changes (not test code errors) are detected.

Prompt design:
- System prompt: Role as contract testing expert analyzing failures
- Fix generation prompt: Takes structured breaking changes + context
- Output schema: Structured JSON with exactly 3 fix options
"""


# =============================================================================
# SYSTEM PROMPT — Sets the AI's role for breaking change analysis
# =============================================================================

BREAKING_CHANGE_SYSTEM_PROMPT = """You are a senior backend engineer and Pact contract testing expert.

Your job is to analyze provider verification failures and generate actionable fix options.

You receive:
1. A list of detected breaking changes (parsed from verification output)
2. Provider source code context (language, framework, routes, data models)
3. Pact context (consumer expectations, interactions, expected responses)

You generate exactly 3 fix options for each breaking change scenario:
- Option 1: Provider adapts (backward compatibility — recommended default)
- Option 2: Consumer updates expectations
- Option 3: API versioning

## Rules

1. **Be specific.** Include actual code snippets, not vague instructions.
2. **Use the correct language.** If the provider is JavaScript, write JavaScript fixes. If Go, write Go.
3. **Reference actual files.** Use the provider's real file paths and function names from the context.
4. **Keep options independent.** Each option should be a complete fix on its own.
5. **Option 1 is always the recommended default** (provider adapts for backward compat).
6. **Do NOT invent endpoints or fields.** Only reference what exists in the provider/pact context.
7. **Be honest about impact.** If an option requires coordination between teams, say so.

## Output Format
Respond with valid JSON matching the required schema. No markdown, no explanations outside the JSON.
"""


# =============================================================================
# FIX GENERATION PROMPT — Generates 3 fix options
# =============================================================================

FIX_GENERATION_PROMPT = """# Breaking Change Fix Generation

## Breaking Changes Detected

<<BREAKING_CHANGES>>

## Provider Context

<<PROVIDER_CONTEXT>>

## Pact Context (Consumer Expectations)

<<PACT_CONTEXT>>

## Provider Language

<<PROVIDER_LANGUAGE>>

---

## Instructions

For each breaking change above, generate exactly 3 fix options.

If multiple breaking changes are related (e.g., same endpoint, same root cause),
group them and generate one set of 3 options for the group.

### Option 1: Provider Adapts (Recommended)
- The provider changes its code to satisfy the consumer's expectations
- This preserves backward compatibility
- Show the exact code change needed in the provider
- Reference the correct file path from the provider context

### Option 2: Consumer Updates Expectations
- The consumer changes its pact test to match the provider's current behavior
- Show what the consumer's pact interaction should look like
- Reference the consumer name and interaction description

### Option 3: API Versioning
- Create a new API version (e.g., /v2/items) with the new schema
- Keep the old version working for existing consumers
- Describe what both sides need to do

For each option include:
- `title`: Short title (e.g., "Provider adds missing 'category' field")
- `description`: 1-2 sentence explanation
- `side`: Who needs to change — "provider", "consumer", or "both"
- `code_suggestion`: Actual code snippet showing the fix
- `impact`: What happens if this option is chosen

Generate the fix options now as JSON.
"""


# =============================================================================
# OUTPUT SCHEMA — Structured output for reliable parsing
# =============================================================================

FIX_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Brief summary of the breaking changes and their root cause"
        },
        "fix_groups": {
            "type": "array",
            "description": "Groups of related breaking changes with fix options",
            "items": {
                "type": "object",
                "properties": {
                    "group_description": {
                        "type": "string",
                        "description": "What this group of breaking changes is about"
                    },
                    "affected_endpoints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of affected endpoints (e.g., 'GET /items/1')"
                    },
                    "options": {
                        "type": "array",
                        "description": "Exactly 3 fix options",
                        "items": {
                            "type": "object",
                            "properties": {
                                "option_number": {
                                    "type": "integer",
                                    "description": "1, 2, or 3"
                                },
                                "title": {
                                    "type": "string",
                                    "description": "Short title for the fix"
                                },
                                "description": {
                                    "type": "string",
                                    "description": "1-2 sentence explanation"
                                },
                                "side": {
                                    "type": "string",
                                    "enum": ["provider", "consumer", "both"],
                                    "description": "Who needs to make changes"
                                },
                                "code_suggestion": {
                                    "type": "string",
                                    "description": "Actual code snippet showing the fix"
                                },
                                "impact": {
                                    "type": "string",
                                    "description": "What happens if this option is chosen"
                                }
                            },
                            "required": ["option_number", "title", "description", "side", "code_suggestion", "impact"]
                        },
                        "minItems": 3,
                        "maxItems": 3
                    }
                },
                "required": ["group_description", "affected_endpoints", "options"]
            }
        }
    },
    "required": ["summary", "fix_groups"]
}


# =============================================================================
# BUILDER FUNCTIONS
# =============================================================================

def build_fix_generation_prompt(
    breaking_changes_formatted: str,
    provider_context: str,
    pact_context: str,
    provider_language: str
) -> str:
    """
    Build the prompt for fix option generation.

    Args:
        breaking_changes_formatted: Formatted string of detected breaking changes
        provider_context: Formatted provider code context
        pact_context: Formatted pact/consumer context
        provider_language: Provider's programming language

    Returns:
        Complete prompt string for Gemini
    """
    return (
        FIX_GENERATION_PROMPT
        .replace("<<BREAKING_CHANGES>>", breaking_changes_formatted)
        .replace("<<PROVIDER_CONTEXT>>", provider_context)
        .replace("<<PACT_CONTEXT>>", pact_context)
        .replace("<<PROVIDER_LANGUAGE>>", provider_language)
    )
