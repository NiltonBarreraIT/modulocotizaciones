UPDATE quote_commercial
SET tax_percent='0.00',
    tax_total='0.00',
    total=printf(
        '%.2f',
        CAST(subtotal AS NUMERIC) - CAST(discount_total AS NUMERIC)
    );
