import { Injectable } from "@nestjs/common";

@Injectable()
export class UsersService {
  constructor(private readonly prisma: PrismaService) {}

  async findById(id: string): Promise<UserDto | null> {
    return this.prisma.user.findUnique({ where: { id } });
  }
}

export class PrismaService {
  user: any = { findUnique: async (_: any) => null };
}

export class UserDto {
  constructor(public id: string, public email: string) {}
}
