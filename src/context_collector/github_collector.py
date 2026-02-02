"""
GitHub Context Collector
========================
Fetches and structures GitHub PR information for AI consumption.

This module extracts context from Pull Requests including:
- PR metadata (title, description, branch)
- Changed files and diffs
- Commit history
"""

import os
import re
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
from github import Github, Auth
from langfuse import observe, get_client

load_dotenv()


@dataclass
class GitHubContext:
    """Structured container for GitHub PR information."""
    pr_number: int
    title: str
    description: str
    state: str
    source_branch: str
    target_branch: str
    author: str
    labels: list[str] = field(default_factory=list)
    changed_files: list[dict] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    review_comments: list[dict] = field(default_factory=list)
    extracted_ticket_key: Optional[str] = None

    def format_for_ai(self) -> str:
        """Format GitHub context into a string optimized for AI consumption."""
        sections = []
        
        # Header
        sections.append(f"## GitHub PR #{self.pr_number}")
        sections.append(f"**Title:** {self.title}")
        sections.append(f"**Author:** {self.author}")
        sections.append(f"**State:** {self.state}")
        sections.append(f"**Branch:** {self.source_branch} -> {self.target_branch}")
        
        if self.extracted_ticket_key:
            sections.append(f"**Linked Ticket:** {self.extracted_ticket_key}")
        
        if self.labels:
            sections.append(f"**Labels:** {', '.join(self.labels)}")
        
        # PR Description
        if self.description:
            sections.append(f"\n### PR Description\n{self.description}")
        
        # Changed Files Summary
        if self.changed_files:
            sections.append(f"\n### Changed Files ({len(self.changed_files)} files)")
            for file in self.changed_files:
                sections.append(f"- `{file['filename']}` ({file['status']}: +{file['additions']}/-{file['deletions']})")
        
        # Diffs (limited to most relevant files)
        api_files = [f for f in self.changed_files if self._is_api_relevant_file(f['filename'])]
        if api_files:
            sections.append(f"\n### API-Relevant Changes")
            for file in api_files[:3]:  # Limit to 3 files
                sections.append(f"\n#### {file['filename']}")
                sections.append(f"```diff\n{file['patch']}\n```")
        
        # Commits
        if self.commits:
            sections.append(f"\n### Commits ({len(self.commits)} total)")
            for commit in self.commits[-5:]:  # Last 5 commits
                sections.append(f"- `{commit['sha']}` {commit['message'].split(chr(10))[0]}")
        
        # Review Comments
        if self.review_comments:
            sections.append(f"\n### Review Comments ({len(self.review_comments)} total)")
            for comment in self.review_comments[-3:]:  # Last 3 comments
                body = comment['body'][:300] if len(comment['body']) > 300 else comment['body']
                sections.append(f"\n**{comment['author']}** on `{comment['path']}`:\n{body}")
        
        formatted = "\n".join(sections)
        return formatted
    
    # Class-level cache for API-relevant patterns to avoid reloading config repeatedly
    _api_relevant_patterns_cache: Optional[list[str]] = None
    
    def _is_api_relevant_file(self, filename: str) -> bool:
        """
        Check if a file is likely API-relevant for contract testing.
        
        Note: Patterns are configured in detection.yaml under github_pr.api_relevant_patterns
        """
        # Load patterns from config (cached to avoid repeated file reads)
        if GitHubContext._api_relevant_patterns_cache is None:
            try:
                from src.context_processor.repo_analyzer import RepoAnalyzer
                analyzer = RepoAnalyzer()
                github_config = analyzer.config.get("github_pr", {})
                GitHubContext._api_relevant_patterns_cache = github_config.get("api_relevant_patterns", [
                    'openapi', 'swagger', 'api-spec',
                    'route', 'controller', 'handler',
                    'schema', 'dto', 'model',
                    'endpoint', 'service'
                ])
            except Exception:
                # Fallback if config loading fails
                GitHubContext._api_relevant_patterns_cache = [
                    'openapi', 'swagger', 'api-spec',
                    'route', 'controller', 'handler',
                    'schema', 'dto', 'model',
                    'endpoint', 'service'
                ]
        
        filename_lower = filename.lower()
        return any(pattern in filename_lower for pattern in GitHubContext._api_relevant_patterns_cache)


