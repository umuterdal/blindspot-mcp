<?php

namespace App\Http;

use App\Services\PaymentService;

class CheckoutController
{
    public function __construct(private PaymentService $payments)
    {
    }

    public function pay(int $userId, float $amount): bool
    {
        // Constructor-promoted DI: the pay method calls
        // $this->payments->charge(...), which must resolve to
        // PaymentService.charge even though the method name alone is
        // shared across many services in real codebases.
        return $this->payments->charge($userId, $amount);
    }
}
