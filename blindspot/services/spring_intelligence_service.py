"""
Spring Boot Intelligence Service - Spring-specific code analysis.

Provides cross-file relationship tracking, endpoint mapping, entity relationship
extraction, Thymeleaf dependency analysis, security filter chain resolution,
and Spring-specific convention detection for Java/Kotlin Spring Boot projects.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .base_service import BaseService

logger = logging.getLogger(__name__)


class SpringIntelligenceService(BaseService):
    """Spring Boot-specific code intelligence for Java/Kotlin projects."""

    # Standard Spring source layouts
    JAVA_SRC = "src/main/java"
    KOTLIN_SRC = "src/main/kotlin"
    RESOURCES = "src/main/resources"
    TEST_JAVA = "src/test/java"
    TEST_KOTLIN = "src/test/kotlin"

    # Annotation regex building blocks
    _ANNOTATION_SIMPLE = re.compile(
        r'@(\w+)(?:\(([^)]*)\))?'
    )
    _ANNOTATION_PARAMS = re.compile(
        r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|\{([^}]*)\}|(\w+(?:\.\w+)*))'
    )

    # ── helpers ────────────────────────────────────────────────────────

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception:
            pass
        return None

    def _find_source_roots(self, base: str) -> List[str]:
        """Return all existing source root directories."""
        roots = []
        for src in [self.JAVA_SRC, self.KOTLIN_SRC]:
            full = os.path.join(base, src)
            if os.path.isdir(full):
                roots.append(full)
        return roots

    def _find_test_roots(self, base: str) -> List[str]:
        """Return all existing test root directories."""
        roots = []
        for src in [self.TEST_JAVA, self.TEST_KOTLIN]:
            full = os.path.join(base, src)
            if os.path.isdir(full):
                roots.append(full)
        return roots

    def _walk_source_files(self, base: str, dirs: Optional[List[str]] = None) -> List[Tuple[str, str, str]]:
        """Walk source files returning (abs_path, rel_path, content) tuples.

        Args:
            base: Project root path
            dirs: Optional list of absolute directories to scan.
                  Defaults to all source roots.
        """
        if dirs is None:
            dirs = self._find_source_roots(base)
        results = []
        for src_root in dirs:
            if not os.path.isdir(src_root):
                continue
            for root, _, files in os.walk(src_root):
                for fname in files:
                    if not fname.endswith(('.java', '.kt')):
                        continue
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, base)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                    except Exception:
                        continue
                    results.append((fpath, rel_path, content))
        return results

    def _parse_annotations(self, content: str) -> List[Dict[str, Any]]:
        """Parse all annotations from a source file into structured dicts.

        Returns list of {name, raw_value, params, line} dicts.
        """
        annotations = []
        for i, line in enumerate(content.split('\n'), 1):
            stripped = line.strip()
            for m in self._ANNOTATION_SIMPLE.finditer(stripped):
                ann_name = m.group(1)
                raw_value = m.group(2) or ""
                params = {}
                if raw_value:
                    # Check for named params
                    for pm in self._ANNOTATION_PARAMS.finditer(raw_value):
                        key = pm.group(1)
                        val = pm.group(2) or pm.group(3) or pm.group(4) or pm.group(5) or ""
                        params[key] = val
                    # If no named params, treat as value param
                    if not params:
                        cleaned = raw_value.strip().strip('"').strip("'")
                        params["value"] = cleaned
                annotations.append({
                    "name": ann_name,
                    "raw_value": raw_value,
                    "params": params,
                    "line": i,
                })
        return annotations

    def _extract_class_info(self, content: str) -> Dict[str, Any]:
        """Extract class/interface declaration info from source content."""
        # Java: public class Foo extends Bar implements Baz, Qux {
        # Kotlin: class Foo : Bar(), Baz {
        java_class = re.search(
            r'(?:public\s+|abstract\s+|open\s+)*(?:class|interface|enum)\s+'
            r'(\w+)(?:<[^>]*>)?'
            r'(?:\s+extends\s+(\w+)(?:<[^>]*>)?)?'
            r'(?:\s+implements\s+([\w\s,<>]+))?',
            content
        )
        kotlin_class = re.search(
            r'(?:open\s+|abstract\s+|data\s+|sealed\s+)*class\s+'
            r'(\w+)(?:<[^>]*>)?'
            r'(?:\s*\([^)]*\))?'
            r'(?:\s*:\s*([\w\s,<>().]+))?',
            content
        )
        package_match = re.search(r'package\s+([\w.]+)', content)
        package = package_match.group(1) if package_match else ""

        info = {"package": package, "class_name": "", "extends": "", "implements": []}

        if java_class:
            info["class_name"] = java_class.group(1)
            info["extends"] = java_class.group(2) or ""
            if java_class.group(3):
                info["implements"] = [i.strip() for i in java_class.group(3).split(',')]
        elif kotlin_class:
            info["class_name"] = kotlin_class.group(1)
            if kotlin_class.group(2):
                parents = [p.strip().rstrip('()') for p in kotlin_class.group(2).split(',')]
                if parents:
                    info["extends"] = parents[0]
                    info["implements"] = parents[1:]

        return info

    def _extract_methods(self, content: str) -> List[Dict[str, Any]]:
        """Extract method declarations from a Java/Kotlin source file."""
        methods = []
        # Java methods
        java_method_re = re.compile(
            r'(?:(?:public|private|protected)\s+)?'
            r'(?:static\s+)?'
            r'(?:(?:final|synchronized|abstract)\s+)?'
            r'(?:<[^>]+>\s+)?'
            r'([\w<>\[\]?,\s]+?)\s+'
            r'(\w+)\s*\(([^)]*)\)\s*'
            r'(?:throws\s+[\w\s,]+)?'
            r'\s*\{',
            re.MULTILINE
        )
        # Kotlin methods
        kotlin_method_re = re.compile(
            r'(?:(?:public|private|protected|internal|override|open|suspend)\s+)*'
            r'fun\s+(?:<[^>]+>\s+)?'
            r'(\w+)\s*\(([^)]*)\)\s*'
            r'(?::\s*([\w<>\[\]?,\s]+))?\s*\{',
            re.MULTILINE
        )
        lines = content.split('\n')

        for m in java_method_re.finditer(content):
            line_no = content[:m.start()].count('\n') + 1
            # Gather annotations from lines above
            ann_lines = []
            idx = line_no - 2
            while idx >= 0 and idx < len(lines):
                stripped = lines[idx].strip()
                if stripped.startswith('@'):
                    ann_lines.insert(0, stripped)
                    idx -= 1
                elif stripped == '' or stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                    idx -= 1
                else:
                    break
            methods.append({
                "return_type": m.group(1).strip(),
                "name": m.group(2),
                "params": m.group(3).strip(),
                "line": line_no,
                "annotations": ann_lines,
                "language": "java",
            })

        for m in kotlin_method_re.finditer(content):
            line_no = content[:m.start()].count('\n') + 1
            ann_lines = []
            idx = line_no - 2
            while idx >= 0 and idx < len(lines):
                stripped = lines[idx].strip()
                if stripped.startswith('@'):
                    ann_lines.insert(0, stripped)
                    idx -= 1
                elif stripped == '' or stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
                    idx -= 1
                else:
                    break
            methods.append({
                "return_type": m.group(3).strip() if m.group(3) else "Unit",
                "name": m.group(1),
                "params": m.group(2).strip(),
                "line": line_no,
                "annotations": ann_lines,
                "language": "kotlin",
            })

        return methods

    def _extract_fields(self, content: str) -> List[Dict[str, Any]]:
        """Extract field declarations from a Java/Kotlin source file."""
        fields = []
        # Java fields: private String name; / @Autowired private UserService userService;
        java_field_re = re.compile(
            r'(?:(?:private|protected|public)\s+)?'
            r'(?:(?:static|final|transient|volatile)\s+)*'
            r'([\w<>\[\]?,\s]+?)\s+'
            r'(\w+)\s*(?:=\s*[^;]+)?;',
            re.MULTILINE
        )
        # Kotlin fields: val/var name: Type
        kotlin_field_re = re.compile(
            r'(?:(?:private|protected|public|internal|override|lateinit)\s+)*'
            r'(val|var)\s+(\w+)\s*:\s*([\w<>\[\]?,\s]+)',
            re.MULTILINE
        )
        lines = content.split('\n')

        # Only look at fields inside the class body (skip method bodies heuristically)
        # We detect class-level fields by checking brace depth
        brace_depth = 0
        in_class = False
        class_body_start = -1

        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r'(?:public\s+|abstract\s+|open\s+)*(?:class|interface|enum)\s+', stripped):
                in_class = True
            if in_class and '{' in stripped and class_body_start == -1:
                class_body_start = i

        for m in java_field_re.finditer(content):
            line_no = content[:m.start()].count('\n') + 1
            field_type = m.group(1).strip()
            field_name = m.group(2)
            # Skip if it looks like a local variable inside a method (heuristic: check indent level)
            line_text = lines[line_no - 1] if line_no <= len(lines) else ""
            indent = len(line_text) - len(line_text.lstrip())
            # Class fields typically have 4-8 spaces indent; method locals have more
            if indent > 12:
                continue
            # Skip common false positives
            if field_type in ('return', 'throw', 'new', 'if', 'for', 'while', 'class', 'import', 'package'):
                continue

            ann_lines = []
            idx = line_no - 2
            while idx >= 0 and idx < len(lines):
                s = lines[idx].strip()
                if s.startswith('@'):
                    ann_lines.insert(0, s)
                    idx -= 1
                elif s == '' or s.startswith('//'):
                    idx -= 1
                else:
                    break
            fields.append({
                "type": field_type,
                "name": field_name,
                "line": line_no,
                "annotations": ann_lines,
                "language": "java",
            })

        for m in kotlin_field_re.finditer(content):
            line_no = content[:m.start()].count('\n') + 1
            mutability = m.group(1)
            field_name = m.group(2)
            field_type = m.group(3).strip()

            ann_lines = []
            idx = line_no - 2
            while idx >= 0 and idx < len(lines):
                s = lines[idx].strip()
                if s.startswith('@'):
                    ann_lines.insert(0, s)
                    idx -= 1
                elif s == '' or s.startswith('//'):
                    idx -= 1
                else:
                    break
            fields.append({
                "type": field_type,
                "name": field_name,
                "line": line_no,
                "annotations": ann_lines,
                "mutability": mutability,
                "language": "kotlin",
            })

        return fields

    def _has_annotation(self, annotations: List[str], *names: str) -> bool:
        """Check if any annotation line matches the given annotation names."""
        for ann_line in annotations:
            for name in names:
                if f'@{name}' in ann_line:
                    return True
        return False

    def _get_annotation_value(self, annotations: List[str], name: str) -> Optional[str]:
        """Extract the value from a specific annotation."""
        for ann_line in annotations:
            m = re.search(rf'@{name}\s*\(\s*(?:value\s*=\s*)?["\']?([^"\')\s,]+)', ann_line)
            if m:
                return m.group(1)
            if f'@{name}' in ann_line and '(' not in ann_line:
                return ""
        return None

    def _get_annotation_params(self, annotations: List[str], name: str) -> Dict[str, str]:
        """Extract all parameters from a specific annotation."""
        params = {}
        for ann_line in annotations:
            m = re.search(rf'@{name}\s*\((.+)\)', ann_line)
            if m:
                raw = m.group(1)
                for pm in self._ANNOTATION_PARAMS.finditer(raw):
                    key = pm.group(1)
                    val = pm.group(2) or pm.group(3) or pm.group(4) or pm.group(5) or ""
                    params[key] = val
                # Handle single-value annotation
                if not params:
                    cleaned = raw.strip().strip('"').strip("'")
                    params["value"] = cleaned
        return params

    def _find_files_with_annotation(self, base: str, annotation: str,
                                     dirs: Optional[List[str]] = None) -> List[Tuple[str, str, str]]:
        """Find all source files containing a specific annotation."""
        results = []
        for fpath, rel_path, content in self._walk_source_files(base, dirs):
            if f'@{annotation}' in content:
                results.append((fpath, rel_path, content))
        return results

    # ══════════════════════════════════════════════════════════════════
    # 1. get_entity_relationships
    # ══════════════════════════════════════════════════════════════════

    def get_entity_relationships(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @Entity classes for JPA relationship annotations.

        Extracts @OneToMany, @ManyToOne, @ManyToMany, @OneToOne, @JoinColumn,
        @JoinTable, @Column annotations with field types, fetch types, and cascade.

        Args:
            entity_name: Optional specific entity class name. If omitted, returns all entities.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        entity_files = self._find_files_with_annotation(base, "Entity")
        if not entity_files:
            return {"status": "error", "message": "No @Entity classes found in project"}

        rel_annotations = {'OneToMany', 'ManyToOne', 'ManyToMany', 'OneToOne'}
        all_entities = {}

        for fpath, rel_path, content in entity_files:
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            if entity_name and cls_name != entity_name:
                continue

            # Extract @Table annotation
            table_match = re.search(r'@Table\s*\(([^)]*)\)', content)
            table_name = ""
            if table_match:
                tbl_params = table_match.group(1)
                name_m = re.search(r'name\s*=\s*"([^"]*)"', tbl_params)
                if name_m:
                    table_name = name_m.group(1)

            fields = self._extract_fields(content)
            relationships = []
            columns = []

            for field in fields:
                ann_lines = field.get("annotations", [])
                ann_text = ' '.join(ann_lines)

                # Check for relationship annotations
                for rel_type in rel_annotations:
                    if self._has_annotation(ann_lines, rel_type):
                        rel_info = {
                            "field": field["name"],
                            "type": rel_type,
                            "target_entity": field["type"],
                            "line": field["line"],
                        }
                        # Extract fetch type
                        fetch_m = re.search(r'fetch\s*=\s*FetchType\.(\w+)', ann_text)
                        if fetch_m:
                            rel_info["fetch"] = fetch_m.group(1)

                        # Extract cascade
                        cascade_m = re.search(r'cascade\s*=\s*\{?([^})\]]+)\}?', ann_text)
                        if cascade_m:
                            cascades = re.findall(r'CascadeType\.(\w+)', cascade_m.group(1))
                            if cascades:
                                rel_info["cascade"] = cascades

                        # Extract mappedBy
                        mapped_m = re.search(r'mappedBy\s*=\s*"([^"]*)"', ann_text)
                        if mapped_m:
                            rel_info["mapped_by"] = mapped_m.group(1)

                        # Extract targetEntity
                        target_m = re.search(r'targetEntity\s*=\s*(\w+)\.class', ann_text)
                        if target_m:
                            rel_info["target_entity"] = target_m.group(1)

                        # Clean generic type for collection fields
                        generic_m = re.match(r'(?:List|Set|Collection)<(\w+)>', field["type"])
                        if generic_m:
                            rel_info["target_entity"] = generic_m.group(1)

                        relationships.append(rel_info)

                # Check for @JoinColumn
                if self._has_annotation(ann_lines, "JoinColumn"):
                    jc_params = self._get_annotation_params(ann_lines, "JoinColumn")
                    rel_info_match = next(
                        (r for r in relationships if r["field"] == field["name"]), None
                    )
                    if rel_info_match:
                        rel_info_match["join_column"] = jc_params

                # Check for @JoinTable
                if self._has_annotation(ann_lines, "JoinTable"):
                    jt_params = self._get_annotation_params(ann_lines, "JoinTable")
                    rel_info_match = next(
                        (r for r in relationships if r["field"] == field["name"]), None
                    )
                    if rel_info_match:
                        rel_info_match["join_table"] = jt_params

                # Check for @Column
                if self._has_annotation(ann_lines, "Column"):
                    col_params = self._get_annotation_params(ann_lines, "Column")
                    col_info = {
                        "field": field["name"],
                        "type": field["type"],
                        "line": field["line"],
                    }
                    col_info.update(col_params)
                    columns.append(col_info)

                # Check for @Id
                if self._has_annotation(ann_lines, "Id"):
                    col_info = {
                        "field": field["name"],
                        "type": field["type"],
                        "is_id": True,
                        "line": field["line"],
                    }
                    # Check @GeneratedValue
                    if self._has_annotation(ann_lines, "GeneratedValue"):
                        gen_params = self._get_annotation_params(ann_lines, "GeneratedValue")
                        col_info["generation_strategy"] = gen_params.get("strategy", "AUTO")
                    columns.append(col_info)

            all_entities[cls_name] = {
                "file": rel_path,
                "package": class_info["package"],
                "table": table_name,
                "extends": class_info["extends"],
                "relationships": relationships,
                "columns": columns,
            }

        if entity_name and entity_name not in all_entities:
            return {"status": "error", "message": f"Entity '{entity_name}' not found"}

        return {
            "status": "success",
            "entities": all_entities,
            "total_entities": len(all_entities),
        }

    # ══════════════════════════════════════════════════════════════════
    # 2. get_endpoint_map
    # ══════════════════════════════════════════════════════════════════

    def get_endpoint_map(self, filter_prefix: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse @RestController/@Controller classes for request mapping annotations.

        Extracts @RequestMapping, @GetMapping, @PostMapping, @PutMapping,
        @DeleteMapping, @PatchMapping with path, method, params, produces/consumes.

        Args:
            filter_prefix: Optional URL prefix filter (e.g., "/api/users")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        mapping_annotations = {
            'GetMapping': 'GET',
            'PostMapping': 'POST',
            'PutMapping': 'PUT',
            'DeleteMapping': 'DELETE',
            'PatchMapping': 'PATCH',
            'RequestMapping': None,  # method specified in annotation
        }

        endpoints = []
        controller_files = []
        for fpath, rel_path, content in self._walk_source_files(base):
            if '@RestController' in content or '@Controller' in content:
                controller_files.append((fpath, rel_path, content))

        for fpath, rel_path, content in controller_files:
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            # Extract class-level @RequestMapping for base path
            class_base_path = ""
            class_rm = re.search(
                r'@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']([^"\']*)["\']',
                content
            )
            if class_rm:
                class_base_path = class_rm.group(1)
            else:
                # Handle @RequestMapping("/path") without named param
                class_rm2 = re.search(r'@RequestMapping\s*\(\s*"([^"]*)"', content)
                if class_rm2:
                    class_base_path = class_rm2.group(1)

            # Determine if REST or MVC controller
            is_rest = '@RestController' in content

            methods = self._extract_methods(content)
            for method_info in methods:
                ann_lines = method_info.get("annotations", [])
                ann_text = '\n'.join(ann_lines)

                for mapping_ann, http_method in mapping_annotations.items():
                    if not self._has_annotation(ann_lines, mapping_ann):
                        continue

                    # Extract path from mapping annotation
                    path_value = ""
                    path_m = re.search(
                        rf'@{mapping_ann}\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']([^"\']*)["\']',
                        ann_text
                    )
                    if path_m:
                        path_value = path_m.group(1)
                    else:
                        # Handle array of paths: @GetMapping({"/a", "/b"})
                        paths_m = re.search(
                            rf'@{mapping_ann}\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{{([^}}]*)\}}',
                            ann_text
                        )
                        if paths_m:
                            path_value = re.findall(r'"([^"]*)"', paths_m.group(1))
                            if path_value:
                                path_value = path_value[0]  # Take first for primary mapping

                    full_path = class_base_path.rstrip('/') + '/' + path_value.lstrip('/') if path_value else class_base_path
                    if not full_path.startswith('/'):
                        full_path = '/' + full_path

                    # Determine HTTP method for @RequestMapping
                    actual_method = http_method
                    if mapping_ann == 'RequestMapping':
                        method_m = re.search(r'method\s*=\s*RequestMethod\.(\w+)', ann_text)
                        if method_m:
                            actual_method = method_m.group(1)
                        else:
                            actual_method = 'GET'

                    # Extract produces/consumes
                    produces_m = re.search(r'produces\s*=\s*["\']([^"\']*)["\']', ann_text)
                    consumes_m = re.search(r'consumes\s*=\s*["\']([^"\']*)["\']', ann_text)

                    # Extract params
                    params_m = re.search(r'params\s*=\s*["\']([^"\']*)["\']', ann_text)

                    endpoint_info = {
                        "method": actual_method,
                        "path": full_path.rstrip('/') or '/',
                        "handler": f"{cls_name}.{method_info['name']}",
                        "controller": cls_name,
                        "handler_method": method_info["name"],
                        "return_type": method_info["return_type"],
                        "params": method_info["params"],
                        "line": method_info["line"],
                        "file": rel_path,
                        "is_rest": is_rest,
                    }
                    if produces_m:
                        endpoint_info["produces"] = produces_m.group(1)
                    if consumes_m:
                        endpoint_info["consumes"] = consumes_m.group(1)
                    if params_m:
                        endpoint_info["request_params"] = params_m.group(1)

                    # Check for @ResponseBody on method (for @Controller)
                    if self._has_annotation(ann_lines, "ResponseBody"):
                        endpoint_info["is_rest"] = True

                    # Check @PreAuthorize
                    preauth = self._get_annotation_value(ann_lines, "PreAuthorize")
                    if preauth is not None:
                        endpoint_info["security"] = preauth

                    endpoints.append(endpoint_info)

        # Apply filter
        if filter_prefix:
            endpoints = [e for e in endpoints if e["path"].startswith(filter_prefix)]

        total = len(endpoints)
        if not filter_prefix and total > 80:
            prefixes = sorted(set(
                '/' + e["path"].strip('/').split('/')[0]
                for e in endpoints if e["path"] != '/'
            ))
            return {
                "status": "success",
                "endpoints": endpoints[:80],
                "total": total,
                "truncated": True,
                "warning": f"Showing 80/{total} endpoints. Use filter_prefix to narrow.",
                "available_prefixes": prefixes[:20],
            }

        return {
            "status": "success",
            "endpoints": endpoints,
            "total": total,
            "filter": filter_prefix,
        }

    # ══════════════════════════════════════════════════════════════════
    # 3. get_schema_info
    # ══════════════════════════════════════════════════════════════════

    def get_schema_info(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse JPA entity fields and migration files (Flyway/Liquibase) to build schema.

        Combines @Column, @Id, @GeneratedValue from entities with migration SQL
        (Flyway V*.sql) and Liquibase changelogs.

        Args:
            entity_name: Optional entity class name to filter by.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        schema = {}

        # ── Parse JPA entities ──
        entity_files = self._find_files_with_annotation(base, "Entity")
        for fpath, rel_path, content in entity_files:
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            if entity_name and cls_name != entity_name:
                continue

            # Get table name
            table_name = cls_name.lower() + "s"  # default JPA convention
            table_m = re.search(r'@Table\s*\(\s*(?:name\s*=\s*)?["\']([^"\']*)["\']', content)
            if table_m:
                table_name = table_m.group(1)

            fields = self._extract_fields(content)
            entity_columns = []
            for field in fields:
                ann_lines = field.get("annotations", [])
                ann_text = ' '.join(ann_lines)
                col = {
                    "field_name": field["name"],
                    "java_type": field["type"],
                }

                # @Id
                if self._has_annotation(ann_lines, "Id"):
                    col["is_id"] = True
                    gen_m = re.search(r'@GeneratedValue\s*\(\s*strategy\s*=\s*GenerationType\.(\w+)', ann_text)
                    if gen_m:
                        col["generation"] = gen_m.group(1)

                # @Column
                if self._has_annotation(ann_lines, "Column"):
                    col_params = self._get_annotation_params(ann_lines, "Column")
                    if "name" in col_params:
                        col["column_name"] = col_params["name"]
                    if "nullable" in col_params:
                        col["nullable"] = col_params["nullable"]
                    if "length" in col_params:
                        col["length"] = col_params["length"]
                    if "unique" in col_params:
                        col["unique"] = col_params["unique"]
                    if "columnDefinition" in col_params:
                        col["column_definition"] = col_params["columnDefinition"]

                # @Enumerated
                if self._has_annotation(ann_lines, "Enumerated"):
                    enum_val = self._get_annotation_value(ann_lines, "Enumerated")
                    col["enumerated"] = enum_val or "ORDINAL"

                # @Temporal
                if self._has_annotation(ann_lines, "Temporal"):
                    temp_val = self._get_annotation_value(ann_lines, "Temporal")
                    col["temporal"] = temp_val

                # @Lob
                if self._has_annotation(ann_lines, "Lob"):
                    col["lob"] = True

                # Skip relationship fields (they are not simple columns)
                rel_anns = {'OneToMany', 'ManyToOne', 'ManyToMany', 'OneToOne'}
                if any(self._has_annotation(ann_lines, ra) for ra in rel_anns):
                    continue

                entity_columns.append(col)

            schema[cls_name] = {
                "table": table_name,
                "file": rel_path,
                "columns": entity_columns,
                "migrations": [],
            }

        # ── Parse Flyway migrations (V*.sql) ──
        flyway_dirs = [
            os.path.join(base, self.RESOURCES, "db", "migration"),
            os.path.join(base, self.RESOURCES, "db", "migrations"),
        ]
        for flyway_dir in flyway_dirs:
            if not os.path.isdir(flyway_dir):
                continue
            for fname in sorted(os.listdir(flyway_dir)):
                if not fname.endswith('.sql'):
                    continue
                # Flyway naming: V1__Description.sql, V1.1__Description.sql
                version_m = re.match(r'V([\d_.]+)__(.+)\.sql', fname)
                if not version_m:
                    continue

                fpath = os.path.join(flyway_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        sql_content = f.read()
                except Exception:
                    continue

                migration_info = {
                    "file": os.path.relpath(fpath, base),
                    "version": version_m.group(1),
                    "description": version_m.group(2).replace('_', ' '),
                    "statements": [],
                }

                # Parse CREATE TABLE
                create_tables = re.findall(
                    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\']?(\w+)[`"\']?\s*\(([^;]+)\)',
                    sql_content, re.IGNORECASE | re.DOTALL
                )
                for tbl_name, tbl_body in create_tables:
                    cols = []
                    for col_line in tbl_body.split(','):
                        col_line = col_line.strip()
                        col_m = re.match(r'[`"\']?(\w+)[`"\']?\s+(\w+(?:\([^)]*\))?)\s*(.*)', col_line)
                        if col_m and col_m.group(1).upper() not in ('PRIMARY', 'CONSTRAINT', 'FOREIGN', 'UNIQUE', 'INDEX', 'KEY', 'CHECK'):
                            cols.append({
                                "name": col_m.group(1),
                                "type": col_m.group(2),
                                "constraints": col_m.group(3).strip(),
                            })
                    migration_info["statements"].append({
                        "type": "CREATE TABLE",
                        "table": tbl_name,
                        "columns": cols,
                    })

                # Parse ALTER TABLE
                alter_stmts = re.findall(
                    r'ALTER\s+TABLE\s+[`"\']?(\w+)[`"\']?\s+(ADD|DROP|MODIFY|ALTER)\s+(?:COLUMN\s+)?(.+?)(?:;|$)',
                    sql_content, re.IGNORECASE | re.DOTALL
                )
                for tbl_name, action, detail in alter_stmts:
                    migration_info["statements"].append({
                        "type": f"ALTER TABLE {action.upper()}",
                        "table": tbl_name,
                        "detail": detail.strip()[:200],
                    })

                # Associate with matching entity
                matched = False
                for ent_name, ent_data in schema.items():
                    if ent_data["table"] and ent_data["table"].lower() == tbl_name.lower() if create_tables else False:
                        ent_data["migrations"].append(migration_info)
                        matched = True

                if not matched and migration_info["statements"]:
                    # Store as standalone migration
                    key = f"_migration_{migration_info['version']}"
                    schema.setdefault("_standalone_migrations", []).append(migration_info)

        # ── Parse Liquibase changelogs ──
        changelog_dirs = [
            os.path.join(base, self.RESOURCES, "db", "changelog"),
            os.path.join(base, self.RESOURCES, "liquibase"),
        ]
        for cl_dir in changelog_dirs:
            if not os.path.isdir(cl_dir):
                continue
            for root, _, files in os.walk(cl_dir):
                for fname in files:
                    if not fname.endswith(('.xml', '.yaml', '.yml', '.sql')):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                            cl_content = f.read()
                    except Exception:
                        continue

                    cl_info = {
                        "file": os.path.relpath(fpath, base),
                        "type": "liquibase",
                        "changesets": [],
                    }

                    if fname.endswith('.xml'):
                        # Parse XML changesets
                        changesets = re.findall(
                            r'<changeSet\s+([^>]*)>(.*?)</changeSet>',
                            cl_content, re.DOTALL
                        )
                        for cs_attrs, cs_body in changesets:
                            cs_id_m = re.search(r'id\s*=\s*"([^"]*)"', cs_attrs)
                            cs_author_m = re.search(r'author\s*=\s*"([^"]*)"', cs_attrs)
                            create_m = re.findall(
                                r'<createTable\s+tableName\s*=\s*"([^"]*)"',
                                cs_body
                            )
                            add_col_m = re.findall(
                                r'<addColumn\s+tableName\s*=\s*"([^"]*)"',
                                cs_body
                            )
                            cl_info["changesets"].append({
                                "id": cs_id_m.group(1) if cs_id_m else "",
                                "author": cs_author_m.group(1) if cs_author_m else "",
                                "creates_tables": create_m,
                                "modifies_tables": add_col_m,
                            })
                    elif fname.endswith(('.yaml', '.yml')):
                        # Simple YAML parsing for changelogs
                        changeset_blocks = re.findall(
                            r'-\s+changeSet:\s*\n((?:\s+.+\n)*)',
                            cl_content
                        )
                        for block in changeset_blocks:
                            cs_id_m = re.search(r'id:\s*(.+)', block)
                            cs_author_m = re.search(r'author:\s*(.+)', block)
                            create_m = re.findall(r'tableName:\s*(\w+)', block)
                            cl_info["changesets"].append({
                                "id": cs_id_m.group(1).strip() if cs_id_m else "",
                                "author": cs_author_m.group(1).strip() if cs_author_m else "",
                                "tables": create_m,
                            })

                    if cl_info["changesets"]:
                        schema.setdefault("_liquibase_changelogs", []).append(cl_info)

        if entity_name and entity_name not in schema:
            return {"status": "error", "message": f"Entity '{entity_name}' not found"}

        return {
            "status": "success",
            "schema": schema,
            "total_entities": len([k for k in schema if not k.startswith('_')]),
        }

    # ══════════════════════════════════════════════════════════════════
    # 4. get_template_dependencies
    # ══════════════════════════════════════════════════════════════════

    def get_template_dependencies(self, template_path: str) -> Dict[str, Any]:
        """
        Parse Thymeleaf template for dependencies.

        Extracts th:replace, th:insert, th:fragment, th:each, th:if, th:text,
        @{} link expressions, ${} variable expressions.

        Args:
            template_path: Relative path to the template file
                          (e.g., "src/main/resources/templates/users/list.html")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, template_path)
        if not os.path.isfile(full_path):
            # Try under templates directory
            full_path = os.path.join(base, self.RESOURCES, "templates", template_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"Template not found: {template_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read template: {e}"}

        dependencies = {
            "file": os.path.relpath(full_path, base),
            "fragments_included": [],
            "fragments_replaced": [],
            "fragments_defined": [],
            "iterations": [],
            "conditionals": [],
            "text_expressions": [],
            "link_expressions": [],
            "variable_expressions": [],
            "model_attributes": set(),
            "form_actions": [],
        }

        # th:replace="fragments/header :: header" or th:replace="~{fragments/header :: header}"
        replace_re = re.compile(r'th:replace\s*=\s*"~?\{?([^"{}]+)\}?"')
        for m in replace_re.finditer(content):
            fragment_ref = m.group(1).strip()
            dependencies["fragments_replaced"].append(fragment_ref)

        # th:insert
        insert_re = re.compile(r'th:insert\s*=\s*"~?\{?([^"{}]+)\}?"')
        for m in insert_re.finditer(content):
            dependencies["fragments_included"].append(m.group(1).strip())

        # th:fragment (definitions)
        fragment_re = re.compile(r'th:fragment\s*=\s*"([^"]*)"')
        for m in fragment_re.finditer(content):
            dependencies["fragments_defined"].append(m.group(1).strip())

        # th:each="item : ${items}"
        each_re = re.compile(r'th:each\s*=\s*"(\w+)\s*(?:,\s*\w+)?\s*:\s*\$\{([^}]+)\}"')
        for m in each_re.finditer(content):
            dependencies["iterations"].append({
                "variable": m.group(1),
                "collection": m.group(2),
            })
            dependencies["model_attributes"].add(m.group(2).split('.')[0])

        # th:if / th:unless
        cond_re = re.compile(r'th:(?:if|unless)\s*=\s*"([^"]*)"')
        for m in cond_re.finditer(content):
            expr = m.group(1)
            dependencies["conditionals"].append(expr)
            # Extract model attributes from conditions
            for var_m in re.finditer(r'\$\{([^}]+)\}', expr):
                dependencies["model_attributes"].add(var_m.group(1).split('.')[0])

        # th:text / th:utext / th:value
        text_re = re.compile(r'th:(?:text|utext|value)\s*=\s*"([^"]*)"')
        for m in text_re.finditer(content):
            expr = m.group(1)
            dependencies["text_expressions"].append(expr)
            for var_m in re.finditer(r'\$\{([^}]+)\}', expr):
                dependencies["model_attributes"].add(var_m.group(1).split('.')[0])

        # @{} link expressions (URLs)
        link_re = re.compile(r'@\{([^}]+)\}')
        for m in link_re.finditer(content):
            dependencies["link_expressions"].append(m.group(1))

        # ${} variable expressions (all occurrences)
        var_re = re.compile(r'\$\{([^}]+)\}')
        seen_vars = set()
        for m in var_re.finditer(content):
            expr = m.group(1)
            if expr not in seen_vars:
                seen_vars.add(expr)
                dependencies["variable_expressions"].append(expr)
                dependencies["model_attributes"].add(expr.split('.')[0].split('[')[0])

        # th:action for forms
        action_re = re.compile(r'th:action\s*=\s*"@\{([^}]+)\}"')
        for m in action_re.finditer(content):
            dependencies["form_actions"].append(m.group(1))

        # th:object (form backing bean)
        object_re = re.compile(r'th:object\s*=\s*"\$\{([^}]+)\}"')
        for m in object_re.finditer(content):
            dependencies["model_attributes"].add(m.group(1))

        # th:field for form fields
        field_re = re.compile(r'th:field\s*=\s*"\*\{([^}]+)\}"')
        form_fields = []
        for m in field_re.finditer(content):
            form_fields.append(m.group(1))
        if form_fields:
            dependencies["form_fields"] = form_fields

        # Convert set to sorted list
        dependencies["model_attributes"] = sorted(dependencies["model_attributes"])

        # Find which controller renders this template
        template_name = os.path.relpath(full_path, os.path.join(base, self.RESOURCES, "templates"))
        template_name = template_name.replace('.html', '').replace(os.sep, '/')
        controllers_rendering = []
        for fpath, rel_path, src_content in self._walk_source_files(base):
            if template_name in src_content:
                # Look for return "template_name" or model.addAttribute + return
                return_m = re.search(
                    rf'return\s+["\']({re.escape(template_name)})["\']',
                    src_content
                )
                if return_m:
                    class_info = self._extract_class_info(src_content)
                    controllers_rendering.append({
                        "controller": class_info["class_name"],
                        "file": rel_path,
                    })
        dependencies["rendered_by"] = controllers_rendering

        return {
            "status": "success",
            "dependencies": dependencies,
        }

    # ══════════════════════════════════════════════════════════════════
    # 5. get_flow_map
    # ══════════════════════════════════════════════════════════════════

    def get_flow_map(self, entry_point: str, method: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace a complete request flow from endpoint to data layer.

        Traces: Endpoint -> Spring Security filter -> @PreAuthorize ->
        Controller method -> @Autowired Service -> Repository -> Entity.
        Detects @Transactional boundaries.

        Args:
            entry_point: Controller class name (e.g., "UserController") or
                        URL path (e.g., "/api/users")
            method: Optional method name. If omitted with controller name,
                   returns flows for all handler methods.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        flows = []
        target_controller = None
        target_path = None

        # Determine if entry_point is a controller name or URL path
        if entry_point.startswith('/'):
            target_path = entry_point
        else:
            target_controller = entry_point

        # Find matching controller(s)
        controller_files = self._find_files_with_annotation(base, "Controller")
        rest_files = self._find_files_with_annotation(base, "RestController")
        all_controllers = {f[1]: f for f in controller_files + rest_files}

        for rel_path, (fpath, _, content) in all_controllers.items():
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            if target_controller and cls_name != target_controller:
                continue

            methods_list = self._extract_methods(content)
            fields = self._extract_fields(content)

            # Get class-level base path
            class_base_path = ""
            class_rm = re.search(
                r'@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']([^"\']*)["\']',
                content
            )
            if class_rm:
                class_base_path = class_rm.group(1)

            # Identify injected services from fields and constructor
            injected_services = []
            for field in fields:
                ann_lines = field.get("annotations", [])
                if self._has_annotation(ann_lines, "Autowired", "Inject"):
                    injected_services.append({
                        "name": field["name"],
                        "type": field["type"],
                        "injection": "field",
                    })

            # Constructor injection
            constructor_re = re.compile(
                rf'(?:public\s+)?{re.escape(cls_name)}\s*\(([^)]+)\)',
                re.DOTALL
            )
            ctor_m = constructor_re.search(content)
            if ctor_m:
                params = ctor_m.group(1)
                for param_m in re.finditer(r'(?:final\s+)?(\w+(?:<[^>]+>)?)\s+(\w+)', params):
                    p_type = param_m.group(1)
                    p_name = param_m.group(2)
                    if p_type.endswith(('Service', 'Repository', 'Client', 'Provider', 'Mapper', 'Helper')):
                        injected_services.append({
                            "name": p_name,
                            "type": p_type,
                            "injection": "constructor",
                        })

            for method_info in methods_list:
                m_name = method_info["name"]
                if method and m_name != method:
                    continue

                ann_lines = method_info.get("annotations", [])
                ann_text = '\n'.join(ann_lines)

                # Check if this is a handler method (has mapping annotation)
                mapping_anns = ['GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping',
                                'PatchMapping', 'RequestMapping']
                is_handler = any(self._has_annotation(ann_lines, ma) for ma in mapping_anns)
                if not is_handler:
                    continue

                # Determine endpoint path
                endpoint_path = class_base_path
                for ma in mapping_anns:
                    path_val = self._get_annotation_value(ann_lines, ma)
                    if path_val is not None and path_val:
                        endpoint_path = class_base_path.rstrip('/') + '/' + path_val.lstrip('/')
                        break

                if target_path and not endpoint_path.startswith(target_path):
                    continue

                flow = {
                    "controller": cls_name,
                    "method": m_name,
                    "endpoint": endpoint_path,
                    "file": rel_path,
                    "line": method_info["line"],
                    "steps": [],
                }

                # Step 1: Security
                preauth = self._get_annotation_value(ann_lines, "PreAuthorize")
                secured = self._get_annotation_value(ann_lines, "Secured")
                if preauth is not None:
                    flow["steps"].append({
                        "type": "security",
                        "annotation": "@PreAuthorize",
                        "expression": preauth,
                    })
                if secured is not None:
                    flow["steps"].append({
                        "type": "security",
                        "annotation": "@Secured",
                        "expression": secured,
                    })

                # Step 2: Validation
                if '@Valid' in ann_text or '@Validated' in ann_text:
                    flow["steps"].append({
                        "type": "validation",
                        "annotation": "@Valid/@Validated",
                        "params": method_info["params"],
                    })

                # Step 3: Service calls - look at method body
                # Find method body
                method_line = method_info["line"]
                lines = content.split('\n')
                brace_start = -1
                for li in range(method_line - 1, min(method_line + 5, len(lines))):
                    if li < len(lines) and '{' in lines[li]:
                        brace_start = li
                        break

                if brace_start >= 0:
                    brace_depth = 0
                    method_body_lines = []
                    for li in range(brace_start, len(lines)):
                        line = lines[li]
                        brace_depth += line.count('{') - line.count('}')
                        method_body_lines.append(line)
                        if brace_depth <= 0:
                            break
                    method_body = '\n'.join(method_body_lines)

                    # Detect service method calls
                    for svc in injected_services:
                        svc_calls = re.findall(
                            rf'{re.escape(svc["name"])}\.(\w+)\s*\(',
                            method_body
                        )
                        for call in svc_calls:
                            flow["steps"].append({
                                "type": "service_call",
                                "service": svc["type"],
                                "method": call,
                            })

                    # Detect @Transactional on called service methods
                    transactional = self._has_annotation(ann_lines, "Transactional")
                    if transactional:
                        flow["steps"].append({
                            "type": "transaction_boundary",
                            "scope": "method",
                        })

                    # Detect repository calls
                    repo_calls = re.findall(r'(\w+Repository)\.(\w+)\s*\(', method_body)
                    for repo, repo_method in repo_calls:
                        flow["steps"].append({
                            "type": "repository_call",
                            "repository": repo,
                            "method": repo_method,
                        })

                    # Detect event publishing
                    event_calls = re.findall(
                        r'(?:applicationEventPublisher|eventPublisher|publisher)\.publish(?:Event)?\s*\(\s*new\s+(\w+)',
                        method_body
                    )
                    for evt in event_calls:
                        flow["steps"].append({
                            "type": "event_published",
                            "event": evt,
                        })

                flow["injected_services"] = injected_services
                flows.append(flow)

        if not flows:
            return {"status": "error", "message": f"No matching flows found for '{entry_point}'"}

        # Also trace service->repository->entity chains
        for flow in flows:
            for step in flow.get("steps", []):
                if step["type"] == "service_call":
                    svc_type = step["service"]
                    svc_method = step["method"]
                    svc_chain = self._trace_service_chain(base, svc_type, svc_method)
                    if svc_chain:
                        step["chain"] = svc_chain

        return {
            "status": "success",
            "flows": flows,
            "total": len(flows),
        }

    def _trace_service_chain(self, base: str, service_type: str, method_name: str) -> Optional[Dict]:
        """Trace a service method to its repository/entity calls."""
        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            if class_info["class_name"] != service_type:
                continue

            methods = self._extract_methods(content)
            for m in methods:
                if m["name"] != method_name:
                    continue

                ann_lines = m.get("annotations", [])
                chain = {
                    "service": service_type,
                    "method": method_name,
                    "file": rel_path,
                    "transactional": self._has_annotation(ann_lines, "Transactional"),
                    "repository_calls": [],
                    "entity_operations": [],
                }

                # Find method body
                lines = content.split('\n')
                brace_start = -1
                for li in range(m["line"] - 1, min(m["line"] + 5, len(lines))):
                    if li < len(lines) and '{' in lines[li]:
                        brace_start = li
                        break

                if brace_start >= 0:
                    brace_depth = 0
                    body_lines = []
                    for li in range(brace_start, len(lines)):
                        line = lines[li]
                        brace_depth += line.count('{') - line.count('}')
                        body_lines.append(line)
                        if brace_depth <= 0:
                            break
                    body = '\n'.join(body_lines)

                    # Repository calls
                    repo_calls = re.findall(r'(\w+)\.(?:save|findBy\w+|findAll|delete\w*|count\w*|exists\w*)\s*\(', body)
                    for rc in repo_calls:
                        chain["repository_calls"].append(rc)

                    # Entity operations (new Entity(), entity.setX())
                    entity_ops = re.findall(r'new\s+(\w+)\s*\(', body)
                    chain["entity_operations"] = entity_ops

                return chain

        return None

    # ══════════════════════════════════════════════════════════════════
    # 6. get_cache_map
    # ══════════════════════════════════════════════════════════════════

    def get_cache_map(self, entity_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Find and map Spring Cache annotations.

        Maps @Cacheable, @CacheEvict, @CachePut, @Caching annotations to
        methods and cache names, tracking eviction triggers.

        Args:
            entity_name: Optional entity/service name to filter by.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        cache_map = {
            "caches": {},       # cache_name -> {readers: [], writers: [], evictors: []}
            "by_class": {},     # class_name -> [{method, annotation, cache_name}]
        }

        cache_annotations = {
            'Cacheable': 'read',
            'CachePut': 'write',
            'CacheEvict': 'evict',
        }

        for fpath, rel_path, content in self._walk_source_files(base):
            has_cache = any(f'@{ann}' in content for ann in list(cache_annotations.keys()) + ['Caching'])
            if not has_cache:
                continue

            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            if entity_name and entity_name not in cls_name:
                continue

            methods = self._extract_methods(content)
            class_entries = []

            for method_info in methods:
                ann_lines = method_info.get("annotations", [])
                ann_text = '\n'.join(ann_lines)

                for cache_ann, cache_action in cache_annotations.items():
                    if not self._has_annotation(ann_lines, cache_ann):
                        continue

                    # Extract cache name(s)
                    cache_names = []
                    # @Cacheable("cacheName")
                    name_m = re.search(rf'@{cache_ann}\s*\(\s*["\']([^"\']+)["\']', ann_text)
                    if name_m:
                        cache_names.append(name_m.group(1))
                    # @Cacheable(value = "cacheName") or @Cacheable(cacheNames = "cacheName")
                    val_m = re.search(rf'@{cache_ann}\s*\([^)]*(?:value|cacheNames)\s*=\s*["\']([^"\']+)["\']', ann_text)
                    if val_m:
                        cache_names.append(val_m.group(1))
                    # @Cacheable(value = {"c1", "c2"})
                    arr_m = re.search(rf'@{cache_ann}\s*\([^)]*(?:value|cacheNames)\s*=\s*\{{([^}}]+)\}}', ann_text)
                    if arr_m:
                        cache_names.extend(re.findall(r'"([^"]+)"', arr_m.group(1)))

                    # Deduplicate
                    cache_names = list(set(cache_names)) or ["unknown"]

                    # Extract key expression
                    key_expr = ""
                    key_m = re.search(rf'@{cache_ann}\s*\([^)]*key\s*=\s*"([^"]*)"', ann_text)
                    if key_m:
                        key_expr = key_m.group(1)

                    # Extract condition
                    condition = ""
                    cond_m = re.search(rf'@{cache_ann}\s*\([^)]*condition\s*=\s*"([^"]*)"', ann_text)
                    if cond_m:
                        condition = cond_m.group(1)

                    # For @CacheEvict: check allEntries
                    all_entries = False
                    if cache_ann == 'CacheEvict':
                        all_entries = 'allEntries' in ann_text and 'true' in ann_text

                    entry = {
                        "class": cls_name,
                        "method": method_info["name"],
                        "action": cache_action,
                        "cache_names": cache_names,
                        "key": key_expr,
                        "condition": condition,
                        "file": rel_path,
                        "line": method_info["line"],
                    }
                    if all_entries:
                        entry["all_entries"] = True

                    class_entries.append(entry)

                    # Register in cache_map by cache name
                    for cn in cache_names:
                        if cn not in cache_map["caches"]:
                            cache_map["caches"][cn] = {"readers": [], "writers": [], "evictors": []}
                        target_list = {
                            "read": "readers",
                            "write": "writers",
                            "evict": "evictors",
                        }[cache_action]
                        cache_map["caches"][cn][target_list].append({
                            "class": cls_name,
                            "method": method_info["name"],
                            "file": rel_path,
                            "key": key_expr,
                        })

                # Handle @Caching (composite)
                if self._has_annotation(ann_lines, "Caching"):
                    caching_text = ann_text
                    # Extract nested annotations
                    for sub_ann in ['cacheable', 'put', 'evict']:
                        sub_re = re.compile(
                            rf'{sub_ann}\s*=\s*\{{?\s*@Cache\w+\s*\(([^)]*)\)\s*\}}?'
                        )
                        for sub_m in sub_re.finditer(caching_text):
                            sub_name_m = re.search(r'["\']([^"\']+)["\']', sub_m.group(1))
                            if sub_name_m:
                                cn = sub_name_m.group(1)
                                act = {"cacheable": "read", "put": "write", "evict": "evict"}[sub_ann]
                                entry = {
                                    "class": cls_name,
                                    "method": method_info["name"],
                                    "action": act,
                                    "cache_names": [cn],
                                    "composite": True,
                                    "file": rel_path,
                                    "line": method_info["line"],
                                }
                                class_entries.append(entry)

            if class_entries:
                cache_map["by_class"][cls_name] = class_entries

        # Detect potential stale cache issues
        warnings = []
        for cn, cache_data in cache_map["caches"].items():
            if cache_data["readers"] and not cache_data["evictors"] and not cache_data["writers"]:
                warnings.append({
                    "cache": cn,
                    "issue": "Cache is read but never explicitly evicted or updated",
                    "readers": [f"{r['class']}.{r['method']}" for r in cache_data["readers"]],
                })
            if cache_data["evictors"] and not cache_data["readers"]:
                warnings.append({
                    "cache": cn,
                    "issue": "Cache is evicted but never read (dead cache?)",
                    "evictors": [f"{e['class']}.{e['method']}" for e in cache_data["evictors"]],
                })

        return {
            "status": "success",
            "cache_map": cache_map,
            "warnings": warnings,
            "total_caches": len(cache_map["caches"]),
        }

    # ══════════════════════════════════════════════════════════════════
    # 7. get_filter_chain
    # ══════════════════════════════════════════════════════════════════

    def get_filter_chain(self, endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse Spring Security configuration for filter chains.

        Parses SecurityFilterChain, http.authorizeHttpRequests(),
        .requestMatchers(), .hasRole(), @PreAuthorize, @Secured.

        Args:
            endpoint: Optional URL path to check security for.
                     If omitted, returns full security configuration.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        security_config = {
            "filter_chains": [],
            "global_config": {},
            "method_security": [],
        }

        # Find security configuration files
        for fpath, rel_path, content in self._walk_source_files(base):
            has_security = (
                'SecurityFilterChain' in content or
                '@EnableWebSecurity' in content or
                '@EnableMethodSecurity' in content or
                'WebSecurityConfigurerAdapter' in content or
                '@EnableGlobalMethodSecurity' in content
            )
            if not has_security:
                continue

            class_info = self._extract_class_info(content)

            # Check for method security enablement
            if '@EnableMethodSecurity' in content or '@EnableGlobalMethodSecurity' in content:
                security_config["global_config"]["method_security_enabled"] = True
                prepost_m = re.search(r'prePostEnabled\s*=\s*(true|false)', content)
                if prepost_m:
                    security_config["global_config"]["pre_post_enabled"] = prepost_m.group(1) == 'true'
                secured_m = re.search(r'securedEnabled\s*=\s*(true|false)', content)
                if secured_m:
                    security_config["global_config"]["secured_enabled"] = secured_m.group(1) == 'true'

            # Parse SecurityFilterChain bean methods
            chain_re = re.compile(
                r'(?:@Bean\s+)?(?:public\s+)?SecurityFilterChain\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+\w+\s*)?\{',
                re.DOTALL
            )
            for chain_m in chain_re.finditer(content):
                chain_name = chain_m.group(1)
                # Extract method body
                start = chain_m.end()
                brace_depth = 1
                pos = start
                while pos < len(content) and brace_depth > 0:
                    if content[pos] == '{':
                        brace_depth += 1
                    elif content[pos] == '}':
                        brace_depth -= 1
                    pos += 1
                body = content[start:pos - 1]

                chain_info = {
                    "name": chain_name,
                    "file": rel_path,
                    "rules": [],
                    "csrf": None,
                    "cors": None,
                    "session": None,
                    "oauth2": False,
                    "jwt": False,
                    "form_login": False,
                    "http_basic": False,
                }

                # Parse authorizeHttpRequests rules
                # .requestMatchers("/api/**").hasRole("ADMIN")
                matcher_re = re.compile(
                    r'\.requestMatchers?\s*\(\s*([^)]+)\)\s*\.\s*(\w+)\s*\(\s*([^)]*)\)',
                    re.DOTALL
                )
                for mm in matcher_re.finditer(body):
                    pattern_raw = mm.group(1).strip()
                    auth_method = mm.group(2)
                    auth_args = mm.group(3).strip().strip('"').strip("'")

                    # Clean up pattern
                    patterns = re.findall(r'"([^"]*)"', pattern_raw)
                    if not patterns:
                        patterns = [pattern_raw.strip().strip('"')]

                    # Extract HTTP methods from HttpMethod.GET etc
                    http_methods = re.findall(r'HttpMethod\.(\w+)', pattern_raw)

                    rule = {
                        "patterns": patterns,
                        "auth": auth_method,
                    }
                    if auth_args:
                        rule["args"] = auth_args
                    if http_methods:
                        rule["http_methods"] = http_methods
                    chain_info["rules"].append(rule)

                # Parse anyRequest()
                any_req_m = re.search(r'\.anyRequest\(\)\s*\.\s*(\w+)\s*\(([^)]*)\)', body)
                if any_req_m:
                    chain_info["rules"].append({
                        "patterns": ["/**"],
                        "auth": any_req_m.group(1),
                        "args": any_req_m.group(2).strip().strip('"'),
                        "catch_all": True,
                    })

                # Check CSRF
                csrf_m = re.search(r'\.csrf\s*\(\s*([^)]*)\)', body)
                if csrf_m:
                    if 'disable' in csrf_m.group(1) or '.disable()' in body[body.find('.csrf'):body.find('.csrf') + 100]:
                        chain_info["csrf"] = "disabled"
                    else:
                        chain_info["csrf"] = "enabled"

                # Check CORS
                if '.cors(' in body:
                    chain_info["cors"] = "configured"

                # Check session management
                session_m = re.search(r'SessionCreationPolicy\.(\w+)', body)
                if session_m:
                    chain_info["session"] = session_m.group(1)

                # Check OAuth2/JWT/form-login/httpBasic
                if 'oauth2' in body.lower():
                    chain_info["oauth2"] = True
                if 'jwt' in body.lower() or 'JwtDecoder' in body:
                    chain_info["jwt"] = True
                if '.formLogin(' in body:
                    chain_info["form_login"] = True
                if '.httpBasic(' in body:
                    chain_info["http_basic"] = True

                security_config["filter_chains"].append(chain_info)

            # Also handle legacy WebSecurityConfigurerAdapter
            if 'WebSecurityConfigurerAdapter' in content:
                configure_m = re.search(
                    r'void\s+configure\s*\(\s*HttpSecurity\s+\w+\s*\)\s*(?:throws\s+\w+\s*)?\{',
                    content
                )
                if configure_m:
                    start = configure_m.end()
                    brace_depth = 1
                    pos = start
                    while pos < len(content) and brace_depth > 0:
                        if content[pos] == '{':
                            brace_depth += 1
                        elif content[pos] == '}':
                            brace_depth -= 1
                        pos += 1
                    body = content[start:pos - 1]
                    chain_info = {
                        "name": "configure (legacy)",
                        "file": rel_path,
                        "rules": [],
                        "legacy": True,
                    }
                    # Parse antMatchers (Spring Security 5.x)
                    ant_re = re.compile(
                        r'\.antMatchers?\s*\(\s*([^)]+)\)\s*\.\s*(\w+)\s*\(\s*([^)]*)\)'
                    )
                    for am in ant_re.finditer(body):
                        patterns = re.findall(r'"([^"]*)"', am.group(1))
                        chain_info["rules"].append({
                            "patterns": patterns,
                            "auth": am.group(2),
                            "args": am.group(3).strip().strip('"'),
                        })
                    security_config["filter_chains"].append(chain_info)

        # Find @PreAuthorize and @Secured on controller methods
        controller_files = self._find_files_with_annotation(base, "Controller")
        rest_files = self._find_files_with_annotation(base, "RestController")
        for fpath, rel_path, content in controller_files + rest_files:
            class_info = self._extract_class_info(content)
            methods = self._extract_methods(content)
            for m in methods:
                ann_lines = m.get("annotations", [])
                preauth = self._get_annotation_value(ann_lines, "PreAuthorize")
                secured = self._get_annotation_value(ann_lines, "Secured")
                roles_allowed = self._get_annotation_value(ann_lines, "RolesAllowed")

                if preauth is not None or secured is not None or roles_allowed is not None:
                    method_sec = {
                        "class": class_info["class_name"],
                        "method": m["name"],
                        "file": rel_path,
                        "line": m["line"],
                    }
                    if preauth is not None:
                        method_sec["pre_authorize"] = preauth
                    if secured is not None:
                        method_sec["secured"] = secured
                    if roles_allowed is not None:
                        method_sec["roles_allowed"] = roles_allowed
                    security_config["method_security"].append(method_sec)

        # Filter by endpoint if specified
        if endpoint:
            matching_rules = []
            for chain in security_config["filter_chains"]:
                for rule in chain.get("rules", []):
                    for pattern in rule.get("patterns", []):
                        if self._matches_ant_pattern(endpoint, pattern):
                            matching_rules.append({
                                "chain": chain["name"],
                                "pattern": pattern,
                                "auth": rule["auth"],
                                "args": rule.get("args", ""),
                            })

            # Also check method-level security
            endpoint_method_sec = []
            endpoint_map = self.get_endpoint_map(filter_prefix=endpoint)
            if endpoint_map.get("status") == "success":
                handler_methods = set()
                for ep in endpoint_map.get("endpoints", []):
                    handler_methods.add((ep["controller"], ep["handler_method"]))
                for ms in security_config["method_security"]:
                    if (ms["class"], ms["method"]) in handler_methods:
                        endpoint_method_sec.append(ms)

            return {
                "status": "success",
                "endpoint": endpoint,
                "matching_rules": matching_rules,
                "method_security": endpoint_method_sec,
                "full_config": security_config,
            }

        return {
            "status": "success",
            "security": security_config,
            "total_chains": len(security_config["filter_chains"]),
            "total_method_rules": len(security_config["method_security"]),
        }

    def _matches_ant_pattern(self, path: str, pattern: str) -> bool:
        """Simple Ant-style pattern matching for URL paths."""
        if pattern == '/**':
            return True
        if pattern == path:
            return True
        # Convert Ant pattern to regex
        regex = pattern.replace('/**', '/.*').replace('/*', '/[^/]*').replace('?', '.')
        try:
            return bool(re.match(regex + '$', path))
        except re.error:
            return False

    # ══════════════════════════════════════════════════════════════════
    # 8. get_validation_chain
    # ══════════════════════════════════════════════════════════════════

    def get_validation_chain(self, controller: str, method: str) -> Dict[str, Any]:
        """
        Trace validation from @Valid DTO through constraint annotations to handler.

        Maps @Valid parameter -> @NotNull/@Size/@Pattern constraints on DTO fields ->
        controller handler -> response. Detects missing validations.

        Args:
            controller: Controller class name (e.g., "UserController")
            method: Handler method name (e.g., "createUser")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        validation_chain = {
            "controller": controller,
            "method": method,
            "handler": None,
            "validated_params": [],
            "dto_constraints": {},
            "warnings": [],
        }

        # Find the controller
        controller_content = None
        controller_path = None
        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            if class_info["class_name"] == controller:
                controller_content = content
                controller_path = rel_path
                break

        if not controller_content:
            return {"status": "error", "message": f"Controller '{controller}' not found"}

        # Find the method
        methods = self._extract_methods(controller_content)
        target_method = None
        for m in methods:
            if m["name"] == method:
                target_method = m
                break

        if not target_method:
            return {"status": "error", "message": f"Method '{method}' not found in {controller}"}

        validation_chain["handler"] = {
            "file": controller_path,
            "line": target_method["line"],
            "return_type": target_method["return_type"],
            "params": target_method["params"],
        }

        # Parse method parameters for @Valid/@Validated
        params_str = target_method["params"]
        # Match patterns like: @Valid @RequestBody CreateUserDto dto
        param_re = re.compile(
            r'(@\w+(?:\([^)]*\))?\s+)*'
            r'([\w<>\[\]]+)\s+(\w+)'
        )
        validation_annotations = {'Valid', 'Validated', 'NotNull', 'NotBlank', 'NotEmpty'}
        binding_annotations = {'RequestBody', 'ModelAttribute', 'RequestParam', 'PathVariable'}

        for pm in param_re.finditer(params_str):
            annotations_raw = pm.group(1) or ""
            param_type = pm.group(2)
            param_name = pm.group(3)

            param_annotations = re.findall(r'@(\w+)', annotations_raw)
            has_valid = any(a in validation_annotations for a in param_annotations)
            binding = next((a for a in param_annotations if a in binding_annotations), None)

            if has_valid:
                validated_param = {
                    "name": param_name,
                    "type": param_type,
                    "binding": binding or "body",
                    "annotations": param_annotations,
                }
                validation_chain["validated_params"].append(validated_param)

                # Find and parse the DTO class for constraints
                dto_constraints = self._parse_dto_constraints(base, param_type)
                if dto_constraints:
                    validation_chain["dto_constraints"][param_type] = dto_constraints
                else:
                    validation_chain["warnings"].append(
                        f"Could not find DTO class '{param_type}' to check constraints"
                    )
            elif binding == 'RequestBody' and not has_valid:
                validation_chain["warnings"].append(
                    f"@RequestBody parameter '{param_name}' ({param_type}) has no @Valid annotation - "
                    f"constraints on {param_type} will NOT be enforced"
                )

        # Check for @RequestParam without validation
        request_params_in_method = re.findall(
            r'@RequestParam(?:\([^)]*\))?\s+(?:@\w+(?:\([^)]*\))?\s+)*(\w+)\s+(\w+)',
            params_str
        )
        for rp_type, rp_name in request_params_in_method:
            if not any(rp_name == vp["name"] for vp in validation_chain["validated_params"]):
                if rp_type in ('String', 'Integer', 'Long'):
                    validation_chain["warnings"].append(
                        f"@RequestParam '{rp_name}' ({rp_type}) has no validation constraints"
                    )

        # Check for custom validator references
        lines = controller_content.split('\n')
        method_body = self._extract_method_body(lines, target_method["line"])
        if method_body:
            # Check for manual validation: BindingResult, Errors
            if 'BindingResult' in params_str or 'Errors' in params_str:
                validation_chain["manual_validation"] = True
                if 'hasErrors' not in method_body and 'hasFieldErrors' not in method_body:
                    validation_chain["warnings"].append(
                        "BindingResult parameter present but hasErrors() not checked"
                    )

            # Check for Validator usage
            if 'validator.validate' in method_body or 'Validator' in params_str:
                validation_chain["custom_validator"] = True

        return {
            "status": "success",
            "validation": validation_chain,
        }

    def _parse_dto_constraints(self, base: str, dto_type: str) -> Optional[Dict]:
        """Parse a DTO class for Bean Validation constraint annotations."""
        constraint_annotations = {
            'NotNull', 'NotBlank', 'NotEmpty',
            'Size', 'Min', 'Max', 'DecimalMin', 'DecimalMax',
            'Pattern', 'Email', 'Past', 'Future', 'PastOrPresent', 'FutureOrPresent',
            'Positive', 'PositiveOrZero', 'Negative', 'NegativeOrZero',
            'Digits', 'AssertTrue', 'AssertFalse',
        }

        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            if class_info["class_name"] != dto_type:
                continue

            fields = self._extract_fields(content)
            constraints = {}

            for field in fields:
                ann_lines = field.get("annotations", [])
                field_constraints = []

                for ann_line in ann_lines:
                    for ca in constraint_annotations:
                        if f'@{ca}' in ann_line:
                            constraint_info = {"annotation": ca}
                            # Extract parameters
                            params = self._get_annotation_params([ann_line], ca)
                            if params:
                                constraint_info["params"] = params
                            # Extract message
                            msg_m = re.search(rf'@{ca}\s*\([^)]*message\s*=\s*"([^"]*)"', ann_line)
                            if msg_m:
                                constraint_info["message"] = msg_m.group(1)
                            field_constraints.append(constraint_info)

                if field_constraints:
                    constraints[field["name"]] = {
                        "type": field["type"],
                        "constraints": field_constraints,
                    }

            # Check for unconstrained fields
            unconstrained = [f["name"] for f in fields if f["name"] not in constraints]

            return {
                "file": rel_path,
                "constraints": constraints,
                "unconstrained_fields": unconstrained,
                "total_fields": len(fields),
                "constrained_fields": len(constraints),
            }

        return None

    def _extract_method_body(self, lines: List[str], method_line: int) -> Optional[str]:
        """Extract method body text from source lines given the method declaration line."""
        brace_start = -1
        for li in range(method_line - 1, min(method_line + 5, len(lines))):
            if li < len(lines) and '{' in lines[li]:
                brace_start = li
                break
        if brace_start < 0:
            return None
        brace_depth = 0
        body_lines = []
        for li in range(brace_start, len(lines)):
            line = lines[li]
            brace_depth += line.count('{') - line.count('}')
            body_lines.append(line)
            if brace_depth <= 0:
                break
        return '\n'.join(body_lines)

    # ══════════════════════════════════════════════════════════════════
    # 9. verify_endpoint
    # ══════════════════════════════════════════════════════════════════

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify an endpoint: mapping exists, handler params match, security
        configured, validation present.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            url: URL path (e.g., "/api/users/{id}")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        result = {
            "method": method.upper(),
            "url": url,
            "checks": {},
            "warnings": [],
        }

        # Check 1: Endpoint mapping exists
        endpoint_map = self.get_endpoint_map()
        if endpoint_map.get("status") != "success":
            result["checks"]["mapping"] = {"status": "error", "message": "Cannot load endpoint map"}
            return {"status": "error", "result": result}

        matching_endpoints = []
        for ep in endpoint_map.get("endpoints", []):
            if ep["method"] == method.upper():
                # Normalize paths for comparison (handle path variables)
                ep_pattern = re.sub(r'\{[^}]+\}', '[^/]+', ep["path"])
                url_normalized = url.rstrip('/')
                if re.match(ep_pattern + '$', url_normalized):
                    matching_endpoints.append(ep)

        if not matching_endpoints:
            result["checks"]["mapping"] = {
                "status": "fail",
                "message": f"No {method.upper()} mapping found for {url}",
            }
            result["warnings"].append("Endpoint not mapped - request will return 404")
        else:
            ep = matching_endpoints[0]
            result["checks"]["mapping"] = {
                "status": "pass",
                "handler": ep["handler"],
                "file": ep["file"],
                "line": ep["line"],
            }

            # Check 2: Security configuration
            filter_result = self.get_filter_chain(endpoint=url)
            if filter_result.get("status") == "success":
                matching_rules = filter_result.get("matching_rules", [])
                method_sec = filter_result.get("method_security", [])
                if matching_rules or method_sec:
                    result["checks"]["security"] = {
                        "status": "pass",
                        "url_rules": matching_rules,
                        "method_rules": method_sec,
                    }
                else:
                    result["checks"]["security"] = {
                        "status": "warning",
                        "message": "No explicit security rules found for this endpoint",
                    }
                    result["warnings"].append("No security rules configured - endpoint may be unprotected")
            else:
                result["checks"]["security"] = {
                    "status": "warning",
                    "message": "No Spring Security configuration found",
                }

            # Check 3: Validation
            ctrl = ep["controller"]
            handler = ep["handler_method"]
            val_result = self.get_validation_chain(ctrl, handler)
            if val_result.get("status") == "success":
                val_data = val_result.get("validation", {})
                if val_data.get("validated_params"):
                    result["checks"]["validation"] = {
                        "status": "pass",
                        "validated_params": val_data["validated_params"],
                    }
                elif method.upper() in ('POST', 'PUT', 'PATCH'):
                    result["checks"]["validation"] = {
                        "status": "warning",
                        "message": "No @Valid annotation found on body parameters for mutating endpoint",
                    }
                    result["warnings"].append("Input validation missing on mutating endpoint")
                else:
                    result["checks"]["validation"] = {"status": "pass", "message": "No body validation needed for GET/DELETE"}

                if val_data.get("warnings"):
                    result["warnings"].extend(val_data["warnings"])
            else:
                result["checks"]["validation"] = {"status": "unknown", "message": val_result.get("message", "")}

            # Check 4: Return type
            if ep.get("is_rest") and ep.get("return_type"):
                rt = ep["return_type"]
                if rt == "void":
                    result["warnings"].append("Handler returns void - no response body")
                result["checks"]["return_type"] = {
                    "status": "info",
                    "type": rt,
                    "is_rest": ep["is_rest"],
                }

        overall_status = "pass"
        for check_name, check_data in result["checks"].items():
            if check_data.get("status") == "fail":
                overall_status = "fail"
                break
            elif check_data.get("status") == "warning":
                overall_status = "warning"

        result["overall_status"] = overall_status
        return {"status": "success", "result": result}

    # ══════════════════════════════════════════════════════════════════
    # 10. get_project_conventions
    # ══════════════════════════════════════════════════════════════════

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the Spring Boot project codebase.

        Args:
            pattern_type: One of: "naming", "injection", "exception",
                         "testing", "logging"
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        handlers = {
            "naming": self._conventions_naming,
            "injection": self._conventions_injection,
            "exception": self._conventions_exception,
            "testing": self._conventions_testing,
            "logging": self._conventions_logging,
        }

        handler = handlers.get(pattern_type)
        if not handler:
            return {
                "status": "error",
                "message": f"Unknown pattern type: '{pattern_type}'. Use one of: {', '.join(handlers.keys())}",
            }

        return handler(base)

    def _conventions_naming(self, base: str) -> Dict[str, Any]:
        """Extract naming conventions from the project."""
        conventions = {
            "packages": {},
            "class_suffixes": {},
            "examples": [],
        }

        suffix_counts: Dict[str, int] = {}
        package_patterns: Dict[str, Set[str]] = {}

        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]
            pkg = class_info["package"]

            if not cls_name:
                continue

            # Track class suffixes
            for suffix in ['Controller', 'Service', 'Repository', 'Entity', 'Dto',
                          'DTO', 'Config', 'Configuration', 'Exception', 'Mapper',
                          'Validator', 'Listener', 'Handler', 'Factory', 'Builder',
                          'Adapter', 'Client', 'Provider', 'Impl']:
                if cls_name.endswith(suffix):
                    suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
                    break

            # Track package organization
            if pkg:
                parts = pkg.split('.')
                if len(parts) >= 3:
                    layer = parts[-1]  # Last segment usually indicates layer
                    package_patterns.setdefault(layer, set()).add(cls_name)

        # Build conventions summary
        for suffix, count in sorted(suffix_counts.items(), key=lambda x: -x[1]):
            conventions["class_suffixes"][suffix] = count

        for layer, classes in sorted(package_patterns.items()):
            conventions["packages"][layer] = {
                "count": len(classes),
                "examples": sorted(list(classes))[:5],
            }

        return {"status": "success", "type": "naming", "conventions": conventions}

    def _conventions_injection(self, base: str) -> Dict[str, Any]:
        """Analyze dependency injection patterns in the project."""
        conventions = {
            "field_injection": 0,
            "constructor_injection": 0,
            "setter_injection": 0,
            "field_injection_files": [],
            "constructor_injection_files": [],
            "recommendation": "",
        }

        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            # Count @Autowired on fields
            field_autowired = len(re.findall(r'@Autowired\s*\n\s*(?:private|protected)', content))
            field_inject = len(re.findall(r'@Inject\s*\n\s*(?:private|protected)', content))

            # Count constructor injection (constructor with multiple params, no @Autowired on fields)
            has_constructor_injection = bool(re.search(
                rf'(?:public\s+)?{re.escape(cls_name)}\s*\([^)]*(?:Service|Repository|Client|Provider|Mapper)\s+\w+',
                content
            ))

            # Count setter injection
            setter_injection = len(re.findall(r'@Autowired\s*\n\s*public\s+void\s+set\w+', content))

            if field_autowired + field_inject > 0:
                conventions["field_injection"] += field_autowired + field_inject
                conventions["field_injection_files"].append({
                    "file": rel_path,
                    "class": cls_name,
                    "count": field_autowired + field_inject,
                })
            if has_constructor_injection:
                conventions["constructor_injection"] += 1
                conventions["constructor_injection_files"].append({
                    "file": rel_path,
                    "class": cls_name,
                })
            conventions["setter_injection"] += setter_injection

        total = conventions["field_injection"] + conventions["constructor_injection"] + conventions["setter_injection"]
        if total > 0:
            if conventions["constructor_injection"] > conventions["field_injection"]:
                conventions["recommendation"] = "Project predominantly uses constructor injection (recommended)"
            elif conventions["field_injection"] > conventions["constructor_injection"]:
                conventions["recommendation"] = (
                    "Project uses field injection heavily. Consider migrating to constructor injection "
                    "for testability and immutability."
                )
            else:
                conventions["recommendation"] = "Mixed injection patterns detected. Consider standardizing."

        # Truncate file lists
        conventions["field_injection_files"] = conventions["field_injection_files"][:20]
        conventions["constructor_injection_files"] = conventions["constructor_injection_files"][:20]

        return {"status": "success", "type": "injection", "conventions": conventions}

    def _conventions_exception(self, base: str) -> Dict[str, Any]:
        """Analyze exception handling patterns."""
        conventions = {
            "global_handlers": [],
            "controller_advice_classes": [],
            "exception_classes": [],
            "patterns": [],
        }

        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]

            # @ControllerAdvice / @RestControllerAdvice
            if '@ControllerAdvice' in content or '@RestControllerAdvice' in content:
                advice_info = {
                    "class": cls_name,
                    "file": rel_path,
                    "handlers": [],
                }
                # Find @ExceptionHandler methods
                handler_re = re.compile(
                    r'@ExceptionHandler\s*\(\s*(?:value\s*=\s*)?\{?([^})]+)\}?\s*\)\s*'
                    r'(?:@\w+(?:\([^)]*\))?\s*)*'
                    r'(?:public\s+)?(\S+)\s+(\w+)',
                    re.DOTALL
                )
                for hm in handler_re.finditer(content):
                    exception_types = re.findall(r'(\w+)\.class', hm.group(1))
                    if not exception_types:
                        exception_types = [hm.group(1).strip().replace('.class', '')]
                    advice_info["handlers"].append({
                        "method": hm.group(3),
                        "handles": exception_types,
                        "return_type": hm.group(2),
                    })
                conventions["controller_advice_classes"].append(advice_info)

            # Custom exception classes
            if cls_name.endswith('Exception') or (class_info["extends"] and 'Exception' in class_info["extends"]):
                conventions["exception_classes"].append({
                    "class": cls_name,
                    "extends": class_info["extends"],
                    "file": rel_path,
                })

            # @ResponseStatus on exception classes
            if '@ResponseStatus' in content and ('Exception' in cls_name or 'extends' in content):
                status_m = re.search(r'@ResponseStatus\s*\(\s*(?:value\s*=\s*)?(?:HttpStatus\.)?(\w+)', content)
                if status_m:
                    conventions["patterns"].append({
                        "class": cls_name,
                        "status": status_m.group(1),
                        "file": rel_path,
                    })

        return {"status": "success", "type": "exception", "conventions": conventions}

    def _conventions_testing(self, base: str) -> Dict[str, Any]:
        """Analyze testing patterns."""
        conventions = {
            "test_annotations": {},
            "mock_framework": None,
            "test_files": 0,
            "patterns": [],
        }

        test_roots = self._find_test_roots(base)
        annotation_counts: Dict[str, int] = {}
        mock_counts = {"mockito": 0, "mockk": 0, "wiremock": 0}

        for fpath, rel_path, content in self._walk_source_files(base, test_roots):
            conventions["test_files"] += 1

            # Count test annotations
            for ann in ['SpringBootTest', 'DataJpaTest', 'WebMvcTest', 'MockBean',
                       'Test', 'ParameterizedTest', 'RepeatedTest', 'Nested',
                       'TestContainers', 'AutoConfigureMockMvc', 'WebFluxTest']:
                if f'@{ann}' in content:
                    annotation_counts[ann] = annotation_counts.get(ann, 0) + 1

            # Detect mock framework
            if 'import org.mockito' in content or '@Mock' in content:
                mock_counts["mockito"] += 1
            if 'import io.mockk' in content or 'mockk' in content:
                mock_counts["mockk"] += 1
            if 'WireMock' in content:
                mock_counts["wiremock"] += 1

            # Look for test patterns
            class_info = self._extract_class_info(content)
            if class_info["class_name"]:
                test_type = "unit"
                if '@SpringBootTest' in content:
                    test_type = "integration"
                elif '@DataJpaTest' in content:
                    test_type = "repository"
                elif '@WebMvcTest' in content:
                    test_type = "controller"

                conventions["patterns"].append({
                    "class": class_info["class_name"],
                    "type": test_type,
                    "file": rel_path,
                })

        conventions["test_annotations"] = annotation_counts
        # Determine primary mock framework
        max_mock = max(mock_counts.items(), key=lambda x: x[1]) if any(mock_counts.values()) else None
        if max_mock and max_mock[1] > 0:
            conventions["mock_framework"] = max_mock[0]

        # Truncate patterns
        conventions["patterns"] = conventions["patterns"][:30]

        return {"status": "success", "type": "testing", "conventions": conventions}

    def _conventions_logging(self, base: str) -> Dict[str, Any]:
        """Analyze logging patterns."""
        conventions = {
            "framework": None,
            "patterns": [],
            "logger_declarations": 0,
            "log_levels_used": {},
        }

        framework_counts = {"slf4j": 0, "log4j": 0, "jul": 0, "lombok_slf4j": 0}
        level_counts: Dict[str, int] = {}

        for fpath, rel_path, content in self._walk_source_files(base):
            # Detect logging framework
            if 'import org.slf4j' in content or 'LoggerFactory' in content:
                framework_counts["slf4j"] += 1
            if '@Slf4j' in content:
                framework_counts["lombok_slf4j"] += 1
            if 'import org.apache.log4j' in content or 'import org.apache.logging.log4j' in content:
                framework_counts["log4j"] += 1
            if 'import java.util.logging' in content:
                framework_counts["jul"] += 1

            # Count logger declarations
            if re.search(r'(?:Logger|Log)\s+\w+\s*=\s*(?:LoggerFactory|LogManager)', content):
                conventions["logger_declarations"] += 1
            if '@Slf4j' in content:
                conventions["logger_declarations"] += 1

            # Count log level usage
            for level in ['trace', 'debug', 'info', 'warn', 'error']:
                count = len(re.findall(rf'log(?:ger)?\.{level}\s*\(', content, re.IGNORECASE))
                if count > 0:
                    level_counts[level] = level_counts.get(level, 0) + count

        conventions["log_levels_used"] = level_counts

        # Determine primary framework
        max_fw = max(framework_counts.items(), key=lambda x: x[1])
        if max_fw[1] > 0:
            conventions["framework"] = max_fw[0]

        return {"status": "success", "type": "logging", "conventions": conventions}

    # ══════════════════════════════════════════════════════════════════
    # 11. get_similar_patterns
    # ══════════════════════════════════════════════════════════════════

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.

        Searches for common Spring Boot patterns: pagination, file-upload,
        authentication, jwt, email, scheduler, event-listener, aspect,
        interceptor, websocket.

        Args:
            description: Natural language description of what to find
                        (e.g., "pagination", "file upload", "jwt authentication")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Pattern registry: keyword -> search patterns
        pattern_registry = {
            "pagination": {
                "keywords": ["pagina", "page", "pageable", "paging"],
                "annotations": [],
                "code_patterns": [
                    r'Pageable\s+\w+',
                    r'Page<',
                    r'PageRequest\.of\s*\(',
                    r'Slice<',
                    r'@PageableDefault',
                    r'spring\.data\.web\.pageable',
                ],
                "description": "Pagination implementation",
            },
            "file-upload": {
                "keywords": ["file", "upload", "multipart", "storage"],
                "annotations": [],
                "code_patterns": [
                    r'MultipartFile\s+\w+',
                    r'@RequestPart',
                    r'transferTo\s*\(',
                    r'StorageService',
                    r'multipart\.max-file-size',
                    r'Files\.copy\s*\(',
                    r'S3Client',
                    r'MinioClient',
                ],
                "description": "File upload handling",
            },
            "authentication": {
                "keywords": ["auth", "login", "signin", "sign-in", "authenticate"],
                "annotations": ["PreAuthorize", "Secured", "RolesAllowed"],
                "code_patterns": [
                    r'AuthenticationManager',
                    r'UserDetailsService',
                    r'PasswordEncoder',
                    r'SecurityContextHolder',
                    r'UsernamePasswordAuthenticationToken',
                    r'@AuthenticationPrincipal',
                    r'Authentication\s+\w+',
                ],
                "description": "Authentication implementation",
            },
            "jwt": {
                "keywords": ["jwt", "token", "bearer"],
                "annotations": [],
                "code_patterns": [
                    r'JwtDecoder',
                    r'JwtEncoder',
                    r'Jwts\.builder',
                    r'Jwts\.parser',
                    r'JwtTokenProvider',
                    r'BearerTokenAuthenticationToken',
                    r'io\.jsonwebtoken',
                    r'jwt\.secret',
                ],
                "description": "JWT token handling",
            },
            "email": {
                "keywords": ["email", "mail", "smtp", "notification"],
                "annotations": [],
                "code_patterns": [
                    r'JavaMailSender',
                    r'SimpleMailMessage',
                    r'MimeMessage',
                    r'MailService',
                    r'spring\.mail\.',
                    r'@Async.*mail',
                    r'TemplateEngine.*mail',
                ],
                "description": "Email sending implementation",
            },
            "scheduler": {
                "keywords": ["schedul", "cron", "periodic", "timer", "job"],
                "annotations": ["Scheduled", "EnableScheduling"],
                "code_patterns": [
                    r'@Scheduled\s*\(',
                    r'cron\s*=',
                    r'fixedRate\s*=',
                    r'fixedDelay\s*=',
                    r'TaskScheduler',
                    r'ScheduledExecutorService',
                ],
                "description": "Scheduled task implementation",
            },
            "event-listener": {
                "keywords": ["event", "listener", "publish", "emit", "observer"],
                "annotations": ["EventListener", "TransactionalEventListener"],
                "code_patterns": [
                    r'ApplicationEventPublisher',
                    r'@EventListener',
                    r'@TransactionalEventListener',
                    r'ApplicationEvent',
                    r'extends\s+ApplicationEvent',
                    r'publishEvent\s*\(',
                ],
                "description": "Event-driven architecture",
            },
            "aspect": {
                "keywords": ["aspect", "aop", "cross-cutting", "interceptor", "advice"],
                "annotations": ["Aspect", "Around", "Before", "After", "AfterReturning", "AfterThrowing"],
                "code_patterns": [
                    r'@Aspect',
                    r'@Around\s*\(',
                    r'@Before\s*\(',
                    r'@After\s*\(',
                    r'ProceedingJoinPoint',
                    r'JoinPoint\s+\w+',
                    r'@Pointcut\s*\(',
                ],
                "description": "AOP / Aspect-oriented programming",
            },
            "interceptor": {
                "keywords": ["interceptor", "filter", "middleware"],
                "annotations": [],
                "code_patterns": [
                    r'HandlerInterceptor',
                    r'implements\s+Filter',
                    r'OncePerRequestFilter',
                    r'preHandle\s*\(',
                    r'postHandle\s*\(',
                    r'afterCompletion\s*\(',
                    r'WebMvcConfigurer',
                    r'addInterceptors\s*\(',
                ],
                "description": "Request interceptor / filter",
            },
            "websocket": {
                "keywords": ["websocket", "stomp", "sockjs", "real-time", "realtime"],
                "annotations": ["MessageMapping", "SendTo", "EnableWebSocket", "EnableWebSocketMessageBroker"],
                "code_patterns": [
                    r'@MessageMapping',
                    r'@SendTo',
                    r'SimpMessagingTemplate',
                    r'WebSocketHandler',
                    r'StompEndpointRegistry',
                    r'WebSocketConfigurer',
                    r'TextWebSocketHandler',
                ],
                "description": "WebSocket implementation",
            },
        }

        # Match description to pattern categories
        description_lower = description.lower()
        matched_categories = []
        for cat_name, cat_data in pattern_registry.items():
            score = 0
            for kw in cat_data["keywords"]:
                if kw in description_lower:
                    score += 2
            if cat_name.replace('-', ' ') in description_lower or cat_name.replace('-', '') in description_lower:
                score += 3
            if score > 0:
                matched_categories.append((cat_name, cat_data, score))

        # If no specific match, search all categories
        if not matched_categories:
            matched_categories = [(name, data, 1) for name, data in pattern_registry.items()]

        # Search source files
        results = []
        for cat_name, cat_data, score in sorted(matched_categories, key=lambda x: -x[2]):
            category_matches = []

            for fpath, rel_path, content in self._walk_source_files(base):
                file_matches = []

                # Check code patterns
                for pattern in cat_data["code_patterns"]:
                    matches = list(re.finditer(pattern, content))
                    for m in matches:
                        line_no = content[:m.start()].count('\n') + 1
                        lines = content.split('\n')
                        line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""
                        file_matches.append({
                            "line": line_no,
                            "text": line_text[:120],
                            "pattern": pattern,
                        })

                # Check annotations
                for ann in cat_data.get("annotations", []):
                    if f'@{ann}' in content:
                        for m in re.finditer(rf'@{ann}', content):
                            line_no = content[:m.start()].count('\n') + 1
                            lines = content.split('\n')
                            line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""
                            file_matches.append({
                                "line": line_no,
                                "text": line_text[:120],
                                "annotation": ann,
                            })

                if file_matches:
                    class_info = self._extract_class_info(content)
                    category_matches.append({
                        "file": rel_path,
                        "class": class_info["class_name"],
                        "matches": file_matches[:10],
                    })

            if category_matches:
                results.append({
                    "category": cat_name,
                    "description": cat_data["description"],
                    "relevance_score": score,
                    "files": category_matches[:15],
                    "total_files": len(category_matches),
                })

        return {
            "status": "success",
            "query": description,
            "results": results,
            "total_categories_matched": len(results),
        }

    # ══════════════════════════════════════════════════════════════════
    # 12. get_project_snapshot
    # ══════════════════════════════════════════════════════════════════

    def get_project_snapshot(self) -> Dict[str, Any]:
        """
        Overview of the Spring Boot project structure.

        Returns: entities, controllers, services, repositories, configs,
        security rules, scheduled tasks, profiles, application properties.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        snapshot = {
            "entities": [],
            "controllers": [],
            "services": [],
            "repositories": [],
            "configurations": [],
            "security_rules": [],
            "scheduled_tasks": [],
            "profiles": [],
            "properties": {},
            "build_tool": None,
            "dependencies": [],
        }

        # Scan source files
        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]
            if not cls_name:
                continue

            entry = {"name": cls_name, "file": rel_path, "package": class_info["package"]}

            if '@Entity' in content:
                table_m = re.search(r'@Table\s*\(\s*(?:name\s*=\s*)?["\']([^"\']*)["\']', content)
                entry["table"] = table_m.group(1) if table_m else ""
                snapshot["entities"].append(entry)

            if '@RestController' in content or '@Controller' in content:
                base_path_m = re.search(
                    r'@RequestMapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?["\']([^"\']*)["\']',
                    content
                )
                entry["base_path"] = base_path_m.group(1) if base_path_m else ""
                entry["is_rest"] = '@RestController' in content
                snapshot["controllers"].append(entry)

            if '@Service' in content:
                snapshot["services"].append(entry)

            if '@Repository' in content or cls_name.endswith('Repository'):
                # Check if it extends JpaRepository etc
                extends_m = re.search(
                    r'extends\s+(?:Jpa|Crud|PagingAndSorting|Reactive)Repository<(\w+)',
                    content
                )
                if extends_m:
                    entry["entity"] = extends_m.group(1)
                snapshot["repositories"].append(entry)

            if '@Configuration' in content or '@SpringBootApplication' in content:
                snapshot["configurations"].append(entry)

            # Scheduled tasks
            if '@Scheduled' in content:
                scheduled_methods = re.findall(
                    r'@Scheduled\s*\(([^)]*)\)\s*(?:public\s+)?(?:void\s+)?(\w+)',
                    content
                )
                for sched_params, sched_method in scheduled_methods:
                    cron_m = re.search(r'cron\s*=\s*"([^"]*)"', sched_params)
                    rate_m = re.search(r'fixedRate\s*=\s*(\d+)', sched_params)
                    delay_m = re.search(r'fixedDelay\s*=\s*(\d+)', sched_params)
                    task_info = {
                        "class": cls_name,
                        "method": sched_method,
                        "file": rel_path,
                    }
                    if cron_m:
                        task_info["cron"] = cron_m.group(1)
                    if rate_m:
                        task_info["fixed_rate_ms"] = int(rate_m.group(1))
                    if delay_m:
                        task_info["fixed_delay_ms"] = int(delay_m.group(1))
                    snapshot["scheduled_tasks"].append(task_info)

        # Parse application properties/yml
        props_files = [
            "application.properties",
            "application.yml",
            "application.yaml",
        ]
        resources_dir = os.path.join(base, self.RESOURCES)
        if os.path.isdir(resources_dir):
            for pf in props_files:
                props_path = os.path.join(resources_dir, pf)
                if not os.path.isfile(props_path):
                    continue
                try:
                    with open(props_path, 'r', encoding='utf-8', errors='replace') as f:
                        props_content = f.read()
                except Exception:
                    continue

                if pf.endswith('.properties'):
                    for line in props_content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            parts = line.split('=', 1)
                            if len(parts) == 2:
                                key = parts[0].strip()
                                val = parts[1].strip()
                                # Mask sensitive values
                                if any(s in key.lower() for s in ['password', 'secret', 'key', 'token']):
                                    val = "***"
                                snapshot["properties"][key] = val
                elif pf.endswith(('.yml', '.yaml')):
                    # Simple YAML key extraction (not full parsing)
                    current_prefix = []
                    for line in props_content.split('\n'):
                        if not line.strip() or line.strip().startswith('#'):
                            continue
                        # Calculate indent level
                        indent = len(line) - len(line.lstrip())
                        stripped = line.strip()
                        if ':' in stripped:
                            key_part = stripped.split(':')[0].strip()
                            val_part = ':'.join(stripped.split(':')[1:]).strip()
                            # Adjust prefix stack based on indent
                            level = indent // 2
                            current_prefix = current_prefix[:level]
                            current_prefix.append(key_part)
                            if val_part:
                                full_key = '.'.join(current_prefix)
                                if any(s in full_key.lower() for s in ['password', 'secret', 'key', 'token']):
                                    val_part = "***"
                                snapshot["properties"][full_key] = val_part

            # Find profiles
            for fname in os.listdir(resources_dir):
                profile_m = re.match(r'application-(\w+)\.(properties|ya?ml)', fname)
                if profile_m:
                    snapshot["profiles"].append(profile_m.group(1))

        # Detect build tool
        if os.path.isfile(os.path.join(base, 'pom.xml')):
            snapshot["build_tool"] = "maven"
            try:
                with open(os.path.join(base, 'pom.xml'), 'r', encoding='utf-8', errors='replace') as f:
                    pom_content = f.read()
                deps = re.findall(
                    r'<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>',
                    pom_content, re.DOTALL
                )
                snapshot["dependencies"] = [
                    {"group": g, "artifact": a} for g, a in deps
                ][:30]
            except Exception:
                pass
        elif os.path.isfile(os.path.join(base, 'build.gradle')) or os.path.isfile(os.path.join(base, 'build.gradle.kts')):
            snapshot["build_tool"] = "gradle"
            gradle_file = os.path.join(base, 'build.gradle')
            if not os.path.isfile(gradle_file):
                gradle_file = os.path.join(base, 'build.gradle.kts')
            try:
                with open(gradle_file, 'r', encoding='utf-8', errors='replace') as f:
                    gradle_content = f.read()
                deps = re.findall(
                    r"(?:implementation|compileOnly|runtimeOnly|testImplementation)\s*[\(']([^)']+)",
                    gradle_content
                )
                snapshot["dependencies"] = [{"dependency": d.strip().strip("'\"()")} for d in deps][:30]
            except Exception:
                pass

        return {
            "status": "success",
            "snapshot": snapshot,
            "summary": {
                "entities": len(snapshot["entities"]),
                "controllers": len(snapshot["controllers"]),
                "services": len(snapshot["services"]),
                "repositories": len(snapshot["repositories"]),
                "configurations": len(snapshot["configurations"]),
                "scheduled_tasks": len(snapshot["scheduled_tasks"]),
                "profiles": len(snapshot["profiles"]),
                "build_tool": snapshot["build_tool"],
            },
        }

    # ══════════════════════════════════════════════════════════════════
    # 13. get_dependency_injection
    # ══════════════════════════════════════════════════════════════════

    def get_dependency_injection(self, class_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Map @Autowired/@Inject fields and constructor injection.

        Builds a dependency graph showing who injects what.

        Args:
            class_name: Optional class name to filter by. If omitted, maps all classes.
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        di_graph = {}  # class -> {dependencies: [...], dependents: [...]}

        for fpath, rel_path, content in self._walk_source_files(base):
            class_info = self._extract_class_info(content)
            cls_name = class_info["class_name"]
            if not cls_name:
                continue

            if class_name and cls_name != class_name:
                # Still collect for graph building (dependents)
                pass

            dependencies = []
            injection_type = None

            # Field injection: @Autowired private Type name;
            field_re = re.compile(
                r'@(?:Autowired|Inject)\s*\n\s*(?:private|protected|public)\s+'
                r'(?:final\s+)?(\w+(?:<[^>]+>)?)\s+(\w+)\s*;',
                re.MULTILINE
            )
            for fm in field_re.finditer(content):
                dependencies.append({
                    "type": fm.group(1),
                    "name": fm.group(2),
                    "injection": "field",
                    "line": content[:fm.start()].count('\n') + 1,
                })

            # Constructor injection
            # Find constructor: ClassName(Type1 param1, Type2 param2)
            ctor_re = re.compile(
                rf'(?:public\s+)?{re.escape(cls_name)}\s*\(([^)]+)\)',
                re.DOTALL
            )
            ctor_m = ctor_re.search(content)
            if ctor_m:
                params_text = ctor_m.group(1)
                for param_m in re.finditer(
                    r'(?:@\w+(?:\([^)]*\))?\s+)*(?:final\s+)?(\w+(?:<[^>]+>)?)\s+(\w+)',
                    params_text
                ):
                    p_type = param_m.group(1)
                    p_name = param_m.group(2)
                    # Filter out primitives and common non-bean types
                    if p_type not in ('String', 'int', 'long', 'boolean', 'double', 'float',
                                     'Integer', 'Long', 'Boolean', 'Double', 'Float',
                                     'List', 'Map', 'Set', 'Optional'):
                        dependencies.append({
                            "type": p_type,
                            "name": p_name,
                            "injection": "constructor",
                            "line": content[:ctor_m.start()].count('\n') + 1,
                        })

            # Setter injection: @Autowired public void setXxx(Type name)
            setter_re = re.compile(
                r'@Autowired\s*\n\s*(?:public\s+)?void\s+(set\w+)\s*\(\s*(\w+(?:<[^>]+>)?)\s+(\w+)\s*\)',
                re.MULTILINE
            )
            for sm in setter_re.finditer(content):
                dependencies.append({
                    "type": sm.group(2),
                    "name": sm.group(3),
                    "injection": "setter",
                    "setter_method": sm.group(1),
                    "line": content[:sm.start()].count('\n') + 1,
                })

            if dependencies:
                injection_type = "constructor" if any(d["injection"] == "constructor" for d in dependencies) else "field"
                if any(d["injection"] == "field" for d in dependencies) and any(d["injection"] == "constructor" for d in dependencies):
                    injection_type = "mixed"

            di_graph[cls_name] = {
                "file": rel_path,
                "package": class_info["package"],
                "injection_type": injection_type,
                "dependencies": dependencies,
                "dependents": [],  # Populated below
            }

        # Build reverse mapping (dependents)
        for cls_name, cls_data in di_graph.items():
            for dep in cls_data["dependencies"]:
                dep_type = dep["type"]
                if dep_type in di_graph:
                    di_graph[dep_type]["dependents"].append({
                        "class": cls_name,
                        "via": dep["injection"],
                    })

        # Filter if class_name specified
        if class_name:
            if class_name in di_graph:
                return {
                    "status": "success",
                    "class": class_name,
                    "injection_info": di_graph[class_name],
                }
            else:
                return {"status": "error", "message": f"Class '{class_name}' not found"}

        # Detect circular dependencies
        circular = []
        for cls_name in di_graph:
            visited = set()
            stack = [cls_name]
            while stack:
                current = stack.pop()
                if current in visited:
                    if current == cls_name and len(visited) > 1:
                        circular.append({
                            "class": cls_name,
                            "cycle_length": len(visited),
                        })
                    continue
                visited.add(current)
                if current in di_graph:
                    for dep in di_graph[current]["dependencies"]:
                        if dep["type"] in di_graph:
                            stack.append(dep["type"])

        return {
            "status": "success",
            "di_graph": di_graph,
            "total_classes": len(di_graph),
            "circular_dependencies": circular,
        }

    # ══════════════════════════════════════════════════════════════════
    # 14. pre_edit_check
    # ══════════════════════════════════════════════════════════════════

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Context-aware pre-edit impact analysis.

        For entity: checks repositories, services, DTOs referencing it.
        For controller: checks security config, validation, route mappings.
        For service: checks consumers (controllers, other services, tests).

        Args:
            file_path: Relative path to the file being edited
            symbol_name: Symbol to check (class name, method name, field name)
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": f"Cannot read file: {e}"}

        class_info = self._extract_class_info(content)
        cls_name = class_info["class_name"]

        result = {
            "file": file_path,
            "symbol": symbol_name,
            "class": cls_name,
            "file_type": self._classify_spring_file(content, file_path),
            "references": [],
            "impact": [],
            "warnings": [],
        }

        # Find all references to the symbol across the project
        for fpath, rel_path, src_content in self._walk_source_files(base):
            if rel_path == file_path:
                continue
            if symbol_name not in src_content:
                continue

            src_class = self._extract_class_info(src_content)
            ref_lines = []
            lines = src_content.split('\n')
            for i, line in enumerate(lines, 1):
                if symbol_name in line:
                    ref_lines.append({
                        "line": i,
                        "text": line.strip()[:120],
                    })

            if ref_lines:
                result["references"].append({
                    "file": rel_path,
                    "class": src_class["class_name"],
                    "usages": ref_lines[:10],
                    "count": len(ref_lines),
                })

        # Context-specific checks based on file type
        file_type = result["file_type"]

        if file_type == "entity":
            # Check repositories
            repos = self._find_files_with_annotation(base, "Repository")
            for fpath, rel_path, repo_content in repos:
                if cls_name in repo_content or symbol_name in repo_content:
                    repo_class = self._extract_class_info(repo_content)
                    # Check for custom queries referencing the symbol
                    queries = re.findall(
                        rf'@Query\s*\(\s*["\']([^"\']*{re.escape(symbol_name)}[^"\']*)["\']',
                        repo_content
                    )
                    result["impact"].append({
                        "type": "repository",
                        "class": repo_class["class_name"],
                        "file": rel_path,
                        "custom_queries": queries[:5],
                    })

            # Check if field is in DTO mappings
            for fpath, rel_path, dto_content in self._walk_source_files(base):
                dto_class = self._extract_class_info(dto_content)
                if dto_class["class_name"].endswith(('Dto', 'DTO', 'Request', 'Response')):
                    if symbol_name in dto_content:
                        result["impact"].append({
                            "type": "dto",
                            "class": dto_class["class_name"],
                            "file": rel_path,
                        })

            # Check Flyway migrations
            flyway_dirs = [
                os.path.join(base, self.RESOURCES, "db", "migration"),
                os.path.join(base, self.RESOURCES, "db", "migrations"),
            ]
            for fd in flyway_dirs:
                if not os.path.isdir(fd):
                    continue
                for fname in os.listdir(fd):
                    if fname.endswith('.sql'):
                        mpath = os.path.join(fd, fname)
                        try:
                            with open(mpath, 'r', encoding='utf-8', errors='replace') as f:
                                sql = f.read()
                            if symbol_name in sql:
                                result["impact"].append({
                                    "type": "migration",
                                    "file": os.path.relpath(mpath, base),
                                })
                        except Exception:
                            pass

            result["warnings"].append(
                "Changing an entity field may require: migration, DTO update, repository query update"
            )

        elif file_type == "controller":
            # Check security configuration
            filter_result = self.get_filter_chain()
            if filter_result.get("status") == "success":
                for ms in filter_result.get("security", {}).get("method_security", []):
                    if ms["class"] == cls_name and (ms["method"] == symbol_name or symbol_name in str(ms)):
                        result["impact"].append({
                            "type": "security",
                            "detail": ms,
                        })

            # Check test files
            test_roots = self._find_test_roots(base)
            for fpath, rel_path, test_content in self._walk_source_files(base, test_roots):
                if cls_name in test_content or symbol_name in test_content:
                    result["impact"].append({
                        "type": "test",
                        "file": rel_path,
                        "class": self._extract_class_info(test_content)["class_name"],
                    })

            result["warnings"].append(
                "Changing a controller method may break: API contract, security rules, tests"
            )

        elif file_type == "service":
            # Find consuming controllers and other services
            for fpath, rel_path, consumer_content in self._walk_source_files(base):
                if rel_path == file_path:
                    continue
                consumer_class = self._extract_class_info(consumer_content)
                # Check if this file injects the service
                if cls_name in consumer_content:
                    # Check if it calls the specific method
                    calls = re.findall(
                        rf'\w+\.{re.escape(symbol_name)}\s*\(',
                        consumer_content
                    )
                    if calls:
                        result["impact"].append({
                            "type": "consumer",
                            "class": consumer_class["class_name"],
                            "file": rel_path,
                            "calls_count": len(calls),
                        })

            # Check if method is @Transactional
            methods = self._extract_methods(content)
            for m in methods:
                if m["name"] == symbol_name:
                    if self._has_annotation(m.get("annotations", []), "Transactional"):
                        result["warnings"].append(
                            f"Method '{symbol_name}' is @Transactional - changes may affect transaction boundaries"
                        )
                    break

            # Check tests
            test_roots = self._find_test_roots(base)
            for fpath, rel_path, test_content in self._walk_source_files(base, test_roots):
                if symbol_name in test_content:
                    result["impact"].append({
                        "type": "test",
                        "file": rel_path,
                    })

            result["warnings"].append(
                "Changing a service method may break: consuming controllers, other services, tests"
            )

        return {
            "status": "success",
            "check": result,
            "total_references": sum(r["count"] for r in result["references"]),
            "total_impact": len(result["impact"]),
        }

    def _classify_spring_file(self, content: str, file_path: str) -> str:
        """Classify a Spring source file by its role."""
        if '@Entity' in content:
            return "entity"
        if '@RestController' in content or '@Controller' in content:
            return "controller"
        if '@Service' in content:
            return "service"
        if '@Repository' in content or 'Repository' in file_path:
            return "repository"
        if '@Configuration' in content:
            return "configuration"
        if '@Component' in content:
            return "component"
        if file_path.endswith(('Dto.java', 'DTO.java', 'Dto.kt', 'DTO.kt',
                               'Request.java', 'Response.java', 'Request.kt', 'Response.kt')):
            return "dto"
        if 'Test' in file_path or '@Test' in content:
            return "test"
        return "other"
