import { Module } from "@nestjs/common";
import { UsersController } from "./users.controller";
import { UsersService, PrismaService } from "./users.service";

@Module({
  controllers: [UsersController],
  providers: [UsersService, PrismaService],
  exports: [UsersService],
})
export class UsersModule {}
