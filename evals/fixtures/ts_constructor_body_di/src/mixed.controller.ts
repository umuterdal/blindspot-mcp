import { UsersService, Logger, AuditService } from "./services";

// Mixed style: some constructor params are promoted (Nest/Angular
// shorthand), some are assigned in the body. Both must resolve in the
// same class so ``this.<prop>.method()`` lands the right owner type
// regardless of declaration style.
export class MixedUsersController {
  private audit: AuditService;

  constructor(
    private readonly users: UsersService,     // promoted
    logger: Logger,                            // NOT promoted
  ) {
    // Classic assignment of the non-promoted param to a field
    // declared separately would also be supported; here we assign to
    // a field that was not declared up top to cover the
    // implicit-property shape that some older TS linters allow.
    this.logger = logger;
    this.audit = new AuditService();
  }

  getOne(id: number): object | null {
    // Each call below exercises a different DI declaration style.
    const primary = this.users.findById(id);     // promoted
    this.logger.info("ok");                       // body assignment (param)
    this.audit.record("mixed.getOne");            // body assignment (new)
    return primary;
  }
}
