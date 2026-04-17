<?php

namespace App\Http;

use App\Services\PaymentService;

class LegacyCtorController
{
    private PaymentService $payments;

    public function __construct(PaymentService $payments)
    {
        $this->payments = $payments;
    }

    public function pay(int $userId, float $amount): bool
    {
        return $this->payments->charge($userId, $amount);
    }
}
