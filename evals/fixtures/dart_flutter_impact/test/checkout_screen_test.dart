import "../lib/screens/checkout_screen.dart";

double runCheckoutScenario() {
  final screen = CheckoutScreen();
  return screen.quoteTotal(100, "vip");
}
