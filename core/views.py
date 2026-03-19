import unicodedata

from django.http import JsonResponse
from django.shortcuts import render

from educadata.mongo import get_mongo_database

SERVICE_LABELS = {
    "CAED": "Educacion inclusiva",
    "CEB": "Bachillerato general",
    "EMSAD": "Educacion media superior a distancia",
    "PAE": "Preparatoria abierta estatal",
    "PAF": "Preparatoria abierta federal",
    "PL-SEP": "Prepa en linea SEP",
    "TBC": "Telebachillerato comunitario",
}

ENTITY_ALIASES = {
    "ESTADO DE MEXICO": "MEXICO",
    "MICHOACAN DE OCAMPO": "MICHOACAN",
    "NUEVO LEON": "NUEVO LEON",
    "QUERETARO": "QUERETARO",
    "SAN LUIS POTOSI": "SAN LUIS POTOSI",
    "VERACRUZ DE IGNACIO DE LA LLAVE": "VERACRUZ",
    "YUCATAN": "YUCATAN",
    "CIUDAD DE MEXICO": "CIUDAD DE MEXICO",
}

INDICATOR_FIELD_MAP = {
    "cobertura": "cobertura",
    "tasa_absorcion": "absorcion",
    "tasa_abandono": "abandono",
    "tasa_reprobacion": "reprobacion",
    "tasa_neta_escolarizacion": "escolarizacion",
    "tasa_terminacion": "terminacion",
}


def normalize_name(value):
    base = unicodedata.normalize("NFKD", value or "")
    ascii_value = "".join(char for char in base if not unicodedata.combining(char))
    ascii_value = ascii_value.upper().replace("'", "").replace(".", "")
    return " ".join(ascii_value.split())


def percent_payload(doc):
    payload = {}
    for source_field, target_field in INDICATOR_FIELD_MAP.items():
        value = doc.get(source_field)
        payload[target_field] = round((value or 0) * 100, 1)
    return payload


def latest_cycle(db):
    cycles = sorted(db.catalog_opciones.distinct("clave", {"tipo": "ciclo"}))
    return cycles[-1] if cycles else ""


def home(request):
    db = get_mongo_database()
    context = {
        "project_name": "Educadata",
        "database_name": db.name,
        "default_cycle": latest_cycle(db),
        "available_cycles": sorted(db.catalog_opciones.distinct("clave", {"tipo": "ciclo"})),
        "available_services": [
            {"code": code, "label": SERVICE_LABELS.get(code, code)}
            for code in sorted(db.catalog_opciones.distinct("clave", {"tipo": "sigla"}))
        ],
    }
    return render(request, "core/home.html", context)


def map_data(request):
    db = get_mongo_database()
    cycle = request.GET.get("ciclo") or latest_cycle(db)

    entities = list(db.catalog_entidades.find({}, {"_id": 0}).sort("clave", 1))
    indicator_docs = list(
        db.indicadores_entidad_ciclo.find({"ciclo": cycle}, {"_id": 0, "entidad_clave": 1, **{field: 1 for field in INDICATOR_FIELD_MAP}})
    )
    service_rows = list(
        db.matricula_plantel_ciclo.aggregate(
            [
                {"$match": {"ciclo": cycle}},
                {
                    "$lookup": {
                        "from": "planteles",
                        "localField": "plantel_cct",
                        "foreignField": "plantel_cct",
                        "as": "plantel",
                    }
                },
                {"$unwind": "$plantel"},
                {
                    "$group": {
                        "_id": {
                            "entidad_clave": "$plantel.entidad_clave",
                            "sigla": "$plantel.sigla",
                        },
                        "alumnos": {"$sum": {"$ifNull": ["$alumnos", 0]}},
                        "mujeres": {"$sum": {"$ifNull": ["$mujeres", 0]}},
                        "hombres": {"$sum": {"$ifNull": ["$hombres", 0]}},
                        "docentes": {"$sum": {"$ifNull": ["$docentes", 0]}},
                        "escuelas": {"$sum": {"$ifNull": ["$escuelas", 0]}},
                    }
                },
                {"$sort": {"_id.entidad_clave": 1, "_id.sigla": 1}},
            ]
        )
    )

    entity_lookup = {}
    for entity in entities:
        normalized = normalize_name(entity["nombre"])
        alias = ENTITY_ALIASES.get(normalized, normalized)
        entity_lookup[entity["clave"]] = {
            "key": entity["clave"],
            "name": entity["nombre"],
            "map_key": alias,
            "indicators": {},
            "services": {},
        }

    indicator_docs_by_entity = {}
    for doc in indicator_docs:
        indicator_docs_by_entity[doc["entidad_clave"]] = percent_payload(doc)

    service_totals = {}
    entity_service_totals = {}
    for row in service_rows:
        entity_key = row["_id"]["entidad_clave"]
        service_code = row["_id"]["sigla"]
        service_payload = {
            "code": service_code,
            "label": SERVICE_LABELS.get(service_code, service_code),
            "alumnos": int(row.get("alumnos", 0) or 0),
            "mujeres": int(row.get("mujeres", 0) or 0),
            "hombres": int(row.get("hombres", 0) or 0),
            "docentes": int(row.get("docentes", 0) or 0),
            "escuelas": int(row.get("escuelas", 0) or 0),
        }
        entity_service_totals.setdefault(entity_key, {})[service_code] = service_payload
        totals = service_totals.setdefault(
            service_code,
            {
                "code": service_code,
                "label": SERVICE_LABELS.get(service_code, service_code),
                "alumnos": 0,
                "mujeres": 0,
                "hombres": 0,
                "docentes": 0,
                "escuelas": 0,
            },
        )
        for field in ("alumnos", "mujeres", "hombres", "docentes", "escuelas"):
            totals[field] += service_payload[field]

    payload_entities = {}
    for entity_key, entity in entity_lookup.items():
        payload_entities[entity["map_key"]] = {
            "entity_key": entity_key,
            "name": entity["name"],
            "indicators": indicator_docs_by_entity.get(entity_key, {}),
            "services": entity_service_totals.get(entity_key, {}),
        }

    national_indicator_source = {}
    for source_field in INDICATOR_FIELD_MAP:
        values = [doc.get(source_field) for doc in indicator_docs if doc.get(source_field) is not None]
        national_indicator_source[source_field] = round((sum(values) / len(values) if values else 0) * 100, 1)
    national_indicators = {
        target_field: national_indicator_source[source_field]
        for source_field, target_field in INDICATOR_FIELD_MAP.items()
    }

    return JsonResponse(
        {
            "cycle": cycle,
            "available_cycles": sorted(db.catalog_opciones.distinct("clave", {"tipo": "ciclo"})),
            "available_services": [
                {"code": code, "label": SERVICE_LABELS.get(code, code)}
                for code in sorted(db.catalog_opciones.distinct("clave", {"tipo": "sigla"}))
            ],
            "entities": payload_entities,
            "national_indicators": national_indicators,
            "national_services": service_totals,
        }
    )
