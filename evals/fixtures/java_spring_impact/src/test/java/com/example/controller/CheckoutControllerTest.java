package com.example.controller;

public class CheckoutControllerTest {
    public void testQuoteTotal() {
        CheckoutController controller = new CheckoutController();
        double total = controller.quoteTotal(100.0, "vip");
        assert total == 80.0;
    }
}
