UPDATE quotes
SET data=json_set(data, '$.estado', 'Abierta')
WHERE number LIKE 'ITS-SOL-%'
  AND COALESCE(json_extract(data, '$.estado'), '')='';
