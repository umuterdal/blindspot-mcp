"""Language syntax adapter — abstracts language-specific syntax patterns.

Instead of hardcoding PHP's "function", "->", "::", "$", this adapter
provides the correct patterns for any supported language.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern, Set


@dataclass
class LanguageSyntax:
    """Syntax patterns for a specific programming language."""

    name: str

    # File extensions
    extensions: List[str] = field(default_factory=list)

    # Keywords
    function_keyword: str = "function"
    class_keyword: str = "class"
    import_keywords: List[str] = field(default_factory=lambda: ["import", "use", "require"])
    visibility_keywords: List[str] = field(default_factory=lambda: ["public", "protected", "private"])

    # Operators
    instance_access: str = "->"      # PHP: ->, Python/JS/Go/Rust: .
    static_access: str = "::"       # PHP: ::, Python/JS: ., Go: ., Rust: ::
    variable_prefix: str = "$"       # PHP: $, others: ""

    # Comment patterns
    line_comment: str = "//"
    block_comment_start: str = "/*"
    block_comment_end: str = "*/"
    alt_line_comment: Optional[str] = None  # Python: #

    # Class/inheritance syntax
    extends_keyword: str = "extends"
    implements_keyword: str = "implements"
    mixin_keywords: List[str] = field(default_factory=list)  # PHP: use (trait), Python: (mixin), Rust: impl

    # Debug functions to detect
    debug_functions: List[str] = field(default_factory=list)

    # Patterns for symbol detection
    class_pattern: Optional[str] = None
    function_pattern: Optional[str] = None
    method_call_pattern: Optional[str] = None
    static_call_pattern: Optional[str] = None
    import_pattern: Optional[str] = None
    instantiation_pattern: Optional[str] = None

    def is_comment(self, line: str) -> bool:
        """Check if a stripped line is a comment."""
        stripped = line.strip()
        if stripped.startswith(self.line_comment):
            return True
        if stripped.startswith(self.block_comment_start) or stripped.startswith("*"):
            return True
        if self.alt_line_comment and stripped.startswith(self.alt_line_comment):
            return True
        return False

    def find_class_declarations(self, content: str) -> List[Dict]:
        """Find class declarations in source code."""
        if not self.class_pattern:
            return []
        results = []
        for m in re.finditer(self.class_pattern, content, re.MULTILINE):
            info = {"name": m.group("name"), "line": content[:m.start()].count("\n") + 1}
            if "parent" in m.groupdict() and m.group("parent"):
                info["extends"] = m.group("parent")
            if "interfaces" in m.groupdict() and m.group("interfaces"):
                info["implements"] = [i.strip() for i in m.group("interfaces").split(",")]
            results.append(info)
        return results

    def find_function_declarations(self, content: str) -> List[Dict]:
        """Find function/method declarations in source code."""
        if not self.function_pattern:
            return []
        results = []
        for m in re.finditer(self.function_pattern, content, re.MULTILINE):
            info = {"name": m.group("name"), "line": content[:m.start()].count("\n") + 1}
            results.append(info)
        return results

    def classify_usage(self, line: str, symbol: str) -> Optional[str]:
        """Classify how a symbol is used on a line.

        Returns: 'import', 'instantiation', 'static_call', 'method_call',
                 'extends_or_implements', 'type_hint', 'reference', or None
        """
        stripped = line.strip()

        # Skip comments
        if self.is_comment(stripped):
            return None

        # Import
        for kw in self.import_keywords:
            if re.search(rf'\b{kw}\b.*\b{re.escape(symbol)}\b', stripped):
                return "import"

        # Extends/implements
        if re.search(rf'\b{self.extends_keyword}\s+{re.escape(symbol)}\b', stripped):
            return "extends_or_implements"
        if self.implements_keyword and re.search(rf'\b{self.implements_keyword}\s+.*\b{re.escape(symbol)}\b', stripped):
            return "extends_or_implements"

        # Instantiation
        if re.search(rf'\bnew\s+{re.escape(symbol)}\b', stripped):
            return "instantiation"

        # Static call
        if self.static_access:
            esc_access = re.escape(self.static_access)
            if re.search(rf'\b{re.escape(symbol)}{esc_access}', stripped):
                return "static_call"

        # Method/property call
        if self.instance_access:
            esc_access = re.escape(self.instance_access)
            if re.search(rf'{esc_access}{re.escape(symbol)}\b', stripped):
                return "method_call"

        # Type hint
        if re.search(rf'\b{re.escape(symbol)}\s+{re.escape(self.variable_prefix)}\w+', stripped):
            return "type_hint"

        # Generic reference
        if re.search(rf'\b{re.escape(symbol)}\b', stripped):
            return "reference"

        return None


# ── Pre-built language syntaxes ──────────────────────────────────────

_PHP = LanguageSyntax(
    name="php",
    extensions=[".php"],
    function_keyword="function",
    class_keyword="class",
    import_keywords=["use"],
    visibility_keywords=["public", "protected", "private"],
    instance_access="->",
    static_access="::",
    variable_prefix="$",
    line_comment="//",
    alt_line_comment=None,
    mixin_keywords=["use"],  # traits
    debug_functions=["dd", "dump", "var_dump", "print_r"],
    class_pattern=r'(?:abstract\s+|final\s+)?class\s+(?P<name>\w+)(?:\s+extends\s+(?P<parent>\w+))?(?:\s+implements\s+(?P<interfaces>[^{]+))?',
    function_pattern=r'(?:public|protected|private)?\s*(?:static\s+)?function\s+(?P<name>\w+)\s*\(',
    import_pattern=r'use\s+([^;]+);',
    instantiation_pattern=r'new\s+(\w+)',
)

_PYTHON = LanguageSyntax(
    name="python",
    extensions=[".py", ".pyw"],
    function_keyword="def",
    class_keyword="class",
    import_keywords=["import", "from"],
    visibility_keywords=[],
    instance_access=".",
    static_access=".",
    variable_prefix="",
    line_comment="#",
    block_comment_start='"""',
    block_comment_end='"""',
    alt_line_comment="#",
    extends_keyword="",  # Python uses (ParentClass)
    implements_keyword="",
    mixin_keywords=[],
    debug_functions=["breakpoint", "pdb.set_trace", "print"],
    class_pattern=r'class\s+(?P<name>\w+)(?:\((?P<parent>[^)]*)\))?',
    function_pattern=r'(?:async\s+)?def\s+(?P<name>\w+)\s*\(',
    import_pattern=r'(?:from\s+\S+\s+)?import\s+(.+)',
    instantiation_pattern=r'(\w+)\(',
)

