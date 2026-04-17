<?php

namespace App\Http;

use App\Services\AuditService;

class TypedPropController
{
    private AuditService $audit;

    public function log(string $msg): void
    {
        $this->audit->record($msg);
    }
}
