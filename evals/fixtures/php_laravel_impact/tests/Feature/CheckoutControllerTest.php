<?php

namespace Tests\Feature;

use App\Http\Controllers\CheckoutController;

class CheckoutControllerTest
{
    public function testQuoteTotal(): void
    {
        $controller = new CheckoutController();
        $controller->quoteTotal(100, 'vip');
    }
}
