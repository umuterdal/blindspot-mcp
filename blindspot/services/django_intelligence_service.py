"""
Django Intelligence Service - Django/Python-specific code analysis.

Provides cross-file relationship tracking, URL mapping, model relationship
extraction, template dependency analysis, and Django-specific flow tracing.
Designed to minimize context window usage by returning only the data needed
for accurate code generation.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class DjangoIntelligenceService(BaseService):
    """Django-specific code intelligence for Python/Django projects."""

    # Django field types that represent relationships
    RELATIONSHIP_FIELDS = {
        "ForeignKey", "OneToOneField", "ManyToManyField",
        "GenericForeignKey", "GenericRelation",
    }

    # Common Django model field types
    MODEL_FIELD_TYPES = {
        "CharField", "TextField", "IntegerField", "FloatField",
        "DecimalField", "BooleanField", "DateField", "DateTimeField",
        "TimeField", "EmailField", "URLField", "SlugField",
        "UUIDField", "FileField", "ImageField", "JSONField",
        "BigIntegerField", "SmallIntegerField", "PositiveIntegerField",
        "PositiveSmallIntegerField", "AutoField", "BigAutoField",
        "BinaryField", "DurationField", "IPAddressField",
        "GenericIPAddressField",
    }

    # Pattern keywords for get_similar_patterns
    PATTERN_REGISTRY = {
        "form": {
            "file_patterns": ["forms.py"],
            "class_regex": r"class\s+(\w+)\s*\([^)]*(?:Form|ModelForm)[^)]*\)",
            "description": "Django form / ModelForm",
        },
        "model-admin": {
            "file_patterns": ["admin.py"],
            "class_regex": r"class\s+(\w+)\s*\([^)]*(?:ModelAdmin|TabularInline|StackedInline|admin\.)[^)]*\)",
            "description": "Django admin registration",
        },
        "api-viewset": {
            "file_patterns": ["views.py", "viewsets.py", "api.py"],
            "class_regex": r"class\s+(\w+)\s*\([^)]*(?:ViewSet|ModelViewSet|GenericViewSet|APIView)[^)]*\)",
            "description": "DRF ViewSet / APIView",
        },
        "signal": {
            "file_patterns": ["signals.py", "receivers.py", "apps.py"],
            "class_regex": r"@receiver\s*\(\s*(\w+)",
            "description": "Django signal receiver",
        },
        "celery-task": {
            "file_patterns": ["tasks.py", "celery.py"],
            "class_regex": r"@(?:shared_task|app\.task|celery_app\.task)\s*(?:\([^)]*\))?\s*\ndef\s+(\w+)",
            "description": "Celery task definition",
        },
        "management-command": {
            "file_patterns": ["management/commands/*.py"],
            "class_regex": r"class\s+Command\s*\(\s*BaseCommand\s*\)",
            "description": "Django management command",
        },
        "templatetag": {
            "file_patterns": ["templatetags/*.py"],
            "class_regex": r"@register\.(simple_tag|inclusion_tag|filter|tag)\s*(?:\([^)]*\))?\s*\ndef\s+(\w+)",
            "description": "Django template tag / filter",
        },
        "middleware": {
            "file_patterns": ["middleware.py", "middleware/*.py"],
            "class_regex": r"class\s+(\w+)\s*\([^)]*Middleware[^)]*\)|def\s+(\w+)\s*\(\s*get_response\s*\)",
            "description": "Django middleware",
        },
        "mixin": {
            "file_patterns": ["mixins.py", "views.py"],
            "class_regex": r"class\s+(\w+Mixin)\s*\(",
            "description": "Django class mixin",
        },
        "decorator": {
            "file_patterns": ["decorators.py", "utils.py"],
            "class_regex": r"def\s+(\w+)\s*\([^)]*\)\s*:\s*\n\s+.*def\s+wrapper",
            "description": "Python decorator",
        },
    }

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Suppressed exception in best-effort path: %s", e)
        return None

    # ── Helper: discover Django apps ───────────────────────────────────

    def _discover_apps(self, base: str) -> List[str]:
        """Discover Django app directories by looking for apps.py or models.py."""
        apps = []
        for root, dirs, files in os.walk(base):
            # Skip hidden dirs, __pycache__, venv, node_modules, .git
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules", "static", "media", "migrations")
            ]
            if "apps.py" in files or ("models.py" in files and "__init__.py" in files):
                rel = os.path.relpath(root, base)
                if rel != ".":
                    apps.append(rel)
        return sorted(apps)

    def _find_settings_files(self, base: str) -> List[str]:
        """Find all settings.py or settings/*.py files."""
        results = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            for f in files:
                if f == "settings.py" or (os.path.basename(root) == "settings" and f.endswith(".py")):
                    results.append(os.path.join(root, f))
        return results

    def _find_files_by_name(self, base: str, filenames: List[str],
                            subdirs: Optional[List[str]] = None) -> List[str]:
        """Find all files matching given names within the project."""
        results = []
        search_roots = [os.path.join(base, sd) for sd in subdirs] if subdirs else [base]
        for search_root in search_roots:
            if not os.path.isdir(search_root):
                continue
            for root, dirs, files in os.walk(search_root):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
                ]
                for f in files:
                    if f in filenames:
                        results.append(os.path.join(root, f))
        return results

    def _find_files_by_pattern(self, base: str, pattern: str) -> List[str]:
        """Find files matching a glob-like pattern (simple: supports * in filename only)."""
        results = []
        # Handle paths like "management/commands/*.py"
        parts = pattern.rsplit("/", 1)
        if len(parts) == 2:
            subdir_pattern, file_pattern = parts
        else:
            subdir_pattern, file_pattern = "", parts[0]

        file_regex = re.compile("^" + file_pattern.replace("*", ".*") + "$")

        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            rel_root = os.path.relpath(root, base)
            if subdir_pattern:
                # Check if rel_root ends with or contains the subdir_pattern
                sub_regex = re.compile(subdir_pattern.replace("*", ".*"))
                if not sub_regex.search(rel_root):
                    continue
            for f in files:
                if file_regex.match(f):
                    results.append(os.path.join(root, f))
        return results

    def _read_file_safe(self, fpath: str) -> Optional[str]:
        """Read file content safely, returning None on failure."""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def _parse_python_classes(self, content: str) -> List[Dict[str, Any]]:
        """Parse Python classes from file content with their bases, decorators, and body ranges."""
        classes = []
        lines = content.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            # Collect decorators
            decorators = []
            while re.match(r"^\s*@", line):
                decorators.append(line.strip())
                i += 1
                if i < len(lines):
                    line = lines[i]
                else:
                    break

            class_match = re.match(r"^(\s*)class\s+(\w+)\s*\(([^)]*)\)\s*:", line)
            if not class_match:
                class_match = re.match(r"^(\s*)class\s+(\w+)\s*:", line)

            if class_match:
                indent = len(class_match.group(1))
                class_name = class_match.group(2)
                bases_str = class_match.group(3) if class_match.lastindex >= 3 else ""
                bases = [b.strip() for b in bases_str.split(",") if b.strip()] if bases_str else []
                class_start = i
                # Find class body end
                class_end = i + 1
                while class_end < len(lines):
                    next_line = lines[class_end]
                    if next_line.strip() == "" or next_line.strip().startswith("#"):
                        class_end += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_line.strip() and next_indent <= indent:
                        break
                    class_end += 1
                body = "\n".join(lines[class_start:class_end])
                classes.append({
                    "name": class_name,
                    "bases": bases,
                    "decorators": decorators,
                    "body": body,
                    "start_line": class_start + 1,
                    "end_line": class_end,
                })
            i += 1
        return classes

    def _parse_functions(self, content: str) -> List[Dict[str, Any]]:
        """Parse top-level and method-level function definitions."""
        functions = []
        lines = content.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            decorators = []
            temp_i = i
            while temp_i < len(lines) and re.match(r"^\s*@", lines[temp_i]):
                decorators.append(lines[temp_i].strip())
                temp_i += 1

            if temp_i < len(lines):
                fn_match = re.match(r"^(\s*)def\s+(\w+)\s*\(([^)]*)\)", lines[temp_i])
                if fn_match:
                    indent = len(fn_match.group(1))
                    fn_name = fn_match.group(2)
                    params = fn_match.group(3)
                    functions.append({
                        "name": fn_name,
                        "params": params,
                        "decorators": decorators,
                        "indent": indent,
                        "line": temp_i + 1,
                    })
                    i = temp_i + 1
                    continue
            i += 1
        return functions

    # ══════════════════════════════════════════════════════════════════════
    #  1. get_model_relationships
    # ══════════════════════════════════════════════════════════════════════

    def get_model_relationships(self, model_name: str = None) -> Dict[str, Any]:
        """
        Parse Django models.py files for class fields, relationships, Meta class,
        managers, properties, and field types. Returns a comprehensive relationship map.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {"status": "success", "models": {}}
        model_files = self._find_files_by_name(base, ["models.py"])

        for fpath in model_files:
            content = self._read_file_safe(fpath)
            if not content:
                continue
            rel_path = os.path.relpath(fpath, base)
            classes = self._parse_python_classes(content)

            for cls in classes:
                name = cls["name"]
                if model_name and name != model_name:
                    continue
                # Check if it looks like a Django model (inherits from models.Model or similar)
                is_model = any(
                    b in ("models.Model", "Model", "AbstractUser", "AbstractBaseUser",
                           "PermissionsMixin", "TimeStampedModel", "BaseModel")
                    or "Model" in b
                    for b in cls["bases"]
                )
                if not is_model and not any("Model" in b for b in cls["bases"]):
                    # Also check if bases contain known abstract models
                    # Accept classes that inherit from other project models
                    parent_names = [b.split(".")[-1] for b in cls["bases"]]
                    if not any(pn in result["models"] for pn in parent_names):
                        # If no known parent and doesn't look like a model, skip
                        if not cls["bases"] or all(
                            b in ("object", "ABC", "type") for b in cls["bases"]
                        ):
                            continue

                model_info: Dict[str, Any] = {
                    "file": rel_path,
                    "bases": cls["bases"],
                    "decorators": cls["decorators"],
                    "fields": {},
                    "relationships": [],
                    "meta": {},
                    "managers": [],
                    "properties": [],
                    "methods": [],
                }

                body = cls["body"]

                # Parse field definitions
                field_pattern = re.compile(
                    r"^\s+(\w+)\s*=\s*(?:models\.)?(\w+Field)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)",
                    re.MULTILINE,
                )
                for fm in field_pattern.finditer(body):
                    field_name = fm.group(1)
                    field_type = fm.group(2)
                    field_args = fm.group(3)

                    field_info: Dict[str, Any] = {"type": field_type}

                    # Extract common kwargs
                    max_len = re.search(r"max_length\s*=\s*(\d+)", field_args)
                    if max_len:
                        field_info["max_length"] = int(max_len.group(1))

                    null_match = re.search(r"null\s*=\s*(True|False)", field_args)
                    if null_match:
                        field_info["null"] = null_match.group(1) == "True"

                    blank_match = re.search(r"blank\s*=\s*(True|False)", field_args)
                    if blank_match:
                        field_info["blank"] = blank_match.group(1) == "True"

                    default_match = re.search(r"default\s*=\s*([^,\)]+)", field_args)
                    if default_match:
                        field_info["default"] = default_match.group(1).strip()

                    unique_match = re.search(r"unique\s*=\s*(True|False)", field_args)
                    if unique_match:
                        field_info["unique"] = unique_match.group(1) == "True"

                    db_index_match = re.search(r"db_index\s*=\s*(True|False)", field_args)
                    if db_index_match:
                        field_info["db_index"] = db_index_match.group(1) == "True"

                    choices_match = re.search(r"choices\s*=\s*(\w+)", field_args)
                    if choices_match:
                        field_info["choices"] = choices_match.group(1)

                    verbose_match = re.search(r"verbose_name\s*=\s*['\"]([^'\"]+)['\"]", field_args)
                    if verbose_match:
                        field_info["verbose_name"] = verbose_match.group(1)

                    help_match = re.search(r"help_text\s*=\s*['\"]([^'\"]+)['\"]", field_args)
                    if help_match:
                        field_info["help_text"] = help_match.group(1)

                    # Check for relationship fields
                    if field_type in self.RELATIONSHIP_FIELDS:
                        rel_info: Dict[str, Any] = {
                            "field_name": field_name,
                            "type": field_type,
                        }
                        # Extract target model
                        target_match = re.match(
                            r"""^\s*['\"]?(\w+(?:\.\w+)?)['\"]?""",
                            field_args,
                        )
                        if target_match:
                            rel_info["target"] = target_match.group(1)

                        on_delete = re.search(r"on_delete\s*=\s*(?:models\.)?(\w+)", field_args)
                        if on_delete:
                            rel_info["on_delete"] = on_delete.group(1)

                        related_name = re.search(r"related_name\s*=\s*['\"]([^'\"]+)['\"]", field_args)
                        if related_name:
                            rel_info["related_name"] = related_name.group(1)

                        through_match = re.search(r"through\s*=\s*['\"]?(\w+)['\"]?", field_args)
                        if through_match:
                            rel_info["through"] = through_match.group(1)

                        model_info["relationships"].append(rel_info)

                    model_info["fields"][field_name] = field_info

                # Parse Meta class
                meta_match = re.search(
                    r"class\s+Meta\s*:\s*\n((?:\s+[^\n]+\n)*)",
                    body,
                )
                if meta_match:
                    meta_body = meta_match.group(1)
                    ordering_match = re.search(r"ordering\s*=\s*\[([^\]]*)\]", meta_body)
                    if ordering_match:
                        model_info["meta"]["ordering"] = [
                            s.strip().strip("'\"")
                            for s in ordering_match.group(1).split(",")
                            if s.strip()
                        ]
                    verbose_match = re.search(r"verbose_name\s*=\s*['\"]([^'\"]+)['\"]", meta_body)
                    if verbose_match:
                        model_info["meta"]["verbose_name"] = verbose_match.group(1)
                    verbose_plural = re.search(r"verbose_name_plural\s*=\s*['\"]([^'\"]+)['\"]", meta_body)
                    if verbose_plural:
                        model_info["meta"]["verbose_name_plural"] = verbose_plural.group(1)
                    db_table = re.search(r"db_table\s*=\s*['\"]([^'\"]+)['\"]", meta_body)
                    if db_table:
                        model_info["meta"]["db_table"] = db_table.group(1)
                    abstract_match = re.search(r"abstract\s*=\s*(True|False)", meta_body)
                    if abstract_match:
                        model_info["meta"]["abstract"] = abstract_match.group(1) == "True"
                    unique_together = re.search(r"unique_together\s*=\s*[\[\(]([^\]\)]+)[\]\)]", meta_body)
                    if unique_together:
                        model_info["meta"]["unique_together"] = unique_together.group(1).strip()
                    indexes_match = re.search(r"indexes\s*=\s*\[([^\]]+)\]", meta_body)
                    if indexes_match:
                        idx_fields = re.findall(r"fields\s*=\s*\[([^\]]+)\]", indexes_match.group(1))
                        model_info["meta"]["indexes"] = [
                            [f.strip().strip("'\"") for f in fields.split(",")]
                            for fields in idx_fields
                        ]
                    constraints_match = re.search(r"constraints\s*=\s*\[", meta_body)
                    if constraints_match:
                        model_info["meta"]["has_constraints"] = True
                    permissions_match = re.search(r"permissions\s*=\s*\[([^\]]+)\]", meta_body)
                    if permissions_match:
                        model_info["meta"]["has_custom_permissions"] = True

                # Parse managers
                manager_pattern = re.compile(
                    r"^\s+(\w+)\s*=\s*(?:models\.)?(\w*Manager)\s*\(",
                    re.MULTILINE,
                )
                for mm in manager_pattern.finditer(body):
                    model_info["managers"].append({
                        "name": mm.group(1),
                        "type": mm.group(2),
                    })

                # Parse @property methods
                prop_pattern = re.compile(
                    r"@property\s*\n\s+def\s+(\w+)\s*\(self\)",
                    re.MULTILINE,
                )
                for pm in prop_pattern.finditer(body):
                    model_info["properties"].append(pm.group(1))

                # Parse regular methods (def xxx(self, ...))
                method_pattern = re.compile(
                    r"^\s+def\s+(\w+)\s*\(self(?:,\s*[^)]*)?\)\s*:",
                    re.MULTILINE,
                )
                for mm in method_pattern.finditer(body):
                    mname = mm.group(1)
                    if mname not in model_info["properties"] and mname != "__init__":
                        model_info["methods"].append(mname)

                # Parse __str__ representation
                str_match = re.search(
                    r"def\s+__str__\s*\(self\)\s*:\s*\n\s+return\s+(.+)",
                    body,
                )
                if str_match:
                    model_info["str_representation"] = str_match.group(1).strip()

                result["models"][name] = model_info

        result["total_models"] = len(result["models"])
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  2. get_url_map
    # ══════════════════════════════════════════════════════════════════════

    def get_url_map(self, filter_prefix: str = None) -> Dict[str, Any]:
        """
        Parse urls.py files for urlpatterns. Extract path(), include(), name,
        view function/class, and namespace. Handle nested includes.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {"status": "success", "urls": [], "namespaces": {}}
        url_files = self._find_files_by_name(base, ["urls.py"])

        for fpath in url_files:
            content = self._read_file_safe(fpath)
            if not content:
                continue
            rel_path = os.path.relpath(fpath, base)

            # Detect app_name for namespace
            app_name_match = re.search(r"app_name\s*=\s*['\"](\w+)['\"]", content)
            namespace = app_name_match.group(1) if app_name_match else None
            if namespace:
                result["namespaces"][namespace] = rel_path

            # Parse urlpatterns list
            # Match path(...) and re_path(...)
            path_pattern = re.compile(
                r"""(?:re_)?path\s*\(\s*"""
                r"""['\"]([^'\"]*)['\"]"""              # URL pattern
                r"""\s*,\s*"""
                r"""([^,\)]+)"""                        # View / include
                r"""(?:\s*,\s*(?:name\s*=\s*)?['\"]([^'\"]+)['\"])?"""  # Optional name
                r"""(?:\s*,\s*name\s*=\s*['\"]([^'\"]+)['\"])?"""       # name=kwarg
                r"""[^)]*\)""",
                re.MULTILINE,
            )

            for pm in path_pattern.finditer(content):
                url_path = pm.group(1)
                view_ref = pm.group(2).strip()
                name = pm.group(3) or pm.group(4) or None

                url_entry: Dict[str, Any] = {
                    "pattern": url_path,
                    "file": rel_path,
                }

                # Check if it's an include()
                include_match = re.match(
                    r"""include\s*\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*namespace\s*=\s*['\"](\w+)['\"])?""",
                    view_ref,
                )
                include_match2 = re.match(
                    r"""include\s*\(\s*\(?\s*['\"]([^'\"]+)['\"]""",
                    view_ref,
                )

                if include_match or include_match2:
                    match = include_match or include_match2
                    url_entry["type"] = "include"
                    url_entry["include_module"] = match.group(1)
                    if include_match and include_match.group(2):
                        url_entry["namespace"] = include_match.group(2)
                    elif namespace:
                        url_entry["namespace"] = namespace
                else:
                    url_entry["type"] = "view"
                    # Parse view reference
                    as_view_match = re.match(r"(\w+(?:\.\w+)*)\.as_view\s*\(", view_ref)
                    if as_view_match:
                        url_entry["view_class"] = as_view_match.group(1)
                        url_entry["view_type"] = "CBV"
                    else:
                        # Function-based view
                        view_name = view_ref.strip().rstrip(",")
                        url_entry["view_function"] = view_name
                        url_entry["view_type"] = "FBV"

                    if name:
                        url_entry["name"] = name

                # Apply filter
                if filter_prefix:
                    matched = False
                    if name and name.startswith(filter_prefix):
                        matched = True
                    elif url_path.startswith(filter_prefix):
                        matched = True
                    elif namespace and filter_prefix.startswith(namespace):
                        matched = True
                    if not matched:
                        continue

                result["urls"].append(url_entry)

            # Parse Router registrations (DRF)
            router_pattern = re.compile(
                r"""router\.register\s*\(\s*r?['\"]([^'\"]+)['\"]"""
                r"""\s*,\s*(\w+(?:\.\w+)*)"""
                r"""(?:\s*,\s*basename\s*=\s*['\"](\w+)['\"])?""",
            )
            for rm in router_pattern.finditer(content):
                url_entry = {
                    "pattern": rm.group(1),
                    "view_class": rm.group(2),
                    "view_type": "ViewSet",
                    "type": "router",
                    "file": rel_path,
                }
                if rm.group(3):
                    url_entry["basename"] = rm.group(3)
                result["urls"].append(url_entry)

        result["total_urls"] = len(result["urls"])
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  3. get_migration_schema
    # ══════════════════════════════════════════════════════════════════════

    def get_migration_schema(self, table_name: str = None) -> Dict[str, Any]:
        """
        Parse migration files in */migrations/. Extract CreateModel fields,
        AddField, AlterField, AddIndex operations. Return schema per model/table.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {"status": "success", "tables": {}}

        # Find all migration directories
        migration_dirs: List[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            if os.path.basename(root) == "migrations" and "__init__.py" in files:
                migration_dirs.append(root)

        for mig_dir in migration_dirs:
            app_dir = os.path.dirname(mig_dir)
            app_name = os.path.basename(app_dir)
            mig_files = sorted([
                f for f in os.listdir(mig_dir)
                if f.endswith(".py") and f != "__init__.py"
            ])

            for mig_file in mig_files:
                fpath = os.path.join(mig_dir, mig_file)
                content = self._read_file_safe(fpath)
                if not content:
                    continue
                rel_path = os.path.relpath(fpath, base)

                # Parse CreateModel operations
                create_pattern = re.compile(
                    r"migrations\.CreateModel\s*\(\s*"
                    r"name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*fields\s*=\s*\[(.*?)\]",
                    re.DOTALL,
                )
                for cm in create_pattern.finditer(content):
                    model_name_val = cm.group(1)
                    tbl_name = f"{app_name}_{model_name_val.lower()}"

                    if table_name and table_name != tbl_name and table_name != model_name_val:
                        continue

                    table_key = tbl_name
                    if table_key not in result["tables"]:
                        result["tables"][table_key] = {
                            "model": model_name_val,
                            "app": app_name,
                            "fields": {},
                            "indexes": [],
                            "migrations": [],
                        }

                    # Parse fields from CreateModel
                    fields_str = cm.group(2)
                    field_entries = re.findall(
                        r"""\(\s*['\"](\w+)['\"]\s*,\s*(?:models\.)?(\w+)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)""",
                        fields_str,
                    )
                    for field_name, field_type, field_args in field_entries:
                        field_info: Dict[str, Any] = {"type": field_type}
                        max_len = re.search(r"max_length\s*=\s*(\d+)", field_args)
                        if max_len:
                            field_info["max_length"] = int(max_len.group(1))
                        null_m = re.search(r"null\s*=\s*(True|False)", field_args)
                        if null_m:
                            field_info["null"] = null_m.group(1) == "True"
                        blank_m = re.search(r"blank\s*=\s*(True|False)", field_args)
                        if blank_m:
                            field_info["blank"] = blank_m.group(1) == "True"
                        default_m = re.search(r"default\s*=\s*([^,\)]+)", field_args)
                        if default_m:
                            field_info["default"] = default_m.group(1).strip()
                        pk_m = re.search(r"primary_key\s*=\s*(True|False)", field_args)
                        if pk_m:
                            field_info["primary_key"] = pk_m.group(1) == "True"
                        unique_m = re.search(r"unique\s*=\s*(True|False)", field_args)
                        if unique_m:
                            field_info["unique"] = unique_m.group(1) == "True"

                        # Foreign key target
                        if field_type in ("ForeignKey", "OneToOneField"):
                            fk_target = re.search(
                                r"""(?:to\s*=\s*)?['\"]?(\w+(?:\.\w+)?)['\"]?""",
                                field_args,
                            )
                            if fk_target:
                                field_info["references"] = fk_target.group(1)
                            on_del = re.search(r"on_delete\s*=\s*(?:django\.db\.)?(?:models\.)?(\w+)", field_args)
                            if on_del:
                                field_info["on_delete"] = on_del.group(1)

                        result["tables"][table_key]["fields"][field_name] = field_info

                    result["tables"][table_key]["migrations"].append({
                        "file": rel_path,
                        "operation": "CreateModel",
                    })

                # Parse AddField operations
                add_field_pattern = re.compile(
                    r"migrations\.AddField\s*\(\s*"
                    r"model_name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*field\s*=\s*(?:models\.)?(\w+)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)",
                    re.DOTALL,
                )
                for af in add_field_pattern.finditer(content):
                    af_model = af.group(1)
                    af_field_name = af.group(2)
                    af_field_type = af.group(3)
                    af_args = af.group(4)
                    af_tbl = f"{app_name}_{af_model.lower()}"

                    if table_name and table_name != af_tbl and table_name != af_model:
                        continue

                    if af_tbl not in result["tables"]:
                        result["tables"][af_tbl] = {
                            "model": af_model,
                            "app": app_name,
                            "fields": {},
                            "indexes": [],
                            "migrations": [],
                        }

                    field_info = {"type": af_field_type, "added_by": rel_path}
                    max_len = re.search(r"max_length\s*=\s*(\d+)", af_args)
                    if max_len:
                        field_info["max_length"] = int(max_len.group(1))
                    null_m = re.search(r"null\s*=\s*(True|False)", af_args)
                    if null_m:
                        field_info["null"] = null_m.group(1) == "True"
                    default_m = re.search(r"default\s*=\s*([^,\)]+)", af_args)
                    if default_m:
                        field_info["default"] = default_m.group(1).strip()

                    result["tables"][af_tbl]["fields"][af_field_name] = field_info
                    result["tables"][af_tbl]["migrations"].append({
                        "file": rel_path,
                        "operation": "AddField",
                        "field": af_field_name,
                    })

                # Parse AlterField operations
                alter_pattern = re.compile(
                    r"migrations\.AlterField\s*\(\s*"
                    r"model_name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*field\s*=\s*(?:models\.)?(\w+)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)",
                    re.DOTALL,
                )
                for alt in alter_pattern.finditer(content):
                    alt_model = alt.group(1)
                    alt_field = alt.group(2)
                    alt_type = alt.group(3)
                    alt_args = alt.group(4)
                    alt_tbl = f"{app_name}_{alt_model.lower()}"

                    if table_name and table_name != alt_tbl and table_name != alt_model:
                        continue

                    if alt_tbl in result["tables"] and alt_field in result["tables"][alt_tbl]["fields"]:
                        result["tables"][alt_tbl]["fields"][alt_field]["type"] = alt_type
                        result["tables"][alt_tbl]["fields"][alt_field]["altered_by"] = rel_path

                # Parse AddIndex operations
                index_pattern = re.compile(
                    r"migrations\.AddIndex\s*\(\s*"
                    r"model_name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*index\s*=\s*models\.Index\s*\("
                    r"[^)]*fields\s*=\s*\[([^\]]+)\]"
                    r"(?:[^)]*name\s*=\s*['\"]([^'\"]+)['\"])?",
                    re.DOTALL,
                )
                for idx in index_pattern.finditer(content):
                    idx_model = idx.group(1)
                    idx_fields_str = idx.group(2)
                    idx_name = idx.group(3) or "unnamed"
                    idx_tbl = f"{app_name}_{idx_model.lower()}"

                    if table_name and table_name != idx_tbl and table_name != idx_model:
                        continue

                    if idx_tbl in result["tables"]:
                        idx_fields = [
                            f.strip().strip("'\"")
                            for f in idx_fields_str.split(",")
                            if f.strip()
                        ]
                        result["tables"][idx_tbl]["indexes"].append({
                            "name": idx_name,
                            "fields": idx_fields,
                            "migration": rel_path,
                        })

                # Parse RemoveField
                remove_pattern = re.compile(
                    r"migrations\.RemoveField\s*\(\s*"
                    r"model_name\s*=\s*['\"](\w+)['\"]"
                    r"\s*,\s*name\s*=\s*['\"](\w+)['\"]",
                )
                for rm in remove_pattern.finditer(content):
                    rm_model = rm.group(1)
                    rm_field = rm.group(2)
                    rm_tbl = f"{app_name}_{rm_model.lower()}"
                    if rm_tbl in result["tables"] and rm_field in result["tables"][rm_tbl]["fields"]:
                        result["tables"][rm_tbl]["fields"][rm_field]["removed_by"] = rel_path

        result["total_tables"] = len(result["tables"])
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  4. get_template_dependencies
    # ══════════════════════════════════════════════════════════════════════

    def get_template_dependencies(self, template_path: str) -> Dict[str, Any]:
        """
        Parse a Django template for extends, include, block, load, url references,
        template tags, and filters. Returns a dependency tree.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Try to resolve template path
        abs_path = os.path.join(base, template_path) if not os.path.isabs(template_path) else template_path
        if not os.path.isfile(abs_path):
            # Search in templates directories
            for root, dirs, files in os.walk(base):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
                ]
                if template_path in [os.path.relpath(os.path.join(root, f), base) for f in files]:
                    abs_path = os.path.join(root, os.path.basename(template_path))
                    break
                # Also check if the basename matches
                for f in files:
                    if os.path.join(root, f).endswith(template_path):
                        abs_path = os.path.join(root, f)
                        break

        if not os.path.isfile(abs_path):
            return {"status": "error", "message": f"Template not found: {template_path}"}

        content = self._read_file_safe(abs_path)
        if not content:
            return {"status": "error", "message": f"Cannot read template: {template_path}"}

        rel_path = os.path.relpath(abs_path, base)
        result: Dict[str, Any] = {
            "status": "success",
            "template": rel_path,
            "extends": None,
            "includes": [],
            "blocks": [],
            "loads": [],
            "url_references": [],
            "static_references": [],
            "template_tags": [],
            "filters": [],
            "variables": [],
            "comments": [],
            "forms": [],
        }

        # Parse {% extends "..." %}
        extends_match = re.search(r"""\{%\s*extends\s+['\"]([^'\"]+)['\"]\s*%\}""", content)
        if extends_match:
            result["extends"] = extends_match.group(1)

        # Parse {% include "..." %}
        include_pattern = re.compile(r"""\{%\s*include\s+['\"]([^'\"]+)['\"](?:\s+with\s+([^%]+))?\s*%\}""")
        for im in include_pattern.finditer(content):
            inc_info: Dict[str, Any] = {"template": im.group(1)}
            if im.group(2):
                inc_info["context"] = im.group(2).strip()
            result["includes"].append(inc_info)

        # Parse {% block name %}
        block_pattern = re.compile(r"""\{%\s*block\s+(\w+)\s*%\}""")
        for bm in block_pattern.finditer(content):
            result["blocks"].append(bm.group(1))

        # Parse {% load ... %}
        load_pattern = re.compile(r"""\{%\s*load\s+([^%]+)\s*%\}""")
        for lm in load_pattern.finditer(content):
            libs = lm.group(1).strip().split()
            result["loads"].extend(libs)

        # Parse {% url 'name' %} references
        url_pattern = re.compile(r"""\{%\s*url\s+['\"]([^'\"]+)['\"]([^%]*)\s*%\}""")
        for um in url_pattern.finditer(content):
            url_info: Dict[str, Any] = {"name": um.group(1)}
            args_str = um.group(2).strip()
            if args_str:
                url_info["args"] = args_str
            result["url_references"].append(url_info)

        # Parse {% static 'path' %} references
        static_pattern = re.compile(r"""\{%\s*static\s+['\"]([^'\"]+)['\"]\s*%\}""")
        for sm in static_pattern.finditer(content):
            result["static_references"].append(sm.group(1))

        # Parse template variables {{ var }}
        var_pattern = re.compile(r"""\{\{\s*([^}|]+?)(?:\|[^}]+)?\s*\}\}""")
        vars_seen: Set[str] = set()
        for vm in var_pattern.finditer(content):
            var_name = vm.group(1).strip()
            if var_name and var_name not in vars_seen:
                vars_seen.add(var_name)
                result["variables"].append(var_name)

        # Parse template filters ({{ var|filter }})
        filter_pattern = re.compile(r"""\{\{[^}]+\|(\w+)(?::[^}]*)?\s*\}\}""")
        filters_seen: Set[str] = set()
        for fm in filter_pattern.finditer(content):
            filt = fm.group(1)
            if filt not in filters_seen:
                filters_seen.add(filt)
                result["filters"].append(filt)

        # Parse custom template tags {% tag_name ... %}
        custom_tag_pattern = re.compile(r"""\{%\s*(\w+)(?:\s+[^%]*)?\s*%\}""")
        builtin_tags = {
            "extends", "include", "block", "endblock", "load", "url", "static",
            "if", "elif", "else", "endif", "for", "endfor", "empty",
            "with", "endwith", "csrf_token", "comment", "endcomment",
            "spaceless", "endspaceless", "autoescape", "endautoescape",
            "verbatim", "endverbatim", "filter", "endfilter",
            "firstof", "cycle", "resetcycle", "now", "regroup",
            "templatetag", "widthratio", "debug", "lorem",
            "trans", "blocktrans", "endblocktrans", "language",
            "get_current_language", "get_available_languages",
        }
        tags_seen: Set[str] = set()
        for tm in custom_tag_pattern.finditer(content):
            tag = tm.group(1)
            if tag not in builtin_tags and tag not in tags_seen and not tag.startswith("end"):
                tags_seen.add(tag)
                result["template_tags"].append(tag)

        # Parse {# comments #}
        comment_pattern = re.compile(r"""\{#\s*(.+?)\s*#\}""")
        for cm in comment_pattern.finditer(content):
            result["comments"].append(cm.group(1))

        # Parse form-related references
        form_pattern = re.compile(r"""\{\{\s*(form[\w.]*)\s*\}\}""")
        for fm in form_pattern.finditer(content):
            form_ref = fm.group(1)
            if form_ref not in result["forms"]:
                result["forms"].append(form_ref)

        # Also detect <form> tags with action
        html_form = re.compile(r"""<form[^>]*action\s*=\s*['\"]([^'\"]*)['\"][^>]*>""", re.IGNORECASE)
        for hf in html_form.finditer(content):
            result["forms"].append({"html_action": hf.group(1)})

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  5. get_flow_map
    # ══════════════════════════════════════════════════════════════════════

    def get_flow_map(self, entry_point: str, method: str = None) -> Dict[str, Any]:
        """
        Trace the complete request flow: URL -> middleware -> view (CBV/FBV) ->
        form/serializer -> model -> template/JSON response.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "entry_point": entry_point,
            "flow": {},
        }

        # Step 1: Find the URL pattern for this entry point
        url_map = self.get_url_map()
        matched_urls = []
        for url_entry in url_map.get("urls", []):
            name = url_entry.get("name", "")
            view_class = url_entry.get("view_class", "")
            view_func = url_entry.get("view_function", "")
            pattern = url_entry.get("pattern", "")
            if (entry_point in (name, view_class, view_func, pattern)
                    or entry_point in view_class or entry_point in view_func):
                matched_urls.append(url_entry)

        result["flow"]["urls"] = matched_urls

        # Step 2: Get middleware chain
        middleware = self.get_middleware_chain()
        result["flow"]["middleware"] = middleware.get("middleware", [])

        # Step 3: Find the view
        view_info: Dict[str, Any] = {"found": False}
        view_files = self._find_files_by_name(base, ["views.py", "viewsets.py", "api.py"])
        view_class_name = entry_point
        # Extract from URL map if possible
        if matched_urls:
            vc = matched_urls[0].get("view_class", "")
            vf = matched_urls[0].get("view_function", "")
            view_class_name = vc or vf or entry_point

        for vf_path in view_files:
            content = self._read_file_safe(vf_path)
            if not content:
                continue
            rel_path = os.path.relpath(vf_path, base)
            classes = self._parse_python_classes(content)

            for cls in classes:
                if cls["name"] == view_class_name or view_class_name in cls["name"]:
                    view_info["found"] = True
                    view_info["file"] = rel_path
                    view_info["class_name"] = cls["name"]
                    view_info["bases"] = cls["bases"]
                    view_info["decorators"] = cls["decorators"]
                    view_info["type"] = "CBV"

                    body = cls["body"]

                    # Extract HTTP method handlers
                    http_methods = []
                    method_pattern = re.compile(
                        r"def\s+(get|post|put|patch|delete|head|options|list|create|retrieve|update|partial_update|destroy)\s*\(",
                    )
                    for mm in method_pattern.finditer(body):
                        http_methods.append(mm.group(1))
                    view_info["http_methods"] = http_methods

                    if method:
                        # Extract specific method body
                        method_body_pattern = re.compile(
                            r"def\s+" + re.escape(method) + r"\s*\([^)]*\)\s*:(.*?)(?=\n\s+def\s|\Z)",
                            re.DOTALL,
                        )
                        mb_match = method_body_pattern.search(body)
                        if mb_match:
                            method_body = mb_match.group(1)
                            view_info["method_body_analysis"] = self._analyze_view_body(method_body)

                    # Extract permission/auth decorators
                    perm_patterns = [
                        r"permission_classes\s*=\s*\[([^\]]+)\]",
                        r"authentication_classes\s*=\s*\[([^\]]+)\]",
                    ]
                    for pp in perm_patterns:
                        pm = re.search(pp, body)
                        if pm:
                            key = "permissions" if "permission" in pp else "authentication"
                            view_info[key] = [c.strip() for c in pm.group(1).split(",") if c.strip()]

                    # Extract serializer_class
                    ser_match = re.search(r"serializer_class\s*=\s*(\w+)", body)
                    if ser_match:
                        view_info["serializer_class"] = ser_match.group(1)

                    # Extract form_class
                    form_match = re.search(r"form_class\s*=\s*(\w+)", body)
                    if form_match:
                        view_info["form_class"] = form_match.group(1)

                    # Extract template_name
                    tmpl_match = re.search(r"template_name\s*=\s*['\"]([^'\"]+)['\"]", body)
                    if tmpl_match:
                        view_info["template_name"] = tmpl_match.group(1)

                    # Extract model
                    model_match = re.search(r"model\s*=\s*(\w+)", body)
                    if model_match:
                        view_info["model"] = model_match.group(1)

                    # Extract queryset
                    qs_match = re.search(r"queryset\s*=\s*(\w+\.\w+\.[^\n]+)", body)
                    if qs_match:
                        view_info["queryset"] = qs_match.group(1).strip()

                    break

            # Also check for FBV
            if not view_info["found"]:
                fn_pattern = re.compile(
                    r"(?:@[^\n]+\n)*def\s+" + re.escape(view_class_name) + r"\s*\(([^)]*)\)\s*:",
                )
                fn_match = fn_pattern.search(content)
                if fn_match:
                    view_info["found"] = True
                    view_info["file"] = rel_path
                    view_info["function_name"] = view_class_name
                    view_info["type"] = "FBV"
                    view_info["params"] = fn_match.group(1)

                    # Find decorators above the function
                    lines = content[:fn_match.start()].split("\n")
                    decorators = []
                    for line in reversed(lines):
                        if line.strip().startswith("@"):
                            decorators.insert(0, line.strip())
                        elif line.strip() == "" or line.strip().startswith("#"):
                            continue
                        else:
                            break
                    view_info["decorators"] = decorators

                    # Analyze the function body
                    func_end_pattern = re.compile(
                        r"def\s+" + re.escape(view_class_name) + r"\s*\([^)]*\)\s*:(.*?)(?=\ndef\s|\Z)",
                        re.DOTALL,
                    )
                    fb_match = func_end_pattern.search(content)
                    if fb_match:
                        view_info["body_analysis"] = self._analyze_view_body(fb_match.group(1))

            if view_info["found"]:
                break

        result["flow"]["view"] = view_info

        # Step 4: Trace form/serializer if found
        serializer_name = view_info.get("serializer_class")
        form_name = view_info.get("form_class")

        if serializer_name:
            ser_info = self._find_serializer(base, serializer_name)
            result["flow"]["serializer"] = ser_info

        if form_name:
            form_info = self._find_form(base, form_name)
            result["flow"]["form"] = form_info

        # Step 5: Model info
        model_name_flow = view_info.get("model")
        if model_name_flow:
            model_info = self.get_model_relationships(model_name_flow)
            if model_info.get("models"):
                result["flow"]["model"] = model_info["models"].get(model_name_flow, {})

        # Step 6: Template info
        template_name = view_info.get("template_name")
        if template_name:
            tmpl_info = self.get_template_dependencies(template_name)
            result["flow"]["template"] = tmpl_info

        return result

    def _analyze_view_body(self, body: str) -> Dict[str, Any]:
        """Analyze a view method/function body for operations performed."""
        analysis: Dict[str, Any] = {
            "model_operations": [],
            "form_operations": [],
            "redirects": [],
            "renders": [],
            "json_responses": [],
            "signals": [],
            "cache_operations": [],
        }

        # Model operations
        orm_ops = re.findall(
            r"(\w+)\.objects\.(filter|get|create|update|delete|all|exclude|annotate|aggregate|select_related|prefetch_related|values|values_list|count|exists|first|last|order_by|distinct|bulk_create|bulk_update|get_or_create|update_or_create)\s*\(",
            body,
        )
        for model, op in orm_ops:
            analysis["model_operations"].append({"model": model, "operation": op})

        # .save() calls
        save_ops = re.findall(r"(\w+)\.save\s*\(", body)
        for var in save_ops:
            analysis["model_operations"].append({"variable": var, "operation": "save"})

        # .delete() calls
        del_ops = re.findall(r"(\w+)\.delete\s*\(", body)
        for var in del_ops:
            analysis["model_operations"].append({"variable": var, "operation": "delete"})

        # Form operations
        form_ops = re.findall(r"(\w+)\.(is_valid|cleaned_data|save|errors|clean)\b", body)
        for form, op in form_ops:
            analysis["form_operations"].append({"form": form, "operation": op})

        # Redirects
        redirect_matches = re.findall(r"redirect\s*\(\s*['\"]([^'\"]*)['\"]", body)
        analysis["redirects"].extend(redirect_matches)
        reverse_matches = re.findall(r"redirect\s*\(\s*reverse\s*\(\s*['\"]([^'\"]+)['\"]", body)
        analysis["redirects"].extend(reverse_matches)

        # Render calls
        render_matches = re.findall(r"render\s*\([^,]*,\s*['\"]([^'\"]+)['\"]", body)
        analysis["renders"].extend(render_matches)

        # JSON responses
        json_matches = re.findall(r"JsonResponse\s*\(|Response\s*\(", body)
        if json_matches:
            analysis["json_responses"].append({"count": len(json_matches)})

        # Signal sends
        signal_matches = re.findall(r"(\w+)\.send\s*\(|(\w+)\.send_robust\s*\(", body)
        for sig1, sig2 in signal_matches:
            analysis["signals"].append(sig1 or sig2)

        # Cache operations
        cache_ops = re.findall(r"cache\.(get|set|delete|clear|get_or_set)\s*\(\s*['\"]?([^'\")\s,]+)", body)
        for op, key in cache_ops:
            analysis["cache_operations"].append({"operation": op, "key": key})

        return analysis

    def _find_serializer(self, base: str, serializer_name: str) -> Dict[str, Any]:
        """Find and parse a DRF serializer class."""
        ser_files = self._find_files_by_name(base, ["serializers.py"])
        for fpath in ser_files:
            content = self._read_file_safe(fpath)
            if not content:
                continue
            rel_path = os.path.relpath(fpath, base)
            classes = self._parse_python_classes(content)
            for cls in classes:
                if cls["name"] == serializer_name:
                    body = cls["body"]
                    info: Dict[str, Any] = {
                        "name": serializer_name,
                        "file": rel_path,
                        "bases": cls["bases"],
                        "fields": [],
                        "meta": {},
                    }
                    # Parse Meta class
                    meta_match = re.search(
                        r"class\s+Meta\s*:\s*\n((?:\s+[^\n]+\n)*)", body,
                    )
                    if meta_match:
                        meta_body = meta_match.group(1)
                        model_m = re.search(r"model\s*=\s*(\w+)", meta_body)
                        if model_m:
                            info["meta"]["model"] = model_m.group(1)
                        fields_m = re.search(r"fields\s*=\s*['\"]__all__['\"]", meta_body)
                        if fields_m:
                            info["meta"]["fields"] = "__all__"
                        else:
                            fields_list_m = re.search(r"fields\s*=\s*\[([^\]]+)\]", meta_body)
                            if fields_list_m:
                                info["meta"]["fields"] = [
                                    f.strip().strip("'\"")
                                    for f in fields_list_m.group(1).split(",")
                                    if f.strip()
                                ]
                            fields_tuple_m = re.search(r"fields\s*=\s*\(([^\)]+)\)", meta_body)
                            if fields_tuple_m:
                                info["meta"]["fields"] = [
                                    f.strip().strip("'\"")
                                    for f in fields_tuple_m.group(1).split(",")
                                    if f.strip()
                                ]
                        exclude_m = re.search(r"exclude\s*=\s*[\[\(]([^\]\)]+)[\]\)]", meta_body)
                        if exclude_m:
                            info["meta"]["exclude"] = [
                                f.strip().strip("'\"")
                                for f in exclude_m.group(1).split(",")
                                if f.strip()
                            ]
                        read_only = re.search(r"read_only_fields\s*=\s*[\[\(]([^\]\)]+)[\]\)]", meta_body)
                        if read_only:
                            info["meta"]["read_only_fields"] = [
                                f.strip().strip("'\"")
                                for f in read_only.group(1).split(",")
                                if f.strip()
                            ]
                        extra_kwargs = re.search(r"extra_kwargs\s*=\s*\{", meta_body)
                        if extra_kwargs:
                            info["meta"]["has_extra_kwargs"] = True

                    # Explicit field declarations
                    field_pattern = re.compile(
                        r"^\s+(\w+)\s*=\s*serializers\.(\w+)\s*\(",
                        re.MULTILINE,
                    )
                    for fm in field_pattern.finditer(body):
                        info["fields"].append({
                            "name": fm.group(1),
                            "type": fm.group(2),
                        })

                    # Methods
                    methods = re.findall(r"def\s+(validate_\w+|validate|create|update|to_representation|to_internal_value)\s*\(", body)
                    info["custom_methods"] = methods

                    return info
        return {"name": serializer_name, "found": False}

    def _find_form(self, base: str, form_name: str) -> Dict[str, Any]:
        """Find and parse a Django Form/ModelForm class."""
        form_files = self._find_files_by_name(base, ["forms.py"])
        for fpath in form_files:
            content = self._read_file_safe(fpath)
            if not content:
                continue
            rel_path = os.path.relpath(fpath, base)
            classes = self._parse_python_classes(content)
            for cls in classes:
                if cls["name"] == form_name:
                    body = cls["body"]
                    info: Dict[str, Any] = {
                        "name": form_name,
                        "file": rel_path,
                        "bases": cls["bases"],
                        "fields": [],
                        "meta": {},
                        "clean_methods": [],
                    }
                    # Parse Meta class
                    meta_match = re.search(
                        r"class\s+Meta\s*:\s*\n((?:\s+[^\n]+\n)*)", body,
                    )
                    if meta_match:
                        meta_body = meta_match.group(1)
                        model_m = re.search(r"model\s*=\s*(\w+)", meta_body)
                        if model_m:
                            info["meta"]["model"] = model_m.group(1)
                        fields_m = re.search(r"fields\s*=\s*['\"]__all__['\"]", meta_body)
                        if fields_m:
                            info["meta"]["fields"] = "__all__"
                        else:
                            fields_list_m = re.search(r"fields\s*=\s*[\[\(]([^\]\)]+)[\]\)]", meta_body)
                            if fields_list_m:
                                info["meta"]["fields"] = [
                                    f.strip().strip("'\"")
                                    for f in fields_list_m.group(1).split(",")
                                    if f.strip()
                                ]
                        widgets_m = re.search(r"widgets\s*=\s*\{", meta_body)
                        if widgets_m:
                            info["meta"]["has_widgets"] = True
                        labels_m = re.search(r"labels\s*=\s*\{", meta_body)
                        if labels_m:
                            info["meta"]["has_labels"] = True

                    # Explicit field declarations
                    field_pattern = re.compile(
                        r"^\s+(\w+)\s*=\s*forms\.(\w+)\s*\(",
                        re.MULTILINE,
                    )
                    for fm in field_pattern.finditer(body):
                        info["fields"].append({
                            "name": fm.group(1),
                            "type": fm.group(2),
                        })

                    # Clean methods
                    clean_methods = re.findall(r"def\s+(clean(?:_\w+)?)\s*\(", body)
                    info["clean_methods"] = clean_methods

                    return info
        return {"name": form_name, "found": False}

    # ══════════════════════════════════════════════════════════════════════
    #  6. get_cache_map
    # ══════════════════════════════════════════════════════════════════════

    def get_cache_map(self, model_name: str = None) -> Dict[str, Any]:
        """
        Find cache usage: cache.get/set/delete, @cache_page, @vary_on_cookie,
        cached_property. Map model signals to cache invalidation.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "cache_operations": [],
            "cache_decorators": [],
            "cached_properties": [],
            "signal_invalidations": [],
            "forward_map": {},
            "reverse_map": {},
        }

        # Scan all Python files for cache usage
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules", "migrations")
            ]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                content = self._read_file_safe(fpath)
                if not content:
                    continue
                rel_path = os.path.relpath(fpath, base)

                # cache.get/set/delete/get_or_set
                cache_ops = re.finditer(
                    r"cache\.(get|set|delete|clear|get_or_set|get_many|set_many|delete_many)\s*\(\s*['\"]?([^'\"),\s]+)['\"]?",
                    content,
                )
                for cm in cache_ops:
                    op = cm.group(1)
                    key = cm.group(2)
                    entry = {"file": rel_path, "operation": op, "key": key}
                    result["cache_operations"].append(entry)

                    # Build forward/reverse maps
                    if op in ("set", "get_or_set", "set_many"):
                        if rel_path not in result["forward_map"]:
                            result["forward_map"][rel_path] = []
                        result["forward_map"][rel_path].append({"key": key, "operation": op})
                    if op in ("get", "get_or_set", "get_many"):
                        if key not in result["reverse_map"]:
                            result["reverse_map"][key] = {"readers": [], "invalidators": []}
                        result["reverse_map"][key]["readers"].append(rel_path)
                    if op in ("delete", "delete_many", "clear"):
                        if key not in result["reverse_map"]:
                            result["reverse_map"][key] = {"readers": [], "invalidators": []}
                        result["reverse_map"][key]["invalidators"].append(rel_path)

                # @cache_page decorator
                cache_page_matches = re.finditer(
                    r"@cache_page\s*\(\s*(\d+)\s*(?:,\s*key_prefix\s*=\s*['\"](\w+)['\"])?\s*\)",
                    content,
                )
                for cpm in cache_page_matches:
                    result["cache_decorators"].append({
                        "file": rel_path,
                        "decorator": "cache_page",
                        "timeout": int(cpm.group(1)),
                        "key_prefix": cpm.group(2),
                    })

                # @vary_on_cookie, @vary_on_headers
                vary_matches = re.finditer(
                    r"@(vary_on_cookie|vary_on_headers)\s*(?:\(([^)]*)\))?",
                    content,
                )
                for vm in vary_matches:
                    result["cache_decorators"].append({
                        "file": rel_path,
                        "decorator": vm.group(1),
                        "args": vm.group(2).strip() if vm.group(2) else None,
                    })

                # @cached_property
                cached_prop_matches = re.finditer(
                    r"@cached_property\s*\n\s+def\s+(\w+)\s*\(self\)",
                    content,
                )
                for cpm in cached_prop_matches:
                    result["cached_properties"].append({
                        "file": rel_path,
                        "property": cpm.group(1),
                    })

                # Signal-based cache invalidation
                signal_pattern = re.compile(
                    r"@receiver\s*\(\s*(post_save|post_delete|pre_save|pre_delete|m2m_changed)"
                    r"(?:\s*,\s*sender\s*=\s*(\w+))?\s*\)",
                )
                for sm in signal_pattern.finditer(content):
                    signal_type = sm.group(1)
                    sender = sm.group(2) or "Any"

                    if model_name and sender != model_name and sender != "Any":
                        continue

                    # Check if the receiver body contains cache operations
                    receiver_body_match = re.search(
                        r"@receiver\s*\([^)]+\)\s*\ndef\s+\w+\s*\([^)]*\)\s*:(.*?)(?=\n(?:@|def\s|class\s)|\Z)",
                        content[sm.start():],
                        re.DOTALL,
                    )
                    cache_in_receiver = False
                    if receiver_body_match:
                        rbody = receiver_body_match.group(1)
                        if re.search(r"cache\.(delete|clear|set)", rbody):
                            cache_in_receiver = True

                    result["signal_invalidations"].append({
                        "file": rel_path,
                        "signal": signal_type,
                        "sender": sender,
                        "invalidates_cache": cache_in_receiver,
                    })

        result["total_cache_operations"] = len(result["cache_operations"])
        result["total_cache_decorators"] = len(result["cache_decorators"])
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  7. get_middleware_chain
    # ══════════════════════════════════════════════════════════════════════

    def get_middleware_chain(self, url_path: str = None) -> Dict[str, Any]:
        """
        Parse MIDDLEWARE in settings.py. Extract class list, order, and any
        per-view @middleware_decorator usage.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "middleware": [],
            "per_view_middleware": [],
            "middleware_classes": [],
        }

        # Find settings files
        settings_files = self._find_settings_files(base)

        for sf in settings_files:
            content = self._read_file_safe(sf)
            if not content:
                continue
            rel_path = os.path.relpath(sf, base)

            # Parse MIDDLEWARE list
            mw_match = re.search(
                r"MIDDLEWARE\s*=\s*\[([^\]]+)\]",
                content,
                re.DOTALL,
            )
            if mw_match:
                mw_items = re.findall(r"['\"]([^'\"]+)['\"]", mw_match.group(1))
                for idx, mw in enumerate(mw_items):
                    mw_entry: Dict[str, Any] = {
                        "order": idx,
                        "class": mw,
                        "short_name": mw.split(".")[-1],
                        "settings_file": rel_path,
                    }
                    # Identify common middleware purpose
                    short = mw.split(".")[-1].lower()
                    if "security" in short:
                        mw_entry["purpose"] = "Security headers"
                    elif "session" in short:
                        mw_entry["purpose"] = "Session management"
                    elif "csrf" in short:
                        mw_entry["purpose"] = "CSRF protection"
                    elif "auth" in short:
                        mw_entry["purpose"] = "Authentication"
                    elif "message" in short:
                        mw_entry["purpose"] = "Messages framework"
                    elif "clickjack" in short:
                        mw_entry["purpose"] = "Clickjacking protection"
                    elif "common" in short:
                        mw_entry["purpose"] = "Common request/response processing"
                    elif "locale" in short:
                        mw_entry["purpose"] = "Locale/i18n"
                    elif "cors" in short:
                        mw_entry["purpose"] = "CORS handling"
                    elif "whitenoise" in short:
                        mw_entry["purpose"] = "Static file serving"
                    elif "gzip" in short:
                        mw_entry["purpose"] = "GZip compression"

                    result["middleware"].append(mw_entry)

        # Find custom middleware classes
        mw_files = self._find_files_by_name(base, ["middleware.py"])
        mw_dirs = []
        for root, dirs, files in os.walk(base):
            if os.path.basename(root) == "middleware" and "__init__.py" in files:
                mw_dirs.append(root)

        all_mw_files = mw_files[:]
        for md in mw_dirs:
            for f in os.listdir(md):
                if f.endswith(".py") and f != "__init__.py":
                    all_mw_files.append(os.path.join(md, f))

        for mf in all_mw_files:
            content = self._read_file_safe(mf)
            if not content:
                continue
            rel_path = os.path.relpath(mf, base)
            classes = self._parse_python_classes(content)
            for cls in classes:
                body = cls["body"]
                methods = re.findall(r"def\s+(\w+)\s*\(", body)
                mw_class_info: Dict[str, Any] = {
                    "name": cls["name"],
                    "file": rel_path,
                    "bases": cls["bases"],
                    "methods": methods,
                }
                # Check for process_request, process_response, __call__
                if "__call__" in methods:
                    mw_class_info["style"] = "new-style (callable)"
                elif "process_request" in methods or "process_response" in methods:
                    mw_class_info["style"] = "old-style (hooks)"
                result["middleware_classes"].append(mw_class_info)

        # Find per-view middleware decorators
        view_files = self._find_files_by_name(base, ["views.py", "viewsets.py"])
        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            rel_path = os.path.relpath(vf, base)

            # @csrf_exempt, @login_required, @permission_required, @method_decorator
            per_view_patterns = [
                (r"@csrf_exempt\s*\n\s*(?:class|def)\s+(\w+)", "csrf_exempt"),
                (r"@csrf_protect\s*\n\s*(?:class|def)\s+(\w+)", "csrf_protect"),
                (r"@login_required\s*(?:\([^)]*\))?\s*\n\s*def\s+(\w+)", "login_required"),
                (r"@permission_required\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\n\s*def\s+(\w+)", "permission_required"),
                (r"@require_http_methods\s*\(\s*\[([^\]]+)\]\s*\)\s*\n\s*def\s+(\w+)", "require_http_methods"),
                (r"@method_decorator\s*\(\s*(\w+)(?:\s*,\s*name\s*=\s*['\"](\w+)['\"])?\s*\)", "method_decorator"),
            ]
            for pattern, decorator_name in per_view_patterns:
                for pm in re.finditer(pattern, content):
                    entry: Dict[str, Any] = {
                        "file": rel_path,
                        "decorator": decorator_name,
                    }
                    if decorator_name == "permission_required":
                        entry["permission"] = pm.group(1)
                        entry["view"] = pm.group(2)
                    elif decorator_name == "require_http_methods":
                        entry["methods"] = pm.group(1)
                        entry["view"] = pm.group(2)
                    elif decorator_name == "method_decorator":
                        entry["wrapped"] = pm.group(1)
                        entry["method_name"] = pm.group(2)
                    else:
                        entry["view"] = pm.group(1)
                    result["per_view_middleware"].append(entry)

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  8. get_form_validation
    # ══════════════════════════════════════════════════════════════════════

    def get_form_validation(self, view_name: str) -> Dict[str, Any]:
        """
        Trace: ModelForm/Form -> fields -> validators -> clean methods ->
        template form rendering. Detect field mismatches.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "view_name": view_name,
            "view": {},
            "form": {},
            "model": {},
            "template": {},
            "mismatches": [],
        }

        # Step 1: Find the view
        view_files = self._find_files_by_name(base, ["views.py"])
        form_name = None
        model_name = None
        template_name = None

        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            rel_path = os.path.relpath(vf, base)
            classes = self._parse_python_classes(content)

            for cls in classes:
                if cls["name"] == view_name:
                    body = cls["body"]
                    result["view"] = {
                        "name": cls["name"],
                        "file": rel_path,
                        "bases": cls["bases"],
                    }

                    # Extract form_class
                    fm = re.search(r"form_class\s*=\s*(\w+)", body)
                    if fm:
                        form_name = fm.group(1)
                        result["view"]["form_class"] = form_name

                    # Extract model
                    mm = re.search(r"model\s*=\s*(\w+)", body)
                    if mm:
                        model_name = mm.group(1)
                        result["view"]["model"] = model_name

                    # Extract template
                    tm = re.search(r"template_name\s*=\s*['\"]([^'\"]+)['\"]", body)
                    if tm:
                        template_name = tm.group(1)
                        result["view"]["template_name"] = template_name

                    # Extract success_url
                    su = re.search(r"success_url\s*=\s*['\"]?([^'\"\n]+)['\"]?", body)
                    if su:
                        result["view"]["success_url"] = su.group(1).strip()

                    # Extract fields (for generic views)
                    fields_m = re.search(r"fields\s*=\s*[\[\(]([^\]\)]+)[\]\)]", body)
                    if fields_m:
                        result["view"]["fields"] = [
                            f.strip().strip("'\"")
                            for f in fields_m.group(1).split(",")
                            if f.strip()
                        ]
                    break

            # Check FBV
            if not result["view"]:
                fn_match = re.search(
                    r"def\s+" + re.escape(view_name) + r"\s*\([^)]*\)\s*:(.*?)(?=\ndef\s|\Z)",
                    content, re.DOTALL,
                )
                if fn_match:
                    result["view"] = {"name": view_name, "file": rel_path, "type": "FBV"}
                    fbody = fn_match.group(1)
                    # Find form instantiation
                    form_inst = re.search(r"(\w+)\s*=\s*(\w+Form\w*)\s*\(", fbody)
                    if form_inst:
                        form_name = form_inst.group(2)
                        result["view"]["form_class"] = form_name
                    render_m = re.search(r"render\s*\([^,]*,\s*['\"]([^'\"]+)['\"]", fbody)
                    if render_m:
                        template_name = render_m.group(1)
                        result["view"]["template_name"] = template_name

            if result["view"]:
                break

        # Step 2: Parse the form
        if form_name:
            form_info = self._find_form(base, form_name)
            result["form"] = form_info

            # Extract validators from form fields
            form_files = self._find_files_by_name(base, ["forms.py"])
            for ff in form_files:
                content = self._read_file_safe(ff)
                if not content:
                    continue
                classes = self._parse_python_classes(content)
                for cls in classes:
                    if cls["name"] == form_name:
                        body = cls["body"]
                        # Find validators=[] in field definitions
                        validator_pattern = re.compile(
                            r"(\w+)\s*=\s*forms\.\w+\s*\([^)]*validators\s*=\s*\[([^\]]+)\]",
                            re.DOTALL,
                        )
                        validators = {}
                        for vm in validator_pattern.finditer(body):
                            field = vm.group(1)
                            vals = [v.strip() for v in vm.group(2).split(",") if v.strip()]
                            validators[field] = vals
                        if validators:
                            result["form"]["validators"] = validators

                        # Find __init__ method for dynamic field modifications
                        init_match = re.search(
                            r"def\s+__init__\s*\(self[^)]*\)\s*:(.*?)(?=\n\s+def\s|\Z)",
                            body, re.DOTALL,
                        )
                        if init_match:
                            init_body = init_match.group(1)
                            field_mods = re.findall(
                                r"self\.fields\[['\"](\w+)['\"]\]\.(\w+)\s*=",
                                init_body,
                            )
                            if field_mods:
                                result["form"]["dynamic_field_modifications"] = [
                                    {"field": f, "attribute": a} for f, a in field_mods
                                ]
                        break

        # Step 3: Parse the model
        form_model = None
        if result.get("form") and isinstance(result["form"], dict):
            fm_meta = result["form"].get("meta", {})
            if isinstance(fm_meta, dict):
                form_model = fm_meta.get("model")
        target_model = model_name or form_model
        if target_model:
            model_data = self.get_model_relationships(target_model)
            if model_data.get("models") and target_model in model_data["models"]:
                result["model"] = model_data["models"][target_model]

        # Step 4: Parse the template
        if template_name:
            tmpl_data = self.get_template_dependencies(template_name)
            result["template"] = tmpl_data

        # Step 5: Detect mismatches
        form_fields_set: Set[str] = set()
        if isinstance(result.get("form"), dict):
            meta_fields = result["form"].get("meta", {}).get("fields", [])
            if isinstance(meta_fields, list):
                form_fields_set.update(meta_fields)
            for f in result["form"].get("fields", []):
                if isinstance(f, dict):
                    form_fields_set.add(f["name"])

        model_fields_set: Set[str] = set()
        if isinstance(result.get("model"), dict):
            model_fields_set = set(result["model"].get("fields", {}).keys())

        # Fields in form but not in model
        if form_fields_set and model_fields_set:
            form_only = form_fields_set - model_fields_set - {"id", "pk"}
            model_only = model_fields_set - form_fields_set
            if form_only:
                result["mismatches"].append({
                    "type": "form_field_not_in_model",
                    "fields": list(form_only),
                    "message": f"Form fields {list(form_only)} not found on model",
                })
            # Check for required model fields not in form
            required_fields = {
                fn for fn, fi in result.get("model", {}).get("fields", {}).items()
                if isinstance(fi, dict) and not fi.get("null") and not fi.get("blank")
                and not fi.get("default") and fn not in ("id", "pk")
                and fi.get("type") not in ("AutoField", "BigAutoField")
            }
            missing_required = required_fields - form_fields_set
            if missing_required:
                result["mismatches"].append({
                    "type": "required_model_field_missing_from_form",
                    "fields": list(missing_required),
                    "message": f"Required model fields {list(missing_required)} not in form",
                })

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  9. verify_endpoint
    # ══════════════════════════════════════════════════════════════════════

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Check: URL pattern exists, view handles HTTP method, permission classes,
        authentication requirements.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "method": method.upper(),
            "url": url,
            "checks": {},
        }

        method_upper = method.upper()

        # Step 1: Check URL pattern exists
        url_map = self.get_url_map()
        matched_url = None
        for ue in url_map.get("urls", []):
            pattern = ue.get("pattern", "")
            # Normalize URL for comparison
            normalized_url = url.strip("/")
            normalized_pattern = pattern.strip("/")
            # Simple match or regex match
            if normalized_url == normalized_pattern:
                matched_url = ue
                break
            # Try regex match for parameterized URLs
            regex_pattern = re.sub(r"<\w+:\w+>", r"[^/]+", normalized_pattern)
            regex_pattern = re.sub(r"<\w+>", r"[^/]+", regex_pattern)
            if re.match(f"^{regex_pattern}$", normalized_url):
                matched_url = ue
                break

        result["checks"]["url_registered"] = matched_url is not None
        if matched_url:
            result["checks"]["url_details"] = matched_url

        # Step 2: Check view handles the HTTP method
        if matched_url:
            view_class = matched_url.get("view_class", "")
            view_func = matched_url.get("view_function", "")
            view_type = matched_url.get("view_type", "")

            if view_type == "CBV" and view_class:
                # Find the view class and check for method handler
                view_files = self._find_files_by_name(base, ["views.py", "viewsets.py", "api.py"])
                class_name = view_class.split(".")[-1]
                for vf in view_files:
                    content = self._read_file_safe(vf)
                    if not content:
                        continue
                    classes = self._parse_python_classes(content)
                    for cls in classes:
                        if cls["name"] == class_name:
                            body = cls["body"]
                            http_method_lower = method_upper.lower()
                            has_method = bool(re.search(
                                r"def\s+" + http_method_lower + r"\s*\(", body,
                            ))
                            # Also check DRF action methods
                            drf_map = {
                                "GET": ["list", "retrieve", "get"],
                                "POST": ["create", "post"],
                                "PUT": ["update", "put"],
                                "PATCH": ["partial_update", "patch"],
                                "DELETE": ["destroy", "delete"],
                            }
                            for drf_method in drf_map.get(method_upper, []):
                                if re.search(r"def\s+" + drf_method + r"\s*\(", body):
                                    has_method = True
                                    break
                            # Also check base classes for implicit support
                            base_class_support = {
                                "ListView": ["GET"],
                                "DetailView": ["GET"],
                                "CreateView": ["GET", "POST"],
                                "UpdateView": ["GET", "POST", "PUT", "PATCH"],
                                "DeleteView": ["GET", "POST", "DELETE"],
                                "FormView": ["GET", "POST"],
                                "TemplateView": ["GET"],
                                "RedirectView": ["GET"],
                                "ModelViewSet": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                                "ReadOnlyModelViewSet": ["GET"],
                                "CreateModelMixin": ["POST"],
                                "UpdateModelMixin": ["PUT", "PATCH"],
                                "DestroyModelMixin": ["DELETE"],
                                "ListModelMixin": ["GET"],
                                "RetrieveModelMixin": ["GET"],
                                "APIView": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                            }
                            if not has_method:
                                for base_cls in cls["bases"]:
                                    base_name = base_cls.split(".")[-1]
                                    if base_name in base_class_support:
                                        if method_upper in base_class_support[base_name]:
                                            has_method = True
                                            break

                            result["checks"]["method_handled"] = has_method
                            if not has_method:
                                result["checks"]["method_warning"] = (
                                    f"View {class_name} may not handle {method_upper}"
                                )

                            # Check permission classes
                            perm_m = re.search(r"permission_classes\s*=\s*\[([^\]]+)\]", body)
                            if perm_m:
                                result["checks"]["permissions"] = [
                                    p.strip() for p in perm_m.group(1).split(",") if p.strip()
                                ]
                            # Check authentication classes
                            auth_m = re.search(r"authentication_classes\s*=\s*\[([^\]]+)\]", body)
                            if auth_m:
                                result["checks"]["authentication"] = [
                                    a.strip() for a in auth_m.group(1).split(",") if a.strip()
                                ]
                            # Check throttle classes
                            throttle_m = re.search(r"throttle_classes\s*=\s*\[([^\]]+)\]", body)
                            if throttle_m:
                                result["checks"]["throttle"] = [
                                    t.strip() for t in throttle_m.group(1).split(",") if t.strip()
                                ]
                            break

            elif view_type == "FBV":
                # Check FBV for method restrictions
                view_files = self._find_files_by_name(base, ["views.py"])
                for vf in view_files:
                    content = self._read_file_safe(vf)
                    if not content:
                        continue
                    fn_match = re.search(
                        r"((?:@[^\n]+\n)*)def\s+" + re.escape(view_func.split(".")[-1]) + r"\s*\(",
                        content,
                    )
                    if fn_match:
                        decorators_str = fn_match.group(1)
                        # Check for @require_http_methods
                        req_methods = re.search(
                            r"@require_http_methods\s*\(\s*\[([^\]]+)\]",
                            decorators_str,
                        )
                        if req_methods:
                            allowed = [
                                m.strip().strip("'\"")
                                for m in req_methods.group(1).split(",")
                            ]
                            result["checks"]["method_handled"] = method_upper in allowed
                            result["checks"]["allowed_methods"] = allowed
                        elif "@require_GET" in decorators_str:
                            result["checks"]["method_handled"] = method_upper == "GET"
                        elif "@require_POST" in decorators_str:
                            result["checks"]["method_handled"] = method_upper == "POST"
                        else:
                            result["checks"]["method_handled"] = True
                            result["checks"]["method_note"] = "No method restriction — all methods accepted"

                        # Check for auth decorators
                        if "@login_required" in decorators_str:
                            result["checks"]["requires_login"] = True
                        perm_req = re.search(r"@permission_required\s*\(\s*['\"]([^'\"]+)['\"]", decorators_str)
                        if perm_req:
                            result["checks"]["requires_permission"] = perm_req.group(1)
                        break

        # Step 3: Check middleware
        middleware = self.get_middleware_chain()
        result["checks"]["middleware_count"] = len(middleware.get("middleware", []))
        # Check for relevant per-view middleware
        relevant_pv = [
            pv for pv in middleware.get("per_view_middleware", [])
            if pv.get("view", "") == (matched_url or {}).get("view_class", "").split(".")[-1]
            or pv.get("view", "") == (matched_url or {}).get("view_function", "").split(".")[-1]
        ]
        if relevant_pv:
            result["checks"]["per_view_middleware"] = relevant_pv

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  10. get_project_conventions
    # ══════════════════════════════════════════════════════════════════════

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the Django project codebase.
        Supported: "naming", "testing", "authentication", "orm", "api".
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {"status": "success", "pattern_type": pattern_type, "conventions": {}}

        if pattern_type == "naming":
            result["conventions"] = self._extract_naming_conventions(base)
        elif pattern_type == "testing":
            result["conventions"] = self._extract_testing_conventions(base)
        elif pattern_type == "authentication":
            result["conventions"] = self._extract_auth_conventions(base)
        elif pattern_type == "orm":
            result["conventions"] = self._extract_orm_conventions(base)
        elif pattern_type == "api":
            result["conventions"] = self._extract_api_conventions(base)
        else:
            result["status"] = "error"
            result["message"] = f"Unknown pattern_type: {pattern_type}. Use: naming, testing, authentication, orm, api"

        return result

    def _extract_naming_conventions(self, base: str) -> Dict[str, Any]:
        """Extract naming patterns from the project."""
        conventions: Dict[str, Any] = {
            "app_names": [],
            "model_names": [],
            "view_names": [],
            "url_names": [],
            "form_names": [],
        }

        # App names
        apps = self._discover_apps(base)
        conventions["app_names"] = apps
        if apps:
            # Detect pattern: singular vs plural, snake_case
            conventions["app_naming_pattern"] = "snake_case"

        # Model names
        model_files = self._find_files_by_name(base, ["models.py"])
        for mf in model_files:
            content = self._read_file_safe(mf)
            if not content:
                continue
            classes = self._parse_python_classes(content)
            for cls in classes:
                if any("Model" in b or "model" in b.lower() for b in cls["bases"]):
                    conventions["model_names"].append(cls["name"])
        if conventions["model_names"]:
            # Check PascalCase
            all_pascal = all(re.match(r"^[A-Z][a-zA-Z0-9]*$", n) for n in conventions["model_names"])
            conventions["model_naming_pattern"] = "PascalCase" if all_pascal else "mixed"

        # View names
        view_files = self._find_files_by_name(base, ["views.py"])
        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            classes = self._parse_python_classes(content)
            for cls in classes:
                conventions["view_names"].append(cls["name"])
            # FBV names
            functions = self._parse_functions(content)
            for fn in functions:
                if fn["indent"] == 0 and fn["name"] not in ("__init__", "__str__"):
                    conventions["view_names"].append(fn["name"])

        # URL names
        url_map = self.get_url_map()
        for ue in url_map.get("urls", []):
            name = ue.get("name")
            if name:
                conventions["url_names"].append(name)
        if conventions["url_names"]:
            # Check naming: kebab-case, snake_case, dotted
            has_dots = any("." in n for n in conventions["url_names"])
            has_dashes = any("-" in n for n in conventions["url_names"])
            has_underscores = any("_" in n for n in conventions["url_names"])
            if has_dots:
                conventions["url_naming_pattern"] = "dotted (namespace.action)"
            elif has_dashes:
                conventions["url_naming_pattern"] = "kebab-case"
            elif has_underscores:
                conventions["url_naming_pattern"] = "snake_case"

        return conventions

    def _extract_testing_conventions(self, base: str) -> Dict[str, Any]:
        """Extract testing patterns from the project."""
        conventions: Dict[str, Any] = {
            "test_files": [],
            "test_classes": [],
            "test_methods": [],
            "base_classes_used": [],
            "fixtures_used": [],
            "factories_used": False,
        }

        test_files = self._find_files_by_name(base, ["tests.py"])
        # Also find test_*.py files
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            for f in files:
                if f.startswith("test_") and f.endswith(".py"):
                    test_files.append(os.path.join(root, f))
            # Also look in tests/ directories
            if os.path.basename(root) == "tests":
                for f in files:
                    if f.endswith(".py") and f != "__init__.py":
                        test_files.append(os.path.join(root, f))

        bases_seen: Set[str] = set()
        for tf in test_files:
            content = self._read_file_safe(tf)
            if not content:
                continue
            rel_path = os.path.relpath(tf, base)
            conventions["test_files"].append(rel_path)

            classes = self._parse_python_classes(content)
            for cls in classes:
                if cls["name"].startswith("Test") or "Test" in cls["name"]:
                    conventions["test_classes"].append(cls["name"])
                    for b in cls["bases"]:
                        bases_seen.add(b)

                    # Count test methods
                    test_methods = re.findall(r"def\s+(test_\w+)\s*\(", cls["body"])
                    conventions["test_methods"].extend(test_methods[:5])  # Sample

            # Check for fixtures
            if re.search(r"fixtures\s*=\s*\[", content):
                conventions["fixtures_used"].append(rel_path)

            # Check for factory_boy
            if "factory" in content.lower() and ("Factory" in content or "factory_boy" in content):
                conventions["factories_used"] = True

            # Check for pytest
            if "pytest" in content or "@pytest" in content:
                conventions["test_framework"] = "pytest"
            elif "TestCase" in content:
                conventions["test_framework"] = "unittest/Django TestCase"

        conventions["base_classes_used"] = list(bases_seen)
        conventions["total_test_files"] = len(conventions["test_files"])
        return conventions

    def _extract_auth_conventions(self, base: str) -> Dict[str, Any]:
        """Extract authentication patterns from the project."""
        conventions: Dict[str, Any] = {
            "auth_backends": [],
            "auth_decorators_used": [],
            "custom_user_model": None,
            "third_party_auth": [],
            "permission_classes": [],
        }

        settings_files = self._find_settings_files(base)
        for sf in settings_files:
            content = self._read_file_safe(sf)
            if not content:
                continue

            # AUTH_USER_MODEL
            auth_user = re.search(r"AUTH_USER_MODEL\s*=\s*['\"]([^'\"]+)['\"]", content)
            if auth_user:
                conventions["custom_user_model"] = auth_user.group(1)

            # AUTHENTICATION_BACKENDS
            auth_backends = re.search(r"AUTHENTICATION_BACKENDS\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
            if auth_backends:
                conventions["auth_backends"] = re.findall(r"['\"]([^'\"]+)['\"]", auth_backends.group(1))

            # REST_FRAMEWORK authentication
            rest_auth = re.search(
                r"DEFAULT_AUTHENTICATION_CLASSES['\"]?\s*:\s*[\[\(]([^\]\)]+)[\]\)]",
                content, re.DOTALL,
            )
            if rest_auth:
                conventions["drf_authentication"] = re.findall(r"['\"]([^'\"]+)['\"]", rest_auth.group(1))

            # DEFAULT_PERMISSION_CLASSES
            rest_perm = re.search(
                r"DEFAULT_PERMISSION_CLASSES['\"]?\s*:\s*[\[\(]([^\]\)]+)[\]\)]",
                content, re.DOTALL,
            )
            if rest_perm:
                conventions["drf_permissions"] = re.findall(r"['\"]([^'\"]+)['\"]", rest_perm.group(1))

            # Third-party auth (allauth, social_auth, etc.)
            installed_apps_m = re.search(r"INSTALLED_APPS\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
            if installed_apps_m:
                apps_str = installed_apps_m.group(1)
                if "allauth" in apps_str:
                    conventions["third_party_auth"].append("django-allauth")
                if "social_django" in apps_str:
                    conventions["third_party_auth"].append("social-auth-app-django")
                if "rest_framework_simplejwt" in apps_str or "simplejwt" in apps_str:
                    conventions["third_party_auth"].append("djangorestframework-simplejwt")
                if "oauth2_provider" in apps_str:
                    conventions["third_party_auth"].append("django-oauth-toolkit")
                if "rest_framework.authtoken" in apps_str:
                    conventions["third_party_auth"].append("DRF TokenAuthentication")

        # Scan views for auth decorators
        view_files = self._find_files_by_name(base, ["views.py", "viewsets.py"])
        decorators_seen: Set[str] = set()
        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            auth_decorators = re.findall(
                r"@(login_required|permission_required|user_passes_test|staff_member_required)",
                content,
            )
            decorators_seen.update(auth_decorators)

            # DRF permission classes in views
            perm_classes = re.findall(r"permission_classes\s*=\s*\[([^\]]+)\]", content)
            for pc in perm_classes:
                perms = [p.strip() for p in pc.split(",") if p.strip()]
                conventions["permission_classes"].extend(perms)

        conventions["auth_decorators_used"] = list(decorators_seen)
        conventions["permission_classes"] = list(set(conventions["permission_classes"]))
        return conventions

    def _extract_orm_conventions(self, base: str) -> Dict[str, Any]:
        """Extract ORM query patterns from the project."""
        conventions: Dict[str, Any] = {
            "common_querysets": [],
            "custom_managers": [],
            "raw_sql_usage": [],
            "select_related_usage": 0,
            "prefetch_related_usage": 0,
            "n_plus_one_risks": [],
            "annotations_used": 0,
            "aggregations_used": 0,
        }

        py_files: List[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules", "migrations")
            ]
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))

        for pf in py_files:
            content = self._read_file_safe(pf)
            if not content:
                continue
            rel_path = os.path.relpath(pf, base)

            # Count ORM patterns
            conventions["select_related_usage"] += len(re.findall(r"\.select_related\s*\(", content))
            conventions["prefetch_related_usage"] += len(re.findall(r"\.prefetch_related\s*\(", content))
            conventions["annotations_used"] += len(re.findall(r"\.annotate\s*\(", content))
            conventions["aggregations_used"] += len(re.findall(r"\.aggregate\s*\(", content))

            # Raw SQL
            raw_matches = re.findall(r"\.raw\s*\(\s*['\"]([^'\"]+)['\"]", content)
            for rm in raw_matches:
                conventions["raw_sql_usage"].append({"file": rel_path, "query_preview": rm[:80]})
            # connection.cursor() / extra()
            if re.search(r"connection\.cursor\s*\(|\.extra\s*\(", content):
                conventions["raw_sql_usage"].append({"file": rel_path, "type": "cursor/extra"})

            # Custom managers
            classes = self._parse_python_classes(content)
            for cls in classes:
                if any("Manager" in b for b in cls["bases"]):
                    conventions["custom_managers"].append({
                        "name": cls["name"],
                        "file": rel_path,
                    })

            # N+1 query risks: loops with .attribute access without select_related
            n1_pattern = re.compile(
                r"for\s+\w+\s+in\s+(\w+)\.objects\.(?:all|filter)\s*\([^)]*\).*?\n(?:\s+.*\n)*?\s+\w+\.\w+",
                re.MULTILINE,
            )
            for n1 in n1_pattern.finditer(content):
                conventions["n_plus_one_risks"].append({
                    "file": rel_path,
                    "model": n1.group(1),
                    "preview": n1.group(0)[:100],
                })

        conventions["total_raw_sql"] = len(conventions["raw_sql_usage"])
        return conventions

    def _extract_api_conventions(self, base: str) -> Dict[str, Any]:
        """Extract DRF / API patterns from the project."""
        conventions: Dict[str, Any] = {
            "drf_installed": False,
            "serializers": [],
            "viewsets": [],
            "routers": [],
            "pagination_class": None,
            "filter_backends": [],
            "versioning": None,
            "throttle_classes": [],
            "renderer_classes": [],
        }

        # Check DRF in settings
        settings_files = self._find_settings_files(base)
        for sf in settings_files:
            content = self._read_file_safe(sf)
            if not content:
                continue
            if "rest_framework" in content:
                conventions["drf_installed"] = True
            # Parse REST_FRAMEWORK setting
            rf_match = re.search(r"REST_FRAMEWORK\s*=\s*\{([^}]+(?:\{[^}]+\}[^}]*)*)\}", content, re.DOTALL)
            if rf_match:
                rf_body = rf_match.group(1)
                pagination = re.search(r"DEFAULT_PAGINATION_CLASS['\"]?\s*:\s*['\"]([^'\"]+)['\"]", rf_body)
                if pagination:
                    conventions["pagination_class"] = pagination.group(1)
                page_size = re.search(r"PAGE_SIZE['\"]?\s*:\s*(\d+)", rf_body)
                if page_size:
                    conventions["page_size"] = int(page_size.group(1))
                filter_b = re.search(
                    r"DEFAULT_FILTER_BACKENDS['\"]?\s*:\s*[\[\(]([^\]\)]+)[\]\)]",
                    rf_body, re.DOTALL,
                )
                if filter_b:
                    conventions["filter_backends"] = re.findall(r"['\"]([^'\"]+)['\"]", filter_b.group(1))
                versioning = re.search(r"DEFAULT_VERSIONING_CLASS['\"]?\s*:\s*['\"]([^'\"]+)['\"]", rf_body)
                if versioning:
                    conventions["versioning"] = versioning.group(1)
                throttle = re.search(
                    r"DEFAULT_THROTTLE_CLASSES['\"]?\s*:\s*[\[\(]([^\]\)]+)[\]\)]",
                    rf_body, re.DOTALL,
                )
                if throttle:
                    conventions["throttle_classes"] = re.findall(r"['\"]([^'\"]+)['\"]", throttle.group(1))

        # Find serializers
        ser_files = self._find_files_by_name(base, ["serializers.py"])
        for sf in ser_files:
            content = self._read_file_safe(sf)
            if not content:
                continue
            rel_path = os.path.relpath(sf, base)
            classes = self._parse_python_classes(content)
            for cls in classes:
                if any("Serializer" in b for b in cls["bases"]):
                    conventions["serializers"].append({
                        "name": cls["name"],
                        "file": rel_path,
                        "bases": cls["bases"],
                    })

        # Find ViewSets
        view_files = self._find_files_by_name(base, ["views.py", "viewsets.py", "api.py"])
        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            rel_path = os.path.relpath(vf, base)
            classes = self._parse_python_classes(content)
            for cls in classes:
                if any("ViewSet" in b or "APIView" in b for b in cls["bases"]):
                    conventions["viewsets"].append({
                        "name": cls["name"],
                        "file": rel_path,
                        "bases": cls["bases"],
                    })

        # Find routers
        url_files = self._find_files_by_name(base, ["urls.py"])
        for uf in url_files:
            content = self._read_file_safe(uf)
            if not content:
                continue
            rel_path = os.path.relpath(uf, base)
            router_matches = re.findall(r"(\w+)\s*=\s*(?:routers\.|DefaultRouter|SimpleRouter)\s*\(", content)
            for rm in router_matches:
                conventions["routers"].append({"variable": rm, "file": rel_path})

        return conventions

    # ══════════════════════════════════════════════════════════════════════
    #  11. get_similar_patterns
    # ══════════════════════════════════════════════════════════════════════

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.
        Searches a pattern registry for matching code structures.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "query": description,
            "matches": [],
        }

        description_lower = description.lower()

        # Determine which pattern types to search based on description
        matched_types: List[str] = []
        for ptype, info in self.PATTERN_REGISTRY.items():
            keywords = ptype.replace("-", " ").split()
            desc_words = info["description"].lower().split()
            if any(kw in description_lower for kw in keywords + desc_words):
                matched_types.append(ptype)

        # If no specific match, search all types
        if not matched_types:
            matched_types = list(self.PATTERN_REGISTRY.keys())

        for ptype in matched_types:
            info = self.PATTERN_REGISTRY[ptype]
            # Find relevant files
            found_files: List[str] = []
            for fp in info["file_patterns"]:
                if "*" in fp or "/" in fp:
                    found_files.extend(self._find_files_by_pattern(base, fp))
                else:
                    found_files.extend(self._find_files_by_name(base, [fp]))

            for fpath in found_files:
                content = self._read_file_safe(fpath)
                if not content:
                    continue
                rel_path = os.path.relpath(fpath, base)

                # Search for matching patterns using the class/function regex
                pattern_regex = re.compile(info["class_regex"], re.MULTILINE)
                for pm in pattern_regex.finditer(content):
                    match_name = None
                    for g in pm.groups():
                        if g:
                            match_name = g
                            break

                    if match_name:
                        # Get surrounding context (a few lines)
                        start = max(0, content.rfind("\n", 0, pm.start()) + 1)
                        end = content.find("\n", pm.end())
                        if end == -1:
                            end = len(content)
                        # Get a few more lines of context
                        for _ in range(5):
                            next_nl = content.find("\n", end + 1)
                            if next_nl != -1:
                                end = next_nl
                            else:
                                break

                        context_snippet = content[start:end].strip()
                        # Limit context to reasonable size
                        if len(context_snippet) > 500:
                            context_snippet = context_snippet[:500] + "..."

                        result["matches"].append({
                            "pattern_type": ptype,
                            "name": match_name,
                            "file": rel_path,
                            "description": info["description"],
                            "context": context_snippet,
                            "line": content[:pm.start()].count("\n") + 1,
                        })

        # Also search for description keywords in all Python files
        keywords = [w for w in description_lower.split() if len(w) > 3]
        if keywords and not result["matches"]:
            # Fallback: search by keywords in class/function names
            for root, dirs, files in os.walk(base):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".")
                    and d not in ("__pycache__", "venv", "env", ".venv", "node_modules", "migrations")
                ]
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    content = self._read_file_safe(fpath)
                    if not content:
                        continue
                    rel_path = os.path.relpath(fpath, base)
                    classes = self._parse_python_classes(content)
                    for cls in classes:
                        name_lower = cls["name"].lower()
                        if any(kw in name_lower for kw in keywords):
                            result["matches"].append({
                                "pattern_type": "class",
                                "name": cls["name"],
                                "file": rel_path,
                                "bases": cls["bases"],
                                "line": cls["start_line"],
                            })
                    functions = self._parse_functions(content)
                    for fn in functions:
                        if any(kw in fn["name"].lower() for kw in keywords):
                            result["matches"].append({
                                "pattern_type": "function",
                                "name": fn["name"],
                                "file": rel_path,
                                "decorators": fn["decorators"],
                                "line": fn["line"],
                            })

        result["total_matches"] = len(result["matches"])
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  12. get_project_snapshot
    # ══════════════════════════════════════════════════════════════════════

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Generate a compact snapshot of the entire Django project structure.
        Covers: installed apps, models per app, URL count, middleware stack,
        templates, static files, management commands, celery tasks.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot: Dict[str, Any] = {
            "status": "success",
            "project_type": "Django",
            "apps": {},
            "settings": {},
            "urls_summary": {},
            "middleware_stack": [],
            "templates_summary": {},
            "static_summary": {},
            "management_commands": [],
            "celery_tasks": [],
            "signals": [],
            "metrics": {},
        }

        # 1. Detect project structure
        manage_py = os.path.isfile(os.path.join(base, "manage.py"))
        snapshot["metrics"]["has_manage_py"] = manage_py

        # 2. Parse settings for INSTALLED_APPS
        settings_files = self._find_settings_files(base)
        installed_apps: List[str] = []
        for sf in settings_files:
            content = self._read_file_safe(sf)
            if not content:
                continue
            rel_path = os.path.relpath(sf, base)
            snapshot["settings"]["file"] = rel_path

            apps_match = re.search(r"INSTALLED_APPS\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
            if apps_match:
                installed_apps = re.findall(r"['\"]([^'\"]+)['\"]", apps_match.group(1))
                snapshot["settings"]["installed_apps"] = installed_apps

            # Database
            db_match = re.search(r"['\"]ENGINE['\"]\s*:\s*['\"]([^'\"]+)['\"]", content)
            if db_match:
                snapshot["settings"]["database_engine"] = db_match.group(1)

            # DEBUG
            debug_match = re.search(r"DEBUG\s*=\s*(True|False)", content)
            if debug_match:
                snapshot["settings"]["debug"] = debug_match.group(1)

            # LANGUAGE_CODE
            lang_match = re.search(r"LANGUAGE_CODE\s*=\s*['\"]([^'\"]+)['\"]", content)
            if lang_match:
                snapshot["settings"]["language_code"] = lang_match.group(1)

            # TIME_ZONE
            tz_match = re.search(r"TIME_ZONE\s*=\s*['\"]([^'\"]+)['\"]", content)
            if tz_match:
                snapshot["settings"]["time_zone"] = tz_match.group(1)

            # CELERY configuration
            if re.search(r"CELERY_|BROKER_URL|CELERY_BROKER_URL", content):
                snapshot["settings"]["celery_configured"] = True

        # 3. Apps and models
        discovered_apps = self._discover_apps(base)
        for app_path in discovered_apps:
            app_name = os.path.basename(app_path)
            app_info: Dict[str, Any] = {"path": app_path, "models": [], "has_admin": False, "has_views": False}

            # Count models
            models_file = os.path.join(base, app_path, "models.py")
            if os.path.isfile(models_file):
                content = self._read_file_safe(models_file)
                if content:
                    classes = self._parse_python_classes(content)
                    for cls in classes:
                        if any("Model" in b for b in cls["bases"]):
                            fields_count = len(re.findall(r"^\s+\w+\s*=\s*(?:models\.)?\w+Field\s*\(", cls["body"], re.MULTILINE))
                            app_info["models"].append({
                                "name": cls["name"],
                                "fields_count": fields_count,
                            })

            # Check for admin.py
            admin_file = os.path.join(base, app_path, "admin.py")
            if os.path.isfile(admin_file):
                app_info["has_admin"] = True
                content = self._read_file_safe(admin_file)
                if content:
                    registrations = re.findall(r"admin\.site\.register\s*\(\s*(\w+)", content)
                    decorator_regs = re.findall(r"@admin\.register\s*\(\s*(\w+)", content)
                    app_info["admin_registered"] = registrations + decorator_regs

            # Check for views.py
            views_file = os.path.join(base, app_path, "views.py")
            if os.path.isfile(views_file):
                app_info["has_views"] = True
                content = self._read_file_safe(views_file)
                if content:
                    classes = self._parse_python_classes(content)
                    app_info["view_count"] = len(classes)
                    functions = self._parse_functions(content)
                    app_info["view_count"] += len([f for f in functions if f["indent"] == 0])

            # Check for serializers
            ser_file = os.path.join(base, app_path, "serializers.py")
            if os.path.isfile(ser_file):
                app_info["has_serializers"] = True

            # Check for forms
            form_file = os.path.join(base, app_path, "forms.py")
            if os.path.isfile(form_file):
                app_info["has_forms"] = True

            # Check for signals
            signal_file = os.path.join(base, app_path, "signals.py")
            if os.path.isfile(signal_file):
                app_info["has_signals"] = True

            snapshot["apps"][app_name] = app_info

        # 4. URL summary
        url_map = self.get_url_map()
        snapshot["urls_summary"]["total"] = url_map.get("total_urls", 0)
        snapshot["urls_summary"]["namespaces"] = url_map.get("namespaces", {})
        view_types = {"CBV": 0, "FBV": 0, "ViewSet": 0, "include": 0}
        for ue in url_map.get("urls", []):
            vt = ue.get("view_type", ue.get("type", ""))
            if vt in view_types:
                view_types[vt] += 1
        snapshot["urls_summary"]["by_type"] = view_types

        # 5. Middleware stack
        middleware = self.get_middleware_chain()
        snapshot["middleware_stack"] = [
            mw.get("short_name", mw.get("class", "")) for mw in middleware.get("middleware", [])
        ]

        # 6. Templates summary
        template_dirs: Dict[str, int] = {}
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            if "templates" in root.split(os.sep):
                template_count = sum(1 for f in files if f.endswith((".html", ".txt", ".xml")))
                if template_count > 0:
                    rel = os.path.relpath(root, base)
                    template_dirs[rel] = template_count
        snapshot["templates_summary"] = template_dirs
        snapshot["metrics"]["total_templates"] = sum(template_dirs.values())

        # 7. Static files summary
        static_count = 0
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            if "static" in root.split(os.sep):
                static_count += len(files)
        snapshot["metrics"]["total_static_files"] = static_count

        # 8. Management commands
        cmd_files = self._find_files_by_pattern(base, "management/commands/*.py")
        for cf in cmd_files:
            if os.path.basename(cf) != "__init__.py":
                rel_path = os.path.relpath(cf, base)
                cmd_name = os.path.basename(cf).replace(".py", "")
                snapshot["management_commands"].append({
                    "name": cmd_name,
                    "file": rel_path,
                })

        # 9. Celery tasks
        task_files = self._find_files_by_name(base, ["tasks.py"])
        for tf in task_files:
            content = self._read_file_safe(tf)
            if not content:
                continue
            rel_path = os.path.relpath(tf, base)
            task_matches = re.findall(
                r"@(?:shared_task|app\.task|celery_app\.task)\s*(?:\([^)]*\))?\s*\ndef\s+(\w+)",
                content,
            )
            for tm in task_matches:
                snapshot["celery_tasks"].append({"name": tm, "file": rel_path})

        # 10. Signals
        signal_files = self._find_files_by_name(base, ["signals.py", "receivers.py"])
        for sf in signal_files:
            content = self._read_file_safe(sf)
            if not content:
                continue
            rel_path = os.path.relpath(sf, base)
            receiver_matches = re.findall(
                r"@receiver\s*\(\s*([\w.]+)(?:\s*,\s*sender\s*=\s*(\w+))?\s*\)\s*\ndef\s+(\w+)",
                content,
            )
            for signal, sender, func in receiver_matches:
                snapshot["signals"].append({
                    "signal": signal,
                    "sender": sender or "Any",
                    "handler": func,
                    "file": rel_path,
                })

        # Metrics summary
        snapshot["metrics"]["total_apps"] = len(snapshot["apps"])
        snapshot["metrics"]["total_models"] = sum(
            len(app.get("models", [])) for app in snapshot["apps"].values()
        )
        snapshot["metrics"]["total_urls"] = snapshot["urls_summary"]["total"]
        snapshot["metrics"]["total_middleware"] = len(snapshot["middleware_stack"])
        snapshot["metrics"]["total_management_commands"] = len(snapshot["management_commands"])
        snapshot["metrics"]["total_celery_tasks"] = len(snapshot["celery_tasks"])
        snapshot["metrics"]["total_signals"] = len(snapshot["signals"])

        return snapshot

    # ══════════════════════════════════════════════════════════════════════
    #  13. get_serializer_chain
    # ══════════════════════════════════════════════════════════════════════

    def get_serializer_chain(self, view_name: str) -> Dict[str, Any]:
        """
        DRF: Trace Serializer -> ModelSerializer -> model fields -> view -> router.
        Detect field mismatches between serializer and model.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result: Dict[str, Any] = {
            "status": "success",
            "view_name": view_name,
            "view": {},
            "serializer": {},
            "model": {},
            "router": {},
            "mismatches": [],
        }

        # Step 1: Find the view and its serializer_class
        view_files = self._find_files_by_name(base, ["views.py", "viewsets.py", "api.py"])
        serializer_name = None
        model_name = None

        for vf in view_files:
            content = self._read_file_safe(vf)
            if not content:
                continue
            rel_path = os.path.relpath(vf, base)
            classes = self._parse_python_classes(content)

            for cls in classes:
                if cls["name"] == view_name:
                    body = cls["body"]
                    result["view"] = {
                        "name": cls["name"],
                        "file": rel_path,
                        "bases": cls["bases"],
                    }

                    # serializer_class
                    ser_m = re.search(r"serializer_class\s*=\s*(\w+)", body)
                    if ser_m:
                        serializer_name = ser_m.group(1)
                        result["view"]["serializer_class"] = serializer_name

                    # model / queryset
                    model_m = re.search(r"model\s*=\s*(\w+)", body)
                    if model_m:
                        model_name = model_m.group(1)
                    qs_m = re.search(r"queryset\s*=\s*(\w+)\.objects", body)
                    if qs_m:
                        model_name = qs_m.group(1)
                    if model_name:
                        result["view"]["model"] = model_name

                    # Check for get_serializer_class override
                    gsc = re.search(r"def\s+get_serializer_class\s*\(self\)\s*:", body)
                    if gsc:
                        result["view"]["has_dynamic_serializer"] = True
                        # Try to extract returned serializers
                        returns = re.findall(r"return\s+(\w+Serializer\w*)", body)
                        if returns:
                            result["view"]["dynamic_serializers"] = returns

                    # Check for action-specific serializers
                    action_serializers = re.findall(
                        r"@action\s*\([^)]*\)\s*\n\s*def\s+(\w+)\s*\([^)]*\).*?(?:serializer_class|get_serializer)\s*(?:=\s*(\w+)|\(\s*(\w+))",
                        body, re.DOTALL,
                    )
                    if action_serializers:
                        result["view"]["action_serializers"] = [
                            {"action": a[0], "serializer": a[1] or a[2]}
                            for a in action_serializers
                        ]

                    # Permission/auth classes
                    perm_m = re.search(r"permission_classes\s*=\s*\[([^\]]+)\]", body)
                    if perm_m:
                        result["view"]["permissions"] = [
                            p.strip() for p in perm_m.group(1).split(",") if p.strip()
                        ]

                    # Pagination
                    pag_m = re.search(r"pagination_class\s*=\s*(\w+)", body)
                    if pag_m:
                        result["view"]["pagination_class"] = pag_m.group(1)

                    # Filter backends
                    fb_m = re.search(r"filter_backends\s*=\s*\[([^\]]+)\]", body)
                    if fb_m:
                        result["view"]["filter_backends"] = [
                            f.strip() for f in fb_m.group(1).split(",") if f.strip()
                        ]

                    break

            if result["view"]:
                break

        # Step 2: Parse the serializer
        if serializer_name:
            ser_info = self._find_serializer(base, serializer_name)
            result["serializer"] = ser_info

            # If serializer references a model, extract it
            if not model_name and isinstance(ser_info, dict):
                model_name = ser_info.get("meta", {}).get("model")

        # Step 3: Parse the model
        if model_name:
            model_data = self.get_model_relationships(model_name)
            if model_data.get("models") and model_name in model_data["models"]:
                result["model"] = model_data["models"][model_name]

        # Step 4: Find router registration
        url_files = self._find_files_by_name(base, ["urls.py"])
        for uf in url_files:
            content = self._read_file_safe(uf)
            if not content:
                continue
            rel_path = os.path.relpath(uf, base)

            router_reg = re.search(
                r"""router\.register\s*\(\s*r?['\"]([^'\"]+)['\"]"""
                r"""\s*,\s*""" + re.escape(view_name) + r"""(?:\s*,\s*basename\s*=\s*['\"](\w+)['\"])?""",
                content,
            )
            if router_reg:
                result["router"] = {
                    "prefix": router_reg.group(1),
                    "basename": router_reg.group(2),
                    "file": rel_path,
                }

        # Step 5: Detect mismatches
        ser_fields: Set[str] = set()
        if isinstance(result.get("serializer"), dict) and result["serializer"].get("meta"):
            meta_fields = result["serializer"]["meta"].get("fields", [])
            if isinstance(meta_fields, list):
                ser_fields.update(meta_fields)
            elif meta_fields == "__all__":
                # All fields — no mismatch to detect
                pass
            for f in result["serializer"].get("fields", []):
                if isinstance(f, dict):
                    ser_fields.add(f["name"])

        model_fields: Set[str] = set()
        if isinstance(result.get("model"), dict):
            model_fields = set(result["model"].get("fields", {}).keys())

        if ser_fields and model_fields:
            ser_only = ser_fields - model_fields - {"id", "pk"}
            if ser_only:
                result["mismatches"].append({
                    "type": "serializer_field_not_in_model",
                    "fields": list(ser_only),
                    "message": f"Serializer fields {list(ser_only)} not found on model",
                })

        # Check for excluded but accessible fields
        ser_exclude = set()
        if isinstance(result.get("serializer"), dict):
            ser_exclude = set(result["serializer"].get("meta", {}).get("exclude", []))
        if ser_exclude:
            result["security_note"] = f"Excluded fields: {list(ser_exclude)} — verify sensitive data is excluded"

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  14. pre_edit_check
    # ══════════════════════════════════════════════════════════════════════

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit check. Based on file type:
        - model -> check migrations, admin, serializers, forms
        - view -> check urls, templates, serializers
        - form -> check views, templates
        - serializer -> check views, models
        - url -> check views
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        abs_path = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        if not os.path.isfile(abs_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        content = self._read_file_safe(abs_path)
        if not content:
            return {"status": "error", "message": f"Cannot read: {file_path}"}

        rel_path = os.path.relpath(abs_path, base)
        fname = os.path.basename(rel_path)

        result: Dict[str, Any] = {
            "status": "success",
            "file": rel_path,
            "symbol": symbol_name,
            "file_type": "unknown",
            "symbol_found": False,
            "impact": [],
            "related_files": [],
            "warnings": [],
        }

        # Detect file type
        if fname == "models.py" or "/models/" in rel_path:
            result["file_type"] = "model"
        elif fname == "views.py" or fname == "viewsets.py" or "/views/" in rel_path:
            result["file_type"] = "view"
        elif fname == "forms.py" or "/forms/" in rel_path:
            result["file_type"] = "form"
        elif fname == "serializers.py" or "/serializers/" in rel_path:
            result["file_type"] = "serializer"
        elif fname == "urls.py":
            result["file_type"] = "url"
        elif fname == "admin.py":
            result["file_type"] = "admin"
        elif fname == "signals.py" or fname == "receivers.py":
            result["file_type"] = "signal"
        elif fname == "tasks.py":
            result["file_type"] = "celery_task"
        elif fname == "middleware.py" or "/middleware/" in rel_path:
            result["file_type"] = "middleware"
        elif fname.startswith("test") or "/tests/" in rel_path:
            result["file_type"] = "test"

        # Check if symbol exists in file
        symbol_pattern = re.compile(
            r"(?:class|def)\s+" + re.escape(symbol_name) + r"\s*[\(:]"
        )
        if symbol_pattern.search(content):
            result["symbol_found"] = True
        elif re.search(re.escape(symbol_name) + r"\s*=", content):
            result["symbol_found"] = True

        # Find references across the project
        references: List[Dict[str, str]] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("__pycache__", "venv", "env", ".venv", "node_modules")
            ]
            for f in files:
                if not f.endswith(".py"):
                    continue
                fpath = os.path.join(root, f)
                if fpath == abs_path:
                    continue
                f_rel = os.path.relpath(fpath, base)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        fcontent = fh.read()
                except Exception:
                    continue

                if symbol_name in fcontent:
                    # Find line numbers
                    for i, line in enumerate(fcontent.split("\n"), 1):
                        if symbol_name in line:
                            usage_type = "unknown"
                            if re.search(r"import\s+.*" + re.escape(symbol_name), line):
                                usage_type = "import"
                            elif re.search(r"from\s+\S+\s+import\s+.*" + re.escape(symbol_name), line):
                                usage_type = "import"
                            elif re.search(re.escape(symbol_name) + r"\s*\(", line):
                                usage_type = "call/instantiation"
                            elif re.search(re.escape(symbol_name) + r"\s*\)", line):
                                usage_type = "argument"
                            elif re.search(r"class\s+\w+\s*\([^)]*" + re.escape(symbol_name), line):
                                usage_type = "inheritance"

                            references.append({
                                "file": f_rel,
                                "line": i,
                                "usage_type": usage_type,
                                "context": line.strip()[:120],
                            })
                            break  # One reference per file is enough for impact

        result["related_files"] = references

        # Context-specific checks
        if result["file_type"] == "model":
            # Check if model has migrations
            app_dir = os.path.dirname(abs_path)
            mig_dir = os.path.join(app_dir, "migrations")
            if os.path.isdir(mig_dir):
                result["impact"].append({
                    "type": "migration_check",
                    "message": f"Model {symbol_name} changes may require new migration (makemigrations)",
                    "migrations_dir": os.path.relpath(mig_dir, base),
                })
            else:
                result["warnings"].append("No migrations directory found for this app")

            # Check admin registrations
            admin_file = os.path.join(app_dir, "admin.py")
            if os.path.isfile(admin_file):
                admin_content = self._read_file_safe(admin_file)
                if admin_content and symbol_name in admin_content:
                    result["impact"].append({
                        "type": "admin_registration",
                        "message": f"{symbol_name} is registered in admin — changes may affect admin interface",
                        "file": os.path.relpath(admin_file, base),
                    })

            # Check serializers
            ser_refs = [r for r in references if "serializer" in r["file"].lower()]
            if ser_refs:
                result["impact"].append({
                    "type": "serializer_reference",
                    "message": f"{symbol_name} is referenced in serializers — field changes affect API",
                    "files": [r["file"] for r in ser_refs],
                })

            # Check forms
            form_refs = [r for r in references if "form" in r["file"].lower()]
            if form_refs:
                result["impact"].append({
                    "type": "form_reference",
                    "message": f"{symbol_name} is referenced in forms — field changes may break form validation",
                    "files": [r["file"] for r in form_refs],
                })

        elif result["file_type"] == "view":
            # Check URL references
            url_refs = [r for r in references if "url" in r["file"].lower()]
            if url_refs:
                result["impact"].append({
                    "type": "url_reference",
                    "message": f"{symbol_name} is referenced in URL configs",
                    "files": [r["file"] for r in url_refs],
                })

            # Check template references by looking at the view's template_name
            classes = self._parse_python_classes(content)
            for cls in classes:
                if cls["name"] == symbol_name:
                    tmpl_m = re.search(r"template_name\s*=\s*['\"]([^'\"]+)['\"]", cls["body"])
                    if tmpl_m:
                        result["impact"].append({
                            "type": "template_reference",
                            "message": f"View renders template: {tmpl_m.group(1)}",
                            "template": tmpl_m.group(1),
                        })

        elif result["file_type"] == "form":
            # Check which views use this form
            view_refs = [r for r in references if "view" in r["file"].lower()]
            if view_refs:
                result["impact"].append({
                    "type": "view_reference",
                    "message": f"Form {symbol_name} is used in views",
                    "files": [r["file"] for r in view_refs],
                })

            # Check template references
            tmpl_refs = [r for r in references if r["file"].endswith((".html", ".txt"))]
            if tmpl_refs:
                result["impact"].append({
                    "type": "template_reference",
                    "message": f"Form {symbol_name} is referenced in templates",
                    "files": [r["file"] for r in tmpl_refs],
                })

        elif result["file_type"] == "serializer":
            # Check views
            view_refs = [r for r in references if "view" in r["file"].lower()]
            if view_refs:
                result["impact"].append({
                    "type": "view_reference",
                    "message": f"Serializer {symbol_name} is used in views",
                    "files": [r["file"] for r in view_refs],
                })

        elif result["file_type"] == "url":
            # Check for view references
            result["impact"].append({
                "type": "routing_change",
                "message": "URL changes affect API contracts and template {% url %} tags",
            })

        result["total_references"] = len(references)
        return result

    # ------------------------------------------------------------------ #
    #  verify_schema / detect_transaction_risks / get_domain_rules /      #
    #  generate_test_skeleton / match_view_guards                         #
    # ------------------------------------------------------------------ #

    def verify_schema(self, table_or_model: str, columns: list) -> Dict[str, Any]:
        """
        Verify that fields exist in Django model definitions (models.py).

        Checks for CharField, IntegerField, ForeignKey, etc.
        Returns missing columns.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        found_fields: Set[str] = set()
        source_files: List[str] = []

        all_field_types = self.MODEL_FIELD_TYPES | self.RELATIONSHIP_FIELDS
        field_type_pattern = "|".join(re.escape(ft) for ft in all_field_types)

        # Build regex for model class and its field definitions
        model_class_pattern = re.compile(
            rf"class\s+{re.escape(table_or_model)}\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
            re.DOTALL,
        )
        field_pattern = re.compile(
            rf"(\w+)\s*=\s*(?:models\.)?(?:{field_type_pattern})\s*\(",
        )

        # Search all models.py files
        for root, _dirs, files in os.walk(base):
            # Skip hidden dirs, venv, migrations
            rel_root = os.path.relpath(root, base)
            if any(part.startswith(".") or part in ("venv", "env", ".venv", "node_modules", "__pycache__")
                   for part in rel_root.split(os.sep)):
                continue

            for fname in files:
                if fname != "models.py":
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue

                for mm in model_class_pattern.finditer(content):
                    model_body = mm.group(1)
                    for fm in field_pattern.finditer(model_body):
                        found_fields.add(fm.group(1))
                    source_files.append(os.path.relpath(fpath, base))

        # Also add implicit fields
        if found_fields:
            found_fields.add("id")  # Django auto PK
            found_fields.add("pk")

        found = [c for c in columns if c in found_fields]
        missing = [c for c in columns if c not in found_fields]

        return {
            "status": "ok",
            "model": table_or_model,
            "found": found,
            "missing": missing,
            "all_model_fields": sorted(found_fields),
            "source_files": source_files,
        }

    def detect_transaction_risks(self, file_path: str) -> Dict[str, Any]:
        """
        Detect transaction / atomicity risks in a Django file.

        Looks for multiple .save()/.create()/.delete() without
        transaction.atomic(), and cache operations inside atomic blocks.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        risks: List[Dict[str, Any]] = []

        write_pattern = re.compile(
            r"\.(save|create|bulk_create|update|bulk_update|delete|get_or_create|"
            r"update_or_create)\s*\("
        )
        atomic_pattern = re.compile(
            r"(transaction\.atomic\s*\(|@transaction\.atomic)"
        )
        cache_pattern = re.compile(
            r"(cache\.(set|get|delete|clear|get_or_set)|django\.core\.cache)"
        )

        # Parse function/method blocks using indentation
        func_pattern = re.compile(
            r"^(\s*)def\s+(\w+)\s*\([^)]*\)\s*(?:->.*?)?:",
            re.MULTILINE,
        )

        for fm in func_pattern.finditer(content):
            indent = len(fm.group(1))
            func_name = fm.group(2)
            start = fm.end()

            # Find the function body by indentation
            lines = content[start:].split("\n")
            body_lines = []
            for line in lines[1:]:  # skip the def line itself
                stripped = line.lstrip()
                if stripped == "" or stripped.startswith("#"):
                    body_lines.append(line)
                    continue
                current_indent = len(line) - len(stripped)
                if current_indent > indent:
                    body_lines.append(line)
                else:
                    break
            func_body = "\n".join(body_lines)

            write_calls = write_pattern.findall(func_body)
            has_atomic = bool(atomic_pattern.search(func_body))

            if len(write_calls) >= 2 and not has_atomic:
                line_num = content[:fm.start()].count("\n") + 1
                risks.append({
                    "type": "missing_atomic",
                    "function": func_name,
                    "line": line_num,
                    "write_count": len(write_calls),
                    "message": f"Function '{func_name}' has {len(write_calls)} DB writes without "
                               "transaction.atomic()",
                    "severity": "high",
                })

            if has_atomic:
                cache_calls = cache_pattern.findall(func_body)
                if cache_calls:
                    line_num = content[:fm.start()].count("\n") + 1
                    risks.append({
                        "type": "cache_in_atomic",
                        "function": func_name,
                        "line": line_num,
                        "message": f"Cache operations inside transaction.atomic() in '{func_name}' – "
                                   "cache may be set before transaction commits",
                        "severity": "medium",
                    })

        return {
            "status": "ok",
            "file": file_path,
            "risks": risks,
            "risk_count": len(risks),
        }

    def get_domain_rules(self, file_path: str) -> Dict[str, Any]:
        """
        Return domain-aware anti-pattern rules based on file location / type.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        rules: List[Dict[str, str]] = []
        norm = file_path.replace("\\", "/").lower()

        full_path = os.path.join(base, file_path)
        content = ""
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                logger.debug("Suppressed exception in best-effort path: %s", e)

        # Views → require permission checks
        if "views" in norm or "viewsets" in norm:
            if content and not re.search(
                r"permission_classes|login_required|@permission_required|"
                r"LoginRequiredMixin|PermissionRequiredMixin|IsAuthenticated|"
                r"@staff_member_required",
                content,
            ):
                rules.append({
                    "rule": "require_permissions",
                    "message": "View should declare permission_classes or use permission decorators/mixins",
                    "severity": "warning",
                })
            if "viewsets" in norm and content and not re.search(r"serializer_class\s*=", content):
                rules.append({
                    "rule": "require_serializer",
                    "message": "ViewSet should declare serializer_class",
                    "severity": "warning",
                })

        # Models → require __str__
        if "models" in norm and norm.endswith(".py"):
            if content:
                # Find model classes without __str__
                model_classes = re.findall(
                    r"class\s+(\w+)\s*\(\s*(?:models\.Model|AbstractUser|AbstractBaseUser)[^)]*\)",
                    content,
                )
                for mc in model_classes:
                    # Check if __str__ is defined in that class
                    class_body_match = re.search(
                        rf"class\s+{re.escape(mc)}\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
                        content, re.DOTALL,
                    )
                    if class_body_match and "__str__" not in class_body_match.group(1):
                        rules.append({
                            "rule": "require_str_method",
                            "message": f"Model '{mc}' should define __str__() for admin and debugging",
                            "severity": "info",
                        })
                    if class_body_match and "class Meta" not in class_body_match.group(1):
                        rules.append({
                            "rule": "recommend_meta_class",
                            "message": f"Model '{mc}' should define class Meta with ordering/verbose_name",
                            "severity": "info",
                        })

        # Serializers → require validation
        if "serializer" in norm and norm.endswith(".py"):
            if content and not re.search(r"def validate|validators\s*=|validate_\w+", content):
                rules.append({
                    "rule": "require_validation",
                    "message": "Serializer should include field validators or validate() method",
                    "severity": "warning",
                })

        # URLs → require named routes
        if "urls" in norm and norm.endswith(".py"):
            # Check for path() calls without name=
            unnamed_paths = re.findall(r"path\s*\([^)]*\)", content)
            unnamed_count = sum(1 for p in unnamed_paths if "name=" not in p and "include(" not in p)
            if unnamed_count > 0:
                rules.append({
                    "rule": "require_named_routes",
                    "message": f"{unnamed_count} path() call(s) without name= parameter – "
                               "named routes are needed for {% url %} and reverse()",
                    "severity": "warning",
                })

        # Admin files
        if "admin" in norm and norm.endswith(".py"):
            if content and not re.search(r"list_display\s*=", content):
                rules.append({
                    "rule": "recommend_list_display",
                    "message": "ModelAdmin should set list_display for better admin usability",
                    "severity": "info",
                })

        return {
            "status": "ok",
            "file": file_path,
            "rules": rules,
            "rule_count": len(rules),
        }

    def generate_test_skeleton(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Generate a pytest / Django TestCase test skeleton for a given function or class method.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        norm = file_path.replace("\\", "/").lower()

        # Extract class name if symbol is a method
        class_match = re.search(r"class\s+(\w+)", content)
        class_name = class_match.group(1) if class_match else None

        # Determine module import path from file_path
        module_path = file_path.replace("/", ".").replace("\\", ".")
        if module_path.endswith(".py"):
            module_path = module_path[:-3]

        if "views" in norm or "viewsets" in norm:
            test_type = "view"
            skeleton = (
                f"import pytest\n"
                f"from django.test import TestCase, Client\n"
                f"from django.urls import reverse\n"
                f"from django.contrib.auth import get_user_model\n\n"
                f"User = get_user_model()\n\n\n"
                f"class {class_name or 'View'}Test(TestCase):\n"
                f"    def setUp(self):\n"
                f"        self.client = Client()\n"
                f"        self.user = User.objects.create_user(\n"
                f"            username='testuser', password=os.environ.get('TEST_PASSWORD', 'changeme')\n"
                f"        )\n\n"
                f"    def test_{symbol}_authenticated(self):\n"
                f"        self.client.login(username='testuser', password=os.environ.get('TEST_PASSWORD', 'changeme'))\n"
                f"        # TODO: replace with actual URL name\n"
                f"        response = self.client.get(reverse('{symbol}'))\n"
                f"        self.assertEqual(response.status_code, 200)\n\n"
                f"    def test_{symbol}_unauthenticated(self):\n"
                f"        # TODO: replace with actual URL name\n"
                f"        response = self.client.get(reverse('{symbol}'))\n"
                f"        self.assertEqual(response.status_code, 302)  # redirect to login\n"
            )
        elif "models" in norm:
            test_type = "model"
            skeleton = (
                f"import pytest\n"
                f"from django.test import TestCase\n"
                f"from {module_path} import {class_name or symbol}\n\n\n"
                f"class {class_name or symbol}Test(TestCase):\n"
                f"    def setUp(self):\n"
                f"        self.instance = {class_name or symbol}.objects.create(\n"
                f"            # TODO: provide required fields\n"
                f"        )\n\n"
                f"    def test_{symbol}(self):\n"
                f"        result = self.instance.{symbol}()\n"
                f"        self.assertIsNotNone(result)\n\n"
                f"    def test_str_representation(self):\n"
                f"        self.assertIsInstance(str(self.instance), str)\n"
            )
        elif "serializer" in norm:
            test_type = "serializer"
            skeleton = (
                f"import pytest\n"
                f"from django.test import TestCase\n"
                f"from {module_path} import {class_name or symbol}\n\n\n"
                f"class {class_name or symbol}Test(TestCase):\n"
                f"    def test_{symbol}_valid_data(self):\n"
                f"        data = {{\n"
                f"            # TODO: provide valid data\n"
                f"        }}\n"
                f"        serializer = {class_name or symbol}(data=data)\n"
                f"        self.assertTrue(serializer.is_valid(), serializer.errors)\n\n"
                f"    def test_{symbol}_invalid_data(self):\n"
                f"        data = {{}}  # empty data\n"
                f"        serializer = {class_name or symbol}(data=data)\n"
                f"        self.assertFalse(serializer.is_valid())\n"
            )
        else:
            test_type = "unit"
            if class_name:
                skeleton = (
                    f"import pytest\n"
                    f"from {module_path} import {class_name}\n\n\n"
                    f"class {class_name}Test:\n"
                    f"    def setup_method(self):\n"
                    f"        self.instance = {class_name}()\n\n"
                    f"    def test_{symbol}(self):\n"
                    f"        # Arrange\n"
                    f"        # TODO: set up test data\n\n"
                    f"        # Act\n"
                    f"        result = self.instance.{symbol}()\n\n"
                    f"        # Assert\n"
                    f"        assert result is not None\n"
                )
            else:
                skeleton = (
                    f"import pytest\n"
                    f"from {module_path} import {symbol}\n\n\n"
                    f"def test_{symbol}():\n"
                    f"    # Arrange\n"
                    f"    # TODO: set up test data\n\n"
                    f"    # Act\n"
                    f"    result = {symbol}()\n\n"
                    f"    # Assert\n"
                    f"    assert result is not None\n\n\n"
                    f"def test_{symbol}_edge_case():\n"
                    f"    # TODO: test edge cases\n"
                    f"    pass\n"
                )

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "test_type": test_type,
            "test_class": class_name,
            "skeleton": skeleton,
        }

    def match_view_guards(self, file_path: str, symbol: str) -> Dict[str, Any]:
        """
        Cross-reference Django view permission checks with matching
        {% if %} conditions in templates.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        # Extract permission/guard conditions from views
        guards: List[Dict[str, Any]] = []
        guard_patterns = [
            (r"@login_required", "login_required"),
            (r"@permission_required\s*\(\s*['\"]([^'\"]+)['\"]", "permission_required"),
            (r"@staff_member_required", "staff_required"),
            (r"@user_passes_test\s*\(\s*(\w+)", "user_passes_test"),
            (r"permission_classes\s*=\s*\[([^\]]+)\]", "permission_classes"),
            (r"LoginRequiredMixin", "login_mixin"),
            (r"PermissionRequiredMixin", "permission_mixin"),
            (r"permission_required\s*=\s*['\"]([^'\"]+)['\"]", "permission_attr"),
            (r"request\.user\.is_authenticated", "is_authenticated_check"),
            (r"request\.user\.is_staff", "is_staff_check"),
            (r"request\.user\.is_superuser", "is_superuser_check"),
            (r"request\.user\.has_perm\s*\(\s*['\"]([^'\"]+)['\"]", "has_perm"),
        ]

        for pattern, guard_type in guard_patterns:
            for m in re.finditer(pattern, content):
                line_num = content[:m.start()].count("\n") + 1
                guards.append({
                    "type": guard_type,
                    "condition": m.group(0),
                    "value": m.group(1) if m.lastindex else None,
                    "line": line_num,
                })

        # Search templates for matching {% if %} conditions
        template_matches: List[Dict[str, Any]] = []
        template_dirs = []
        for candidate in ["templates", "*/templates"]:
            import glob as glob_mod
            for d in glob_mod.glob(os.path.join(base, candidate)):
                if os.path.isdir(d):
                    template_dirs.append(d)

        template_patterns = [
            (r"\{%\s*if\s+user\.is_authenticated\s*%\}", "template_auth_check"),
            (r"\{%\s*if\s+user\.is_staff\s*%\}", "template_staff_check"),
            (r"\{%\s*if\s+user\.is_superuser\s*%\}", "template_superuser_check"),
            (r"\{%\s*if\s+perms\.(\w+\.\w+)\s*%\}", "template_perm_check"),
            (r"\{%\s*if\s+user\.has_perm\s+['\"]([^'\"]+)['\"]\s*%\}", "template_has_perm"),
            (r"\{%\s*if\s+not\s+user\.is_authenticated\s*%\}", "template_not_auth"),
            (r"\{%\s*if\s+request\.user\.is_authenticated\s*%\}", "template_request_auth"),
        ]

        for tdir in template_dirs:
            for root, _dirs, files in os.walk(tdir):
                for fname in files:
                    if not fname.endswith(".html"):
                        continue
                    tpath = os.path.join(root, fname)
                    try:
                        with open(tpath, "r", encoding="utf-8", errors="replace") as f:
                            tcontent = f.read()
                    except Exception:
                        continue

                    rel_template = os.path.relpath(tpath, base)
                    for tp, ttype in template_patterns:
                        for tm in re.finditer(tp, tcontent):
                            tline = tcontent[:tm.start()].count("\n") + 1
                            for guard in guards:
                                is_match = False
                                if guard["type"] in ("login_required", "login_mixin",
                                                     "is_authenticated_check") and ttype in (
                                    "template_auth_check", "template_request_auth"
                                ):
                                    is_match = True
                                elif guard["type"] in ("staff_required", "is_staff_check") and \
                                        ttype == "template_staff_check":
                                    is_match = True
                                elif guard["type"] == "is_superuser_check" and \
                                        ttype == "template_superuser_check":
                                    is_match = True
                                elif guard["type"] in ("permission_required", "permission_attr",
                                                       "has_perm") and ttype in (
                                    "template_perm_check", "template_has_perm"
                                ):
                                    if guard.get("value") and tm.lastindex:
                                        if guard["value"] == tm.group(1):
                                            is_match = True
                                    else:
                                        is_match = True

                                if is_match:
                                    template_matches.append({
                                        "template_file": rel_template,
                                        "template_line": tline,
                                        "template_type": ttype,
                                        "template_condition": tm.group(0),
                                        "backend_guard": guard,
                                    })

        return {
            "status": "ok",
            "file": file_path,
            "symbol": symbol,
            "backend_guards": guards,
            "template_matches": template_matches,
            "matched_count": len(template_matches),
            "unmatched_guards": len(guards) - len({m["backend_guard"]["line"] for m in template_matches}),
        }
