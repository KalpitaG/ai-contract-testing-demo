"""
JIRA Context Collector
======================
Fetches and structures JIRA ticket information for AI consumption.
"""

import os
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from langfuse import observe, get_client

load_dotenv()


@dataclass
class JiraContext:
    """Structured container for JIRA ticket information."""
    ticket_key: str
    summary: str
    description: str
    status: str
    issue_type: str
    labels: list[str] = field(default_factory=list)
    acceptance_criteria: Optional[str] = None
    comments: list[dict] = field(default_factory=list)
    parent_epic: Optional[dict] = None
    linked_issues: list[dict] = field(default_factory=list)
    raw_response: dict = field(default_factory=dict)

    def format_for_ai(self) -> str:
        """Format JIRA context into a string optimized for AI consumption."""
        sections = []
        
        # Header
        sections.append(f"## JIRA Ticket: {self.ticket_key}")
        sections.append(f"**Type:** {self.issue_type}")
        sections.append(f"**Status:** {self.status}")
        sections.append(f"**Summary:** {self.summary}")
        
        if self.labels:
            sections.append(f"**Labels:** {', '.join(self.labels)}")
        
        # Description
        if self.description:
            sections.append(f"\n### Description\n{self.description}")
        
        # Acceptance Criteria (only shown if not already in description)
        if self.acceptance_criteria:
            if not self.description or self.acceptance_criteria not in self.description:
                sections.append(f"\n### Acceptance Criteria\n{self.acceptance_criteria}")
        
        # Parent Epic
        if self.parent_epic:
            sections.append(f"\n### Parent Epic")
            sections.append(f"- {self.parent_epic['key']}: {self.parent_epic['summary']}")
        
        # Linked Issues
        if self.linked_issues:
            sections.append(f"\n### Linked Issues")
            for link in self.linked_issues:
                sections.append(f"- {link['relationship']}: {link['key']} - {link['summary']}")
        
        # Recent Comments
        if self.comments:
            sections.append(f"\n### Recent Comments ({len(self.comments)} total)")
            for comment in self.comments[-3:]:
                body = comment['body'][:500] if len(comment['body']) > 500 else comment['body']
                sections.append(f"\n**{comment['author']}:**\n{body}")
        
        formatted = "\n".join(sections)
        return formatted


