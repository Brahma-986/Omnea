import asyncio

import asyncpg


async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/procure_os")
    roles = ["procure_admin", "procure_workflow", "procure_reviewer", "procure_ingest"]

    for role in roles:
        print(f"\n================ ROLE: {role} ================")
        rows = await conn.fetch(
            """
            SELECT table_name, privilege_type 
            FROM information_schema.table_privileges 
            WHERE grantee = $1
            ORDER BY table_name, privilege_type;
        """,
            role,
        )
        if not rows:
            print("  (No direct table_privileges rows)")
        for r in rows:
            print(f"  {r['table_name']:32} {r['privilege_type']}")

        # Also check column_privileges (for column-level grants)
        col_rows = await conn.fetch(
            """
            SELECT table_name, column_name, privilege_type
            FROM information_schema.column_privileges
            WHERE grantee = $1
            ORDER BY table_name, column_name, privilege_type;
        """,
            role,
        )
        if col_rows:
            print("  --- Column-Level Privileges ---")
            for cr in col_rows:
                print(f"  {cr['table_name']}.{cr['column_name']:20} {cr['privilege_type']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
