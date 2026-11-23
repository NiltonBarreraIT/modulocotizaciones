# ITS — Gestión de Cotizaciones

Aplicación web Flask compatible con PostgreSQL y SQLite, basada en los campos comerciales A–S del libro de Excel entregado. Permite crear, versionar, aprobar y emitir cotizaciones con líneas de productos y servicios, PDF, adjuntos, comentarios, importación histórica, exportación, tablero y auditoría. Los números nuevos son correlativos `ITS-COT-000001`, etc. PostgreSQL es el motor recomendado para producción; SQLite se conserva para desarrollo local y migración de instalaciones existentes.

## Instalación

```bash
cd its_cotizaciones
python -m venv .venv
source .venv/bin/activate # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
export ADMIN_PASSWORD='CAMBIAR_POR_CLAVE_FUERTE_12_CARACTERES'
export SECRET_KEY='GENERAR_SECRETO_LARGO_ALEATORIO'
python app.py
```

Abrir http://127.0.0.1:5000 — usuario inicial: `admin`. En Windows, sustituya `export` por `set` (CMD) o `$env:VARIABLE='valor'` (PowerShell).

## PostgreSQL y migración desde SQLite

La aplicación utiliza PostgreSQL cuando existe `DATABASE_URL`. Si la variable no está definida continúa usando `DATABASE_PATH` y SQLite.

Ejemplo de creación de una base vacía, ejecutado con una cuenta administradora de PostgreSQL:

```sql
CREATE ROLE its_cotizaciones LOGIN PASSWORD 'CAMBIAR_ESTA_CLAVE';
CREATE DATABASE its_cotizaciones OWNER its_cotizaciones;
```

Antes del primer arranque contra PostgreSQL, detener las escrituras y respaldar tanto `cotizaciones.db` como `data/attachments`. Luego instalar las dependencias, definir la conexión y ejecutar el migrador:

```powershell
$env:DATABASE_URL='postgresql://its_cotizaciones:CLAVE@127.0.0.1:5432/its_cotizaciones'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py
```

En Linux:

```bash
export DATABASE_URL='postgresql://its_cotizaciones:CLAVE@127.0.0.1:5432/its_cotizaciones'
python -m pip install -r requirements.txt
python scripts/migrate_sqlite_to_postgres.py
```

El destino debe estar vacío. El migrador comprueba primero la integridad de SQLite, crea el esquema PostgreSQL, preserva todos los identificadores, ajusta las secuencias y compara cantidad y huella SHA-256 del contenido de cada tabla. Si algo falla, la transacción PostgreSQL se revierte completa. No elimina ni modifica la base SQLite de origen.

Una vez terminada la copia, mantener `DATABASE_URL` en el servicio que ejecuta Gunicorn y arrancar la aplicación normalmente. Los adjuntos no están dentro de la base: `UPLOAD_FOLDER` debe apuntar a la copia persistente de `data/attachments`.

Para una instalación nueva sin datos SQLite, basta con definir `DATABASE_URL`, `ADMIN_PASSWORD` y `SECRET_KEY` antes del primer arranque. La aplicación crea el esquema y el administrador inicial bajo un bloqueo de migración PostgreSQL.

### Configuración de seguridad y límites

Los siguientes valores se pueden ajustar mediante variables de entorno:

- `LOGIN_MAX_ATTEMPTS=5`: intentos fallidos antes del bloqueo.
- `LOGIN_WINDOW_MINUTES=15`: ventana en la que se cuentan los intentos.
- `LOGIN_LOCK_MINUTES=15`: duración del bloqueo temporal.
- `IMPORT_MAX_ROWS=5000`: filas máximas aceptadas por importación.
- `IMPORT_PREVIEW_ROWS=100`: filas mostradas en la previsualización.
- `IMPORT_BATCH_HOURS=24`: vigencia de una importación pendiente de confirmar.
- `QUOTE_PAGE_SIZE=25`: cotizaciones mostradas por página.
- `ASSIGNMENT_TARGET_HOURS=24`: plazo objetivo para que un cotizador tome una solicitud; al vencer se marca visualmente como atrasada.
- `DISPLAY_TIMEZONE=America/Santiago`: zona horaria usada para fechas, horas y vencimientos visibles.
- `UPLOAD_FOLDER=data/attachments`: almacenamiento privado de adjuntos.
- `ATTACHMENT_MAX_BYTES=10485760`: tamaño máximo de cada adjunto.
- `MAX_XLSX_UNCOMPRESSED=104857600`: tamaño descomprimido máximo del Excel, en bytes.

Las cotizaciones aceptan montos no negativos, margen entre 0 y 100, fechas ISO válidas y únicamente los estados definidos por la aplicación. Las importaciones deben usar `.xlsx`, contener 19 columnas A–S y ubicar cliente y descripción en las columnas L y M.

La fábrica de aplicación aplica automáticamente las migraciones SQL pendientes y crea el administrador únicamente cuando la base está vacía. Para servidor Ubuntu, configurar HTTPS con Nginx, `COOKIE_SECURE=1`, una clave `SECRET_KEY` persistente, copias de seguridad de `cotizaciones.db` y de `UPLOAD_FOLDER`, y ejecutar `gunicorn -w 2 -b 127.0.0.1:5000 app:app`. Nunca exponer el servidor de desarrollo Flask directamente a Internet. Para despliegues de mayor escala, incorporar PostgreSQL, almacenamiento de objetos, respaldos automáticos y autenticación empresarial.

