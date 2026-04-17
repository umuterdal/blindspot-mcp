package api

import "testing"

func TestQuoteTotal(t *testing.T) {
	if QuoteTotal(100, "vip") != 80 {
		t.Fatal("unexpected total")
	}
}
