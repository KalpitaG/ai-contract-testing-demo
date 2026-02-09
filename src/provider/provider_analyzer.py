"""
Provider Code Analyzer Module
=============================

Analyzes provider source code to understand:
- Data storage mechanism (database, in-memory, external service)
- Data models and schemas
- How to manipulate test data for state handlers

Usage:
    from provider_analyzer import ProviderAnalyzer
    
    analyzer = ProviderAnalyzer(repo_path="/path/to/provider")
    context = analyzer.analyze()
    
    print(context.storage_type)  # "in_memory", "database", "external"
    print(context.data_files)    # Files that handle data
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from langfuse.decorators import observe


@dataclass
class DataModel:
    """Detected data model in the provider."""
    name: str
    fields: list
    source_file: str
    storage_type: str  # "in_memory", "database", "external"


@dataclass 
class ProviderCodeContext:
    """Context extracted from provider source code."""
    language: str
    framework: str  # express, fastify, flask, spring, etc.
    storage_type: str  # "in_memory", "database", "external"
    data_models: list
    route_files: list
    data_files: list
    setup_hints: list  # Hints for how to set up test data
    source_snippets: dict  # Key code snippets for AI
    
    def format_for_ai(self) -> str:
        """Format provider code context for AI prompt."""
        output = []
        output.append("# Provider Code Analysis")
        output.append(f"\n## Language: {self.language}")
        output.append(f"## Framework: {self.framework}")
        output.append(f"## Data Storage: {self.storage_type}")
        
        output.append("\n## Data Models Detected:")
        for model in self.data_models:
            output.append(f"  - {model.name}: {model.fields} (in {model.source_file})")
        
        output.append("\n## Route Files:")
        for f in self.route_files:
            output.append(f"  - {f}")
        
        output.append("\n## Data Files:")
        for f in self.data_files:
            output.append(f"  - {f}")
        
        output.append("\n## Setup Hints:")
        for hint in self.setup_hints:
            output.append(f"  - {hint}")
        
        output.append("\n## Key Source Code:")
        for filename, content in self.source_snippets.items():
            output.append(f"\n### {filename}")
            output.append("```")
            output.append(content[:3000])  # Limit size
            if len(content) > 3000:
                output.append("... (truncated)")
            output.append("```")
        
        return "\n".join(output)


class ProviderAnalyzer:
    """
    Analyzes provider source code to understand data management.
    
    Detects:
    - Programming language and framework
    - How data is stored (in-memory, database, etc.)
    - Data models and their structure
    - Key files for state handler generation
    """
    
    # Patterns for detecting storage types
    DATABASE_PATTERNS = [
        r'mongoose|mongodb|mongo\.',
        r'sequelize|typeorm|prisma',
        r'pg|postgres|postgresql',
        r'mysql|mysql2',
        r'sqlite|sqlite3',
        r'knex|objection',
        r'\.query\s*\(',
        r'\.execute\s*\(',
        r'repository\.',
        r'@Entity|@Table|@Column',
    ]
    
    IN_MEMORY_PATTERNS = [
        r'const\s+\w+\s*=\s*\[',  # const items = [
        r'let\s+\w+\s*=\s*\[',    # let items = [
        r'=\s*\[\s*\{',           # = [{
        r'Map\(\)',
        r'new\s+Map\(',
        r'new\s+Set\(',
    ]
    
    FRAMEWORK_PATTERNS = {
        'express': [r'express\(\)', r'require\([\'"]express[\'"]\)', r'from\s+[\'"]express[\'"]'],
        'fastify': [r'fastify\(\)', r'require\([\'"]fastify[\'"]\)'],
        'koa': [r'new\s+Koa\(\)', r'require\([\'"]koa[\'"]\)'],
        'flask': [r'Flask\(__name__\)', r'from\s+flask\s+import'],
        'django': [r'from\s+django', r'django\.'],
        'spring': [r'@SpringBootApplication', r'@RestController'],
        'gin': [r'gin\.Default\(\)', r'"github.com/gin-gonic/gin"'],
    }
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        if not self.repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
    
    @observe(name="analyze_provider")
    def analyze(self) -> ProviderCodeContext:
        """
        Analyze the provider repository.
        
        Returns:
            ProviderCodeContext with analysis results
        """
        print(f"\n🔍 Analyzing provider code: {self.repo_path}")
        
        # Detect language
        language = self._detect_language()
        print(f"  📝 Language: {language}")
        
        # Detect framework
        framework = self._detect_framework(language)
        print(f"  🔧 Framework: {framework}")
        
        # Find relevant files
        route_files = self._find_route_files(language)
        data_files = self._find_data_files(language)
        print(f"  📁 Route files: {len(route_files)}")
        print(f"  📁 Data files: {len(data_files)}")
        
        # Analyze storage type
        storage_type, setup_hints = self._detect_storage_type(route_files + data_files)
        print(f"  💾 Storage type: {storage_type}")
        
        # Extract data models
        data_models = self._extract_data_models(data_files + route_files, language)
        print(f"  📊 Data models: {len(data_models)}")
        
        # Get source snippets for AI
        source_snippets = self._get_source_snippets(route_files + data_files)
        
        return ProviderCodeContext(
            language=language,
            framework=framework,
            storage_type=storage_type,
            data_models=data_models,
            route_files=route_files,
            data_files=data_files,
            setup_hints=setup_hints,
            source_snippets=source_snippets
        )
    
    def _detect_language(self) -> str:
        """Detect the primary programming language."""
        if (self.repo_path / "package.json").exists():
            return "javascript"
        elif (self.repo_path / "go.mod").exists():
            return "go"
        elif (self.repo_path / "requirements.txt").exists() or (self.repo_path / "setup.py").exists():
            return "python"
        elif (self.repo_path / "pom.xml").exists() or (self.repo_path / "build.gradle").exists():
            return "java"
        elif (self.repo_path / "Cargo.toml").exists():
            return "rust"
        return "unknown"
    
    def _detect_framework(self, language: str) -> str:
        """Detect the web framework used."""
        # Read all relevant source files
        source_content = ""
        
        for pattern in ["**/*.js", "**/*.ts", "**/*.py", "**/*.java", "**/*.go"]:
            for file in self.repo_path.glob(pattern):
                if "node_modules" in str(file) or "venv" in str(file):
                    continue
                try:
                    source_content += file.read_text(errors='ignore')
                except:
                    pass
        
        # Check for framework patterns
        for framework, patterns in self.FRAMEWORK_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, source_content, re.IGNORECASE):
                    return framework
        
        # Default based on language
        defaults = {
            "javascript": "express",
            "python": "flask",
            "java": "spring",
            "go": "gin"
        }
        return defaults.get(language, "unknown")
    
    def _find_route_files(self, language: str) -> list:
        """Find files that contain route definitions."""
        route_files = []
        
        patterns = {
            "javascript": ["**/routes/**/*.js", "**/controllers/**/*.js", "**/api/**/*.js",
                         "**/routes/**/*.ts", "**/controllers/**/*.ts"],
            "python": ["**/routes/**/*.py", "**/views/**/*.py", "**/api/**/*.py"],
            "go": ["**/handlers/**/*.go", "**/routes/**/*.go", "**/api/**/*.go"],
            "java": ["**/controller/**/*.java", "**/api/**/*.java"]
        }
        
        for pattern in patterns.get(language, []):
            for file in self.repo_path.glob(pattern):
                if "node_modules" not in str(file) and "test" not in str(file).lower():
                    route_files.append(str(file.relative_to(self.repo_path)))
        
        # Also check src directory
        src_dir = self.repo_path / "src"
        if src_dir.exists():
            ext = {"javascript": ".js", "python": ".py", "go": ".go", "java": ".java"}.get(language, ".js")
            for file in src_dir.rglob(f"*{ext}"):
                rel_path = str(file.relative_to(self.repo_path))
                if rel_path not in route_files and "test" not in rel_path.lower():
                    # Check if file contains route definitions
                    content = file.read_text(errors='ignore')
                    if re.search(r'router\.|app\.(get|post|put|patch|delete)|@(Get|Post|Put)', content):
                        route_files.append(rel_path)
        
        return route_files
    
    def _find_data_files(self, language: str) -> list:
        """Find files that contain data models or data storage."""
        data_files = []
        
        patterns = {
            "javascript": ["**/models/**/*.js", "**/data/**/*.js", "**/db/**/*.js",
                         "**/models/**/*.ts", "**/data/**/*.ts"],
            "python": ["**/models/**/*.py", "**/data/**/*.py", "**/db/**/*.py"],
            "go": ["**/models/**/*.go", "**/data/**/*.go"],
            "java": ["**/model/**/*.java", "**/entity/**/*.java"]
        }
        
        for pattern in patterns.get(language, []):
            for file in self.repo_path.glob(pattern):
                if "node_modules" not in str(file):
                    data_files.append(str(file.relative_to(self.repo_path)))
        
        return data_files
    
    def _detect_storage_type(self, files: list) -> tuple:
        """
        Detect how data is stored.
        
        Returns:
            (storage_type, setup_hints)
        """
        all_content = ""
        for file_path in files:
            full_path = self.repo_path / file_path
            if full_path.exists():
                try:
                    all_content += full_path.read_text(errors='ignore')
                except:
                    pass
        
        setup_hints = []
        
        # Check for database patterns
        for pattern in self.DATABASE_PATTERNS:
            if re.search(pattern, all_content, re.IGNORECASE):
                setup_hints.append("Database detected - state handlers need to insert/delete records")
                setup_hints.append("Consider using test transactions or cleanup after each test")
                return "database", setup_hints
        
        # Check for in-memory patterns
        for pattern in self.IN_MEMORY_PATTERNS:
            if re.search(pattern, all_content):
                setup_hints.append("In-memory data storage detected")
                setup_hints.append("State handlers can directly manipulate arrays/objects")
                setup_hints.append("May need to reset data between tests")
                return "in_memory", setup_hints
        
        # Default
        setup_hints.append("Storage type unclear - assume in-memory or mock")
        return "unknown", setup_hints
    
    def _extract_data_models(self, files: list, language: str) -> list:
        """Extract data model definitions from files."""
        models = []
        
        for file_path in files:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            try:
                content = full_path.read_text(errors='ignore')
            except:
                continue
            
            if language == "javascript":
                # Look for object patterns like { id: 1, name: 'Item' }
                # and const items = [...]
                array_match = re.search(
                    r'(?:const|let)\s+(\w+)\s*=\s*\[\s*\{([^}]+)\}',
                    content,
                    re.DOTALL
                )
                if array_match:
                    name = array_match.group(1)
                    fields_str = array_match.group(2)
                    fields = re.findall(r'(\w+)\s*:', fields_str)
                    models.append(DataModel(
                        name=name,
                        fields=fields,
                        source_file=file_path,
                        storage_type="in_memory"
                    ))
            
            elif language == "python":
                # Look for class definitions
                class_match = re.findall(r'class\s+(\w+)', content)
                for class_name in class_match:
                    if not class_name.startswith('_'):
                        models.append(DataModel(
                            name=class_name,
                            fields=[],  # Would need deeper parsing
                            source_file=file_path,
                            storage_type="unknown"
                        ))
        
        return models
    
    def _get_source_snippets(self, files: list) -> dict:
        """Get relevant source code snippets for AI context."""
        snippets = {}
        
        for file_path in files[:10]:  # Limit to 10 files
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            try:
                content = full_path.read_text(errors='ignore')
                # Include the full file if it's small enough
                if len(content) < 5000:
                    snippets[file_path] = content
                else:
                    # Include first 3000 chars
                    snippets[file_path] = content[:3000] + "\n... (truncated)"
            except:
                pass
        
        return snippets


# Convenience function
def analyze_provider(repo_path: str) -> ProviderCodeContext:
    """Analyze a provider repository."""
    analyzer = ProviderAnalyzer(repo_path)
    return analyzer.analyze()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python provider_analyzer.py <repo-path>")
        sys.exit(1)
    
    repo_path = sys.argv[1]
    context = analyze_provider(repo_path)
    
    print("\n" + "="*60)
    print(context.format_for_ai())
