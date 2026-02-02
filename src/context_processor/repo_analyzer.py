"""
Repository Analyzer
===================
Analyzes repository structure to detect language, find OpenAPI specs,
and extract relevant context for AI-powered contract test generation.

Uses rules defined in config/detection.yaml for all detection logic,
making it easy to add new languages or patterns without code changes.
"""

import os
import re
import fnmatch
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv
import yaml
from github import Github
from langfuse import observe, get_client

load_dotenv()


@dataclass
class PactLibraryInfo:
    """Information about the Pact library for a specific language."""
    package: str
    import_statement: str
    test_framework: str
    file_extension: str
    file_naming: str
    example_test_structure: str


@dataclass
class RepoAnalysis:
    """Result of analyzing a repository."""
    # Language detection results
    detected_language: str
    language_confidence: str  # high, medium, low
    pact_library: Optional[PactLibraryInfo]
    
    # OpenAPI spec detection results
    all_specs_found: list[str] = field(default_factory=list)
    relevant_specs: list[str] = field(default_factory=list)
    common_specs: list[str] = field(default_factory=list)
    spec_match_strategy: str = ""  # Which strategy matched
    
    # Ticket extraction results
    ticket_key: Optional[str] = None
    ticket_source: Optional[str] = None  # "pr_title" or "branch_name"
    
    # Test output configuration
    test_directory: str = ""
    test_file_naming: str = ""
    
    def format_for_ai(self) -> str:
        """Format analysis results for AI consumption."""
        lines = [
            "=== REPOSITORY ANALYSIS ===",
            "",
            "LANGUAGE:",
            f"  Detected: {self.detected_language}",
            f"  Confidence: {self.language_confidence}",
        ]
        
        if self.pact_library:
            lines.extend([
                "",
                "PACT LIBRARY:",
                f"  Package: {self.pact_library.package}",
                f"  Test Framework: {self.pact_library.test_framework}",
                f"  File Extension: {self.pact_library.file_extension}",
                "",
                "IMPORT STATEMENT:",
                self.pact_library.import_statement,
            ])
        
        lines.extend([
            "",
            "OPENAPI SPECS:",
            f"  Total Found: {len(self.all_specs_found)}",
            f"  Relevant to PR: {len(self.relevant_specs)}",
            f"  Match Strategy: {self.spec_match_strategy}",
        ])
        
        if self.relevant_specs:
            lines.append("  Relevant Specs:")
            for spec in self.relevant_specs:
                lines.append(f"    - {spec}")
        
        if self.common_specs:
            lines.append("  Common/Shared Specs (always included):")
            for spec in self.common_specs:
                lines.append(f"    - {spec}")
        
        if self.ticket_key:
            lines.extend([
                "",
                "JIRA TICKET:",
                f"  Key: {self.ticket_key}",
                f"  Source: {self.ticket_source}",
            ])
        
        lines.extend([
            "",
            "TEST OUTPUT:",
            f"  Directory: {self.test_directory}",
            f"  File Naming: {self.test_file_naming}",
        ])
        
        return "\n".join(lines)