class GitHubCollector:
    """
    Collects context from GitHub Pull Requests.
    
    Usage:
        collector = GitHubCollector()
        context = collector.collect("owner/repo", 123)
        formatted = context.format_for_ai()
    """
    
    def __init__(self):
        """Initialize GitHub client with token from environment."""
        token = os.getenv("GITHUB_TOKEN")
        
        if not token:
            raise ValueError("Missing environment variable: GITHUB_TOKEN")
        
        auth = Auth.Token(token)
        self.client = Github(auth=auth)
    
    @observe(name="github_collect")
    def collect(self, repo_full_name: str, pr_number: int) -> GitHubContext:
        """
        Collect all relevant context from a GitHub PR.
        
        Args:
            repo_full_name: Repository in "owner/repo" format
            pr_number: Pull request number
            
        Returns:
            GitHubContext object with structured PR information
        """
        print(f"[GitHub] Fetching PR: {repo_full_name}#{pr_number}")
        
        try:
            get_client().update_current_span(
                input={"repo": repo_full_name, "pr_number": pr_number}
            )
        except Exception:
            pass
        
        # Get repository and PR
        repo = self.client.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        
        # Extract basic PR info
        title = pr.title
        description = pr.body or ""
        state = pr.state
        source_branch = pr.head.ref
        target_branch = pr.base.ref
        author = pr.user.login
        labels = [label.name for label in pr.labels]
        
        # Extract ticket key from title or branch (e.g., "PROJ-123" from "PROJ-123: Add feature")
        extracted_ticket_key = self._extract_ticket_key(title, source_branch)
        
        # Get changed files with diffs
        changed_files = self._extract_changed_files(pr)
        
        # Get commit history
        commits = self._extract_commits(pr)
        
        # Get review comments
        review_comments = self._extract_review_comments(pr)
        
        context = GitHubContext(
            pr_number=pr_number,
            title=title,
            description=description,
            state=state,
            source_branch=source_branch,
            target_branch=target_branch,
            author=author,
            labels=labels,
            changed_files=changed_files,
            commits=commits,
            review_comments=review_comments,
            extracted_ticket_key=extracted_ticket_key
        )
        
        try:
            get_client().update_current_span(
                output={
                    "pr_number": pr_number,
                    "title": title,
                    "files_changed": len(changed_files),
                    "commits_count": len(commits),
                    "extracted_ticket": extracted_ticket_key
                }
            )
        except Exception:
            pass
        
        print(f"  [OK] Collected context for: {title[:50]}...")
        return context
    
    def _extract_ticket_key(self, title: str, branch: str) -> Optional[str]:
        """
        Extract JIRA ticket key from PR title or branch name.
        
        Common patterns:
        - "PROJ-123: Add feature" (title)
        - "feature/PROJ-123-add-feature" (branch)
        - "[PROJ-123] Add feature" (title)
        """
        # Pattern matches: ABC-123, PROJ-1, etc.
        pattern = r'([A-Z]+-\d+)'
        
        # Try title first
        match = re.search(pattern, title)
        if match:
            return match.group(1)
        
        # Try branch name
        match = re.search(pattern, branch.upper())
        if match:
            return match.group(1)
        
        return None
    
    def _extract_changed_files(self, pr) -> list[dict]:
        """
        Extract changed files with their diffs.
        
        For AI context, we want to know:
        - Which files changed
        - What type of change (added, modified, deleted)
        - The actual diff (limited to avoid token explosion)
        """
        files = []
        
        for file in pr.get_files():
            # Limit patch size to avoid huge diffs
            patch = file.patch or ""
            if len(patch) > 2000:
                patch = patch[:2000] + "\n... [truncated]"
            
            files.append({
                "filename": file.filename,
                "status": file.status,  # added, modified, removed, renamed
                "additions": file.additions,
                "deletions": file.deletions,
                "patch": patch
            })
        
        return files
    
    def _extract_commits(self, pr) -> list[dict]:
        """
        Extract commit history for the PR.
        
        Commits show the logical progression of changes.
        """
        commits = []
        
        for commit in pr.get_commits():
            commits.append({
                "sha": commit.sha[:7],  # Short SHA
                "message": commit.commit.message or "",
                "author": commit.commit.author.name if commit.commit.author else "Unknown"
            })
        
        return commits
    
    def _extract_review_comments(self, pr) -> list[dict]:
        """
        Extract review comments from the PR.
        
        Review comments often contain important context about
        code decisions and requested changes.
        """
        comments = []
        
        try:
            for comment in pr.get_review_comments():
                comments.append({
                    "author": comment.user.login,
                    "body": comment.body,
                    "path": comment.path,
                    "created_at": comment.created_at.isoformat() if comment.created_at else ""
                })
        except Exception as e:
            print(f"  [WARN] Could not fetch review comments: {e}")
        
        return comments