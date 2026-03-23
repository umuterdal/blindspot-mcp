"""
File Matching Tool - Pure technical component for pattern matching operations.

This tool handles file pattern matching without any business logic.
It provides technical capabilities for finding files based on various patterns.
"""

import fnmatch
from typing import List, Set
from pathlib import Path

# FileInfo defined locally for file matching operations
from dataclasses import dataclass

@dataclass
class FileInfo:
    """File information structure."""
    relative_path: str
    language: str


class FileMatchingTool:
    """
    Pure technical component for file pattern matching.

    This tool provides low-level pattern matching capabilities without
    any business logic. It can match files using glob patterns, regex,
    or other matching strategies.
    """

    def __init__(self):
        pass

    def match_glob_pattern(self, files: List[FileInfo], pattern: str) -> List[FileInfo]:
        """
        Match files using glob pattern.

        Args:
            files: List of FileInfo objects to search through
            pattern: Glob pattern (e.g., "*.py", "test_*.js", "src/**/*.ts")

        Returns:
            List of FileInfo objects that match the pattern
        """
        if not pattern:
            return files

        matched_files = []

        for file_info in files:
            # Try matching against full path
            if fnmatch.fnmatch(file_info.relative_path, pattern):
                matched_files.append(file_info)
                continue

            # Try matching against just the filename
            filename = Path(file_info.relative_path).name
            if fnmatch.fnmatch(filename, pattern):
                matched_files.append(file_info)

        return matched_files

    def match_multiple_patterns(self, files: List[FileInfo], patterns: List[str]) -> List[FileInfo]:
        """
        Match files using multiple glob patterns (OR logic).

        Args:
            files: List of FileInfo objects to search through
            patterns: List of glob patterns

        Returns:
            List of FileInfo objects that match any of the patterns
        """
        if not patterns:
            return files

        matched_files = set()

        for pattern in patterns:
            pattern_matches = self.match_glob_pattern(files, pattern)
            matched_files.update(pattern_matches)

        return list(matched_files)

    def match_by_language(self, files: List[FileInfo], languages: List[str]) -> List[FileInfo]:
        """
        Match files by programming language.

        Args:
            files: List of FileInfo objects to search through
            languages: List of language names (e.g., ["python", "javascript"])

        Returns:
            List of FileInfo objects with matching languages
        """
        if not languages:
            return files

        # Normalize language names for comparison
        normalized_languages = {lang.lower() for lang in languages}

        matched_files = []
        for file_info in files:
            if file_info.language.lower() in normalized_languages:
                matched_files.append(file_info)

        return matched_files

    def match_by_directory(self, files: List[FileInfo], directory_patterns: List[str]) -> List[FileInfo]:
        """
        Match files by directory patterns.

        Args:
            files: List of FileInfo objects to search through
            directory_patterns: List of directory patterns (e.g., ["src/*", "test/**"])

        Returns:
            List of FileInfo objects in matching directories
        """
        if not directory_patterns:
            return files

        matched_files = []

        for file_info in files:
            file_dir = str(Path(file_info.relative_path).parent)

            for dir_pattern in directory_patterns:
                if fnmatch.fnmatch(file_dir, dir_pattern):
                    matched_files.append(file_info)
                    break

        return matched_files

    def exclude_patterns(self, files: List[FileInfo], exclude_patterns: List[str]) -> List[FileInfo]:
        """
        Exclude files matching the given patterns.

        Args:
            files: List of FileInfo objects to filter
            exclude_patterns: List of patterns to exclude

        Returns:
            List of FileInfo objects that don't match any exclude pattern
        """
        if not exclude_patterns:
            return files

        filtered_files = []

        for file_info in files:
            should_exclude = False

            for exclude_pattern in exclude_patterns:
                if (fnmatch.fnmatch(file_info.relative_path, exclude_pattern) or
                    fnmatch.fnmatch(Path(file_info.relative_path).name, exclude_pattern)):
                    should_exclude = True
                    break

            if not should_exclude:
                filtered_files.append(file_info)

        return filtered_files

    def sort_by_relevance(self, files: List[FileInfo], pattern: str) -> List[FileInfo]:
        """
        Sort files by relevance to the search pattern.

        Args:
            files: List of FileInfo objects to sort
            pattern: Original search pattern for relevance scoring

        Returns:
            List of FileInfo objects sorted by relevance (most relevant first)
        """
        def relevance_score(file_info: FileInfo) -> int:
            """Calculate relevance score for a file."""
            score = 0
            filename = Path(file_info.relative_path).name

            # Exact filename match gets highest score
            if filename == pattern:
                score += 100

            # Filename starts with pattern
            elif filename.startswith(pattern.replace('*', '')):
                score += 50

            # Pattern appears in filename
            elif pattern.replace('*', '') in filename:
                score += 25

            # Shorter paths are generally more relevant
            path_depth = len(Path(file_info.relative_path).parts)
            score += max(0, 10 - path_depth)

            return score

        return sorted(files, key=relevance_score, reverse=True)

    def limit_results(self, files: List[FileInfo], max_results: int) -> List[FileInfo]:
        """
        Limit the number of results returned.

        Args:
            files: List of FileInfo objects
            max_results: Maximum number of results to return

        Returns:
            List of FileInfo objects limited to max_results
        """
        if max_results <= 0:
            return files

        return files[:max_results]
