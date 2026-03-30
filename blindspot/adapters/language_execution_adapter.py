"""Language execution adapter for syntax/static/format/test command orchestration.

This adapter provides a single interface across languages for command selection
and diff-aware quality matrix planning.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import EXTENSION_LANGUAGE_MAP, get_config


@dataclass(frozen=True)
class LanguageExecutionProfile:
    """Execution profile for one language."""

    language: str
    parser: str
    syntax_command: str
    static_command: str
    format_command: str
    test_command: str


DEFAULT_EXECUTION_PROFILES: Dict[str, LanguageExecutionProfile] = {
    "python": LanguageExecutionProfile(
        language="python",
        parser="tree_sitter",
        syntax_command="python3 -m py_compile {files}",
        static_command="python3 -m py_compile {files}",
        format_command="python3 -m py_compile {files}",
        test_command="python3 -m unittest -v",
    ),
    "php": LanguageExecutionProfile(
        language="php",
        parser="tree_sitter",
        syntax_command="php -l {file}",
        static_command=(
            "sh -lc \"if [ -f artisan ]; then "
            "php artisan view:cache >/dev/null && php artisan route:list >/dev/null; "
            "else php -l {file} >/dev/null; fi\""
        ),
        format_command="php -l {file}",
        test_command="vendor/bin/phpunit --colors=never",
    ),
    "javascript": LanguageExecutionProfile(
        language="javascript",
        parser="tree_sitter",
        syntax_command="node --check {file}",
        static_command="node --check {file}",
        format_command="node --check {file}",
        test_command="npm test -- --runInBand",
    ),
    "typescript": LanguageExecutionProfile(
        language="typescript",
        parser="tree_sitter",
        syntax_command="npx tsc --noEmit --pretty false",
        static_command="npx tsc --noEmit --pretty false",
        format_command="npx tsc --noEmit --pretty false",
        test_command="npm test -- --runInBand",
    ),
    "go": LanguageExecutionProfile(
        language="go",
        parser="tree_sitter",
        syntax_command="go test ./...",
        static_command="go vet ./...",
        format_command="gofmt -l {files}",
        test_command="go test ./...",
    ),
    "rust": LanguageExecutionProfile(
        language="rust",
        parser="tree_sitter",
        syntax_command="cargo check --quiet",
        static_command="cargo clippy --all-targets -- -D warnings",
        format_command="cargo fmt -- --check",
        test_command="cargo test --all-targets --quiet",
    ),
    "java": LanguageExecutionProfile(
        language="java",
        parser="tree_sitter",
        syntax_command="./gradlew --quiet classes",
        static_command="./gradlew --quiet check",
        format_command="./gradlew --quiet check",
        test_command="./gradlew --quiet test",
    ),
    "ruby": LanguageExecutionProfile(
        language="ruby",
        parser="regex_fallback",
        syntax_command="ruby -c {file}",
        static_command="ruby -c {file}",
        format_command="ruby -c {file}",
        test_command="bundle exec rspec",
    ),
}

DEFAULT_REQUIRED_CHECKS: Dict[str, Dict[str, bool]] = {
    "python": {"syntax": True, "static": True, "format": False, "tests": True},
    "php": {"syntax": True, "static": True, "format": False, "tests": False},
    "javascript": {"syntax": True, "static": True, "format": False, "tests": True},
    "typescript": {"syntax": True, "static": True, "format": False, "tests": True},
    "go": {"syntax": True, "static": True, "format": False, "tests": True},
    "rust": {"syntax": True, "static": True, "format": False, "tests": True},
    "java": {"syntax": True, "static": True, "format": False, "tests": True},
    "ruby": {"syntax": True, "static": True, "format": False, "tests": True},
}


class LanguageExecutionAdapter:
    """Resolve language quality commands from diff and project configuration."""

    def __init__(self, project_path: str):
        self.project_path = project_path

    def _raw_config(self) -> Dict[str, Any]:
        cfg = get_config(self.project_path)
        raw = cfg.raw if cfg and isinstance(cfg.raw, dict) else {}
        return raw if isinstance(raw, dict) else {}

    def _adapter_config(self) -> Dict[str, Any]:
        raw = self._raw_config()
        section = raw.get("language_adapters", {}) if isinstance(raw.get("language_adapters", {}), dict) else {}
        return {
            "hard_block_missing_tools": bool(section.get("hard_block_missing_tools", True)),
            "default_matrix_always": bool(section.get("default_matrix_always", True)),
            "require_format_checks": bool(section.get("require_format_checks", False)),
            "languages": section.get("languages", {}) if isinstance(section.get("languages", {}), dict) else {},
        }

    def detect_language(self, file_path: str) -> Optional[str]:
        base = os.path.basename(file_path)
        if base.endswith(".blade.php"):
            return "php"
        ext = os.path.splitext(file_path)[1].lower()
        return EXTENSION_LANGUAGE_MAP.get(ext)

    def _profile_for_language(self, language: str) -> LanguageExecutionProfile:
        profile = DEFAULT_EXECUTION_PROFILES.get(language)
        if not profile:
            return LanguageExecutionProfile(
                language=language,
                parser="regex_fallback",
                syntax_command="",
                static_command="",
                format_command="",
                test_command="",
            )

        overrides = self._adapter_config().get("languages", {}).get(language, {})
        if not isinstance(overrides, dict):
            return profile

        return LanguageExecutionProfile(
            language=language,
            parser=str(overrides.get("parser", profile.parser)),
            syntax_command=str(overrides.get("syntax_command", profile.syntax_command)),
            static_command=str(overrides.get("static_command", profile.static_command)),
            format_command=str(overrides.get("format_command", profile.format_command)),
            test_command=str(overrides.get("test_command", profile.test_command)),
        )

    def _required_checks_for_language(self, language: str) -> Dict[str, bool]:
        base = dict(
            DEFAULT_REQUIRED_CHECKS.get(
                language,
                {"syntax": True, "static": True, "format": False, "tests": True},
            )
        )
        overrides = self._adapter_config().get("languages", {}).get(language, {})
        if not isinstance(overrides, dict):
            return base
        required_cfg = overrides.get("required_checks", {})
        if not isinstance(required_cfg, dict):
            return base
        for key in ("syntax", "static", "format", "tests"):
            if key in required_cfg:
                base[key] = bool(required_cfg[key])
        return base

    @staticmethod
    def _render_command(template: str, files: List[str], language: str) -> str:
        normalized_files = [str(p).replace("\\", "/") for p in files]
        first = normalized_files[0] if normalized_files else ""
        quoted_files = " ".join(shlex.quote(p) for p in normalized_files)
        paths = sorted({shlex.quote(os.path.dirname(p) or ".") for p in normalized_files})
        quoted_paths = " ".join(paths)

        cmd = (template or "").strip()
        if not cmd:
            return ""

        cmd = cmd.replace("{file}", shlex.quote(first))
        cmd = cmd.replace("{files}", quoted_files)
        cmd = cmd.replace("{paths}", quoted_paths)
        cmd = cmd.replace("{language}", shlex.quote(language))
        return cmd

    def build_quality_matrix(self, target_files: List[str]) -> Dict[str, Any]:
        cfg = self._adapter_config()
        file_list = [str(f).replace("\\", "/") for f in (target_files or []) if f]

        grouped: Dict[str, List[str]] = {}
        for path in file_list:
            language = self.detect_language(path)
            if not language:
                continue
            grouped.setdefault(language, []).append(path)

        if not grouped and not cfg["default_matrix_always"]:
            return {
                "status": "success",
                "matrix_status": "skipped",
                "message": "No supported language files detected",
                "languages": [],
                "checks": [],
                "config": cfg,
            }

        checks: List[Dict[str, Any]] = []
        for language, files in sorted(grouped.items()):
            profile = self._profile_for_language(language)
            required = self._required_checks_for_language(language)
            # Global format policy can force format checks to required.
            if bool(cfg["require_format_checks"]):
                required["format"] = True
            entries = [
                ("syntax", profile.syntax_command, bool(required.get("syntax", True))),
                ("static", profile.static_command, bool(required.get("static", True))),
                ("format", profile.format_command, bool(required.get("format", False))),
                ("tests", profile.test_command, bool(required.get("tests", True))),
            ]
            for check_type, template, required in entries:
                command = self._render_command(template, files, language)
                checks.append(
                    {
                        "check_id": f"{language}:{check_type}",
                        "language": language,
                        "parser": profile.parser,
                        "check_type": check_type,
                        "required": bool(required),
                        "files": files,
                        "command": command,
                    }
                )

        return {
            "status": "success",
            "matrix_status": "planned",
            "languages": sorted(grouped.keys()),
            "checks": checks,
            "config": cfg,
        }
