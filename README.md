# AI-Powered Contract Testing

> Automatically generate Pact contract tests using AI when developers add a label to their Pull Requests.

## Overview

This is the **central repository** for the AI-powered contract testing workflow. It provides:

- A reusable GitHub Actions workflow that consumer repos can call
- Python scripts for context collection, AI generation, and PR creation
- Configuration for language detection and Pact library selection

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Consumer Repository                       │
│  ┌─────────────┐                                            │
│  │  Developer  │ ─── Creates PR ───► PR #123                │
│  └─────────────┘        │                                   │
│                         ▼                                   │
│              Adds 'contract-testing' label                  │
│                         │                                   │
│                         ▼                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  .github/workflows/ai-contract-testing.yml           │  │
│  │  (Thin caller workflow)                              │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                    Calls reusable workflow
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Central Repository (this repo)                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  .github/workflows/generate-tests.yml                │  │
│  │  (Main workflow logic)                               │  │
│  └──────────────────────────────────────────────────────┘  │
│                         │                                   │
│                         ▼                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Python Pipeline                                     │  │
│  │  1. Collect context (GitHub, JIRA, OpenAPI, Pactflow)│  │
│  │  2. Compress context (~80% reduction)                │  │
│  │  3. Generate tests with Gemini AI                    │  │
│  │  4. Create PR with generated tests                   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### For Consumer Repos

1. Add this workflow file to your repo at `.github/workflows/ai-contract-testing.yml`:

```yaml
name: AI Contract Testing

on:
  pull_request:
    types: [labeled]

jobs:
  generate-tests:
    if: github.event.label.name == 'contract-testing'
    uses: YOUR_ORG/ai-contract-testing-demo/.github/workflows/generate-tests.yml@main
    with:
      repository: ${{ github.repository }}
      pr_number: ${{ github.event.pull_request.number }}
      target_repo: ${{ github.repository }}
    secrets:
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      PACTFLOW_TOKEN: ${{ secrets.PACTFLOW_TOKEN }}
      PACTFLOW_BASE_URL: ${{ secrets.PACTFLOW_BASE_URL }}
      PAT_TOKEN: ${{ secrets.PAT_TOKEN }}
```

2. Add required secrets to your repo (Settings > Secrets > Actions)

3. Add the `contract-testing` label to a PR to trigger test generation

### For Development

1. Clone this repo
2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and fill in your credentials
5. Run the pipeline locally:
   ```bash
   python -m src.pipeline owner/repo 123
   ```

## Configuration

### Required Secrets

| Secret | Description |
|--------|-------------|
| `GEMINI_API_KEY` | Google AI Studio API key |
| `PACTFLOW_TOKEN` | Pactflow API token |
| `PACTFLOW_BASE_URL` | Pactflow URL (e.g., `https://your-account.pactflow.io`) |
| `PAT_TOKEN` | GitHub Personal Access Token with `repo` scope |

### Optional Secrets

| Secret | Description |
|--------|-------------|
| `JIRA_BASE_URL` | JIRA instance URL |
| `JIRA_EMAIL` | JIRA user email |
| `JIRA_API_TOKEN` | JIRA API token |
| `LANGFUSE_PUBLIC_KEY` | Langfuse observability |
| `LANGFUSE_SECRET_KEY` | Langfuse observability |

## Supported Languages

The pipeline automatically detects the repository language and uses the appropriate Pact library:

| Language | Pact Library | Test Framework |
|----------|-------------|----------------|
| JavaScript | @pact-foundation/pact | Jest |
| TypeScript | @pact-foundation/pact | Jest |
| Go | github.com/pact-foundation/pact-go/v2 | testing |
| Python | pact-python | pytest |
| Java | au.com.dius.pact.consumer | JUnit 5 |
| Kotlin | au.com.dius.pact.consumer | JUnit 5 |

## License

MIT
