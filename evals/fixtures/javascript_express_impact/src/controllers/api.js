import { rateFor } from "../services/pricing.js";

export function quoteTotal(subtotal, tier) {
  return subtotal * rateFor(tier);
}
