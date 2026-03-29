"""Project structure adapter — resolves directories, extensions, and file categories.

Instead of hardcoding "app/Models/" or ".php", all path resolution goes through
this adapter which reads from .blindspot.yaml config + sensible defaults.
"""

import logging
import os

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..config import BlindspotConfig, get_config


# Default scan directories per framework
_FRAMEWORK_DEFAULTS: Dict[str, Dict[str, str]] = {
    "laravel": {
        "models": "app/Models",
        "controllers": "app/Http/Controllers",
        "views": "resources/views",
        "services": "app/Services",
        "migrations": "database/migrations",
        "routes": "routes",
        "middleware": "app/Http/Middleware",
        "requests": "app/Http/Requests",
        "tests": "tests",
    },
    "django": {
        "models": ".",
        "controllers": ".",
        "views": "templates",
        "services": ".",
        "migrations": ".",
        "routes": ".",
        "middleware": ".",
        "tests": "tests",
    },
    "rails": {
        "models": "app/models",
        "controllers": "app/controllers",
        "views": "app/views",
        "services": "app/services",
        "migrations": "db/migrate",
        "routes": "config",
        "middleware": "app/middleware",
        "tests": "test",
    },
    "nextjs": {
        "models": "src/models",
        "controllers": "src/app/api",
        "views": "src/app",
        "services": "src/services",
        "migrations": "prisma/migrations",
        "routes": "src/app",
        "middleware": "src/middleware",
        "tests": "__tests__",
    },
    "express": {
        "models": "src/models",
        "controllers": "src/controllers",
        "views": "src/views",
        "services": "src/services",
        "migrations": "migrations",
        "routes": "src/routes",
        "middleware": "src/middleware",
        "tests": "tests",
    },
    "none": {},
}

# Default source code extensions per language
_LANGUAGE_EXTENSIONS: Dict[str, List[str]] = {
    "php": [".php"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "python": [".py", ".pyw"],
    "go": [".go"],
    "rust": [".rs"],
    "java": [".java"],
    "kotlin": [".kt", ".kts"],
    "csharp": [".cs"],
    "ruby": [".rb"],
    "swift": [".swift"],
}

# Template extensions per framework
_TEMPLATE_EXTENSIONS: Dict[str, List[str]] = {
    "laravel": [".blade.php"],
    "django": [".html", ".htm", ".txt"],
    "rails": [".erb", ".haml", ".slim"],
    "nextjs": [".tsx", ".jsx"],
    "express": [".ejs", ".hbs", ".pug"],
    "none": [],
}


@dataclass
class ProjectStructure:
    """Resolved project structure for a specific project.

    Provides all directory paths, file extensions, and categorization
    needed by intelligence tools — no hardcoded paths anywhere else.
    """

    project_path: str
    language: Optional[str] = None
    framework: Optional[str] = None
    scan_dirs: Dict[str, str] = field(default_factory=dict)
    source_extensions: List[str] = field(default_factory=list)
    template_extensions: List[str] = field(default_factory=list)
    all_extensions: List[str] = field(default_factory=list)
    exclude_dirs: Set[str] = field(default_factory=set)

    def get_dir(self, category: str) -> Optional[str]:
        """Get absolute path for a category directory.

        Args:
            category: e.g., 'models', 'controllers', 'views', 'services'

        Returns:
            Absolute path, or None if category not configured
        """
        rel = self.scan_dirs.get(category)
        if rel:
            return os.path.join(self.project_path, rel)
        return None

    def get_rel_dir(self, category: str) -> Optional[str]:
        """Get relative path for a category directory."""
        return self.scan_dirs.get(category)

    def get_all_scan_dirs(self) -> List[str]:
        """Get all configured scan directory relative paths."""
        return list(self.scan_dirs.values())

    def get_all_scan_dirs_absolute(self) -> List[str]:
        """Get all configured scan directories as absolute paths (existing only)."""
        dirs = []
        for rel in self.scan_dirs.values():
            full = os.path.join(self.project_path, rel)
            if os.path.isdir(full):
                dirs.append(full)
        return dirs

    def is_source_file(self, file_path: str) -> bool:
        """Check if a file is a source code file based on extension."""
        for ext in self.source_extensions:
            if file_path.endswith(ext):
                return True
        return False

    def is_template_file(self, file_path: str) -> bool:
        """Check if a file is a template file based on extension."""
        for ext in self.template_extensions:
            if file_path.endswith(ext):
                return True
        return False

    def categorize_file(self, rel_path: str) -> Optional[str]:
        """Determine the category of a file based on its path.

        Args:
            rel_path: Path relative to project root

        Returns:
            Category name (e.g., 'models', 'controllers') or None
        """
        normalized = rel_path.replace("\\", "/")
        for category, dir_path in self.scan_dirs.items():
            dir_normalized = dir_path.replace("\\", "/")
            if normalized.startswith(dir_normalized + "/") or normalized.startswith(dir_normalized + "\\"):
                return category
        return None

    def should_exclude(self, dir_name: str) -> bool:
        """Check if a directory should be excluded from scanning."""
        return dir_name in self.exclude_dirs

    def walk_source_files(self) -> list:
        """Walk all source files in the project, respecting exclusions.

        Yields (rel_path, abs_path) tuples.
        """
        results = []
        for root, dirs, files in os.walk(self.project_path):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if not self.should_exclude(d)]

            for fname in files:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, self.project_path)
                if self.is_source_file(fname) or self.is_template_file(fname):
                    results.append((rel_path, abs_path))
        return results


