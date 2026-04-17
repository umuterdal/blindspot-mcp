<?php

namespace App\Services;

class PricingService
{
    public function rateFor(string $tier): float
    {
        return $tier === 'vip' ? 0.8 : 1.0;
    }
}
