import { ÖdemeService, Günlükçü } from "./services";

// Kullanıcıdan siparişi alır — "ödeme" servisini tetikler.
// The following block intentionally stacks non-ASCII characters BEFORE
// every declaration so a byte-vs-char slicing bug would corrupt class
// names, method names, property names, and signatures simultaneously:
//
//   * em-dash (—) spans 3 bytes in UTF-8.
//   * smart quotes (" ") span 3 bytes each.
//   * Turkish letters (ö, ğ, ü, ç, ş, İ) span 2 bytes each.
//   * Bullet (•) spans 3 bytes.
//
// If _get_class_name / _get_method_name / _get_ts_function_signature
// slice a str form of the file with byte offsets, the class name here
// comes out mangled (missing leading chars and/or carrying trailing
// punctuation), and every downstream consumer — refs, direct_callers,
// get_symbol_body — then operates on a phantom symbol id.
export class SiparişController {
  constructor(
    private readonly ödeme: ÖdemeService,
    private readonly günlük: Günlükçü,
  ) {}

  // Ödeme alındıktan sonra "başarılı" log'u yazar.
  async process(amount: number): Promise<string> {
    const sonuç = await this.ödeme.pay(amount);
    this.günlük.info("ödeme tamam");
    return sonuç;
  }
}
