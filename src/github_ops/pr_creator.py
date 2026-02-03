"""
GitHub Operations - PR Creator
==============================
Creates Pull Requests with AI-generated contract tests.

This module:
1. Creates a new branch from the PR's head branch
2. Commits generated test files
3. Creates a PR targeting the original PR's branch
4. Posts comments back to the original PR

This is designed to work both:
- Locally (for testing)
- In GitHub Actions (production)
"""

import os
import base64
from typing import Optional
from dataclasses import dataclass
from github import Github, GithubException
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PRCreationResult:
    """Result of PR creation operation."""
    success: bool
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    branch_name: Optional[str] = None
    error: Optional[str] = None
    already_exists: bool = False


@dataclass  
class GeneratedTestFile:
    """Represents a generated test file to be committed."""
    filename: str
    content: str
    path: str = "tests/contract-tests"  # Default path in target repo


class PRCreator:
    """
    Creates Pull Requests with generated contract tests.
    
    Usage:
        creator = PRCreator()
        result = creator.create_test_pr(
            repo="owner/repo",
            pr_number=123,
            test_files=[GeneratedTestFile(filename="test.js", content="...")],
        )
        
        if result.success:
            print(f"Created PR: {result.pr_url}")
    """
    
    def __init__(self, token: Optional[str] = None):
        """
        Initialize the PR creator.
        
        Args:
            token: GitHub token (PAT with repo scope). 
                   If not provided, uses GITHUB_TOKEN env var.
        """
        self.token = token or os.getenv("GITHUB_TOKEN") or os.getenv("PAT_TOKEN")
        if not self.token:
            raise ValueError("GitHub token required. Set GITHUB_TOKEN or PAT_TOKEN env var.")
        
        self.github = Github(self.token)
        self.bot_name = "AI Contract Testing Bot"
        self.bot_email = "ai-contract-testing@users.noreply.github.com"
    
    def create_test_pr(
        self,
        repo: str,
        pr_number: int,
        test_files: list[GeneratedTestFile],
        base_path: str = "tests/contract-tests"
    ) -> PRCreationResult:
        """
        Create a PR with generated test files.
        
        Args:
            repo: Repository in "owner/repo" format
            pr_number: Original PR number that triggered generation
            test_files: List of generated test files to commit
            base_path: Base path in repo for test files
            
        Returns:
            PRCreationResult with PR details or error
        """
        try:
            repository = self.github.get_repo(repo)
            
            # Get the original PR
            original_pr = repository.get_pull(pr_number)
            head_branch = original_pr.head.ref
            head_sha = original_pr.head.sha
            
            # Create branch name for tests
            test_branch = f"contract-tests/pr-{pr_number}"
            
            # Check if branch already exists
            branch_exists = self._branch_exists(repository, test_branch)
            
            if branch_exists:
                # Get existing branch
                ref = repository.get_git_ref(f"heads/{test_branch}")
                print(f"Branch {test_branch} already exists, updating...")
            else:
                # Create new branch from PR head
                print(f"Creating new branch {test_branch} from {head_branch}")
                ref = repository.create_git_ref(
                    ref=f"refs/heads/{test_branch}",
                    sha=head_sha
                )
            
            # Commit test files
            commit_sha = self._commit_files(
                repository=repository,
                branch=test_branch,
                files=test_files,
                base_path=base_path,
                pr_number=pr_number
            )
            
            if not commit_sha:
                return PRCreationResult(
                    success=False,
                    error="Failed to commit files"
                )
            
            # Create or update PR
            pr_result = self._create_or_update_pr(
                repository=repository,
                test_branch=test_branch,
                target_branch=head_branch,
                pr_number=pr_number,
                branch_existed=branch_exists
            )
            
            return pr_result
            
        except GithubException as e:
            return PRCreationResult(
                success=False,
                error=f"GitHub API error: {e.data.get('message', str(e))}"
            )
        except Exception as e:
            return PRCreationResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def _branch_exists(self, repository, branch_name: str) -> bool:
        """Check if a branch exists in the repository."""
        try:
            repository.get_branch(branch_name)
            return True
        except GithubException:
            return False
    
    def _commit_files(
        self,
        repository,
        branch: str,
        files: list[GeneratedTestFile],
        base_path: str,
        pr_number: int
    ) -> Optional[str]:
        """
        Commit multiple files to a branch.
        
        Returns the commit SHA if successful, None otherwise.
        """
        try:
            # Get the current commit on the branch
            ref = repository.get_git_ref(f"heads/{branch}")
            current_sha = ref.object.sha
            current_commit = repository.get_git_commit(current_sha)
            base_tree = current_commit.tree
            
            # Create tree elements for each file
            tree_elements = []
            for test_file in files:
                file_path = f"{base_path}/{test_file.filename}"
                
                # Create blob for file content
                blob = repository.create_git_blob(
                    content=base64.b64encode(test_file.content.encode()).decode(),
                    encoding="base64"
                )
                
                tree_elements.append({
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob.sha
                })
            
            # Create new tree
            new_tree = repository.create_git_tree(
                tree=tree_elements,
                base_tree=base_tree
            )
            
            # Create commit
            commit_message = f"""AI-generated contract tests for PR #{pr_number}

These Pact contract tests were automatically generated.
Please review before merging.

Files:
{chr(10).join(f'- {f.filename}' for f in files)}
"""
            
            new_commit = repository.create_git_commit(
                message=commit_message,
                tree=new_tree,
                parents=[current_commit]
            )
            
            # Update branch reference
            ref.edit(sha=new_commit.sha)
            
            print(f"Committed {len(files)} file(s) to {branch}")
            return new_commit.sha
            
        except GithubException as e:
            print(f"Failed to commit files: {e}")
            return None
    
    def _create_or_update_pr(
        self,
        repository,
        test_branch: str,
        target_branch: str,
        pr_number: int,
        branch_existed: bool
    ) -> PRCreationResult:
        """Create a new PR or return existing one."""
        
        # Check for existing PR
        existing_prs = repository.get_pulls(
            state="open",
            head=f"{repository.owner.login}:{test_branch}"
        )
        
        for pr in existing_prs:
            if pr.base.ref == target_branch:
                print(f"Found existing PR #{pr.number}")
                return PRCreationResult(
                    success=True,
                    pr_url=pr.html_url,
                    pr_number=pr.number,
                    branch_name=test_branch,
                    already_exists=True
                )
        
        # Create new PR
        pr_body = f"""## 🤖 AI-Generated Contract Tests

This PR contains Pact contract tests automatically generated for PR #{pr_number}.

### What to do:
1. **Review** the generated test code
2. **Approve and merge** this PR into your feature branch if tests look correct
3. **Request changes** by commenting `ai-revise: <your feedback>` on PR #{pr_number}

### How it works:
- Tests are generated based on your OpenAPI spec and PR changes
- They verify the contract between consumer and provider
- Merge these tests before merging your feature PR

---
*Generated by AI Contract Testing Workflow powered by Gemini*
"""
        
        try:
            new_pr = repository.create_pull(
                title=f"🧪 Contract Tests for PR #{pr_number}",
                body=pr_body,
                head=test_branch,
                base=target_branch
            )
            
            print(f"Created new PR #{new_pr.number}: {new_pr.html_url}")
            
            return PRCreationResult(
                success=True,
                pr_url=new_pr.html_url,
                pr_number=new_pr.number,
                branch_name=test_branch,
                already_exists=False
            )
            
        except GithubException as e:
            return PRCreationResult(
                success=False,
                error=f"Failed to create PR: {e.data.get('message', str(e))}"
            )
    
    def comment_on_pr(
        self,
        repo: str,
        pr_number: int,
        comment: str
    ) -> bool:
        """
        Post a comment on a PR.
        
        Args:
            repo: Repository in "owner/repo" format
            pr_number: PR number to comment on
            comment: Comment body (markdown supported)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            repository = self.github.get_repo(repo)
            pr = repository.get_pull(pr_number)
            pr.create_issue_comment(comment)
            print(f"Posted comment on PR #{pr_number}")
            return True
        except GithubException as e:
            print(f"Failed to post comment: {e}")
            return False
    
    def post_success_comment(
        self,
        repo: str,
        original_pr_number: int,
        test_pr_url: str
    ) -> bool:
        """Post a success comment with link to test PR."""
        comment = f"""## ✅ Contract Tests Generated

