import os
import tempfile
import unittest

from blindspot.services.laravel_validation_service import LaravelValidationService


class _FakeLifespan:
    def __init__(self, base_path: str):
        self.base_path = base_path
        self.settings = None
        self.file_count = 0
        self.index_manager = None


class _FakeReqCtx:
    def __init__(self, base_path: str):
        self.lifespan_context = _FakeLifespan(base_path)


class _FakeCtx:
    def __init__(self, base_path: str):
        self.request_context = _FakeReqCtx(base_path)


class LaravelValidationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.ctx = _FakeCtx(self.base)
        self.svc = LaravelValidationService(self.ctx)

        os.makedirs(os.path.join(self.base, "routes"), exist_ok=True)
        os.makedirs(os.path.join(self.base, "app", "Http", "Controllers", "Auth"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_controller(self) -> None:
        content = """<?php
namespace App\\Http\\Controllers\\Auth;

class AuthenticatedSessionController
{
    public function create()
    {
        return 'ok';
    }
}
"""
        path = os.path.join(
            self.base,
            "app",
            "Http",
            "Controllers",
            "Auth",
            "AuthenticatedSessionController.php",
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_large_routes(self) -> None:
        lines = [
            "<?php",
            "use App\\Http\\Controllers\\Auth\\AuthenticatedSessionController;",
        ]
        # 60 routes so old get_route_map truncation (50) would hide /giris
        for i in range(1, 61):
            lines.append(
                f"Route::get('/r{i}', [AuthenticatedSessionController::class, 'create'])->name('r{i}');"
            )
        lines.append(
            "Route::get('/giris', [AuthenticatedSessionController::class, 'create'])->name('login');"
        )
        path = os.path.join(self.base, "routes", "web.php")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def test_verify_endpoint_finds_route_beyond_truncation_window(self):
        self._write_controller()
        self._write_large_routes()

        result = self.svc.verify_endpoint("GET", "/giris")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["route"]["path"], "/giris")
        self.assertEqual(result["controller"]["method"], "create")

    def test_method_and_path_matching_helpers(self):
        self.assertTrue(self.svc._method_matches("GET", "GET|HEAD"))
        self.assertTrue(self.svc._method_matches("HEAD", "GET"))
        self.assertTrue(self.svc._path_matches("/api/users/42", "/api/users/{id}"))
        self.assertTrue(self.svc._path_matches("/api/users", "/api/{section?}"))


if __name__ == "__main__":
    unittest.main()