# Default excluded directories
_DEFAULT_EXCLUDE = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "vendor", "dist", "build", "target", "out", "bin", "obj", ".idea",
    ".vscode", ".vs", ".pytest_cache", ".tox", ".coverage", ".nyc_output",
    "coverage", "htmlcov", ".DS_Store", "storage", ".next", ".nuxt",
}


def _detect_language(project_path: str) -> Optional[str]:
    """Auto-detect project language from common files."""
    indicators = {
        "python": ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile", "poetry.lock"],
        "php": ["composer.json", "artisan"],
        "javascript": ["package.json"],
        "typescript": ["tsconfig.json"],
        "go": ["go.mod", "go.sum"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle"],
        "kotlin": ["build.gradle.kts"],
        "ruby": ["Gemfile"],
        "csharp": ["*.csproj", "*.sln"],
        "dart": ["pubspec.yaml"],
    }
    for lang, files in indicators.items():
        for f in files:
            if "*" in f:
                import glob
                if glob.glob(os.path.join(project_path, f)):
                    return lang
            elif os.path.exists(os.path.join(project_path, f)):
                # typescript overrides javascript if tsconfig.json exists
                if lang == "javascript" and os.path.exists(os.path.join(project_path, "tsconfig.json")):
                    return "typescript"
                return lang
    return None


def _detect_framework(project_path: str, language: Optional[str]) -> Optional[str]:
    """Auto-detect framework from project files."""
    if language == "php":
        if os.path.exists(os.path.join(project_path, "artisan")):
            return "laravel"
    elif language in ("javascript", "typescript"):
        pkg_path = os.path.join(project_path, "package.json")
        if os.path.exists(pkg_path):
            try:
                import json
                with open(pkg_path, "r") as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps:
                    return "nextjs"
                if "@nestjs/core" in deps:
                    return "nestjs"
                if "nuxt" in deps:
                    return "nuxt"
                if "@sveltejs/kit" in deps:
                    return "sveltekit"
                if "express" in deps:
                    return "express"
            except Exception as e:
                logger.debug("Failed to parse package.json for framework detection: %s", e)
    elif language == "python":
        # Check for Django first, then FastAPI/Flask
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in _DEFAULT_EXCLUDE]
            if "manage.py" in files or "settings.py" in files:
                return "django"
            break  # Only check top-level
        # Check for FastAPI/Flask in requirements or imports
        for req_file in ["requirements.txt", "pyproject.toml", "Pipfile"]:
            req_path = os.path.join(project_path, req_file)
            if os.path.isfile(req_path):
                try:
                    with open(req_path, "r") as f:
                        content = f.read().lower()
                    if "fastapi" in content:
                        return "fastapi"
                    if "flask" in content:
                        return "fastapi"  # Flask shares FastAPI plugin
                except Exception as e:
                    logger.debug("Failed to read %s for framework detection: %s", req_path, e)
    elif language == "ruby":
        if os.path.exists(os.path.join(project_path, "config", "routes.rb")):
            return "rails"
    elif language == "go":
        # Check go.mod for web frameworks
        gomod = os.path.join(project_path, "go.mod")
        if os.path.isfile(gomod):
            try:
                with open(gomod, "r") as f:
                    content = f.read()
                if "gin-gonic" in content or "echo" in content or "go-chi" in content:
                    return "go"
            except Exception as e:
                logger.debug("Failed to read go.mod: %s", e)
    elif language == "rust":
        cargo = os.path.join(project_path, "Cargo.toml")
        if os.path.isfile(cargo):
            try:
                with open(cargo, "r") as f:
                    content = f.read()
                if "actix" in content or "axum" in content or "rocket" in content:
                    return "rust"
            except Exception as e:
                logger.debug("Failed to read Cargo.toml: %s", e)
    elif language == "java":
        # Spring Boot detection
        pom = os.path.join(project_path, "pom.xml")
        gradle = os.path.join(project_path, "build.gradle")
        for build_file in [pom, gradle]:
            if os.path.isfile(build_file):
                try:
                    with open(build_file, "r") as f:
                        content = f.read()
                    if "spring-boot" in content:
                        return "spring"
                except Exception as e:
                    logger.debug("Failed to read %s: %s", build_file, e)
    elif language == "kotlin":
        gradle_kts = os.path.join(project_path, "build.gradle.kts")
        if os.path.isfile(gradle_kts):
            try:
                with open(gradle_kts, "r") as f:
                    content = f.read()
                if "spring-boot" in content:
                    return "spring"
            except Exception as e:
                logger.debug("Failed to read build.gradle.kts: %s", e)
    elif language == "csharp":
        # ASP.NET Core detection
        import glob as glob_mod
        csproj_files = glob_mod.glob(os.path.join(project_path, "*.csproj"))
        for csproj in csproj_files:
            try:
                with open(csproj, "r") as f:
                    content = f.read()
                if "Microsoft.AspNetCore" in content or "Microsoft.NET.Sdk.Web" in content:
                    return "aspnet"
            except Exception as e:
                logger.debug("Failed to read %s: %s", csproj, e)
    elif language == "dart":
        pubspec = os.path.join(project_path, "pubspec.yaml")
        if os.path.isfile(pubspec):
            try:
                with open(pubspec, "r") as f:
                    content = f.read()
                if "flutter:" in content:
                    return "flutter"
            except Exception as e:
                logger.debug("Failed to read pubspec.yaml: %s", e)

    # Check for React Native (separate from Next.js)
    if language in ("javascript", "typescript"):
        pkg_path = os.path.join(project_path, "package.json")
        if os.path.isfile(pkg_path):
            try:
                import json
                with open(pkg_path, "r") as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "react-native" in deps and "next" not in deps:
                    return "reactnative"
            except Exception as e:
                logger.debug("Failed to parse package.json for React Native detection: %s", e)

    return "none"