class JiraCollector:
    """
    Collects context from JIRA tickets.
    
    Usage:
        collector = JiraCollector()
        context = collector.collect("PROJ-123")
        formatted = context.format_for_ai()
    """
    
    # Class-level cache for JIRA config to avoid reloading config repeatedly
    _jira_config_cache: Optional[dict] = None
    
    @classmethod
    def _get_jira_config(cls) -> dict:
        """Get JIRA config from detection.yaml (cached)."""
        if cls._jira_config_cache is None:
            try:
                from src.context_processor.repo_analyzer import RepoAnalyzer
                analyzer = RepoAnalyzer()
                cls._jira_config_cache = analyzer.config.get("jira", {})
            except Exception:
                cls._jira_config_cache = {}
        return cls._jira_config_cache
    
    def __init__(self, timeout: int = None):
        """
        Initialize JIRA client with credentials from environment.
        
        Args:
            timeout: Request timeout in seconds (default: from env or 30)
        """
        from atlassian import Jira
        
        self.base_url = os.getenv("JIRA_BASE_URL")
        self.email = os.getenv("JIRA_EMAIL")
        self.api_token = os.getenv("JIRA_API_TOKEN")
        self.timeout = timeout or int(os.getenv("API_TIMEOUT_SECONDS", "30"))
        
        if not all([self.base_url, self.email, self.api_token]):
            missing = []
            if not self.base_url: missing.append("JIRA_BASE_URL")
            if not self.email: missing.append("JIRA_EMAIL")
            if not self.api_token: missing.append("JIRA_API_TOKEN")
            raise ValueError(f"Missing JIRA credentials: {', '.join(missing)}")
        
        self.client = Jira(
            url=self.base_url,
            username=self.email,
            password=self.api_token,
            cloud=True,
            timeout=self.timeout
        )
    
    @observe(name="jira_collect")
    def collect(self, ticket_key: str) -> JiraContext:
        """
        Collect all relevant context from a JIRA ticket.
        
        Args:
            ticket_key: JIRA ticket identifier (e.g., "PROJ-123")
            
        Returns:
            JiraContext object with structured ticket information
        """
        print(f"[JIRA] Fetching ticket: {ticket_key}")
        
        try:
            get_client().update_current_span(
                input={"ticket_key": ticket_key}
            )
        except Exception:
            pass
        
        issue = self.client.issue(ticket_key)
        fields = issue.get("fields", {})
        
        summary = fields.get("summary", "")
        description = fields.get("description", "") or ""
        status = fields.get("status", {}).get("name", "Unknown")
        issue_type = fields.get("issuetype", {}).get("name", "Unknown")
        labels = fields.get("labels", [])
        
        # Extract AC from custom field first, then fall back to description parsing
        # This avoids duplication when AC is in both places
        acceptance_criteria = self._extract_acceptance_criteria(fields, description)
        comments = self._extract_comments(ticket_key)
        parent_epic = self._extract_parent_epic(fields)
        linked_issues = self._extract_linked_issues(fields)
        
        context = JiraContext(
            ticket_key=ticket_key,
            summary=summary,
            description=description,
            status=status,
            issue_type=issue_type,
            labels=labels,
            acceptance_criteria=acceptance_criteria,
            comments=comments,
            parent_epic=parent_epic,
            linked_issues=linked_issues,
            raw_response=issue
        )
        
        try:
            get_client().update_current_span(
                output={
                    "ticket_key": ticket_key,
                    "summary": summary,
                    "has_description": bool(description),
                    "has_acceptance_criteria": bool(acceptance_criteria),
                    "comments_count": len(comments),
                    "linked_issues_count": len(linked_issues)
                }
            )
        except Exception:
            pass
        
        print(f"  [OK] Collected context for: {summary[:50]}...")
        return context
    
    def _extract_acceptance_criteria(self, fields: dict, description: str) -> Optional[str]:
        """
        Extract acceptance criteria from JIRA fields.
        
        Priority:
        1. Custom field (if exists) - this is the canonical source
        2. Parse from description (only if no custom field)
        
        This prevents duplication when AC appears in both places.
        
        Note: Custom field IDs are configured in detection.yaml under jira.acceptance_criteria_fields
        """
        # Load custom field IDs from cached config (with fallback defaults)
        jira_config = self._get_jira_config()
        possible_fields = jira_config.get("acceptance_criteria_fields", [
            "customfield_10016",
            "customfield_10017", 
            "customfield_10020",
        ])
        
        for field_name in possible_fields:
            value = fields.get(field_name)
            if value:
                # Found AC in custom field - use this and don't parse description
                return value
        
        # No custom field AC found - try parsing from description
        if description and "acceptance criteria" in description.lower():
            lower_desc = description.lower()
            ac_start = lower_desc.find("acceptance criteria")
            if ac_start != -1:
                return description[ac_start:]
        
        return None
    
    def _extract_comments(self, ticket_key: str) -> list[dict]:
        """Extract comments from a JIRA ticket."""
        try:
            comments_data = self.client.issue_get_comments(ticket_key)
            comments = comments_data.get("comments", [])
            
            extracted = []
            for comment in comments:
                extracted.append({
                    "author": comment.get("author", {}).get("displayName", "Unknown"),
                    "created": comment.get("created", ""),
                    "body": comment.get("body", ""),
                })
            
            return extracted
            
        except Exception as e:
            print(f"  [WARN] Could not fetch comments: {e}")
            return []
    
    def _extract_parent_epic(self, fields: dict) -> Optional[dict]:
        """Extract parent epic information if this is a story/task."""
        parent = fields.get("parent")
        if parent:
            return {
                "key": parent.get("key"),
                "summary": parent.get("fields", {}).get("summary", ""),
            }
        
        # Get epic link field from cached config (with fallback default)
        jira_config = self._get_jira_config()
        epic_link_field = jira_config.get("epic_link_field", "customfield_10014")
        
        epic_link = fields.get(epic_link_field)
        if epic_link:
            try:
                epic = self.client.issue(epic_link)
                return {
                    "key": epic_link,
                    "summary": epic.get("fields", {}).get("summary", ""),
                }
            except Exception as e:
                print(f"  [WARN] Could not fetch epic {epic_link}: {e}")
                return {"key": epic_link, "summary": ""}
        
        return None
    
    def _extract_linked_issues(self, fields: dict) -> list[dict]:
        """Extract linked issues (blocks, relates to, etc.)."""
        links = fields.get("issuelinks", [])
        extracted = []
        
        for link in links:
            if "inwardIssue" in link:
                issue = link["inwardIssue"]
                direction = link.get("type", {}).get("inward", "relates to")
            elif "outwardIssue" in link:
                issue = link["outwardIssue"]
                direction = link.get("type", {}).get("outward", "relates to")
            else:
                continue
            
            extracted.append({
                "key": issue.get("key"),
                "summary": issue.get("fields", {}).get("summary", ""),
                "relationship": direction,
            })
        
        return extracted