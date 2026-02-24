"""
Provider Code Analyzer Module
=============================

Analyzes provider source code to understand:
- Data storage mechanism (database, in-memory, external service)
- Data models and schemas
- Module exports (what is importable vs what is closure-scoped)
- How to manipulate test data for state handlers

Usage:
    from provider_analyzer import ProviderAnalyzer
    
    analyzer = ProviderAnalyzer(repo_path="/path/to/provider")
    context = analyzer.analyze()
    
    print(context.storage_type)  # "in_memory", "database", "external"
    print(context.data_files)    # Files that handle data
    print(context.export_analysis)  # What each file exports
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from langfuse import observe


@dataclass
class DataModel:
    """Detected data model in the provider."""
    name: str
    fields: list
    source_file: str
    storage_type: str  # "in_memory", "database", "external"


@dataclass
class FileExportInfo:
    """What a source file exports and what data it contains."""
    file_path: str
    exports: list           # What module.exports contains (e.g., ['router'])
    export_type: str        # 'router', 'object', 'function', 'class', 'data', 'mixed'
    data_arrays: list       # In-memory data arrays found (e.g., ['items', 'users'])
    exported_data: list     # Data arrays that ARE exported (accessible via require)
    non_exported_data: list # Data arrays that are NOT exported (closure-scoped, inaccessible)
    has_test_exports: bool  # Whether file has ._testData or similar test hooks


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
    export_analysis: list = field(default_factory=list)  # FileExportInfo per file
    data_access_strategy: str = "unknown"  # Recommended strategy for state handlers
    
    def format_for_ai(self) -> str:
        """Format provider code context for AI prompt."""
        output = []
        output.append("# Provider Code Analysis")
        output.append(f"\n## Language: {self.language}")
        output.append(f"## Framework: {self.framework}")
        output.append(f"## Data Storage: {self.storage_type}")
        output.append(f"## Recommended Data Access Strategy: {self.data_access_strategy}")
        
        output.append("\n## Data Models Detected:")
        for model in self.data_models:
            output.append(f"  - {model.name}: {model.fields} (in {model.source_file})")
        
        output.append("\n## Route Files:")
        for f in self.route_files:
            output.append(f"  - {f}")
        
        output.append("\n## Data Files:")
        for f in self.data_files:
            output.append(f"  - {f}")
        
        # =====================================================================
        # CRITICAL: Export analysis tells the AI what's actually importable
        # =====================================================================
        output.append("\n## Module Export Analysis (CRITICAL — read before writing state handlers):")
        
        has_non_exported_data = False
        for info in self.export_analysis:
            output.append(f"\n### {info.file_path}")
            output.append(f"  Exports: {info.exports} (type: {info.export_type})")
            output.append(f"  Data arrays in file: {info.data_arrays}")
            
            if info.exported_data:
                output.append(f"  ✅ ACCESSIBLE data (exported): {info.exported_data}")
                output.append(f"     → You CAN use: require('{info.file_path}').{info.exported_data[0]}")
            
            if info.non_exported_data:
                has_non_exported_data = True
                output.append(f"  ❌ INACCESSIBLE data (closure-scoped, NOT exported): {info.non_exported_data}")
                output.append(f"     → require('{info.file_path}').{info.non_exported_data[0]} will be UNDEFINED")
                output.append(f"     → You CANNOT import these arrays — they are local variables")
            
            if info.has_test_exports:
                output.append(f"  🧪 Test hooks available: check ._testData or similar exports")
        
        if has_non_exported_data:
            output.append("\n## ⚠️  DATA ACCESS WARNING:")
            output.append("Some data arrays are closure-scoped (local variables) and NOT exported.")
            output.append("You CANNOT use `require(file).arrayName` for these — it will be undefined.")
            output.append(f"Recommended strategy: {self.data_access_strategy}")
            
            if self.data_access_strategy == "rest_api":
                output.append("\nUse the provider's own REST API endpoints to set up test data:")
                output.append("  - POST endpoints to create data")
                output.append("  - DELETE endpoints to remove data")
                output.append("  - Use http.request() or fetch() to call these endpoints")
                output.append("  - The provider server runs on the test PORT (e.g., 3002)")
        
        output.append("\n## Setup Hints:")
        for hint in self.setup_hints:
            output.append(f"  - {hint}")
        
        output.append("\n## Key Source Code:")
        for filename, content in self.source_snippets.items():
            output.append(f"\n### {filename}")
            output.append("```")
            output.append(content[:3000])
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
    - Module exports (what is actually importable)
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
        print(f"  📂 Data files: {len(data_files)}")
        
        # Analyze storage type
        storage_type, setup_hints = self._detect_storage_type(route_files + data_files)
        print(f"  💾 Storage type: {storage_type}")
        
        # Extract data models
        data_models = self._extract_data_models(data_files + route_files, language)
        print(f"  📦 Data models: {len(data_models)}")
        
        # Analyze exports (CRITICAL for state handler generation)
        export_analysis = self._analyze_exports(route_files + data_files, language)
        
        # Determine recommended data access strategy
        data_access_strategy = self._determine_access_strategy(
            export_analysis, storage_type, framework
        )
        print(f"  🔑 Data access strategy: {data_access_strategy}")
        
        # Update setup hints based on export analysis
        setup_hints = self._refine_setup_hints(
            setup_hints, export_analysis, data_access_strategy
        )
        
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
            source_snippets=source_snippets,
            export_analysis=export_analysis,
            data_access_strategy=data_access_strategy
        )
    
    def _detect_language(self) -> str:
        """Detect the primary programming language."""
        if (self.repo_path / "package.json").exists():
            # Check for TypeScript
            if (self.repo_path / "tsconfig.json").exists():
                return "typescript"
            return "javascript"
        elif (self.repo_path / "go.mod").exists():
            return "go"
        elif (self.repo_path / "requirements.txt").exists() or (self.repo_path / "setup.py").exists():
            return "python"
        elif (self.repo_path / "pom.xml").exists() or (self.repo_path / "build.gradle").exists():
            # Check for Kotlin
            for kt_file in self.repo_path.rglob("*.kt"):
                if "test" not in str(kt_file).lower():
                    return "kotlin"
            return "java"
        elif (self.repo_path / "Cargo.toml").exists():
            return "rust"
        return "unknown"
    
    def _detect_framework(self, language: str) -> str:
        """Detect the web framework used."""
        source_content = ""
        
        for pattern in ["**/*.js", "**/*.ts", "**/*.py", "**/*.java", "**/*.go"]:
            for file in self.repo_path.glob(pattern):
                if "node_modules" in str(file) or "venv" in str(file):
                    continue
                try:
                    source_content += file.read_text(errors='ignore')
                except Exception:
                    pass
        
        for framework, patterns in self.FRAMEWORK_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, source_content, re.IGNORECASE):
                    return framework
        
        defaults = {
            "javascript": "express",
            "typescript": "express",
            "python": "flask",
            "java": "spring",
            "kotlin": "spring",
            "go": "gin"
        }
        return defaults.get(language, "unknown")
    
    def _find_route_files(self, language: str) -> list:
        """Find files that contain route definitions."""
        route_files = []
        
        patterns = {
            "javascript": ["**/routes/**/*.js", "**/controllers/**/*.js", "**/api/**/*.js",
                         "**/routes/**/*.ts", "**/controllers/**/*.ts"],
            "typescript": ["**/routes/**/*.ts", "**/controllers/**/*.ts", "**/api/**/*.ts",
                          "**/routes/**/*.js"],
            "python": ["**/routes/**/*.py", "**/views/**/*.py", "**/api/**/*.py"],
            "go": ["**/handlers/**/*.go", "**/routes/**/*.go", "**/api/**/*.go"],
            "java": ["**/controller/**/*.java", "**/api/**/*.java"],
            "kotlin": ["**/controller/**/*.kt", "**/api/**/*.kt"]
        }
        
        for pattern in patterns.get(language, []):
            for file in self.repo_path.glob(pattern):
                if "node_modules" not in str(file) and "test" not in str(file).lower():
                    route_files.append(str(file.relative_to(self.repo_path)))
        
        # Also check src directory for route definitions
        src_dir = self.repo_path / "src"
        if src_dir.exists():
            ext_map = {
                "javascript": ".js", "typescript": ".ts",
                "python": ".py", "go": ".go",
                "java": ".java", "kotlin": ".kt"
            }
            ext = ext_map.get(language, ".js")
            for file in src_dir.rglob(f"*{ext}"):
                rel_path = str(file.relative_to(self.repo_path))
                if rel_path not in route_files and "test" not in rel_path.lower():
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
            "typescript": ["**/models/**/*.ts", "**/data/**/*.ts", "**/db/**/*.ts"],
            "python": ["**/models/**/*.py", "**/data/**/*.py", "**/db/**/*.py"],
            "go": ["**/models/**/*.go", "**/data/**/*.go"],
            "java": ["**/model/**/*.java", "**/entity/**/*.java"],
            "kotlin": ["**/model/**/*.kt", "**/entity/**/*.kt"]
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
                except Exception:
                    pass
        
        setup_hints = []
        
        # Check for database patterns
        for pattern in self.DATABASE_PATTERNS:
            if re.search(pattern, all_content, re.IGNORECASE):
                setup_hints.append("Database detected — state handlers need DB operations")
                setup_hints.append("Consider using test transactions or cleanup after each test")
                return "database", setup_hints
        
        # Check for in-memory patterns
        for pattern in self.IN_MEMORY_PATTERNS:
            if re.search(pattern, all_content):
                setup_hints.append("In-memory data storage detected")
                # Don't say "can directly manipulate" yet — export analysis will refine this
                return "in_memory", setup_hints
        
        setup_hints.append("Storage type unclear — assume in-memory or mock")
        return "unknown", setup_hints
    
    def _analyze_exports(self, files: list, language: str) -> list:
        """
        Analyze what each source file exports.
        
        This is CRITICAL for state handler generation — the AI needs to know
        what is actually importable vs what is closure-scoped/inaccessible.
        
        Returns:
            List of FileExportInfo objects
        """
        export_analysis = []
        
        for file_path in files:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            try:
                content = full_path.read_text(errors='ignore')
            except Exception:
                continue
            
            if language in ("javascript", "typescript"):
                info = self._analyze_js_exports(file_path, content)
            elif language == "python":
                info = self._analyze_python_exports(file_path, content)
            elif language == "go":
                info = self._analyze_go_exports(file_path, content)
            elif language in ("java", "kotlin"):
                info = self._analyze_jvm_exports(file_path, content, language)
            else:
                continue
            
            if info:
                export_analysis.append(info)
                
                # Log findings
                if info.non_exported_data:
                    print(f"  ⚠️  {file_path}: data arrays {info.non_exported_data} are NOT exported")
                if info.exported_data:
                    print(f"  ✅ {file_path}: data arrays {info.exported_data} ARE exported")
        
        return export_analysis
    
    def _analyze_js_exports(self, file_path: str, content: str) -> FileExportInfo:
        """Analyze JavaScript/TypeScript module exports."""
        
        # Find all data arrays (const/let xxx = [...])
        data_arrays = re.findall(
            r'(?:const|let)\s+(\w+)\s*=\s*\[', content
        )
        
        # Find all data maps (const/let xxx = {} or new Map())
        data_maps = re.findall(
            r'(?:const|let)\s+(\w+)\s*=\s*(?:\{[^}]*\}|new\s+Map\(\))', content
        )
        
        all_data = list(set(data_arrays + data_maps))
        
        # Detect export patterns
        exports = []
        export_type = "unknown"
        
        # CommonJS: module.exports = xxx
        default_export = re.findall(r'module\.exports\s*=\s*(\w+)', content)
        
        # CommonJS: module.exports = { a, b, c }
        object_export = re.search(
            r'module\.exports\s*=\s*\{([^}]+)\}', content
        )
        
        # CommonJS: module.exports.xxx = yyy
        named_exports = re.findall(
            r'module\.exports\.(\w+)\s*=\s*(\w+)', content
        )
        
        # CommonJS: exports.xxx = yyy
        short_exports = re.findall(
            r'(?<!module\.)exports\.(\w+)\s*=\s*(\w+)', content
        )
        
        # ES modules: export default xxx
        es_default = re.findall(r'export\s+default\s+(\w+)', content)
        
        # ES modules: export { a, b }
        es_named = re.findall(r'export\s+\{([^}]+)\}', content)
        
        # ES modules: export const xxx
        es_const = re.findall(r'export\s+(?:const|let|function)\s+(\w+)', content)
        
        # Build export list
        if default_export:
            exports = default_export
            # Determine type
            for exp in default_export:
                if exp == 'router' or 'Router' in content:
                    export_type = "router"
                elif exp == 'app':
                    export_type = "app"
                else:
                    export_type = "object"
        
        if object_export:
            export_names = [n.strip() for n in object_export.group(1).split(',')]
            exports.extend(export_names)
            export_type = "object"
        
        if named_exports:
            for name, value in named_exports:
                exports.append(name)
        
        if short_exports:
            for name, value in short_exports:
                exports.append(name)
        
        if es_default:
            exports.extend(es_default)
        
        if es_named:
            for group in es_named:
                names = [n.strip() for n in group.split(',')]
                exports.extend(names)
        
        if es_const:
            exports.extend(es_const)
        
        # Clean up exports list
        exports = list(set(exports))
        
        # Determine which data arrays are exported
        exported_data = [d for d in all_data if d in exports]
        non_exported_data = [d for d in all_data if d not in exports]
        
        # Check for test hooks (._testData, ._items, etc.)
        has_test_exports = bool(re.search(
            r'module\.exports\._\w+\s*=|exports\._testData', content
        ))
        
        return FileExportInfo(
            file_path=file_path,
            exports=exports,
            export_type=export_type,
            data_arrays=all_data,
            exported_data=exported_data,
            non_exported_data=non_exported_data,
            has_test_exports=has_test_exports
        )
    
    def _analyze_python_exports(self, file_path: str, content: str) -> FileExportInfo:
        """Analyze Python module exports (all module-level names are importable)."""
        
        # In Python, all module-level variables are importable
        module_vars = re.findall(
            r'^(\w+)\s*=\s*\[', content, re.MULTILINE
        )
        
        # Check __all__ for explicit exports
        all_match = re.search(r'__all__\s*=\s*\[([^\]]+)\]', content)
        explicit_exports = []
        if all_match:
            explicit_exports = [
                n.strip().strip("'\"") for n in all_match.group(1).split(',')
            ]
        
        # Python: all module-level variables are accessible via import
        all_data = module_vars
        exported_data = all_data  # Python exports everything at module level
        non_exported_data = []
        
        return FileExportInfo(
            file_path=file_path,
            exports=explicit_exports or module_vars,
            export_type="module",
            data_arrays=all_data,
            exported_data=exported_data,
            non_exported_data=non_exported_data,
            has_test_exports=False
        )
    
    def _analyze_go_exports(self, file_path: str, content: str) -> FileExportInfo:
        """Analyze Go exports (capitalized = exported, lowercase = unexported)."""
        
        # Find package-level variables
        var_matches = re.findall(
            r'var\s+(\w+)\s*=\s*\[', content
        )
        
        # In Go, capitalized names are exported
        exported_data = [v for v in var_matches if v[0].isupper()]
        non_exported_data = [v for v in var_matches if v[0].islower()]
        
        return FileExportInfo(
            file_path=file_path,
            exports=exported_data,
            export_type="package",
            data_arrays=var_matches,
            exported_data=exported_data,
            non_exported_data=non_exported_data,
            has_test_exports=False
        )
    
    def _analyze_jvm_exports(
        self, file_path: str, content: str, language: str
    ) -> FileExportInfo:
        """Analyze Java/Kotlin exports (public fields/methods)."""
        
        # Find fields that look like data stores
        if language == "java":
            fields = re.findall(
                r'(?:private|protected|public)\s+(?:static\s+)?(?:List|Map|Set|Collection)<[^>]+>\s+(\w+)',
                content
            )
            public_fields = re.findall(
                r'public\s+(?:static\s+)?(?:List|Map|Set|Collection)<[^>]+>\s+(\w+)',
                content
            )
        else:
            fields = re.findall(
                r'(?:private|internal|public)\s+(?:val|var)\s+(\w+)\s*[:=]\s*(?:mutableListOf|listOf|mapOf)',
                content
            )
            public_fields = re.findall(
                r'(?:public\s+)?(?:val|var)\s+(\w+)\s*[:=]\s*(?:mutableListOf|listOf|mapOf)',
                content
            )
        
        exported_data = public_fields
        non_exported_data = [f for f in fields if f not in public_fields]
        
        return FileExportInfo(
            file_path=file_path,
            exports=public_fields,
            export_type="class",
            data_arrays=fields,
            exported_data=exported_data,
            non_exported_data=non_exported_data,
            has_test_exports=False
        )
    
    def _determine_access_strategy(
        self,
        export_analysis: list,
        storage_type: str,
        framework: str
    ) -> str:
        """
        Determine the recommended data access strategy for state handlers.
        
        Returns one of:
        - "direct_import": Data arrays are exported and can be imported directly
        - "rest_api": Use provider's own REST API to set up data
        - "repository": Use repository/service layer
        - "database": Use database client directly
        """
        if storage_type == "database":
            return "database"
        
        # Check if any data arrays are exported
        has_exported_data = any(
            info.exported_data for info in export_analysis
        )
        has_non_exported_data = any(
            info.non_exported_data for info in export_analysis
        )
        
        if has_exported_data and not has_non_exported_data:
            return "direct_import"
        
        if has_non_exported_data:
            # Data exists but isn't exported — must use REST API
            return "rest_api"
        
        # Default: try REST API (safest)
        return "rest_api"
    
    def _refine_setup_hints(
        self,
        base_hints: list,
        export_analysis: list,
        strategy: str
    ) -> list:
        """Refine setup hints based on export analysis and strategy."""
        hints = list(base_hints)
        
        if strategy == "direct_import":
            hints.append("Data arrays are exported — state handlers can import and manipulate them directly")
            for info in export_analysis:
                for data_name in info.exported_data:
                    hints.append(
                        f"Import '{data_name}' from '{info.file_path}': "
                        f"require('{info.file_path}').{data_name}"
                    )
        
        elif strategy == "rest_api":
            hints.append("⚠️  Data arrays are NOT exported (closure-scoped local variables)")
            hints.append("State handlers MUST use the provider's REST API to set up test data")
            hints.append("Use http.request() or fetch() to call POST/PUT/DELETE endpoints")
            hints.append("The provider server runs on the test PORT variable")
            
            for info in export_analysis:
                if info.non_exported_data:
                    hints.append(
                        f"❌ CANNOT use: require('{info.file_path}').{info.non_exported_data[0]} "
                        f"— this is UNDEFINED because only '{info.export_type}' is exported"
                    )
        
        elif strategy == "database":
            hints.append("Database storage — state handlers need DB operations")
            hints.append("Use the same DB connection/ORM that the provider uses")
        
        return hints
    
    def _extract_data_models(self, files: list, language: str) -> list:
        """Extract data model definitions from files."""
        models = []
        
        for file_path in files:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            try:
                content = full_path.read_text(errors='ignore')
            except Exception:
                continue
            
            if language in ("javascript", "typescript"):
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
                class_match = re.findall(r'class\s+(\w+)', content)
                for class_name in class_match:
                    if not class_name.startswith('_'):
                        models.append(DataModel(
                            name=class_name,
                            fields=[],
                            source_file=file_path,
                            storage_type="unknown"
                        ))
        
        return models
    
    def _get_source_snippets(self, files: list) -> dict:
        """Get relevant source code snippets for AI context."""
        snippets = {}
        
        # Also include main entry point (index.js, app.js, main.go, etc.)
        entry_files = [
            "src/index.js", "src/app.js", "src/server.js",
            "src/index.ts", "src/app.ts",
            "app.py", "main.py", "main.go", "cmd/main.go",
        ]
        for entry in entry_files:
            full_path = self.repo_path / entry
            if full_path.exists():
                try:
                    content = full_path.read_text(errors='ignore')
                    snippets[entry] = content[:3000]
                except Exception:
                    pass
        
        for file_path in files[:10]:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            try:
                content = full_path.read_text(errors='ignore')
                if len(content) < 5000:
                    snippets[file_path] = content
                else:
                    snippets[file_path] = content[:3000] + "\n... (truncated)"
            except Exception:
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