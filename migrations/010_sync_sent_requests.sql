UPDATE quotes
SET request_status='entregada'
WHERE workflow_status='enviada'
  AND request_status IN (
      'en_cotizacion',
      'requiere_informacion',
      'en_correccion'
  );