## Calidad y pruebas

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python -m pip_audit -r requirements.txt
```

Las pruebas verifican CSRF y bloqueo de login, validaciones, cabeceras HTTP, neutralización de fórmulas, importaciones, auditoría, roles, aislamiento entre usuarios, ventana de edición, migraciones, concurrencia, filtros, paginación, duplicados, cálculos comerciales, versiones, aprobación, PDF, adjuntos, comentarios y tablero. El flujo `.github/workflows/ci.yml` ejecuta estos controles en cada cambio cuando el proyecto está alojado en GitHub.

## Arquitectura

- `app.py`: punto de entrada compatible con Flask y Gunicorn.
- `cotizaciones/__init__.py`: fábrica y configuración de la aplicación.
- `cotizaciones/auth.py`: sesiones, roles y protección del login.
- `cotizaciones/quotes.py`: creación, consulta, edición y exportación.
- `cotizaciones/admin.py`: usuarios e importaciones históricas.
- `cotizaciones/commercial.py`: líneas, aprobación, versiones, comentarios, adjuntos y PDF.
- `cotizaciones/commercial_service.py`: cálculos y snapshots comerciales.
- `cotizaciones/dashboard.py`: indicadores y actividad reciente.
- `cotizaciones/pdf_builder.py`: composición del PDF corporativo.
- `cotizaciones/db.py`: conexiones compatibles con PostgreSQL y SQLite.
- `migrations/`: cambios de esquema SQLite versionados.
- `migrations_postgresql/`: esquema y cambios PostgreSQL versionados.
- `scripts/migrate_sqlite_to_postgres.py`: copia y verifica los datos existentes.
- `tests/`: pruebas de regresión y flujos críticos.

## Reglas de acceso

- Los usuarios creados desde la administración reciben una contraseña temporal y deben reemplazarla por otra de al menos 12 caracteres en su primer ingreso. Un administrador puede restablecerla y volver a exigir ese cambio.
- **Usuario:** solo visualiza sus cotizaciones y las puede editar antes de 24 h desde **su último guardado**. Cada guardado suyo reinicia la ventana de 24 h.
- **Administrador:** puede ver y editar todas las cotizaciones en cualquier momento. Sus cambios **no reabren** el plazo de los usuarios.
- La validación ocurre en el **servidor**, incluso justo antes de guardar. Cambiar la hora del navegador no evita el bloqueo. El tiempo se guarda en UTC.
- Cada cotización tiene una versión. Si dos personas editan simultáneamente, el segundo guardado recibe una comparación y no sobrescribe los cambios anteriores.
- El historial muestra fecha, autor y diferencias campo por campo entre las versiones.
- Las importadas son cotizaciones históricas bloqueadas para usuarios normales, asignadas al administrador que importó.
- La importación presenta una previsualización, marca duplicados y solo guarda las filas nuevas después de una confirmación explícita.

## Flujo de estados

Los administradores pueden corregir cualquier estado. Los usuarios regulares siguen estas transiciones:

- Sin estado → Referencial, Elaborando cotización o Abierta.
- Referencial → Elaborando cotización o Anulada.
- Elaborando cotización → Abierta o Anulada.
- Abierta → Enviada o Anulada.
- Enviada → Adjudicada, Perdida, Postergada o Anulada.
- Postergada → Abierta, Enviada o Anulada.
- Adjudicada, Perdida y Anulada son estados finales.

## Reglas financieras

Los montos CLP, USD, EUR y utilidad USD se ingresan manualmente y deben ser no negativos. El margen es un porcentaje entre 0 y 100. La plataforma no convierte monedas ni recalcula automáticamente utilidad o margen.

Cada cotización comercial utiliza una única moneda (`CLP`, `USD` o `EUR`). Por línea se calcula cantidad × precio unitario menos descuento y después se aplica el descuento global. La oferta muestra exclusivamente valores netos, sin agregar impuestos. Todos los cálculos monetarios se redondean a dos decimales en el servidor.

## Aprobación y emisión

El flujo documental es `borrador → en revisión → aprobada → enviada`. Un administrador puede aprobar o rechazar; el rechazo puede incluir un comentario. Cualquier modificación de datos o líneas crea una versión nueva, invalida la aprobación anterior y devuelve la cotización a borrador. Solo puede marcarse como enviada la misma versión que fue aprobada.

El PDF incluye cliente, versión, vigencia, moneda, descripción, productos y servicios, totales, condiciones de pago, notas, encabezado y numeración de páginas. Los borradores muestran una marca de agua. Los adjuntos admitidos son PDF, XLSX, DOCX, PNG y JPEG y requieren autorización sobre la cotización para descargarse.

## Notas de alcance

El sistema ya cubre creación, líneas comerciales, aprobación, versiones, adjuntos y PDF, pero todavía no envía correos ni se conecta con OneDrive, SharePoint, Power Automate, CRM o ERP. Para importar históricos con fórmulas, abrir/guardar primero el Excel original en Excel para garantizar que sus valores almacenados estén calculados.

Mejoras necesarias antes de producción corporativa: autenticación con Microsoft Entra ID y MFA, recuperación autoservicio y una política corporativa completa de contraseñas, limitación de acceso distribuida para despliegues con múltiples servidores, auditoría central inmutable, control de revisiones concurrentes, inspección antivirus de archivos y eliminación de cuentas controlada. Los controles actuales constituyen un prototipo funcional, no un sistema de seguridad empresarial completo.
