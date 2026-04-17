import { UsersService, Logger, AuditService } from "./services";

// Legacy Angular / older Nest style: constructor params are typed but
// NOT promoted. No field declarations up top — properties exist only
// via constructor-body assignment. Exercises the three supported
// shapes the strategy must resolve, plus one FP-guard case.
export class ClassicUsersController {
  constructor(users: UsersService, logger: Logger, opaque: any) {
    // Shape 1: ``this.prop = paramName`` where ``paramName`` has a
    // user-defined type annotation on the constructor parameter.
    // Must type ``users`` -> UsersService.
    this.users = users;
    this.logger = logger;

    // Shape 2: ``this.prop = new Foo(...)``. Must type ``audit`` ->
    // AuditService via the ``new_expression`` identifier.
    this.audit = new AuditService();

    // FP guard: RHS is an opaque ``any`` parameter — no user-defined
    // type is available, so the strategy MUST NOT register a type for
    // ``this.orphan``. A later ``this.orphan.get(...)`` therefore stays
    // unresolved (better than a silent wrong-owner edge).
    this.orphan = opaque;
  }

  getOne(id: number): object | null {
    const primary = this.users.findById(id);        // UsersService.findById
    this.logger.info("looked up user");              // Logger.info
    this.audit.record("users.getOne");               // AuditService.record
    this.orphan.get(String(id));                     // UNRESOLVED on purpose
    return primary;
  }
}
