package com.example.service;

public class PricingService {
    public double rateFor(String tier) {
        return tier.equals("vip") ? 0.8 : 1.0;
    }
}