class RepoAnalyzer:
    """
    Analyzes repository structure using rules from detection.yaml.
    
    Responsibilities:
    - Detect programming language from package files
    - Find OpenAPI specifications in the repository
    - Match specs to PR context (which specs are relevant)
    - Extract JIRA ticket from PR title/branch
    
    Usage:
        analyzer = RepoAnalyzer()
        analysis = analyzer.analyze("owner/repo", pr_number=123)
        print(analysis.detected_language)  # "go"
        print(analysis.relevant_specs)     # ["api/specs/email-service-api.yaml"]
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize RepoAnalyzer with configuration.
        
        Args:
            config_path: Path to detection.yaml. If None, uses default location.
        """
        if config_path is None:
            # Default: look in root config/ folder
            # Path: src/context_processor/repo_analyzer.py -> ../../config/detection.yaml
            this_dir = Path(__file__).parent
            config_path = this_dir.parent.parent / "config" / "detection.yaml"
        
        self.config = self._load_config(config_path)
        self.github = Github(os.getenv("GITHUB_TOKEN"))
    
    def _load_config(self, config_path: str | Path) -> dict:
        """Load detection configuration from YAML file."""
        config_path = Path(config_path)
        
        if not config_path.exists():
            raise FileNotFoundError(
                f"Detection config not found: {config_path}\n"
                "Please ensure config/detection.yaml exists."
            )
        
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    
    @observe(name="repo_analyze")
    def analyze(self, repo: str, pr_number: int) -> RepoAnalysis:
        """
        Perform full repository analysis.
        
        Args:
            repo: Repository name (e.g., "owner/repo")
            pr_number: Pull request number
            
        Returns:
            RepoAnalysis with all detected information
        """
        print(f"[RepoAnalyzer] Analyzing {repo} PR #{pr_number}")
        
        # Get repository and PR objects
        gh_repo = self.github.get_repo(repo)
        pr = gh_repo.get_pull(pr_number)
        
        # Get list of files changed in PR
        changed_files = [f.filename for f in pr.get_files()]
        print(f"[RepoAnalyzer] PR has {len(changed_files)} changed files")
        
        # Step 1: Detect language
        language, confidence = self._detect_language(gh_repo)
        pact_library = self._get_pact_library(language)
        print(f"[RepoAnalyzer] Detected language: {language} ({confidence} confidence)")
        
        # Step 2: Find all OpenAPI specs
        all_specs = self._find_openapi_specs(gh_repo)
        print(f"[RepoAnalyzer] Found {len(all_specs)} OpenAPI specs")
        
        # Step 3: Match specs to PR context
        relevant_specs, match_strategy = self._match_specs_to_pr(
            all_specs, pr, changed_files
        )
        print(f"[RepoAnalyzer] Matched {len(relevant_specs)} relevant specs using '{match_strategy}'")
        
        # Step 4: Find common/shared specs
        common_specs = self._find_common_specs(all_specs)
        if common_specs:
            print(f"[RepoAnalyzer] Found {len(common_specs)} common/shared specs")
        
        # Step 5: Extract JIRA ticket
        ticket_key, ticket_source = self._extract_ticket(pr.title, pr.head.ref)
        if ticket_key:
            print(f"[RepoAnalyzer] Extracted ticket: {ticket_key} from {ticket_source}")
        else:
            print("[RepoAnalyzer] No JIRA ticket found")
        
        # Step 6: Get test output configuration
        test_directory = self._get_test_directory(language)
        test_file_naming = self._get_test_file_naming(language)
        
        # Add metadata to Langfuse trace
        try:
            get_client().update_current_span(
                metadata={
                    "repo": repo,
                    "pr_number": pr_number,
                    "detected_language": language,
                    "language_confidence": confidence,
                    "specs_found": len(all_specs),
                    "specs_matched": len(relevant_specs),
                    "match_strategy": match_strategy,
                    "ticket_found": ticket_key is not None
                }
            )
        except Exception:
            pass
        
        return RepoAnalysis(
            detected_language=language,
            language_confidence=confidence,
            pact_library=pact_library,
            all_specs_found=all_specs,
            relevant_specs=relevant_specs,
            common_specs=common_specs,
            spec_match_strategy=match_strategy,
            ticket_key=ticket_key,
            ticket_source=ticket_source,
            test_directory=test_directory,
            test_file_naming=test_file_naming
        )
    
    @observe(name="detect_language")
    def _detect_language(self, gh_repo) -> tuple[str, str]:
        """
        Detect repository programming language from package files.
        
        Returns:
            Tuple of (language, confidence)
        """
        # Get root directory contents
        try:
            contents = gh_repo.get_contents("")
            root_files = [c.name for c in contents if c.type == "file"]
        except Exception as e:
            print(f"[RepoAnalyzer] Warning: Could not list repo contents: {e}")
            return ("unknown", "low")
        
        # Check each indicator in priority order
        indicators = self.config["language_detection"]["indicators"]
        
        for indicator in indicators:
            if indicator["file"] in root_files:
                return (indicator["language"], indicator["confidence"])
        
        # No match found
        return ("unknown", "low")
    
    def _get_pact_library(self, language: str) -> Optional[PactLibraryInfo]:
        """Get Pact library configuration for detected language."""
        libraries = self.config["language_detection"].get("pact_libraries", {})
        
        if language not in libraries:
            return None
        
        lib_config = libraries[language]
        return PactLibraryInfo(
            package=lib_config.get("package", ""),
            import_statement=lib_config.get("import_statement", ""),
            test_framework=lib_config.get("test_framework", ""),
            file_extension=lib_config.get("file_extension", ""),
            file_naming=lib_config.get("file_naming", ""),
            example_test_structure=lib_config.get("example_test_structure", "")
        )
    
    @observe(name="find_openapi_specs")
    def _find_openapi_specs(self, gh_repo) -> list[str]:
        """
        Find all OpenAPI specification files in the repository.
        
        Returns:
            List of file paths to OpenAPI specs
        """
        specs_found = []
        openapi_config = self.config["openapi_detection"]
        search_paths = openapi_config["search_paths"]
        file_patterns = openapi_config["file_patterns"]
        exclude_patterns = openapi_config.get("exclude_patterns", [])
        
        for search_path in search_paths:
            try:
                if search_path == ".":
                    contents = gh_repo.get_contents("")
                else:
                    contents = gh_repo.get_contents(search_path)
                
                # Handle single file returned (not a directory)
                if not isinstance(contents, list):
                    contents = [contents]
                
                for content in contents:
                    if content.type != "file":
                        continue
                    
                    file_name = content.name
                    file_path = content.path
                    
                    # Check if file matches any pattern
                    matches_pattern = any(
                        fnmatch.fnmatch(file_name, pattern)
                        for pattern in file_patterns
                    )
                    
                    if not matches_pattern:
                        continue
                    
                    # Check if file should be excluded
                    is_excluded = any(
                        fnmatch.fnmatch(file_path, pattern) or 
                        fnmatch.fnmatch(file_name, pattern)
                        for pattern in exclude_patterns
                    )
                    
                    if is_excluded:
                        continue
                    
                    # Verify it's actually an OpenAPI spec by checking content
                    if self._is_openapi_spec(gh_repo, file_path):
                        specs_found.append(file_path)
                        
            except Exception as e:
                # Directory doesn't exist, skip silently
                if "404" not in str(e):
                    print(f"[RepoAnalyzer] Warning: Error searching {search_path}: {e}")
                continue
        
        return specs_found
    
    def _is_openapi_spec(self, gh_repo, file_path: str) -> bool:
        """
        Verify a file is actually an OpenAPI spec by checking its content.
        
        Args:
            gh_repo: GitHub repository object
            file_path: Path to the file
            
        Returns:
            True if file contains OpenAPI/Swagger indicators
        """
        try:
            content = gh_repo.get_contents(file_path)
            # Only check first 500 chars to avoid downloading huge files
            file_content = content.decoded_content.decode("utf-8")[:500]
            
            indicators = self.config["openapi_detection"].get("content_indicators", [])
            
            for indicator in indicators:
                if indicator in file_content:
                    return True
            
            return False
            
        except Exception:
            # If we can't read the file, assume it's not a spec
            return False
    
    @observe(name="match_specs_to_pr")
    def _match_specs_to_pr(
        self,
        all_specs: list[str],
        pr,
        changed_files: list[str]
    ) -> tuple[list[str], str]:
        """
        Determine which OpenAPI specs are relevant to the PR.
        
        Uses multiple strategies in priority order:
        1. Spec file itself was changed
        2. Match changed code paths to spec names
        3. Match PR title/branch to spec names
        4. Fallback: include all (limited)
        
        Returns:
            Tuple of (relevant_specs, strategy_used)
        """
        if not all_specs:
            return ([], "no_specs_found")
        
        # Strategy 1: Check if any spec files were changed in the PR
        changed_specs = [f for f in changed_files if f in all_specs]
        if changed_specs:
            return (changed_specs, "changed_spec")
        
        # Strategy 2: Match changed file paths to spec names
        matched_specs = self._match_by_path(all_specs, changed_files)
        if matched_specs:
            return (matched_specs, "path_match")
        
        # Strategy 3: Match PR title or branch name to spec names
        matched_specs = self._match_by_title_branch(all_specs, pr.title, pr.head.ref)
        if matched_specs:
            return (matched_specs, "title_branch_match")
        
        # Strategy 4: Fallback - include all specs up to max limit
        fallback_config = self.config["spec_matching"].get("fallback", {})
        max_specs = fallback_config.get("max_specs", 3)
        
        if len(all_specs) <= max_specs:
            return (all_specs, "fallback_all")
        else:
            # Return first N specs (could be improved with better heuristics)
            return (all_specs[:max_specs], "fallback_limited")
    
    def _match_by_path(self, specs: list[str], changed_files: list[str]) -> list[str]:
        """
        Match specs by analyzing changed file paths.
        
        Example: If PR changes internal/galleries/handler.go,
        look for a spec containing "galleries" in its name.
        """
        matched = set()
        
        # Extract potential feature names from changed file paths
        feature_names = set()
        for file_path in changed_files:
            parts = file_path.split("/")
            # Look for meaningful directory names (not generic ones)
            generic_dirs = {"src", "lib", "pkg", "internal", "cmd", "main", "java", "kotlin", "test", "tests"}
            for part in parts:
                if part and part not in generic_dirs and not part.startswith("."):
                    # Remove file extension if it's the last part
                    if "." in part:
                        part = part.rsplit(".", 1)[0]
                    feature_names.add(part.lower())
        
        # Match feature names to spec file names
        for spec in specs:
            spec_name = Path(spec).stem.lower()  # e.g., "email-service-api" -> "email-service-api"
            
            for feature in feature_names:
                # Check if feature name appears in spec name
                if feature in spec_name or spec_name.startswith(feature):
                    matched.add(spec)
                    break
        
        return list(matched)
    
    def _match_by_title_branch(
        self,
        specs: list[str],
        pr_title: str,
        branch_name: str
    ) -> list[str]:
        """
        Match specs by analyzing PR title and branch name.
        
        Example: Branch "feature/galleries-update" might match "galleries-api.yaml"
        """
        matched = set()
        
        # Combine and normalize title and branch
        search_text = f"{pr_title} {branch_name}".lower()
        
        for spec in specs:
            spec_name = Path(spec).stem.lower()
            # Remove common suffixes to get core name
            for suffix in ["-api", "_api", "-spec", "_spec", "-openapi"]:
                spec_name = spec_name.replace(suffix, "")
            
            # Check if spec name appears in title or branch
            if spec_name in search_text:
                matched.add(spec)
        
        return list(matched)
    
    def _find_common_specs(self, all_specs: list[str]) -> list[str]:
        """
        Find common/shared spec files that should always be included.
        
        These typically contain shared schemas and definitions.
        """
        common_files = self.config["openapi_detection"].get("common_files", [])
        common_specs = []
        
        for spec in all_specs:
            spec_name = Path(spec).name
            if spec_name in common_files:
                common_specs.append(spec)
        
        return common_specs
    
    @observe(name="extract_ticket")
    def _extract_ticket(
        self,
        pr_title: str,
        branch_name: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Extract JIRA ticket key from PR title or branch name.
        
        Returns:
            Tuple of (ticket_key, source) where source is "pr_title" or "branch_name"
        """
        ticket_config = self.config["ticket_extraction"]
        pattern = ticket_config["pattern"]
        sources = ticket_config.get("sources", ["pr_title", "branch_name"])
        placeholder_patterns = ticket_config.get("placeholder_patterns", [])
        
        # Build lookup for sources
        source_values = {
            "pr_title": pr_title,
            "branch_name": branch_name
        }
        
        # Check each source in priority order
        for source in sources:
            text = source_values.get(source, "")
            match = re.search(pattern, text)
            
            if match:
                ticket = match.group(1)
                
                # Check if it's a placeholder ticket
                is_placeholder = any(
                    re.search(placeholder, ticket)
                    for placeholder in placeholder_patterns
                )
                
                if not is_placeholder:
                    return (ticket, source)
        
        return (None, None)
    
    def _get_test_directory(self, language: str) -> str:
        """Get the default test directory for the detected language."""
        directories = self.config["test_output"].get("default_directories", {})
        return directories.get(language, "tests/pact")
    
    def _get_test_file_naming(self, language: str) -> str:
        """Get the test file naming convention for the detected language."""
        conventions = self.config["test_output"].get("naming_conventions", {})
        return conventions.get(language, "{consumer}_{provider}_pact_test")

