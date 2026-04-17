import { PaymentService, NotificationService } from "../services/payment.service";

// Exercises four variable-bound call patterns the strategy must resolve
// to cross-file ref edges. Any regression shows up as a missing edge in
// the CrossFileRefsFixtureRegressionTests probe.

export class OrdersController {
  // Field declaration: ``notifications: NotificationService`` must be
  // tracked so ``this.notifications.notify(...)`` resolves to
  // ``NotificationService.notify`` below.
  private notifications: NotificationService = new NotificationService();

  // Constructor-promoted DI: ``payments`` is a promoted property typed
  // as ``PaymentService``. ``this.payments.charge(...)`` below must
  // resolve via ``property_types['OrdersController']['payments']``.
  constructor(private readonly payments: PaymentService) {}

  placeOrder(userId: number, amount: number): boolean {
    // Local ``new`` expression: ``const extra = new PaymentService()``.
    // ``extra.charge(...)`` must resolve to ``PaymentService.charge``
    // via ``local_types``.
    const extra = new PaymentService();

    // Explicit local annotation: ``const bonus: PaymentService = ...``.
    // Type annotation wins even though the RHS is an opaque expression.
    const bonus: PaymentService = this.payments;

    const okA = this.payments.charge(amount);            // promoted DI
    const okB = extra.charge(amount);                    // new-expr local
    const okC = bonus.charge(amount);                    // annotated local
    this.notifications.notify(userId);                   // field decl

    return okA && okB && okC;
  }
}
