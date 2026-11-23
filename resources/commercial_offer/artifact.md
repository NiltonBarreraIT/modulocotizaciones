# Contrato de fidelidad del formato de oferta comercial ITS

## Referencia

- Archivo fuente retenido: `C:\Users\nbarr\Downloads\ITS-3871-EC-CI-SQM-Rev.0-Oferta Comercial.docx`
- Copia operativa: `resources/commercial_offer/formato_oferta_comercial_ITS.docx`
- SHA-256 de ambos archivos: `200E8C0E9FBD30C9666ADEFFF65A2562BF9D2E484EBECEDBCD4417E8663C646A`
- Extensión visual verificada: 4 páginas.
- Secciones OOXML: 6.
- Render de evidencia: `C:\Users\nbarr\AppData\Local\Temp\its-phase6-template\reference-render`.
- Evidencia de estilos: `C:\Users\nbarr\AppData\Local\Temp\its-phase6-template\style-evidence.json`.

## Sistema de página

- Tamaño Carta de 8,50 × 11,00 pulgadas, orientación vertical, en las seis secciones.
- Márgenes: izquierdo 0,69 pulgadas, derecho 0,00, superior 0,35 y fondo 0,76.
- Distancia de encabezado 0,00 y pie 0,631 pulgadas; sin páginas pares/impares diferenciadas.
- Secciones 2, 3 y 6 comienzan en página nueva; secciones 1, 4 y 5 son continuas.
- Los pies posteriores están vinculados al anterior; el pie de la primera sección es independiente.

## Identidad visual y tipografía

- Conservar el logotipo azul ITS, lema, marca de agua gris, franja negra del pie, iconos, QR y datos de contacto.
- Conservar las ocho imágenes flotantes y sus anclajes. `image1.png` corresponde al logotipo, `image8.png` a la marca de agua y `image9.png` a la imagen referencial del producto.
- Familias observadas: Montserrat, Calibri y Arial. Los componentes de tabla usan además el estilo `Table Paragraph` basado en Century Gothic.
- Título principal comercial: Heading 1, 18 pt, negrita, centrado y subrayado según el elemento fuente.
- Encabezados de condiciones: Heading 2, 13 pt, negrita.
- Etiquetas y subtítulos: Heading 3, 12 pt, negrita.
- El documento contiene formato directo deliberado en 338 fragmentos y 133 párrafos; no debe normalizarse.

## Componentes y tablas

- Página 1: identidad corporativa, referencia de cotización, cliente/RUT/atención, tabla de metadatos de oferta, carta introductoria, alcance, plazo, total neto, pago y lugar de entrega.
- Página 2: título `DETALLE COMERCIAL`, marca de agua e inventario comercial.
- Tabla comercial: 5 columnas de aproximadamente 0,43; 0,54; 3,66; 1,18 y 1,11 pulgadas. Contiene cabecera, líneas de producto/servicio y total neto.
- Página 3: condiciones comerciales, moneda/validez, recepción de orden de compra, tabla bancaria de 2 columnas (2,22 y 4,71 pulgadas) y tabla de lugar de entrega (2,21 y 4,71 pulgadas).
- Página 4: continuación de entrega, cancelaciones/devoluciones y garantía.
- No existen controles de contenido ni campos Word. La edición se realiza sobre texto, celdas e imágenes existentes.

## Mapa de campos editables

- Referencia: número de oferta, fecha de elaboración, validez y revisión.
- Cliente: razón social, RUT y persona de atención.
- Responsables: elaborado por, contacto comercial, correo y teléfono.
- Resumen: división, alcance, plazo de entrega, total neto, moneda, términos de pago y lugar de entrega.
- Detalle comercial: número de línea, cantidad, descripción/especificaciones, imagen referencial opcional, valor unitario, valor total y total neto.
- Condiciones variables: moneda y vigencia; datos bancarios y condiciones corporativas se preservan salvo instrucción empresarial expresa.
- Las condiciones de cancelación, devolución y garantía se preservan sin cambios.

## Capacidad y flujo

- La plantilla operativa se descarga sin modificaciones desde la plataforma para ser completada en Word.
- El cotizador debe mantener las cuatro páginas, estilos, imágenes corporativas y condiciones; puede ampliar las líneas comerciales respetando la tabla fuente.
- Si el contenido adicional cambia la paginación, debe revisarse manualmente antes de entregar.
- La plataforma acepta como oferta final únicamente `.docx`; no interpreta el contenido ni sustituye condiciones automáticamente.

## Inventario protegido

- Preservar todos los elementos de `word/media`, el pie `word/footer1.xml` y su relación, `styles.xml`, `numbering.xml`, `theme1.xml`, `fontTable.xml`, `settings.xml`, footnotes/endnotes y los tres grupos `customXml`.
- Preservar todas las relaciones y tipos de contenido. No convertir la plantilla mediante LibreOffice ni reconstruirla desde un documento vacío.
- La copia operativa debe seguir coincidiendo byte por byte con el SHA-256 de referencia mientras se distribuya como formato oficial.

## Puertas de fidelidad

- Comprobar SHA-256 antes de publicar o descargar la plantilla oficial.
- Renderizar e inspeccionar las cuatro páginas después de cualquier actualización autorizada del formato.
- Verificar logotipo, marca de agua, tabla comercial, tablas de condiciones, franja de pie, QR y paginación.
- Rechazar cualquier cambio no explicado en elementos protegidos o geometría de página.
