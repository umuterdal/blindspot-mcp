import "../services/pricing_service.dart";

class CheckoutScreen {
  final PricingService service = PricingService();

  double quoteTotal(double subtotal, String tier) {
    return subtotal * service.rateFor(tier);
  }
}
