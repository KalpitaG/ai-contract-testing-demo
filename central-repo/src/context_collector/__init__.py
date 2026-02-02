"""
Context Collector Package
=========================
Modules for collecting context from various sources.
"""

from .github_collector import GitHubCollector, GitHubContext
from .openapi_collector import OpenAPICollector, OpenAPIContext
from .pactflow_collector import PactflowCollector, PactflowContext

# JIRA is optional
try:
    from .jira_collector import JiraCollector, JiraContext
except ImportError:
    JiraCollector = None
    JiraContext = None

__all__ = [
    "GitHubCollector",
    "GitHubContext",
    "JiraCollector",
    "JiraContext",
    "OpenAPICollector",
    "OpenAPIContext",
    "PactflowCollector",
    "PactflowContext",
]
