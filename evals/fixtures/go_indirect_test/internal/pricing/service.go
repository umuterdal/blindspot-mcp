package pricing

func RateFor(tier string) float64 {
	if tier == "vip" {
		return 0.8
	}
	return 1.0
}
