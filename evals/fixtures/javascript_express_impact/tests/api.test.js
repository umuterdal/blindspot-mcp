import { quoteTotal } from "../src/controllers/api.js";

export function runQuoteScenario() {
  return quoteTotal(100, "vip");
}
