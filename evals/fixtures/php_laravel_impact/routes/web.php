<?php

use App\Http\Controllers\CheckoutController;
use Illuminate\Support\Facades\Route;

Route::post('/checkout/quote', [CheckoutController::class, 'quoteTotal']);