I've analyzed your PR and generated Pact contract tests.

**Test PR:** {test_pr_url}

### Next Steps:
1. Review the generated tests in the linked PR
2. If the tests look good, merge the test PR into your feature branch
3. If you need changes, comment here with: `ai-revise: <your feedback>`

---
*Generated by AI Contract Testing Workflow*
"""
        return self.comment_on_pr(repo, original_pr_number, comment)
    
    def post_skip_comment(
        self,
        repo: str,
        pr_number: int,
        reason: str
    ) -> bool:
        """Post a comment explaining why tests were skipped."""
        comment = f"""## ℹ️ Contract Test Generation Skipped

I analyzed your PR but did not generate contract tests.

**Reason:** {reason}

This usually means:
- No API-related changes were detected in the PR
- No OpenAPI specification was found
- The changes don't require contract testing

If you believe this is incorrect, please check:
1. Your PR contains API endpoint changes
2. An OpenAPI spec exists in `oas/`, `api/`, or `docs/` folder

---
*Generated by AI Contract Testing Workflow*
"""
        return self.comment_on_pr(repo, pr_number, comment)
    
    def post_error_comment(
        self,
        repo: str,
        pr_number: int,
        error: str
    ) -> bool:
        """Post a comment about an error during generation."""
        comment = f"""## ❌ Contract Test Generation Failed

An error occurred while generating contract tests.

**Error:** {error}

Please check the workflow logs for more details or contact the maintainers.

---
*Generated by AI Contract Testing Workflow*
"""
        return self.comment_on_pr(repo, pr_number, comment)


# =============================================================================
# CLI for Testing
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test PR Creator")
    parser.add_argument("repo", help="Repository in owner/repo format")
    parser.add_argument("pr", type=int, help="PR number")
    parser.add_argument("--comment", help="Post a test comment")
    parser.add_argument("--create-test-pr", action="store_true", help="Create a test PR with dummy content")
    
    args = parser.parse_args()
    
    creator = PRCreator()
    
    if args.comment:
        success = creator.comment_on_pr(args.repo, args.pr, args.comment)
        print(f"Comment posted: {success}")
    
    if args.create_test_pr:
        # Create a dummy test file for testing
        test_files = [
            GeneratedTestFile(
                filename="example.pact.test.js",
                content="""// Example generated test
const { Pact } = require('@pact-foundation/pact');

describe('Example Contract Test', () => {
  it('should work', () => {
    expect(true).toBe(true);
  });
});
"""
            )
        ]
        
        result = creator.create_test_pr(
            repo=args.repo,
            pr_number=args.pr,
            test_files=test_files
        )
        
        if result.success:
            print(f"Created PR: {result.pr_url}")
        else:
            print(f"Failed: {result.error}")
