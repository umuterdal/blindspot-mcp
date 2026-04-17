// Service module containing a mix of non-ASCII content that is SAFE to
// appear anywhere in a TypeScript file: Turkish comments, smart quotes
// ("like these"), em-dashes — and bullets •. Every byte offset tree-sitter
// returns indexes into the UTF-8 encoding, so naïvely slicing the str
// form mangles every symbol declared below the first non-ASCII byte.

export class ÖdemeService {
  // Kredi kartı ile ödeme alır — async / await pattern.
  pay(amount: number): Promise<string> {
    return Promise.resolve("ok");
  }
}

// Açıklama: aşağıdaki sınıf loglama için kullanılır.
// Yıldız karakterleri • • • bazı tree-sitter sürümlerinde offset kayması
// yaratıyordu; burada test ediyoruz.
export class Günlükçü {
  info(mesaj: string): void {
    // no-op
  }
}
