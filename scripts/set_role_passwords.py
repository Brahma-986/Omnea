"""Set distinct passwords for all 4 roles."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine("postgresql+asyncpg://postgres:postgres@localhost:5432/procure_os")
    async with engine.begin() as conn:
        await conn.execute(text("ALTER ROLE procure_admin WITH PASSWORD 'procure_admin_pass'"))
        await conn.execute(
            text("ALTER ROLE procure_workflow WITH PASSWORD 'procure_workflow_pass'")
        )
        await conn.execute(
            text("ALTER ROLE procure_reviewer WITH PASSWORD 'procure_reviewer_pass'")
        )
        await conn.execute(text("ALTER ROLE procure_ingest WITH PASSWORD 'procure_ingest_pass'"))
        print("All 4 role passwords updated successfully!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