def detect_workspaces(project_path: str) -> List[Dict[str, str]]:
    """Detect monorepo workspaces and their frameworks.

    Checks for:
    - package.json workspaces (npm/yarn/pnpm)
    - pnpm-workspace.yaml
    - Subdirectories with their own package.json/go.mod/Cargo.toml/manage.py

    Returns:
        List of dicts with 'name', 'path', 'language', 'framework' keys.
        Empty list if not a monorepo.
    """
    workspaces = []

    # Check npm/yarn workspaces in package.json
    pkg_path = os.path.join(project_path, "package.json")
    workspace_dirs = []
    if os.path.isfile(pkg_path):
        try:
            import json
            with open(pkg_path, "r") as f:
                pkg = json.load(f)
            ws = pkg.get("workspaces", [])
            # workspaces can be a list or {"packages": [...]}
            if isinstance(ws, dict):
                ws = ws.get("packages", [])
            if isinstance(ws, list):
                import glob
                for pattern in ws:
                    full_pattern = os.path.join(project_path, pattern)
                    for match in glob.glob(full_pattern):
                        if os.path.isdir(match):
                            workspace_dirs.append(match)
        except Exception as e:
            logger.debug("Failed to parse package.json workspaces: %s", e)

    # Check pnpm-workspace.yaml
    pnpm_ws = os.path.join(project_path, "pnpm-workspace.yaml")
    if os.path.isfile(pnpm_ws) and not workspace_dirs:
        try:
            import yaml
            with open(pnpm_ws, "r") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                import glob
                for pattern in data.get("packages", []):
                    full_pattern = os.path.join(project_path, pattern)
                    for match in glob.glob(full_pattern):
                        if os.path.isdir(match):
                            workspace_dirs.append(match)
        except Exception as e:
            logger.debug("Failed to parse pnpm-workspace.yaml: %s", e)

    # Fallback: check common subdirectory names
    if not workspace_dirs:
        for name in ["frontend", "backend", "web", "api", "server", "client",
                      "app", "mobile", "shared", "packages", "services"]:
            sub = os.path.join(project_path, name)
            if os.path.isdir(sub):
                # Check if it has its own project indicator
                indicators = ["package.json", "go.mod", "Cargo.toml", "pyproject.toml",
                              "requirements.txt", "composer.json", "Gemfile", "manage.py"]
                for ind in indicators:
                    if os.path.isfile(os.path.join(sub, ind)):
                        workspace_dirs.append(sub)
                        break

    # Analyze each workspace
    for ws_path in workspace_dirs:
        name = os.path.basename(ws_path)
        lang = _detect_language(ws_path)
        fw = _detect_framework(ws_path, lang)
        if fw == "none":
            fw = None
        workspaces.append({
            "name": name,
            "path": ws_path,
            "rel_path": os.path.relpath(ws_path, project_path),
            "language": lang,
            "framework": fw,
        })

    return workspaces


