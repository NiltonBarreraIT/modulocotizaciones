FIELDS = [
    ("solicitud", "Fecha solicitud", "date"),
    ("fecha", "Fecha cotización", "date"),
    ("vendedor", "Vendedor", "text"),
    ("deteccion", "Detección de oportunidad", "text"),
    ("levantamiento", "Levantamiento en terreno", "text"),
    ("propuesta", "Elaboró propuesta técnica", "text"),
    ("fabrica", "Fábrica", "text"),
    ("area", "Área", "text"),
    ("rfq", "RFQ", "text"),
    ("cliente", "Cliente final", "text"),
    ("descripcion", "Descripción", "textarea"),
    ("clp", "Monto CLP", "number"),
    ("usd", "Monto USD", "number"),
    ("eur", "Monto EUR", "number"),
    ("margen", "% utilidad", "number"),
    ("utilidad_usd", "Utilidad US$", "number"),
    ("estado", "Estado", "select"),
]

STATUSES = [
    "Referencial",
    "Elaborando cotización",
    "Abierta",
    "Enviada",
    "Adjudicada",
    "Perdida",
    "Postergada",
    "Anulada",
]

INITIAL_STATUSES = ["Referencial", "Elaborando cotización", "Abierta"]

SELLER_COMMERCIAL_STATUSES = [
    "Abierta",
    "Referencial",
    "Postergada",
    "Adjudicada",
    "Perdida",
]

STATUS_TRANSITIONS = {
    "": INITIAL_STATUSES,
    "Referencial": ["Elaborando cotización", "Anulada"],
    "Elaborando cotización": ["Abierta", "Anulada"],
    "Abierta": ["Enviada", "Anulada"],
    "Enviada": ["Adjudicada", "Perdida", "Postergada", "Anulada"],
    "Postergada": ["Abierta", "Enviada", "Anulada"],
    "Adjudicada": [],
    "Perdida": [],
    "Anulada": [],
}

REQUEST_STATUS_LABELS = {
    "registrada": "Registrada",
    "pendiente_asignacion": "Pendiente de asignación",
    "en_cotizacion": "En cotización",
    "requiere_informacion": "Requiere información",
    "entregada": "Entregada",
    "en_correccion": "En corrección",
}
