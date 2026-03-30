import os
import tempfile
import unittest

from blindspot.adapters.language_execution_adapter import LanguageExecutionAdapter
from blindspot.config import clear_config_cache


class LanguageExecutionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        clear_config_cache(self.tmp.name)
        self.tmp.cleanup()

    def _write_config(self, text: str) -> None:
        with open(os.path.join(self.tmp.name, ".blindspot.yaml"), "w", encoding="utf-8") as f:
            f.write(text)
        clear_config_cache(self.tmp.name)

    def test_build_quality_matrix_groups_python_files(self):
        adapter = LanguageExecutionAdapter(self.tmp.name)
        result = adapter.build_quality_matrix(["src/a.py", "src/b.py"])
        self.assertEqual(result["status"], "success")
        self.assertIn("python", result["languages"])
        check_ids = [c.get("check_id") for c in result.get("checks", [])]
        self.assertIn("python:syntax", check_ids)
        self.assertIn("python:static", check_ids)
        self.assertIn("python:tests", check_ids)

    def test_overrides_apply_from_config(self):
        self._write_config(
            "language_adapters:\n"
            "  languages:\n"
            "    python:\n"
            "      static_command: \"python3 -m unittest -v\"\n"
        )
        adapter = LanguageExecutionAdapter(self.tmp.name)
        result = adapter.build_quality_matrix(["src/a.py"])
        static_checks = [
            c for c in result.get("checks", [])
            if c.get("check_id") == "python:static"
        ]
        self.assertEqual(len(static_checks), 1)
        self.assertIn("python3 -m unittest -v", static_checks[0].get("command", ""))

    def test_detect_language_handles_blade_php(self):
        adapter = LanguageExecutionAdapter(self.tmp.name)
        self.assertEqual(adapter.detect_language("resources/views/home.blade.php"), "php")


if __name__ == "__main__":
    unittest.main()
