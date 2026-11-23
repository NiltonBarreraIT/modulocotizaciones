def quote_number(prefix, correlative):
    return f"ITS-{prefix}-{correlative:06d}"


def bump_correlative(database, value):
    database.execute(
        """UPDATE quote_number_sequence
        SET current_value=CASE
            WHEN current_value < ? THEN ? ELSE current_value END
        WHERE id=1""",
        (value, value),
    )


def next_correlative(database):
    return database.execute(
        """UPDATE quote_number_sequence
        SET current_value=current_value+1 WHERE id=1
        RETURNING current_value"""
    ).fetchone()[0]


def shift_solution_conflicts(database, reserved_numbers):
    """Move platform-created quotes when imported legacy numbers overlap them."""
    reserved = set(reserved_numbers)
    solutions = database.execute(
        """SELECT id,correlative FROM quotes
        WHERE number LIKE 'ITS-SOL-%' AND correlative IS NOT NULL
        ORDER BY created_at,id"""
    ).fetchall()
    collisions = [row for row in solutions if row["correlative"] in reserved]
    if not collisions:
        bump_correlative(database, max(reserved, default=0))
        return 0

    first_collision = min(row["correlative"] for row in collisions)
    affected = [row for row in solutions if row["correlative"] >= first_collision]
    affected_ids = {row["id"] for row in affected}
    other_max = max(
        (
            row["correlative"]
            for row in database.execute(
                "SELECT id,correlative FROM quotes WHERE correlative IS NOT NULL"
            )
            if row["id"] not in affected_ids
        ),
        default=0,
    )
    current = max(max(reserved, default=0), other_max)

    for row in affected:
        database.execute(
            "UPDATE quotes SET number=?,correlative=NULL WHERE id=?",
            (f"TEMP-SOL-{row['id']}", row["id"]),
        )
    for row in affected:
        current += 1
        database.execute(
            "UPDATE quotes SET number=?,correlative=? WHERE id=?",
            (quote_number("SOL", current), current, row["id"]),
        )
    bump_correlative(database, current)
    return len(affected)