_JAVASCRIPT = LanguageSyntax(
    name="javascript",
    extensions=[".js", ".jsx", ".mjs", ".cjs"],
    function_keyword="function",
    class_keyword="class",
    import_keywords=["import", "require"],
    visibility_keywords=[],
    instance_access=".",
    static_access=".",
    variable_prefix="",
    line_comment="//",
    alt_line_comment=None,
    extends_keyword="extends",
    implements_keyword="",
    mixin_keywords=[],
    debug_functions=["console.log", "console.warn", "console.error", "debugger"],
    class_pattern=r'class\s+(?P<name>\w+)(?:\s+extends\s+(?P<parent>\w+))?',
    function_pattern=r'(?:async\s+)?(?:function\s+(?P<name>\w+)|(?P<name2>\w+)\s*(?:=|:)\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>))',
    import_pattern=r'(?:import\s+.+\s+from|require\s*\()\s*[\'"]([^\'"]+)[\'"]',
    instantiation_pattern=r'new\s+(\w+)',
)

_TYPESCRIPT = LanguageSyntax(
    name="typescript",
    extensions=[".ts", ".tsx"],
    function_keyword="function",
    class_keyword="class",
    import_keywords=["import"],
    visibility_keywords=["public", "protected", "private"],
    instance_access=".",
    static_access=".",
    variable_prefix="",
    line_comment="//",
    alt_line_comment=None,
    extends_keyword="extends",
    implements_keyword="implements",
    mixin_keywords=[],
    debug_functions=["console.log", "console.warn", "console.error", "debugger"],
    class_pattern=r'(?:abstract\s+)?class\s+(?P<name>\w+)(?:\s+extends\s+(?P<parent>\w+))?(?:\s+implements\s+(?P<interfaces>[^{]+))?',
    function_pattern=r'(?:async\s+)?(?:function\s+(?P<name>\w+)|(?P<name2>\w+)\s*(?:=|:)\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>))',
    import_pattern=r'import\s+.+\s+from\s+[\'"]([^\'"]+)[\'"]',
    instantiation_pattern=r'new\s+(\w+)',
)

