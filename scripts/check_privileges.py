import asyncio

import asyncpg


async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/procure_os")
    rows = await conn.fetch("""
        SELECT table_name, privilege_type 
        FROM information_schema.table_privileges 
        WHERE grantee = 'procure_app' 
        ORDER BY table_name, privilege_type;
    """)
    for r in rows:
        print(f"{r['table_name']:32} {r['privilege_type']}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
