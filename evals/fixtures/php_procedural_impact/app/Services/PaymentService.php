<?php

namespace App\Services;

class PaymentService
{
    public function charge(int $userId, float $amount): bool
    {
        return $amount > 0;
    }
}
