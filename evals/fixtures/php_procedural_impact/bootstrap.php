<?php
// Top-level script: no enclosing function. Emulates procedural PHP
// entry points (WordPress plugins, Laravel bootstrap hooks, legacy
// front controllers). Blindspot must still capture these calls as
// cross-file edges via the synthetic __file_scope__ caller.

require_once __DIR__ . '/app/Services/SessionService.php';

use App\Services\SessionService;

// Scoped call -> SessionService.getInstance (cross-file).
$session = SessionService::getInstance();

// Variable-bound member call: receiver $session was typed at
// assignment time, so this must resolve to SessionService.refresh
// rather than a bare "refresh" that could match any class.
$session->refresh(42);

// Also exercise a file-scope call to ``SessionService.refresh`` which
// is ALSO called from inside ``SessionWorker.process`` in
// ``app/Jobs/SessionWorker.php``. The fixture test asserts that the
// in-method caller outranks this file-scope caller in
// ``direct_callers``, which is the whole point of the
// ``module_script`` usage role.
SessionService::getInstance()->refresh(99);
