<?php

namespace App\Services;

class SessionService
{
    private static ?SessionService $instance = null;

    public static function getInstance(): SessionService
    {
        if (self::$instance === null) {
            self::$instance = new SessionService();
        }
        return self::$instance;
    }

    public function refresh(int $userId): bool
    {
        return $userId > 0;
    }
}
