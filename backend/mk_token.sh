#!/bin/sh
cd /opt/reportpilot
docker compose exec -T backend python - <<'PYEOF' 2>&1 | tail -3
import asyncio
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.security import create_token


async def main():
    async with AsyncSessionLocal() as db:
        u = (await db.scalars(select(User).where(User.username == "admin"))).first()
        print(create_token(u.id, u.username, u.role))


asyncio.run(main())
PYEOF
