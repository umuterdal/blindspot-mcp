<?php

namespace App\Jobs;

use App\Services\SessionService;

class SessionWorker
{
    public function __construct(private SessionService $sessions)
    {
    }

    public function process(int $userId): bool
    {
        // Real in-method call site. For the same target symbol
        // (``SessionService.refresh``) this must outrank the file-scope
        // call emitted from ``bootstrap.php`` in the sorted
        // direct_callers list.
        return $this->sessions->refresh($userId);
    }
}
