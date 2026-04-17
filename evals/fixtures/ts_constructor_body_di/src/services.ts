export class UsersService {
  findById(id: number): object | null {
    return { id };
  }
}

export class Logger {
  info(message: string): void {
    // no-op
  }
}

export class AuditService {
  record(event: string): void {
    // no-op
  }
}

// Note: a ``Cache`` class is intentionally NOT exported here. The
// classic fixture's ``this.orphan = opaque`` assignment must stay
// unresolved; having a ``Cache.get`` symbol around would still be safe
// (qualifier resolution keys on the class-and-prop map, not name match)
// but removing it keeps the fixture's intent obvious.
