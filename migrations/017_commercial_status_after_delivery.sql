UPDATE quotes
SET data = json_set(data, '$.estado', ''),
    fingerprint = NULL
WHERE number LIKE 'ITS-SOL-%'
  AND request_status IN (
      'pendiente_asignacion',
      'en_cotizacion',
      'requiere_informacion'
  )
  AND COALESCE(json_extract(data, '$.estado'), '') = 'Abierta'
  AND sent_at IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM quote_deliveries
      WHERE quote_deliveries.quote_id = quotes.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM audit
      WHERE audit.quote_id = quotes.id
        AND audit.action = 'cambiar_estado_comercial'
  );
