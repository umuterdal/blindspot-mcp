"""
Laravel Validation Service - Higher-level Laravel analysis tools.

Provides cross-cutting analysis tools that combine multiple intelligence
service methods to deliver actionable insights: validation chain tracing,
middleware stack resolution, and pre-edit impact checking.
"""

import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Set

from .base_service import BaseService
from .laravel_intelligence_service import LaravelIntelligenceService

logger = logging.getLogger(__name__)


class LaravelValidationService(BaseService):
    """Higher-level Laravel analysis combining multiple intelligence methods."""

    # Fields to ignore when cross-referencing form fields vs validation rules
    IGNORED_FORM_FIELDS = {'_token', '_method', 'cf-turnstile-response', 'website'}

    def _get_project_path(self) -> Optional[str]:
        """Get the project base path from MCP context."""
        try:
            base = self.base_path
            if base and os.path.isdir(base):
                return base
        except Exception as e:
            logger.debug("Failed to resolve project path: %s", e)
        return None

    def _get_intel(self) -> LaravelIntelligenceService:
        """Create a LaravelIntelligenceService instance sharing the same context."""
        return LaravelIntelligenceService(self.ctx)

    # ── get_cache_map ────────────────────────────────────────────────

    def get_cache_map(self, model_name: str = None) -> Dict[str, Any]:
        """
        Scan Laravel Model booted() methods for cache invalidation patterns.

        Builds forward_map (model -> cache keys it invalidates) and
        reverse_map (cache key -> which models invalidate it).

        Args:
            model_name: Optional specific model name (e.g., "Category").
                        If None, scans ALL models in app/Models/.
        """
        from pathlib import Path

        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        models_dir = Path(base) / "app" / "Models"
        if not models_dir.is_dir():
            return {"status": "error", "message": "app/Models directory not found"}

        # Collect model files to scan
        if model_name:
            target = models_dir / f"{model_name}.php"
            if not target.is_file():
                return {
                    "status": "error",
                    "message": f"Model file not found: {model_name}.php",
                }
            model_files = [target]
        else:
            model_files = sorted(models_dir.glob("*.php"))

        forward_map: Dict[str, Any] = {}
        reverse_map: Dict[str, List[str]] = {}

        for fpath in model_files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("Failed to read model %s: %s", fpath, e)
                continue

            name = fpath.stem  # e.g. "Category"

            # Find booted() method body using brace-counting
            booted_match = re.search(
                r'protected\s+static\s+function\s+booted\s*\(\s*\)\s*:\s*void\s*\{',
                content,
            )
            if not booted_match:
                continue

            # Extract the full booted() body via brace-counting
            start_pos = booted_match.end() - 1  # position of opening {
            booted_body = self._extract_brace_block(content, start_pos)
            if not booted_body:
                continue

            # Detect event types: static::saved, static::deleted, static::created, etc.
            # Map regions of the body to their event type
            event_regions = self._map_event_regions(booted_body)

            # Extract Cache::forget(...) and Cache::flush(...) calls
            cache_keys: List[Dict[str, Any]] = []
            for m in re.finditer(
                r"Cache::(?:forget|flush)\(\s*(['\"])(.*?)\1\s*\)",
                booted_body,
            ):
                quote = m.group(1)
                raw_key = m.group(2)
                key_pos = m.start()

                # Determine enclosing event
                event_type = self._find_enclosing_event(key_pos, event_regions)

                # Determine if key is static or dynamic
                if quote == '"' and ('{$' in raw_key or '$' in raw_key):
                    # Double-quoted with interpolation
                    key_type = "dynamic"
                    key_value = raw_key  # keep template as-is
                else:
                    key_type = "static"
                    key_value = raw_key

                cache_keys.append({
                    "key": key_value,
                    "type": key_type,
                    "event": event_type,
                })

            # Also handle concatenation patterns: Cache::forget('key.' . $var)
            for m in re.finditer(
                r"Cache::(?:forget|flush)\(\s*'([^']+)'\s*\.\s*(\$[\w>-]+)",
                booted_body,
            ):
                prefix = m.group(1)
                var_part = m.group(2)
                key_pos = m.start()
                event_type = self._find_enclosing_event(key_pos, event_regions)
                cache_keys.append({
                    "key": f"{prefix}{{{var_part}}}",
                    "type": "dynamic",
                    "event": event_type,
                })

            if not cache_keys:
                continue

            # Deduplicate events list
            events = sorted({ck["event"] for ck in cache_keys if ck["event"]})

            forward_map[name] = {
                "file": str(fpath.relative_to(base)),
                "events": events,
                "invalidates": cache_keys,
            }

            # Build reverse map
            for ck in cache_keys:
                key = ck["key"]
                if key not in reverse_map:
                    reverse_map[key] = []
                if name not in reverse_map[key]:
                    reverse_map[key].append(name)

        # Format reverse_map as dicts
        reverse_map_formatted: Dict[str, Dict[str, Any]] = {}
        for key, models in sorted(reverse_map.items()):
            reverse_map_formatted[key] = {"invalidated_by": models}

        return {
            "status": "success",
            "forward_map": forward_map,
            "reverse_map": reverse_map_formatted,
            "stats": {
                "total_models_with_cache": len(forward_map),
                "total_unique_cache_keys": len(reverse_map),
            },
        }

    @staticmethod
    def _extract_brace_block(content: str, start: int) -> Optional[str]:
        """Extract a brace-delimited block starting at the '{' at position start."""
        if start >= len(content) or content[start] != '{':
            return None
        depth = 0
        pos = start
        while pos < len(content):
            ch = content[pos]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[start:pos + 1]
            pos += 1
        return None

    @staticmethod
    def _map_event_regions(booted_body: str) -> List[Dict[str, Any]]:
        """
        Find static::event(...) blocks in the booted body and map their
        character ranges to event names.
        """
        regions: List[Dict[str, Any]] = []
        for m in re.finditer(
            r'static::(\w+)\s*\(',
            booted_body,
        ):
            event_name = m.group(1)
            # Find the opening paren and then the callback body
            paren_start = m.end() - 1
            # Walk to find matching closing paren
            depth = 0
            pos = paren_start
            while pos < len(booted_body):
                ch = booted_body[pos]
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        regions.append({
                            "event": event_name,
                            "start": paren_start,
                            "end": pos,
                        })
                        break
                pos += 1
        return regions

    @staticmethod
    def _find_enclosing_event(
        pos: int, regions: List[Dict[str, Any]]
    ) -> str:
        """Find which event region contains the given position."""
        for r in regions:
            if r["start"] <= pos <= r["end"]:
                return r["event"]
        return "unknown"

    # ── get_view_data_flow ─────────────────────────────────────────────

    def get_view_data_flow(self, view_path: str) -> Dict[str, Any]:
        """
        Map controller -> Blade variable data flow and flag mismatches.

        Finds which controllers render a view, what variables they pass,
        and which variables the Blade template actually uses.

        Args:
            view_path: Relative path to the Blade view
                       (e.g., "resources/views/public/blog/index.blade.php")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, view_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"View not found: {view_path}"}

        intel = self._get_intel()

        # 1. Get blade dependencies to find rendered_by info
        blade_deps = intel.get_blade_dependencies(view_path)
        rendered_by = blade_deps.get("rendered_by", []) if blade_deps.get("status") == "success" else []

        # 2. For each controller/method, extract passed variables
        controller_data: List[Dict[str, Any]] = []

        for rb in rendered_by:
            ctrl_file = rb.get("controller", "")
            method_name = rb.get("method")
            if not ctrl_file or not method_name:
                continue

            ctrl_full_path = os.path.join(base, ctrl_file)
            if not os.path.isfile(ctrl_full_path):
                continue

            try:
                with open(ctrl_full_path, 'r', encoding='utf-8', errors='replace') as f:
                    ctrl_content = f.read()
            except Exception as e:
                logger.debug("Failed to read controller %s: %s", ctrl_full_path, e)
                continue

            # Extract the specific method body
            method_body = self._extract_method_body(ctrl_content, method_name)
            if not method_body:
                continue

            passed_vars: Set[str] = set()

            # compact('var1', 'var2', ...)
            for m in re.finditer(r"compact\(([^)]+)\)", method_body):
                args = m.group(1)
                for vm in re.finditer(r"['\"](\w+)['\"]", args):
                    passed_vars.add(vm.group(1))

            # view('name', ['key' => ..., ...])
            for m in re.finditer(
                r"view\(\s*['\"][^'\"]+['\"]\s*,\s*\[([^\]]*)\]",
                method_body,
                re.DOTALL,
            ):
                array_body = m.group(1)
                for km in re.finditer(r"['\"](\w+)['\"]\s*=>", array_body):
                    passed_vars.add(km.group(1))

            # ->with('key', $value) — but not flash-style keys
            flash_keys = {'success', 'error', 'warning', 'info', 'status', 'message'}
            for m in re.finditer(r"->with\(\s*['\"](\w+)['\"]", method_body):
                key = m.group(1)
                if key not in flash_keys:
                    passed_vars.add(key)

            controller_data.append({
                "controller": ctrl_file,
                "method": method_name,
                "passed_variables": sorted(passed_vars),
            })

        # 3. Read the Blade file and extract $variable references
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                blade_content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # All $variable references
        all_blade_vars: Set[str] = set()
        for m in re.finditer(r'\$([a-zA-Z_]\w*)', blade_content):
            all_blade_vars.add(m.group(1))

        # 4. Filter out built-in variables
        builtin_vars = {
            'slot', 'attributes', 'loop', 'errors', '__env', '__data',
            '__path', 'this', 'app', 'component',
        }
        all_blade_vars -= builtin_vars

        # 5. Filter out locally-defined variables
        local_vars: Set[str] = set()

        # @foreach($items as $item) / @foreach($items as $key => $item)
        for m in re.finditer(
            r'@foreach\s*\([^)]+\s+as\s+\$(\w+)\s*(?:=>\s*\$(\w+))?\s*\)',
            blade_content,
        ):
            local_vars.add(m.group(1))
            if m.group(2):
                local_vars.add(m.group(2))

        # @forelse($items as $item)
        for m in re.finditer(
            r'@forelse\s*\([^)]+\s+as\s+\$(\w+)\s*(?:=>\s*\$(\w+))?\s*\)',
            blade_content,
        ):
            local_vars.add(m.group(1))
            if m.group(2):
                local_vars.add(m.group(2))

        # @for($i = ...)
        for m in re.finditer(r'@for\s*\(\s*\$(\w+)\s*=', blade_content):
            local_vars.add(m.group(1))

        # @php $local = ... @endphp
        for m in re.finditer(r'@php\s[^@]*\$(\w+)\s*=', blade_content):
            local_vars.add(m.group(1))

        # @props(['name', 'name2' => default])
        props_match = re.search(
            r"@props\(\[((?:[^\[\]]*|\[[^\]]*\])*)\]\)",
            blade_content,
            re.DOTALL,
        )
        if props_match:
            props_str = props_match.group(1)
            for pm in re.finditer(r"['\"](\w+)['\"]", props_str):
                local_vars.add(pm.group(1))

        all_blade_vars -= local_vars

        # 6. Cross-reference: combine all passed vars from all controllers
        all_passed: Set[str] = set()
        for cd in controller_data:
            all_passed.update(cd["passed_variables"])

        unused_data = sorted(all_passed - all_blade_vars) if all_passed else []
        potentially_undefined = sorted(all_blade_vars - all_passed) if all_passed else []

        return {
            "status": "success",
            "view": view_path,
            "rendered_by": controller_data,
            "blade_variables": sorted(all_blade_vars),
            "local_variables": sorted(local_vars),
            "cross_reference": {
                "unused_data": unused_data,
                "potentially_undefined": potentially_undefined,
            },
        }

    @staticmethod
    def _extract_method_body(content: str, method_name: str) -> Optional[str]:
        """Extract the body of a PHP method from class content."""
        pattern = re.compile(
            rf'function\s+{re.escape(method_name)}\s*\([^)]*\)[^{{]*\{{',
            re.DOTALL,
        )
        m = pattern.search(content)
        if not m:
            return None

        # Find the opening brace position
        brace_pos = m.end() - 1
        depth = 0
        pos = brace_pos
        while pos < len(content):
            ch = content[pos]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return content[brace_pos:pos + 1]
            pos += 1
        return None

    # ── get_validation_chain ──────────────────────────────────────────

    def get_validation_chain(self, controller: str, method: str) -> Dict[str, Any]:
        """
        Trace the full form -> validation pipeline and flag mismatches.

        Given a controller and method, finds:
        1. The route and its FormRequest rules
        2. Blade forms that submit to this route
        3. Mismatches between form fields and validation rules

        Args:
            controller: Controller name (e.g., "ProfileController" or "Provider/ProfileController")
            method: Method name (e.g., "update")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        intel = self._get_intel()

        # 1. Get flow map for this controller+method
        flow_data = intel.get_flow_map(controller, method)
        if flow_data.get("status") == "error":
            return flow_data

        flows = flow_data.get("flows", {})
        if method not in flows:
            return {
                "status": "error",
                "message": f"Method '{method}' not found in controller '{controller}'",
            }

        flow = flows[method]
        route_info = flow.get("route")
        validation_info = flow.get("request_validation")

        result: Dict[str, Any] = {
            "status": "success",
            "controller": flow_data.get("controller"),
            "method": method,
            "route": route_info,
            "form_request": None,
            "blade_forms": [],
            "mismatches": {"form_only": [], "validation_only": []},
        }

        # 2. Extract validated field names from FormRequest
        validated_fields: Set[str] = set()
        required_fields: Set[str] = set()

        if isinstance(validation_info, dict) and validation_info.get("class"):
            result["form_request"] = {
                "class": validation_info["class"],
                "file": validation_info.get("file"),
            }
            rules_body = validation_info.get("rules", "")
            if rules_body:
                # Parse top-level field names from rules array
                for m in re.finditer(r"'([a-zA-Z_][a-zA-Z0-9_.*]*)'\s*=>", rules_body):
                    field = m.group(1)
                    # Only top-level fields (no dot notation children)
                    if '.' not in field and '*' not in field:
                        validated_fields.add(field)
                    elif '.*.' in field:
                        # Array field like "images.*.file" -> "images"
                        validated_fields.add(field.split('.')[0])

                # Identify required fields
                for m in re.finditer(
                    r"'([a-zA-Z_][a-zA-Z0-9_]*)'\s*=>\s*\[?[^]]*?'required'",
                    rules_body
                ):
                    required_fields.add(m.group(1))
                for m in re.finditer(
                    r"'([a-zA-Z_][a-zA-Z0-9_]*)'\s*=>\s*'[^']*required[^']*'",
                    rules_body
                ):
                    required_fields.add(m.group(1))

                result["form_request"]["fields"] = sorted(validated_fields)
                result["form_request"]["required_fields"] = sorted(required_fields)

        # 3. Find Blade forms that submit to this route
        route_name = route_info.get("name", "") if route_info else ""
        route_path = route_info.get("path", "") if route_info else ""

        if route_name or route_path:
            blade_forms = self._find_blade_forms(base, route_name, route_path)
            result["blade_forms"] = blade_forms

            # 4. Cross-reference form fields vs validation rules
            all_form_fields: Set[str] = set()
            for bf in blade_forms:
                all_form_fields.update(bf.get("fields", []))

            # Remove ignored fields
            all_form_fields -= self.IGNORED_FORM_FIELDS

            if validated_fields and all_form_fields:
                form_only = sorted(all_form_fields - validated_fields)
                validation_only = sorted(
                    (required_fields - all_form_fields)
                    if required_fields
                    else (validated_fields - all_form_fields)
                )
                result["mismatches"] = {
                    "form_only": form_only,
                    "validation_only": validation_only,
                }

        return result

    def _find_blade_forms(
        self, base: str, route_name: str, route_path: str
    ) -> List[Dict[str, Any]]:
        """Find Blade templates containing forms that submit to a given route."""
        results = []
        views_dir = os.path.join(base, "resources", "views")
        if not os.path.isdir(views_dir):
            return results

        # Build search patterns
        search_patterns = []
        if route_name:
            # route('name') or route('name', ...) in action attribute
            search_patterns.append(re.compile(
                rf"route\(\s*['\"]" + re.escape(route_name) + r"['\"]"
            ))
        if route_path:
            # Direct URL in action attribute
            search_patterns.append(re.compile(
                r"action\s*=\s*['\"]" + re.escape(route_path) + r"['\"]"
            ))

        if not search_patterns:
            return results

        for root, _, files in os.walk(views_dir):
            for fname in files:
                if not fname.endswith('.blade.php'):
                    continue

                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read %s: %s", fpath, e)
                    continue

                # Check if any search pattern matches
                matched = False
                for pat in search_patterns:
                    if pat.search(content):
                        matched = True
                        break

                if not matched:
                    continue

                rel = os.path.relpath(fpath, base)
                fields = self._extract_form_fields(content)
                results.append({
                    "file": rel,
                    "fields": sorted(fields - self.IGNORED_FORM_FIELDS),
                })

        return results

    @staticmethod
    def _extract_form_fields(content: str) -> Set[str]:
        """Extract form field names from Blade template content."""
        fields: Set[str] = set()

        # <input ... name="field_name">
        for m in re.finditer(r'<input\b[^>]*\bname\s*=\s*["\']([^"\']+)["\']', content):
            name = m.group(1)
            # Handle array notation: images[] -> images
            name = re.sub(r'\[\]$', '', name)
            fields.add(name)

        # <textarea ... name="field_name">
        for m in re.finditer(r'<textarea\b[^>]*\bname\s*=\s*["\']([^"\']+)["\']', content):
            fields.add(m.group(1))

        # <select ... name="field_name">
        for m in re.finditer(r'<select\b[^>]*\bname\s*=\s*["\']([^"\']+)["\']', content):
            fields.add(m.group(1))

        # <x-select-modal name="field_name">
        for m in re.finditer(r'<x-select-modal\b[^>]*\bname\s*=\s*["\']([^"\']+)["\']', content):
            fields.add(m.group(1))

        # x-model="field" Alpine bindings (simple cases)
        for m in re.finditer(r'x-model(?:\.defer|\.lazy)?\s*=\s*["\']([a-zA-Z_]\w*)["\']', content):
            fields.add(m.group(1))

        return fields

    # ── get_middleware_chain ───────────────────────────────────────────

    def get_middleware_chain(self, route_name: str) -> Dict[str, Any]:
        """
        Show full middleware stack for a route and group routes sharing
        throttle counters.

        Args:
            route_name: Route name (e.g., "provider.profile.update") or
                       URL path prefix (e.g., "/panel")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        intel = self._get_intel()

        # 1. Find the target route
        # Try as route name prefix first
        if '.' in route_name:
            prefix = route_name.rsplit('.', 1)[0] + '.'
            route_data = intel.get_route_map(prefix)
        else:
            route_data = intel.get_route_map()

        all_routes = route_data.get("routes", [])
        target_route = None
        for r in all_routes:
            if r.get("name") == route_name or r.get("path") == route_name:
                target_route = r
                break

        if not target_route:
            return {
                "status": "error",
                "message": f"Route not found: {route_name}",
                "available_routes": [
                    r.get("name") for r in all_routes[:20] if r.get("name")
                ],
            }

        # 2. Read bootstrap/app.php for middleware aliases
        middleware_aliases = self._parse_middleware_aliases(base)

        # 3. Read AppServiceProvider for rate limiter definitions
        rate_limiters = self._parse_rate_limiters(base)

        # 4. Build full middleware stack for target route
        raw_middleware = target_route.get("middleware") or []
        middleware_stack = []
        for mw in raw_middleware:
            entry: Dict[str, Any] = {"name": mw}
            # Check if it's a known alias
            if ':' in mw:
                mw_name, mw_params = mw.split(':', 1)
            else:
                mw_name = mw
                mw_params = None

            if mw_name in middleware_aliases:
                entry["class"] = middleware_aliases[mw_name]

            if mw_params:
                entry["parameters"] = mw_params

            # Annotate throttle middleware
            if mw_name == 'throttle':
                if mw_params:
                    # Check if it's a named limiter or anonymous (N,M format)
                    if re.match(r'^\d+', mw_params):
                        parts = mw_params.split(',')
                        entry["type"] = "anonymous"
                        entry["max_attempts"] = int(parts[0])
                        entry["decay_minutes"] = int(parts[1]) if len(parts) > 1 else 1
                    else:
                        limiter_name = mw_params.split(',')[0]
                        entry["type"] = "named"
                        entry["limiter_name"] = limiter_name
                        if limiter_name in rate_limiters:
                            entry["limiter_definition"] = rate_limiters[limiter_name]

            middleware_stack.append(entry)

        # 5. Group ALL routes by their throttle middleware
        throttle_groups: Dict[str, List[Dict[str, str]]] = {}
        # Re-fetch all routes (no filter) for grouping
        all_route_data = intel.get_route_map()
        for r in all_route_data.get("routes", []):
            r_middleware = r.get("middleware") or []
            for mw in r_middleware:
                if mw.startswith('throttle'):
                    bucket = mw
                    if bucket not in throttle_groups:
                        throttle_groups[bucket] = []
                    throttle_groups[bucket].append({
                        "name": r.get("name", ""),
                        "path": r.get("path", ""),
                        "method": r.get("method", ""),
                    })

        # Identify which throttle bucket the target route belongs to
        target_throttle = None
        for mw in raw_middleware:
            if mw.startswith('throttle'):
                target_throttle = mw
                break

        result: Dict[str, Any] = {
            "status": "success",
            "route": {
                "name": target_route.get("name"),
                "path": target_route.get("path"),
                "method": target_route.get("method"),
                "controller": target_route.get("controller"),
                "action": target_route.get("action"),
            },
            "middleware_stack": middleware_stack,
            "middleware_aliases": middleware_aliases,
            "rate_limiters": rate_limiters,
        }

        if target_throttle and target_throttle in throttle_groups:
            result["shared_throttle_bucket"] = {
                "key": target_throttle,
                "routes": throttle_groups[target_throttle][:30],
                "total_routes": len(throttle_groups[target_throttle]),
            }

        # Include all throttle groups summary
        result["throttle_groups_summary"] = {
            k: len(v) for k, v in throttle_groups.items()
        }

        return result

    def _parse_middleware_aliases(self, base: str) -> Dict[str, str]:
        """Parse middleware aliases from bootstrap/app.php."""
        aliases: Dict[str, str] = {}
        app_file = os.path.join(base, "bootstrap", "app.php")
        if not os.path.isfile(app_file):
            return aliases

        try:
            with open(app_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            logger.debug("Failed to read bootstrap app file: %s", e)
            return aliases

        # Find $middleware->alias([...]) block
        alias_match = re.search(
            r'\$middleware\s*->\s*alias\s*\(\s*\[(.*?)\]\s*\)',
            content,
            re.DOTALL,
        )
        if alias_match:
            block = alias_match.group(1)
            for m in re.finditer(
                r"['\"](\w+)['\"]\s*=>\s*([\w\\]+)(?:::class)?", block
            ):
                aliases[m.group(1)] = m.group(2)

        return aliases

    def _parse_rate_limiters(self, base: str) -> Dict[str, str]:
        """Parse RateLimiter::for() definitions from AppServiceProvider."""
        limiters: Dict[str, str] = {}
        provider_file = os.path.join(
            base, "app", "Providers", "AppServiceProvider.php"
        )
        if not os.path.isfile(provider_file):
            return limiters

        try:
            with open(provider_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception as e:
            logger.debug("Failed to read route service provider: %s", e)
            return limiters

        # Find RateLimiter::for('name', function ($request) { ... })
        for m in re.finditer(
            r"RateLimiter::for\(\s*['\"](\w+)['\"]",
            content,
        ):
            limiter_name = m.group(1)
            # Extract a snippet of the callback for context
            start = m.start()
            # Find the closing of this RateLimiter::for() call
            depth = 0
            pos = content.index('(', start)
            snippet_start = pos
            while pos < len(content):
                if content[pos] == '(':
                    depth += 1
                elif content[pos] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1

            snippet = content[snippet_start:pos + 1].strip()
            # Extract key info: Limit::perMinute(N)
            limit_match = re.search(r'Limit::per(?:Minute|Hour|Day)\(\s*(\d+)', snippet)
            if limit_match:
                period_match = re.search(r'Limit::(perMinute|perHour|perDay)', snippet)
                period = period_match.group(1) if period_match else "perMinute"
                limiters[limiter_name] = f"{period}({limit_match.group(1)})"
            else:
                # Fallback: just note that it exists
                limiters[limiter_name] = "custom_limiter"

        return limiters

    # ── pre_edit_check ────────────────────────────────────────────────

    def pre_edit_check(self, file_path: str, symbol_name: str) -> Dict[str, Any]:
        """
        Meta-tool combining ripple effect + contextual deep checks.

        Before editing a symbol, this tells you:
        - What will be affected (ripple effect)
        - Deep context depending on file type (flow, validation, cache, view)
        - Actionable items to address

        Args:
            file_path: Relative file path (e.g., "app/Models/User.php")
            symbol_name: Symbol to check (e.g., "scopeActive", "update", "rules")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        intel = self._get_intel()

        # 1. Determine file type
        file_type = self._classify_file_type(file_path)

        # 2. Always run ripple effect
        ripple = intel.get_ripple_effect(file_path, symbol_name)
        risk_level = ripple.get("summary", {}).get("risk_level", "unknown")

        result: Dict[str, Any] = {
            "status": "success",
            "file": file_path,
            "symbol": symbol_name,
            "file_type": file_type,
            "risk_level": risk_level,
            "ripple_summary": ripple.get("summary", {}),
            "deep_checks": {},
            "action_items": [],
            "files_to_check": [],
        }

        # 3. Conditional deep checks based on file type
        if file_type == "controller":
            controller_name = os.path.basename(file_path).replace('.php', '')
            # Determine namespace prefix from path
            if '/Provider/' in file_path:
                controller_name = f"Provider/{controller_name}"
            elif '/Admin/' in file_path:
                controller_name = f"Admin/{controller_name}"
            elif '/Public/' in file_path:
                controller_name = f"Public/{controller_name}"
            elif '/Auth/' in file_path:
                controller_name = f"Auth/{controller_name}"

            flow = intel.get_flow_map(controller_name, symbol_name)
            if flow.get("status") == "success":
                result["deep_checks"]["flow_map"] = flow.get("flows", {}).get(
                    symbol_name
                )

        elif file_type == "form_request":
            # Find which controller uses this FormRequest
            fr_class = os.path.basename(file_path).replace('.php', '')
            controller_info = self._find_controller_using_request(base, fr_class)
            if controller_info:
                ctrl_name, ctrl_method = controller_info
                validation_chain = self.get_validation_chain(ctrl_name, ctrl_method)
                if validation_chain.get("status") == "success":
                    result["deep_checks"]["validation_chain"] = validation_chain

        elif file_type == "model":
            model_name = os.path.basename(file_path).replace('.php', '')
            # Find cache keys related to this model
            cache_impacts = intel._find_cache_impacts(base, model_name, symbol_name)
            if cache_impacts:
                result["deep_checks"]["cache_keys"] = cache_impacts

        elif file_type == "blade":
            # Get blade dependencies
            blade_deps = intel.get_blade_dependencies(file_path)
            if blade_deps.get("status") == "success":
                result["deep_checks"]["blade_dependencies"] = {
                    "layout": blade_deps.get("layout"),
                    "components": blade_deps.get("components", [])[:10],
                    "routes_referenced": blade_deps.get("routes", [])[:10],
                    "rendered_by": blade_deps.get("rendered_by"),
                }

        # 4. Generate action items
        action_items = []

        # Ripple risk
        total_affected = ripple.get("summary", {}).get("total_files_affected", 0)
        if risk_level in ("medium", "high", "critical"):
            action_items.append(
                f"Check {total_affected} affected file(s) before editing"
            )

        # Validation mismatches
        validation_chain = result["deep_checks"].get("validation_chain")
        if validation_chain:
            mismatches = validation_chain.get("mismatches", {})
            form_only = mismatches.get("form_only", [])
            val_only = mismatches.get("validation_only", [])
            if form_only:
                action_items.append(
                    f"Form fields without validation: {', '.join(form_only)}"
                )
            if val_only:
                action_items.append(
                    f"Validation rules without form fields: {', '.join(val_only)}"
                )

        # Cache impacts
        cache_keys = result["deep_checks"].get("cache_keys", [])
        if cache_keys:
            action_items.append(
                f"Clear {len(cache_keys)} cache key(s) after change"
            )

        # View impacts from ripple
        views_affected = ripple.get("summary", {}).get("views_affected", 0)
        if views_affected > 0:
            action_items.append(
                f"Update {views_affected} Blade template(s) that reference this symbol"
            )

        # Route impacts
        routes_affected = ripple.get("summary", {}).get("routes_affected", 0)
        if routes_affected > 0:
            action_items.append(
                f"Verify {routes_affected} route endpoint(s) still work"
            )

        result["action_items"] = action_items

        # 5. Collect files to check (max 10)
        files_to_check: List[str] = []
        for impact in ripple.get("direct_impacts", []):
            if impact.get("file") and impact["file"] not in files_to_check:
                files_to_check.append(impact["file"])
        for impact in ripple.get("view_impacts", []):
            if impact.get("file") and impact["file"] not in files_to_check:
                files_to_check.append(impact["file"])
        for impact in ripple.get("indirect_impacts", []):
            if impact.get("file") and impact["file"] not in files_to_check:
                files_to_check.append(impact["file"])

        result["files_to_check"] = files_to_check[:10]

        return result

    @staticmethod
    def _classify_file_type(file_path: str) -> str:
        """Determine the Laravel file type from its path."""
        if 'app/Models/' in file_path:
            return "model"
        if 'app/Http/Controllers/' in file_path:
            return "controller"
        if 'app/Http/Requests/' in file_path:
            return "form_request"
        if 'app/Services/' in file_path:
            return "service"
        if 'resources/views/' in file_path:
            return "blade"
        if 'app/Http/Middleware/' in file_path:
            return "middleware"
        if 'database/migrations/' in file_path:
            return "migration"
        if 'app/Events/' in file_path:
            return "event"
        if 'routes/' in file_path:
            return "route"
        return "other"

    def _find_controller_using_request(
        self, base: str, form_request_class: str
    ) -> Optional[tuple]:
        """Find which controller method uses a given FormRequest class.

        Returns (controller_name, method_name) or None.
        """
        controllers_dir = os.path.join(base, "app", "Http", "Controllers")
        if not os.path.isdir(controllers_dir):
            return None

        for root, _, files in os.walk(controllers_dir):
            for fname in files:
                if not fname.endswith('.php'):
                    continue

                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read controller %s: %s", fpath, e)
                    continue

                if form_request_class not in content:
                    continue

                # Find the method that type-hints this FormRequest
                pattern = re.compile(
                    rf'function\s+(\w+)\s*\([^)]*{re.escape(form_request_class)}\s+\$'
                )
                m = pattern.search(content)
                if m:
                    method_name = m.group(1)
                    ctrl_name = fname.replace('.php', '')

                    # Add namespace prefix
                    rel = os.path.relpath(root, controllers_dir)
                    if rel != '.':
                        ctrl_name = f"{rel}/{ctrl_name}"

                    return (ctrl_name, method_name)

        return None

    # ── get_similar_patterns ──────────────────────────────────────────

    def get_similar_patterns(self, description: str) -> Dict[str, Any]:
        """
        Find similar implementation patterns already in the project.
        Prevents reinventing the wheel by discovering existing solutions.

        Args:
            description: Natural language description of what you're looking for
                        (e.g., "modal overlay", "file upload", "form validation")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        # Pattern registry: keywords -> search strategies
        patterns: Dict[str, Dict[str, Any]] = {
            "modal": {
                "search": ["x-show", "fixed inset-0", "x-teleport", "z-["],
                "dirs": ["resources/views/components/"],
                "description": "Modal/overlay components",
            },
            "dropdown": {
                "search": ["x-show", "x-transition", "absolute"],
                "dirs": ["resources/views/components/"],
                "description": "Dropdown menus",
            },
            "upload": {
                "search": ['type="file"', "image-compressor", "storeAs", "Storage::disk"],
                "dirs": ["resources/views/", "app/Http/Controllers/"],
                "description": "File upload handling",
            },
            "pagination": {
                "search": ["->paginate(", "->links()", "withQueryString"],
                "dirs": ["app/Http/Controllers/", "resources/views/"],
                "description": "Pagination patterns",
            },
            "form": {
                "search": ["@csrf", 'method="POST"', "x-data.*submitting", "FormRequest"],
                "dirs": ["resources/views/", "app/Http/Requests/"],
                "description": "Form submission patterns",
            },
            "cache": {
                "search": ["Cache::remember", "Cache::forget", "Cache::put"],
                "dirs": ["app/", "config/"],
                "description": "Cache usage patterns",
            },
            "notification": {
                "search": ["NotificationService", "NewNotification", "dispatch"],
                "dirs": ["app/"],
                "description": "Notification patterns",
            },
            "auth": {
                "search": ["auth()->user()", "Auth::guard", "abort_unless", "authorize"],
                "dirs": ["app/Http/Controllers/", "app/Http/Middleware/"],
                "description": "Authentication/authorization patterns",
            },
            "websocket": {
                "search": ["Echo.", "channel(", "ShouldBroadcast", "broadcastOn"],
                "dirs": ["app/Events/", "resources/views/"],
                "description": "WebSocket/broadcasting patterns",
            },
            "search": {
                "search": ["where(", "orWhere", "whereHas", "search", "filter"],
                "dirs": ["app/Http/Controllers/"],
                "description": "Search/filter patterns",
            },
            "select": {
                "search": ["x-select-modal", "select-modal", "x-model"],
                "dirs": ["resources/views/"],
                "description": "Select/dropdown components",
            },
            "table": {
                "search": ["@foreach", "thead", "tbody", "paginate"],
                "dirs": ["resources/views/"],
                "description": "Data table patterns",
            },
            "toast": {
                "search": ["session('success')", "session('error')", "x-alert", "flash"],
                "dirs": ["resources/views/", "app/Http/Controllers/"],
                "description": "Toast/alert patterns",
            },
            "scroll": {
                "search": ["scroll", "IntersectionObserver", "scrollY", "sticky"],
                "dirs": ["resources/views/"],
                "description": "Scroll behavior patterns",
            },
            "alpine": {
                "search": ["x-data", "Alpine.data", "Alpine.store", "$store"],
                "dirs": ["resources/views/"],
                "description": "Alpine.js component patterns",
            },
        }

        # Tokenize description and match against pattern keys
        tokens = description.lower().split()
        matched_pattern_keys: List[str] = []

        for token in tokens:
            # Direct match
            if token in patterns:
                if token not in matched_pattern_keys:
                    matched_pattern_keys.append(token)
                continue
            # Fuzzy match: token is substring of key or key is substring of token
            for key in patterns:
                if (token in key or key in token) and key not in matched_pattern_keys:
                    matched_pattern_keys.append(key)

        if not matched_pattern_keys:
            return {
                "status": "success",
                "query": description,
                "matched_patterns": [],
                "examples": [],
                "suggestion": f"No matching patterns found for '{description}'. "
                              f"Available patterns: {', '.join(sorted(patterns.keys()))}",
            }

        # Collect search terms and directories from matched patterns
        all_search_terms: List[str] = []
        all_dirs: List[str] = []
        for key in matched_pattern_keys:
            pat = patterns[key]
            all_search_terms.extend(pat["search"])
            all_dirs.extend(pat["dirs"])

        # Deduplicate
        all_search_terms = list(dict.fromkeys(all_search_terms))
        all_dirs = list(dict.fromkeys(all_dirs))

        # Use ripgrep to search for each term in specified directories
        file_match_counts: Dict[str, int] = {}
        file_matched_patterns: Dict[str, List[str]] = {}
        rg_path = "/opt/homebrew/bin/rg"

        for term in all_search_terms:
            for search_dir in all_dirs:
                full_dir = os.path.join(base, search_dir)
                if not os.path.isdir(full_dir):
                    continue

                try:
                    result = subprocess.run(
                        [rg_path, "--files-with-matches", "--no-messages",
                         "-l", term, full_dir],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            # Convert to relative path
                            rel_path = os.path.relpath(line, base)
                            file_match_counts[rel_path] = file_match_counts.get(rel_path, 0) + 1
                            if rel_path not in file_matched_patterns:
                                file_matched_patterns[rel_path] = []
                            if term not in file_matched_patterns[rel_path]:
                                file_matched_patterns[rel_path].append(term)
                except (subprocess.TimeoutExpired, Exception):
                    continue

        # Sort files by match count (most matches first)
        sorted_files = sorted(file_match_counts.items(), key=lambda x: x[1], reverse=True)

        # Limit to top 10 files
        sorted_files = sorted_files[:10]

        # For top 5 files, read snippets around the first match
        examples: List[Dict[str, Any]] = []
        for rel_path, match_count in sorted_files:
            full_path = os.path.join(base, rel_path)
            matched_terms = file_matched_patterns.get(rel_path, [])

            # Determine relevance
            if match_count >= 5:
                relevance = "high"
            elif match_count >= 3:
                relevance = "medium"
            else:
                relevance = "low"

            snippet = ""
            if len(examples) < 5:
                # Get a snippet using ripgrep context for the first matched term
                if matched_terms:
                    try:
                        result = subprocess.run(
                            [rg_path, "-n", "-m", "1", "-A", "4",
                             matched_terms[0], full_path],
                            capture_output=True, text=True, timeout=5,
                        )
                        if result.returncode == 0:
                            lines = result.stdout.strip().split("\n")
                            snippet = "\n".join(lines[:5])
                    except (subprocess.TimeoutExpired, Exception):
                        pass

            examples.append({
                "file": rel_path,
                "relevance": relevance,
                "matches": match_count,
                "key_patterns": matched_terms[:5],
                "snippet": snippet,
            })

        # Generate suggestion based on matched patterns
        pattern_descriptions = [patterns[k]["description"] for k in matched_pattern_keys]
        suggestion = f"Found {len(examples)} file(s) matching: {', '.join(pattern_descriptions)}."
        if examples:
            top_file = examples[0]["file"]
            suggestion += f" Start with '{top_file}' which has the most pattern matches."

        return {
            "status": "success",
            "query": description,
            "matched_patterns": matched_pattern_keys,
            "examples": examples,
            "suggestion": suggestion,
        }

    # ── post_edit_checklist ───────────────────────────────────────────

    def post_edit_checklist(self, file_path: str) -> Dict[str, Any]:
        """
        Return a checklist of steps to perform after editing a specific file.

        Args:
            file_path: Relative path of the edited file
        """
        checklist: List[Dict[str, str]] = []

        # Route changes
        if "routes/" in file_path:
            checklist.append({
                "command": "php artisan route:clear && php artisan route:cache",
                "reason": "Route cache must be rebuilt after route changes",
                "priority": "required",
            })

        # Blade view changes
        if ".blade.php" in file_path:
            checklist.append({
                "command": "php artisan view:clear",
                "reason": "Compiled views must be cleared",
                "priority": "required",
            })

        # Config changes
        if "config/" in file_path:
            checklist.append({
                "command": "php artisan config:clear",
                "reason": "Config cache must be cleared",
                "priority": "required",
            })

        # Migration added
        if "database/migrations/" in file_path:
            checklist.append({
                "command": "php artisan migrate",
                "reason": "New migration must be run",
                "priority": "required",
            })

        # Model booted() or relationship changes
        if "app/Models/" in file_path:
            base = self._get_project_path()
            if base:
                full = os.path.join(base, file_path)
                if os.path.isfile(full):
                    try:
                        content = open(full, encoding="utf-8", errors="replace").read()
                        if "Cache::forget" in content or "Cache::flush" in content:
                            checklist.append({
                                "command": "php artisan cache:clear (or targeted Cache::forget)",
                                "reason": "Model has cache invalidation in booted() — verify cache keys",
                                "priority": "recommended",
                            })
                    except Exception as e:
                        logger.debug("Failed to read %s for cache check: %s", full, e)

        # CSS/Tailwind changes
        if ".blade.php" in file_path or "resources/css/" in file_path or "tailwind.config" in file_path:
            checklist.append({
                "command": "npx tailwindcss -i resources/css/app.css -o public/css/app.css --config tailwind.config.js --minify",
                "reason": "Tailwind CSS must be rebuilt if new utility classes were used",
                "priority": "check",
            })
            checklist.append({
                "command": "git add public/css/app.css",
                "reason": "Don't forget to commit rebuilt CSS",
                "priority": "check",
            })

        # Middleware changes
        if "app/Http/Middleware/" in file_path or "bootstrap/app.php" in file_path:
            checklist.append({
                "command": "php artisan route:clear && php artisan route:cache",
                "reason": "Middleware changes require route cache rebuild",
                "priority": "required",
            })

        # Service Provider changes
        if "app/Providers/" in file_path:
            checklist.append({
                "command": "php artisan config:clear && php artisan cache:clear",
                "reason": "Service provider changes may affect cached config",
                "priority": "required",
            })

        # Controller changes
        if "app/Http/Controllers/" in file_path:
            checklist.append({
                "command": "php artisan route:clear",
                "reason": "Ensure route cache reflects any changes",
                "priority": "recommended",
            })

        # JS/vendor asset changes
        if "public/vendor/" in file_path or "public/js/" in file_path:
            checklist.append({
                "command": "Hard refresh browser (Cmd+Shift+R)",
                "reason": "Browser may cache old JS/CSS files",
                "priority": "recommended",
            })

        # .env changes
        if ".env" in file_path:
            checklist.append({
                "command": "php artisan config:clear",
                "reason": "Env changes require config cache clear",
                "priority": "required",
            })

        # Composer changes
        if "composer.json" in file_path:
            checklist.append({
                "command": "composer install",
                "reason": "Dependencies may have changed",
                "priority": "required",
            })

        # PHP syntax check for PHP files
        if file_path.endswith(".php"):
            checklist.append({
                "command": f"php -l {file_path}",
                "reason": "Verify PHP syntax",
                "priority": "required",
            })

        # Production-critical file check
        production_critical = any(x in file_path for x in [
            "routes/web.php", "bootstrap/app.php", "config/",
            "app/Http/Middleware/", "app/Providers/",
            "Caddyfile", "ecosystem.config",
        ])
        if production_critical:
            checklist.append({
                "command": "Test locally before deploying",
                "reason": "This is a production-critical file",
                "priority": "warning",
            })

        # Determine file type label
        file_type = self._classify_file_type(file_path)

        return {
            "status": "success",
            "file": file_path,
            "file_type": file_type,
            "checklist": checklist,
            "production_critical": production_critical,
        }

    # ── verify_endpoint ───────────────────────────────────────────────

    def verify_endpoint(self, method: str, url: str) -> Dict[str, Any]:
        """
        Verify an endpoint: route registration, controller syntax, middleware,
        validation rules, and potential failure points.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: URL path (e.g., "/panel/sohbet/baslat")
        """
        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        intel = self._get_intel()
        method_upper = method.upper()

        # 1. Search routes for matching method+URL
        route_data = intel.get_route_map()
        all_routes = route_data.get("routes", [])

        target_route = None
        url_normalized = url.rstrip("/") or "/"
        for r in all_routes:
            r_method = (r.get("method") or "").upper()
            r_path = (r.get("path") or "").rstrip("/") or "/"
            if r_method == method_upper and r_path == url_normalized:
                target_route = r
                break

        if not target_route:
            # Try partial match
            for r in all_routes:
                r_method = (r.get("method") or "").upper()
                r_path = (r.get("path") or "")
                if r_method == method_upper and url_normalized in r_path:
                    target_route = r
                    break

        if not target_route:
            return {
                "status": "error",
                "message": f"Route not found: {method_upper} {url}",
                "suggestion": "Check route registration in routes/web.php",
            }

        # 2. Extract controller and method
        controller_ref = target_route.get("controller", "")
        action = target_route.get("action", "")
        route_middleware = target_route.get("middleware") or []

        # Parse controller file path and method name
        ctrl_file = None
        ctrl_method_name = None
        if controller_ref:
            if "Controllers" in controller_ref:
                idx = controller_ref.find("Controllers")
                after = controller_ref[idx + len("Controllers"):].replace("\\", "/").strip("/")
                ctrl_file = f"app/Http/Controllers/{after}.php"
            else:
                ctrl_file = f"app/Http/Controllers/{controller_ref}.php"

        if action:
            ctrl_method_name = action

        # 3. Read the controller file and extract method body
        syntax_check = "skipped"
        failure_points: List[Dict[str, Any]] = []
        required_fields: List[str] = []
        auth_required = False
        response_type = "unknown"

        if ctrl_file:
            full_ctrl_path = os.path.join(base, ctrl_file)
            if os.path.isfile(full_ctrl_path):
                # Run php -l syntax check
                try:
                    result = subprocess.run(
                        ["php", "-l", full_ctrl_path],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        syntax_check = "OK"
                    else:
                        syntax_check = result.stderr.strip() or result.stdout.strip()
                except Exception as e:
                    syntax_check = f"check_failed: {e}"

                # Read controller content
                try:
                    with open(full_ctrl_path, "r", encoding="utf-8", errors="replace") as f:
                        ctrl_content = f.read()
                except Exception as e:
                    logger.debug("Failed to read controller %s: %s", full_ctrl_path, e)
                    ctrl_content = ""

                if ctrl_method_name and ctrl_content:
                    method_body = self._extract_method_body(ctrl_content, ctrl_method_name)
                    if method_body:
                        # Check for failure points
                        body_lines = method_body.split("\n")
                        for li, line in enumerate(body_lines, 1):
                            stripped = line.strip()

                            # findOrFail / firstOrFail
                            for pattern_name, pattern_re in [
                                ("findOrFail", r'(\w+)::.*?findOrFail'),
                                ("firstOrFail", r'(\w+)::.*?firstOrFail'),
                            ]:
                                pm = re.search(pattern_re, stripped)
                                if pm:
                                    failure_points.append({
                                        "type": pattern_name,
                                        "model": pm.group(1),
                                        "line": li,
                                    })

                            # ->findOrFail on query builder
                            if "->findOrFail(" in stripped and not any(
                                fp.get("line") == li for fp in failure_points
                            ):
                                failure_points.append({
                                    "type": "findOrFail",
                                    "model": "query",
                                    "line": li,
                                })

                            # abort_if / abort_unless
                            for abort_fn in ["abort_if", "abort_unless"]:
                                am = re.search(
                                    rf'{abort_fn}\s*\((.+?),\s*(\d+)(?:,\s*[\'"](.+?)[\'"])?\s*\)',
                                    stripped,
                                )
                                if am:
                                    failure_points.append({
                                        "type": abort_fn,
                                        "condition": am.group(1).strip(),
                                        "status": int(am.group(2)),
                                        "message": am.group(3) or "",
                                    })

                            # authorize
                            if re.search(r'(?:\$this->)?authorize\s*\(', stripped):
                                failure_points.append({
                                    "type": "authorize",
                                    "line": li,
                                })

                            # $request->validated()
                            if "$request->validated()" in stripped:
                                failure_points.append({
                                    "type": "form_request_validation",
                                    "line": li,
                                })

                            # auth()->user()
                            if "auth()->user()" in stripped or "auth()->id()" in stripped:
                                auth_required = True

                        # Determine response type
                        if "return redirect(" in method_body or "->redirect(" in method_body:
                            response_type = "redirect"
                        elif "return response()->json(" in method_body or "->json(" in method_body:
                            response_type = "json"
                        elif "return view(" in method_body:
                            response_type = "view"
                        elif "return back(" in method_body:
                            response_type = "redirect"

                    # Check for FormRequest usage
                    sig_match = re.search(
                        rf'function\s+{re.escape(ctrl_method_name)}\s*\(([^)]*)\)',
                        ctrl_content,
                    )
                    if sig_match:
                        params = sig_match.group(1)
                        fr_match = re.search(r'(\w+Request)\s+\$', params)
                        if fr_match:
                            fr_class = fr_match.group(1)
                            req_dir = os.path.join(base, "app", "Http", "Requests")
                            if os.path.isdir(req_dir):
                                for root, _, files in os.walk(req_dir):
                                    for fname in files:
                                        if fname == f"{fr_class}.php":
                                            fr_path = os.path.join(root, fname)
                                            try:
                                                with open(fr_path, "r", encoding="utf-8", errors="replace") as f:
                                                    fr_content = f.read()
                                                for rm in re.finditer(
                                                    r"'([a-zA-Z_]\w*)'\s*=>\s*\[?[^]]*?'required'",
                                                    fr_content,
                                                ):
                                                    required_fields.append(rm.group(1))
                                                for rm in re.finditer(
                                                    r"'([a-zA-Z_]\w*)'\s*=>\s*'[^']*required[^']*'",
                                                    fr_content,
                                                ):
                                                    if rm.group(1) not in required_fields:
                                                        required_fields.append(rm.group(1))
                                            except Exception as e:
                                                logger.debug("Failed to parse form request: %s", e)
                                            break

        # Check middleware for auth requirement
        for mw in route_middleware:
            if mw in ("auth", "auth:web") or mw.startswith("auth"):
                auth_required = True
                break

        return {
            "status": "success",
            "route": {
                "method": method_upper,
                "path": target_route.get("path", ""),
                "name": target_route.get("name", ""),
            },
            "controller": {
                "file": ctrl_file or "",
                "method": ctrl_method_name or "",
            },
            "syntax_check": syntax_check,
            "middleware": route_middleware,
            "required_fields": required_fields,
            "failure_points": failure_points,
            "auth_required": auth_required,
            "response_type": response_type,
        }

    # ── get_project_conventions ────────────────────────────────────────

    def get_project_conventions(self, pattern_type: str) -> Dict[str, Any]:
        """
        Extract real conventions from the project codebase.

        Args:
            pattern_type: One of: "naming", "validation", "cache", "component", "error_handling", "route"
        """
        valid_types = {"naming", "validation", "cache", "component", "error_handling", "route"}
        if pattern_type not in valid_types:
            return {
                "status": "error",
                "message": f"Invalid pattern_type: {pattern_type}. Must be one of: {', '.join(sorted(valid_types))}",
            }

        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        if pattern_type == "naming":
            return self._conventions_naming(base)
        elif pattern_type == "validation":
            return self._conventions_validation(base)
        elif pattern_type == "cache":
            return self._conventions_cache(base)
        elif pattern_type == "component":
            return self._conventions_component(base)
        elif pattern_type == "error_handling":
            return self._conventions_error_handling(base)
        elif pattern_type == "route":
            return self._conventions_route(base)

        return {"status": "error", "message": "Unknown pattern_type"}

    def _conventions_naming(self, base: str) -> Dict[str, Any]:
        """Extract naming conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "naming"}

        # Route name patterns
        web_routes = os.path.join(base, "routes", "web.php")
        route_names: Dict[str, List[str]] = {}
        if os.path.isfile(web_routes):
            try:
                with open(web_routes, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                for m in re.finditer(r"->name\(\s*['\"]([^'\"]+)['\"]\s*\)", content):
                    name = m.group(1)
                    prefix = name.split(".")[0] if "." in name else "_root"
                    if prefix not in route_names:
                        route_names[prefix] = []
                    if len(route_names[prefix]) < 5:
                        route_names[prefix].append(name)
            except Exception as e:
                logger.debug("Failed to parse route names: %s", e)
        result["route_names"] = route_names

        # Controller naming
        controllers: Dict[str, List[str]] = {}
        ctrl_dir = os.path.join(base, "app", "Http", "Controllers")
        if os.path.isdir(ctrl_dir):
            for root, dirs, files in os.walk(ctrl_dir):
                rel = os.path.relpath(root, ctrl_dir)
                folder = rel if rel != "." else "_root"
                for fname in sorted(files):
                    if fname.endswith(".php"):
                        if folder not in controllers:
                            controllers[folder] = []
                        if len(controllers[folder]) < 10:
                            controllers[folder].append(fname.replace(".php", ""))
        result["controllers"] = controllers

        # View path convention
        views: Dict[str, int] = {}
        views_dir = os.path.join(base, "resources", "views")
        if os.path.isdir(views_dir):
            for root, dirs, files in os.walk(views_dir):
                rel = os.path.relpath(root, views_dir)
                folder = rel if rel != "." else "_root"
                blade_count = sum(1 for f in files if f.endswith(".blade.php"))
                if blade_count > 0:
                    views[folder] = blade_count
        result["views"] = dict(sorted(views.items(), key=lambda x: -x[1])[:20])

        return result

    def _conventions_validation(self, base: str) -> Dict[str, Any]:
        """Extract validation conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "validation"}

        req_dir = os.path.join(base, "app", "Http", "Requests")
        form_requests: List[Dict[str, Any]] = []
        rule_counts: Dict[str, int] = {}

        if os.path.isdir(req_dir):
            for root, _, files in os.walk(req_dir):
                for fname in sorted(files):
                    if not fname.endswith(".php"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read form request %s: %s", fpath, e)
                        continue

                    class_name = fname.replace(".php", "")
                    rel_path = os.path.relpath(fpath, base)

                    # Find which controller uses this
                    used_by = None
                    ctrl_dir_path = os.path.join(base, "app", "Http", "Controllers")
                    if os.path.isdir(ctrl_dir_path):
                        for croot, _, cfiles in os.walk(ctrl_dir_path):
                            for cfname in cfiles:
                                if not cfname.endswith(".php"):
                                    continue
                                cpath = os.path.join(croot, cfname)
                                try:
                                    with open(cpath, "r", encoding="utf-8", errors="replace") as cf:
                                        ccontent = cf.read()
                                    if class_name in ccontent:
                                        used_by = os.path.relpath(cpath, base)
                                        break
                                except Exception as e:
                                    logger.debug("Failed to read controller %s: %s", cpath, e)
                                    continue
                            if used_by:
                                break

                    form_requests.append({
                        "class": class_name,
                        "file": rel_path,
                        "used_by": used_by,
                    })

                    # Count rules
                    for rm in re.finditer(
                        r"'(required|nullable|string|integer|max|min|email|boolean|array|image|mimes|exists|unique|in|numeric|url|regex|date|confirmed)",
                        content,
                    ):
                        rule = rm.group(1)
                        rule_counts[rule] = rule_counts.get(rule, 0) + 1

        result["form_requests"] = form_requests[:20]
        result["common_rules"] = dict(sorted(rule_counts.items(), key=lambda x: -x[1])[:15])

        return result

    def _conventions_cache(self, base: str) -> Dict[str, Any]:
        """Extract cache conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "cache"}

        cache_patterns: List[Dict[str, str]] = []
        ttl_values: Dict[str, int] = {}
        prefix_groups: Dict[str, int] = {}

        for root, _, files in os.walk(base):
            rel = os.path.relpath(root, base)
            if any(skip in rel for skip in ["vendor", "node_modules", ".git", "storage"]):
                continue
            for fname in files:
                if not fname.endswith(".php"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read %s for cache analysis: %s", fpath, e)
                    continue

                for m in re.finditer(
                    r"Cache::remember\(\s*['\"]([^'\"]+)['\"]\s*,\s*(\d+)",
                    content,
                ):
                    key = m.group(1)
                    ttl = m.group(2)
                    rel_file = os.path.relpath(fpath, base)

                    if len(cache_patterns) < 20:
                        cache_patterns.append({
                            "key": key,
                            "ttl": ttl,
                            "file": rel_file,
                        })

                    ttl_values[ttl] = ttl_values.get(ttl, 0) + 1

                    prefix = key.split(".")[0] if "." in key else key
                    prefix_groups[prefix] = prefix_groups.get(prefix, 0) + 1

        result["cache_patterns"] = cache_patterns
        result["ttl_distribution"] = dict(sorted(ttl_values.items(), key=lambda x: -x[1]))
        result["prefix_groups"] = dict(sorted(prefix_groups.items(), key=lambda x: -x[1])[:20])

        return result

    def _conventions_component(self, base: str) -> Dict[str, Any]:
        """Extract Blade component conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "component"}

        comp_dir = os.path.join(base, "resources", "views", "components")
        components: List[Dict[str, Any]] = []

        if os.path.isdir(comp_dir):
            for fname in sorted(os.listdir(comp_dir)):
                if not fname.endswith(".blade.php"):
                    continue
                fpath = os.path.join(comp_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    logger.debug("Failed to read component %s: %s", fpath, e)
                    continue

                comp_name = fname.replace(".blade.php", "")

                # Extract @props
                props: List[str] = []
                props_match = re.search(
                    r"@props\(\[((?:[^\[\]]*|\[[^\]]*\])*)\]\)",
                    content,
                    re.DOTALL,
                )
                if props_match:
                    for pm in re.finditer(r"['\"](\w+)['\"]", props_match.group(1)):
                        props.append(pm.group(1))

                # Count usage across views
                usage_count = 0
                views_dir = os.path.join(base, "resources", "views")
                tag_pattern = f"<x-{comp_name}"
                for vroot, _, vfiles in os.walk(views_dir):
                    for vfname in vfiles:
                        if not vfname.endswith(".blade.php"):
                            continue
                        vpath = os.path.join(vroot, vfname)
                        if vpath == fpath:
                            continue
                        try:
                            with open(vpath, "r", encoding="utf-8", errors="replace") as vf:
                                vcontent = vf.read()
                            if tag_pattern in vcontent:
                                usage_count += 1
                        except Exception as e:
                            logger.debug("Failed to read view %s: %s", vpath, e)
                            continue

                components.append({
                    "name": comp_name,
                    "props": props,
                    "usage_count": usage_count,
                })

        components.sort(key=lambda x: -x["usage_count"])
        result["components"] = components[:20]

        return result

    def _conventions_error_handling(self, base: str) -> Dict[str, Any]:
        """Extract error handling conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "error_handling"}

        patterns: List[Dict[str, Any]] = []
        ctrl_dir = os.path.join(base, "app", "Http", "Controllers")

        if os.path.isdir(ctrl_dir):
            for root, _, files in os.walk(ctrl_dir):
                for fname in sorted(files):
                    if not fname.endswith(".php"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                    except Exception as e:
                        logger.debug("Failed to read %s: %s", fpath, e)
                        continue

                    rel_file = os.path.relpath(fpath, base)

                    for m in re.finditer(
                        r'catch\s*\(\s*(\w+(?:\\\w+)*)\s+(\$\w+)\s*\)',
                        content,
                    ):
                        exception_type = m.group(1)

                        after_catch = content[m.end():m.end() + 300]
                        has_report = "report(" in after_catch
                        has_flash = "->with(" in after_catch or "flash(" in after_catch
                        has_redirect = "redirect(" in after_catch or "back()" in after_catch
                        has_log = "Log::" in after_catch or "logger(" in after_catch

                        if len(patterns) < 20:
                            patterns.append({
                                "file": rel_file,
                                "exception": exception_type,
                                "has_report": has_report,
                                "has_flash": has_flash,
                                "has_redirect": has_redirect,
                                "has_log": has_log,
                            })

        result["try_catch_patterns"] = patterns

        total = len(patterns)
        if total > 0:
            result["summary"] = {
                "total_catch_blocks": total,
                "with_report": sum(1 for p in patterns if p["has_report"]),
                "with_flash": sum(1 for p in patterns if p["has_flash"]),
                "with_redirect": sum(1 for p in patterns if p["has_redirect"]),
                "with_log": sum(1 for p in patterns if p["has_log"]),
            }

        return result

    def _conventions_route(self, base: str) -> Dict[str, Any]:
        """Extract route conventions."""
        result: Dict[str, Any] = {"status": "success", "pattern_type": "route"}

        web_routes = os.path.join(base, "routes", "web.php")
        if not os.path.isfile(web_routes):
            return {**result, "groups": [], "message": "routes/web.php not found"}

        try:
            with open(web_routes, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {**result, "message": str(e)}

        # Find middleware groups
        middleware_groups: List[Dict[str, str]] = []
        for m in re.finditer(
            r"->middleware\(\s*\[([^\]]+)\]",
            content,
        ):
            mw_list = m.group(1).strip()
            start = max(0, m.start() - 200)
            context = content[start:m.start()]
            prefix_m = re.search(r"prefix\(\s*['\"]([^'\"]+)['\"]", context)
            prefix = prefix_m.group(1) if prefix_m else "unknown"
            if len(middleware_groups) < 20:
                middleware_groups.append({
                    "prefix": prefix,
                    "middleware": mw_list.replace("'", "").replace('"', "").strip(),
                })

        # Named route conventions per prefix
        intel = self._get_intel()
        route_data = intel.get_route_map()
        prefix_routes: Dict[str, List[str]] = {}
        for r in route_data.get("routes", []):
            name = r.get("name", "")
            if name and "." in name:
                prefix = name.split(".")[0]
                if prefix not in prefix_routes:
                    prefix_routes[prefix] = []
                if len(prefix_routes[prefix]) < 5:
                    prefix_routes[prefix].append(name)

        result["middleware_groups"] = middleware_groups
        result["route_name_prefixes"] = {
            k: {"count": len(v), "examples": v}
            for k, v in sorted(prefix_routes.items())
        }

        return result

    # ── detect_anti_patterns ──────────────────────────────────────────

    def detect_anti_patterns(self, file_path: str) -> Dict[str, Any]:
        """
        Scan a file for anti-patterns using config-based rules.

        Checks built-in generic rules plus any custom rules from .blindspot.yaml.

        Args:
            file_path: Relative path to the file to check
        """
        from ..config import get_config

        base = self._get_project_path()
        if not base:
            return {"status": "error", "message": "Project path not set"}

        full_path = os.path.join(base, file_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": f"File not found: {file_path}"}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        lines = content.split("\n")
        issues: List[Dict[str, Any]] = []
        ext = os.path.splitext(file_path)[1].lstrip(".")

        # Determine file type
        if file_path.endswith(".blade.php"):
            file_type = "blade"
            ext = "blade.php"
        elif file_path.endswith(".php"):
            if "routes/" in file_path:
                file_type = "route"
            elif "migrations/" in file_path:
                file_type = "migration"
            else:
                file_type = "php"
        elif ext in ("js", "jsx", "mjs", "cjs", "ts", "tsx"):
            file_type = "javascript"
        elif ext in ("py", "pyw"):
            file_type = "python"
        elif ext in ("go",):
            file_type = "go"
        elif ext in ("rs",):
            file_type = "rust"
        else:
            file_type = "other"

        in_blade_comment = False
        in_block_comment = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Skip Blade comments
            if file_type == "blade":
                if "{{--" in stripped and "--}}" in stripped:
                    continue
                if "{{--" in stripped:
                    in_blade_comment = True
                    continue
                if "--}}" in stripped:
                    in_blade_comment = False
                    continue
                if in_blade_comment:
                    continue

            # Skip comments (PHP, JS, Go, Rust, etc.)
            if file_type in ("php", "route", "migration", "javascript", "go", "rust"):
                if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                    continue

            # Skip Python comments
            if file_type == "python":
                if stripped.startswith("#"):
                    continue

            # ── Built-in generic anti-patterns ──

            # PHP debug functions
            if file_type in ("php", "route", "migration", "blade"):
                if re.search(r'\bdd\s*\(', stripped) or re.search(r'\bdump\s*\(', stripped):
                    issues.append({
                        "line": i, "severity": "error", "code": "debug-function",
                        "message": "Debug function (dd/dump) in code — remove before commit",
                        "snippet": stripped[:100],
                    })

                if re.search(r'\$guarded\s*=\s*\[\s*\]', stripped):
                    issues.append({
                        "line": i, "severity": "error", "code": "empty-guarded",
                        "message": "Empty $guarded array — use $fillable instead",
                        "snippet": stripped[:100],
                    })

                if "DB::raw(" in stripped and "?" not in stripped:
                    issues.append({
                        "line": i, "severity": "warning", "code": "raw-sql-no-binding",
                        "message": "DB::raw() without parameter binding — SQL injection risk",
                        "snippet": stripped[:100],
                    })

            # JavaScript/TypeScript anti-patterns
            if file_type == "javascript":
                if re.search(r'\bconsole\.log\s*\(', stripped):
                    issues.append({
                        "line": i, "severity": "warning", "code": "console-log",
                        "message": "console.log() found — remove before commit",
                        "snippet": stripped[:100],
                    })
                if stripped.strip() == "debugger" or re.search(r'\bdebugger\b', stripped):
                    issues.append({
                        "line": i, "severity": "error", "code": "debugger",
                        "message": "debugger statement found — remove before commit",
                        "snippet": stripped[:100],
                    })
                # any type usage
                if re.search(r':\s*any\b', stripped) and '// eslint-disable' not in stripped:
                    issues.append({
                        "line": i, "severity": "warning", "code": "any-type",
                        "message": "Explicit 'any' type — use a specific type instead",
                        "snippet": stripped[:100],
                    })
                # @ts-ignore without explanation
                if re.search(r'@ts-ignore(?!\s+\S)', stripped):
                    issues.append({
                        "line": i, "severity": "warning", "code": "ts-ignore",
                        "message": "@ts-ignore without explanation — use @ts-expect-error with a reason",
                        "snippet": stripped[:100],
                    })
                # Non-null assertion operator (!) overuse
                if re.search(r'\w+!\.\w+', stripped) or re.search(r'\w+!;', stripped):
                    issues.append({
                        "line": i, "severity": "info", "code": "non-null-assertion",
                        "message": "Non-null assertion (!) — consider proper null handling",
                        "snippet": stripped[:100],
                    })
                # Empty catch blocks
                if re.search(r'catch\s*\([^)]*\)\s*\{\s*\}', stripped):
                    issues.append({
                        "line": i, "severity": "warning", "code": "empty-catch",
                        "message": "Empty catch block — errors are silently swallowed",
                        "snippet": stripped[:100],
                    })
                # Nested ternary (exclude optional chaining ?. from count)
                clean_for_ternary = re.sub(r'\?\.|(\?\?)', '', stripped)
                real_questions = clean_for_ternary.count('?')
                real_colons = clean_for_ternary.count(':')
                if real_questions >= 2 and real_colons >= 2:
                    if re.search(r'\?[^:?]+\?', clean_for_ternary):
                        issues.append({
                            "line": i, "severity": "warning", "code": "nested-ternary",
                            "message": "Nested ternary operator — extract to if/else or variable for readability",
                            "snippet": stripped[:100],
                        })
                # TODO/FIXME/HACK comments
                if re.search(r'//\s*(TODO|FIXME|HACK|XXX)\b', stripped):
                    issues.append({
                        "line": i, "severity": "info", "code": "todo-comment",
                        "message": "TODO/FIXME comment found — track in issue tracker",
                        "snippet": stripped[:100],
                    })
                # Magic numbers (numeric literals not -1, 0, 1, 2 in logic)
                if re.search(r'===?\s*\d{2,}|!==?\s*\d{2,}|[<>]=?\s*\d{2,}', stripped):
                    issues.append({
                        "line": i, "severity": "info", "code": "magic-number",
                        "message": "Magic number in comparison — extract to a named constant",
                        "snippet": stripped[:100],
                    })

            # Python debug
            if file_type == "python":
                if re.search(r'\bbreakpoint\s*\(', stripped) or re.search(r'import\s+pdb', stripped):
                    issues.append({
                        "line": i, "severity": "error", "code": "python-debugger",
                        "message": "Python debugger found — remove before commit",
                        "snippet": stripped[:100],
                    })
                if re.search(r'\bprint\s*\(', stripped) and "# noqa" not in stripped:
                    issues.append({
                        "line": i, "severity": "info", "code": "print-statement",
                        "message": "print() statement — consider using logging instead",
                        "snippet": stripped[:100],
                    })

            # Blade-specific (generic)
            if file_type == "blade":
                if re.search(r'\bstyle\s*=\s*"', stripped):
                    issues.append({
                        "line": i, "severity": "warning", "code": "inline-style",
                        "message": 'Inline style="" found — consider using CSS classes',
                        "snippet": stripped[:100],
                    })

                if 'href="#"' in stripped:
                    issues.append({
                        "line": i, "severity": "warning", "code": "href-hash",
                        "message": 'href="#" found — use a button or javascript:void(0)',
                        "snippet": stripped[:100],
                    })

                raw_match = re.search(r'\{!!\s*\$', stripped)
                if raw_match and "nl2br(e(" not in stripped:
                    issues.append({
                        "line": i, "severity": "error", "code": "raw-html",
                        "message": "Raw HTML output {!! $... !!} — XSS risk unless trusted HTML",
                        "snippet": stripped[:100],
                    })

                cdn_match = re.search(r'(?:src|href)\s*=\s*["\']https?://(?:cdn\.|cdnjs\.|unpkg\.)', stripped)
                if cdn_match:
                    issues.append({
                        "line": i, "severity": "warning", "code": "external-cdn",
                        "message": "External CDN detected — consider self-hosting for reliability",
                        "snippet": stripped[:100],
                    })

            # Route anti-patterns (PHP)
            if file_type == "route":
                if re.search(r"Route::\w+\(\s*['\"][^'\"]+['\"]\s*,\s*function", stripped):
                    issues.append({
                        "line": i, "severity": "error", "code": "closure-route",
                        "message": "Closure route — use controller reference for route caching",
                        "snippet": stripped[:100],
                    })

        # ── Config-based custom rules ──
        config = get_config(base)
        custom_rules = config.get_anti_patterns_for_file(file_path)
        for rule in custom_rules:
            try:
                pattern = re.compile(rule.pattern)
            except re.error:
                continue
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    if rule.exclude_if_near:
                        context_start = max(0, i - 4)
                        context_end = min(len(lines), i + 3)
                        context_text = "\n".join(lines[context_start:context_end])
                        if rule.exclude_if_near in context_text:
                            continue
                    issues.append({
                        "line": i,
                        "severity": rule.severity,
                        "code": "custom-rule",
                        "message": rule.message,
                        "snippet": line.strip()[:100],
                    })

        errors = sum(1 for iss in issues if iss["severity"] == "error")
        warnings = sum(1 for iss in issues if iss["severity"] == "warning")
        infos = sum(1 for iss in issues if iss["severity"] == "info")

        return {
            "status": "success",
            "file": file_path,
            "file_type": file_type,
            "issues": issues,
            "summary": {
                "errors": errors,
                "warnings": warnings,
                "info": infos,
            },
        }
