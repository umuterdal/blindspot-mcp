package com.example.controller;

import com.example.service.PricingService;

public class CheckoutController {
    private final PricingService pricing;

    public CheckoutController() {
        this.pricing = new PricingService();
    }

    public double quoteTotal(double subtotal, String tier) {
        return subtotal * pricing.rateFor(tier);
    }
}