def get_project_structure(project_path: str) -> ProjectStructure:
    """Build a ProjectStructure for a project directory.

    Reads .blindspot.yaml if present, otherwise auto-detects language/framework
    and applies sensible defaults.

    Args:
        project_path: Absolute path to the project root

    Returns:
        Fully resolved ProjectStructure instance
    """
    config = get_config(project_path)

    # Determine language and framework
    language = config.language or _detect_language(project_path)
    framework = config.framework or _detect_framework(project_path, language)

    # Build scan dirs: config overrides > framework defaults > empty
    scan_dirs: Dict[str, str] = {}
    if framework and framework in _FRAMEWORK_DEFAULTS:
        defaults = dict(_FRAMEWORK_DEFAULTS[framework])
        # Smart src/ detection: check if the actual code lives under src/ or at root level.
        # Many Next.js projects have app/ at root instead of src/app/.
        # If a default path starts with "src/" but that path doesn't exist, try without "src/".
        adjusted: Dict[str, str] = {}
        for key, path in defaults.items():
            full = os.path.join(project_path, path)
            if os.path.isdir(full):
                adjusted[key] = path
            elif path.startswith("src/"):
                # Try without src/ prefix
                stripped = path[4:]
                if os.path.isdir(os.path.join(project_path, stripped)):
                    adjusted[key] = stripped
                else:
                    adjusted[key] = path  # Keep original, might be created later
            else:
                adjusted[key] = path
        scan_dirs.update(adjusted)
    if config.scan_dirs:
        scan_dirs.update(config.scan_dirs)

    # Ensure we also scan root-level common directories that exist
    for extra_dir in ["app", "lib", "hooks", "store", "services", "components", "utils", "types"]:
        full = os.path.join(project_path, extra_dir)
        if os.path.isdir(full) and extra_dir not in scan_dirs.values():
            # Add with a sensible category name if not already covered
            if extra_dir not in scan_dirs:
                scan_dirs[extra_dir] = extra_dir

    # Monorepo enhancement: detect workspaces and merge their info
    workspaces = detect_workspaces(project_path)
    workspace_languages: Set[str] = set()
    workspace_frameworks: Set[str] = set()

    if workspaces:
        for ws in workspaces:
            if ws.get("language"):
                workspace_languages.add(ws["language"])
            if ws.get("framework"):
                workspace_frameworks.add(ws["framework"])

        # Add workspace directories to scan_dirs
        for ws in workspaces:
            ws_name = ws["name"]
            ws_rel = ws["rel_path"]
            # Add the workspace itself as a scannable directory
            if ws_name not in scan_dirs:
                scan_dirs[ws_name] = ws_rel

        # Merge framework defaults from all detected workspace frameworks
        for ws_fw in workspace_frameworks:
            if ws_fw in _FRAMEWORK_DEFAULTS:
                ws_defaults = _FRAMEWORK_DEFAULTS[ws_fw]
                # Find which workspace has this framework
                for ws in workspaces:
                    if ws.get("framework") == ws_fw:
                        ws_rel = ws["rel_path"]
                        for key, path in ws_defaults.items():
                            # Prefix with workspace path
                            full_path = os.path.join(ws_rel, path)
                            if os.path.isdir(os.path.join(project_path, full_path)):
                                prefixed_key = f"{ws['name']}_{key}"
                                scan_dirs[prefixed_key] = full_path
                            elif path.startswith("src/"):
                                stripped = path[4:]
                                full_stripped = os.path.join(ws_rel, stripped)
                                if os.path.isdir(os.path.join(project_path, full_stripped)):
                                    prefixed_key = f"{ws['name']}_{key}"
                                    scan_dirs[prefixed_key] = full_stripped
                        break

    # Build extensions — include all workspace languages too
    source_ext = []
    all_languages = {language} | workspace_languages if language else workspace_languages
    for lang in all_languages:
        if lang and lang in _LANGUAGE_EXTENSIONS:
            for ext in _LANGUAGE_EXTENSIONS[lang]:
                if ext not in source_ext:
                    source_ext.append(ext)
    # Always include TS if JS is present and vice versa
    if any(ext in source_ext for ext in [".js", ".jsx"]):
        for ext in [".ts", ".tsx"]:
            if ext not in source_ext:
                source_ext.append(ext)
    if any(ext in source_ext for ext in [".ts", ".tsx"]):
        for ext in [".js", ".jsx", ".mjs", ".cjs"]:
            if ext not in source_ext:
                source_ext.append(ext)

    template_ext = []
    all_frameworks = {framework} | workspace_frameworks if framework else workspace_frameworks
    for fw in all_frameworks:
        if fw and fw in _TEMPLATE_EXTENSIONS:
            for ext in _TEMPLATE_EXTENSIONS[fw]:
                if ext not in template_ext:
                    template_ext.append(ext)

    all_ext = list(set(source_ext + template_ext))

    # Build exclusions
    exclude = set(_DEFAULT_EXCLUDE)
    if config.exclude_dirs:
        exclude.update(config.exclude_dirs)

    return ProjectStructure(
        project_path=project_path,
        language=language,
        framework=framework,
        scan_dirs=scan_dirs,
        source_extensions=source_ext,
        template_extensions=template_ext,
        all_extensions=all_ext,
        exclude_dirs=exclude,
    )


# Module-level cache
_structure_cache: Dict[str, ProjectStructure] = {}


def get_cached_structure(project_path: str) -> ProjectStructure:
    """Get ProjectStructure with caching."""
    if project_path not in _structure_cache:
        _structure_cache[project_path] = get_project_structure(project_path)
    return _structure_cache[project_path]