_GO = LanguageSyntax(
    name="go",
    extensions=[".go"],
    function_keyword="func",
    class_keyword="type",  # Go uses type ... struct
    import_keywords=["import"],
    visibility_keywords=[],  # Go uses capitalization
    instance_access=".",
    static_access=".",
    variable_prefix="",
    line_comment="//",
    alt_line_comment=None,
    extends_keyword="",  # Go uses embedding
    implements_keyword="",  # implicit
    mixin_keywords=[],
    debug_functions=["fmt.Println", "fmt.Printf", "log.Println"],
    class_pattern=r'type\s+(?P<name>\w+)\s+struct\b',
    function_pattern=r'func\s+(?:\([^)]+\)\s+)?(?P<name>\w+)\s*\(',
    import_pattern=r'"([^"]+)"',
    instantiation_pattern=r'(\w+)\{',
)

_RUST = LanguageSyntax(
    name="rust",
    extensions=[".rs"],
    function_keyword="fn",
    class_keyword="struct",
    import_keywords=["use"],
    visibility_keywords=["pub"],
    instance_access=".",
    static_access="::",
    variable_prefix="",
    line_comment="//",
    alt_line_comment=None,
    extends_keyword="",
    implements_keyword="",
    mixin_keywords=["impl"],
    debug_functions=["println!", "dbg!", "eprintln!"],
    class_pattern=r'(?:pub\s+)?struct\s+(?P<name>\w+)',
    function_pattern=r'(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)',
    import_pattern=r'use\s+([^;]+);',
    instantiation_pattern=r'(\w+)\s*\{',
)

_JAVA = LanguageSyntax(
    name="java",
    extensions=[".java"],
    function_keyword="",
    class_keyword="class",
    import_keywords=["import"],
    visibility_keywords=["public", "protected", "private"],
    instance_access=".",
    static_access=".",
    variable_prefix="",
    line_comment="//",
    alt_line_comment=None,
    extends_keyword="extends",
    implements_keyword="implements",
    mixin_keywords=[],
    debug_functions=["System.out.println", "System.out.print"],
    class_pattern=r'(?:public\s+|abstract\s+|final\s+)*class\s+(?P<name>\w+)(?:\s+extends\s+(?P<parent>\w+))?(?:\s+implements\s+(?P<interfaces>[^{]+))?',
    function_pattern=r'(?:public|protected|private)\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(?P<name>\w+)\s*\(',
    import_pattern=r'import\s+([^;]+);',
    instantiation_pattern=r'new\s+(\w+)',
)

_RUBY = LanguageSyntax(
    name="ruby",
    extensions=[".rb"],
    function_keyword="def",
    class_keyword="class",
    import_keywords=["require", "require_relative", "include"],
    visibility_keywords=["public", "protected", "private"],
    instance_access=".",
    static_access="::",
    variable_prefix="@",
    line_comment="#",
    alt_line_comment="#",
    extends_keyword="<",
    implements_keyword="",
    mixin_keywords=["include", "extend", "prepend"],
    debug_functions=["puts", "p", "pp", "binding.pry", "byebug"],
    class_pattern=r'class\s+(?P<name>\w+)(?:\s*<\s*(?P<parent>\w+(?:::\w+)*))?',
    function_pattern=r'def\s+(?:self\.)?(?P<name>\w+[?!=]?)',
    import_pattern=r'require(?:_relative)?\s+[\'"]([^\'"]+)[\'"]',
    instantiation_pattern=r'(\w+)\.new',
)

# Registry
_SYNTAX_MAP: Dict[str, LanguageSyntax] = {
    "php": _PHP,
    "python": _PYTHON,
    "javascript": _JAVASCRIPT,
    "typescript": _TYPESCRIPT,
    "go": _GO,
    "rust": _RUST,
    "java": _JAVA,
    "ruby": _RUBY,
}

# Extension to language mapping
_EXT_TO_LANG: Dict[str, str] = {}
for lang, syntax in _SYNTAX_MAP.items():
    for ext in syntax.extensions:
        _EXT_TO_LANG[ext] = lang


def get_language_syntax(language: str) -> LanguageSyntax:
    """Get syntax patterns for a language.

    Args:
        language: Language name (e.g., 'php', 'python', 'typescript')

    Returns:
        LanguageSyntax instance. Falls back to JavaScript-like defaults.
    """
    return _SYNTAX_MAP.get(language, _JAVASCRIPT)


def get_syntax_for_file(file_path: str) -> LanguageSyntax:
    """Get syntax patterns based on file extension.

    Args:
        file_path: File path to determine language from

    Returns:
        LanguageSyntax instance
    """
    import os
    ext = os.path.splitext(file_path)[1]
    lang = _EXT_TO_LANG.get(ext, "javascript")
    return _SYNTAX_MAP.get(lang, _JAVASCRIPT)


def get_all_languages() -> List[str]:
    """Get all supported language names."""
    return list(_SYNTAX_MAP.keys())
