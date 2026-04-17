export class PaymentService {
  charge(amount: number): boolean {
    return amount > 0;
  }
}

export class NotificationService {
  notify(userId: number): void {
    // no-op
  }
}
