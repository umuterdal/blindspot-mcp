package api

import "example.com/go-indirect-test/internal/pricing"

func QuoteTotal(subtotal float64, tier string) float64 {
	return subtotal * pricing.RateFor(tier)
}
