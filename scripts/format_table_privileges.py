import asyncio
from collections import defaultdict

import asyncpg


async def run():
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/procure_os")
    roles = ["procure_admin", "procure_workflow", "procure_reviewer", "procure_ingest"]

    table_privs = {r: defaultdict(list) for r in roles}
    col_privs = {r: defaultdict(list) for r in roles}

    rows = await conn.fetch(
        """
        SELECT grantee, table_name, privilege_type 
        FROM information_schema.table_privileges 
        WHERE grantee = ANY($1)
        ORDER BY grantee, table_name, privilege_type;
    """,
        roles,
    )
    for r in rows:
        table_privs[r["grantee"]][r["table_name"]].append(r["privilege_type"])

    crows = await conn.fetch(
        """
        SELECT grantee, table_name, column_name, privilege_type
        FROM information_schema.column_privileges
        WHERE grantee = ANY($1)
        ORDER BY grantee, table_name, column_name, privilege_type;
    """,
        roles,
    )
    for cr in crows:
        col_privs[cr["grantee"]][f"{cr['table_name']}.{cr['column_name']}"].append(
            cr["privilege_type"]
        )

    await conn.close()

    tables = sorted(
        list(
            set(
                t
                for r in roles
                for t in table_privs[r].keys()
                if not t.startswith("pg_") and t != "alembic_version"
            )
        )
    )
    print("| Table Name | procure_workflow | procure_reviewer | procure_ingest | procure_admin |")
    print("|---|---|---|---|---|")
    for t in tables:
        pw = ", ".join(sorted(table_privs["procure_workflow"].get(t, []))) or "-"
        pr = ", ".join(sorted(table_privs["procure_reviewer"].get(t, []))) or "-"
        pi = ", ".join(sorted(table_privs["procure_ingest"].get(t, []))) or "-"
        pa = ", ".join(sorted(table_privs["procure_admin"].get(t, []))) or "-"
        print(f"| `{t}` | {pw} | {pr} | {pi} | {pa} |")

    print("\n### Column-Level Privileges on department_budgets and requests:")
    for r in roles:
        if col_privs[r]:
            relevant_cols = {
                col: privs
                for col, privs in col_privs[r].items()
                if "department_budgets" in col or "requests." in col
            }
            if relevant_cols:
                print(f"**{r}**:")
                for col, privs in sorted(relevant_cols.items()):
                    print(f"  - `{col}`: {', '.join(sorted(privs))}")


if __name__ == "__main__":
    asyncio.run(run())
