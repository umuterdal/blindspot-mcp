<?php

namespace App\Http\Controllers;

use App\Services\PricingService;

class CheckoutController
{
    public function quoteTotal(float $subtotal, string $tier): float
    {
        $service = new PricingService();
        return $subtotal * $service->rateFor($tier);
    }
}
