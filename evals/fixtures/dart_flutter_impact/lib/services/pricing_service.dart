class PricingService {
  double rateFor(String tier) {
    return tier == "vip" ? 0.8 : 1.0;
  }
}
