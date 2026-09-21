import unicodedata
from functools import wraps

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.views import LoginView
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
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
    "COAHUILA DE ZARAGOZA": "COAHUILA",
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


def auth_gate(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if settings.AUTH_DISABLED or request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL, REDIRECT_FIELD_NAME)

    return _wrapped


def normalize_name(value):
    base = unicodedata.normalize("NFKD", value or "")
    ascii_value = "".join(char for char in base if not unicodedata.combining(char))
    ascii_value = ascii_value.upper().replace("'", "").replace(".", "")
    return " ".join(ascii_value.split())


def percent_payload(doc):
    payload = {}
    for source_field, target_field in INDICATOR_FIELD_MAP.items():
        value = doc.get(source_field)
        payload[target_field] = round(indicator_to_percent(value, source_field), 1)
    return payload


def indicator_to_percent(value, source_field=None):
    if value is None:
        return 0
    numeric = float(value)
    if source_field == "tasa_absorcion":
        # Absorción puede venir como razón > 1 (ej. 1.024) o como porcentaje (ej. 103.4).
        return numeric * 100 if abs(numeric) <= 2 else numeric
    # Accept both stored formats:
    # - ratio (0.89) -> 89.0
    # - percent (89.0) -> 89.0
    return numeric * 100 if abs(numeric) <= 1 else numeric


def build_national_indicators(indicator_docs, entity_weights):
    national_indicators = {}
    for source_field, target_field in INDICATOR_FIELD_MAP.items():
        weighted_total = 0
        total_weight = 0
        values = []

        for doc in indicator_docs:
            raw_value = doc.get(source_field)
            if raw_value is None:
                continue
            value = indicator_to_percent(raw_value, source_field)

            values.append(value)
            weight = entity_weights.get(doc["entidad_clave"], 0)
            if weight > 0:
                weighted_total += value * weight
                total_weight += weight

        if total_weight > 0:
            national_value = weighted_total / total_weight
        else:
            national_value = sum(values) / len(values) if values else 0

        national_indicators[target_field] = round(national_value, 1)

    return national_indicators


def get_national_indicators(db, cycle, indicator_docs, entity_weights):
    national_doc = db.indicadores_nacionales_ciclo.find_one(
        {"ciclo": cycle},
        {"_id": 0, **{field: 1 for field in INDICATOR_FIELD_MAP}},
    )
    if national_doc:
        return percent_payload(national_doc)
    return build_national_indicators(indicator_docs, entity_weights)


def latest_cycle(db):
    cycles = sorted(db.catalog_opciones.distinct("clave", {"tipo": "ciclo"}))
    return cycles[-1] if cycles else ""




class DashboardLoginView(LoginView):
    redirect_authenticated_user = True
    template_name = "registration/login.html"

    def dispatch(self, request, *args, **kwargs):
        if settings.AUTH_DISABLED:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        if next_url:
            script_name = (settings.FORCE_SCRIPT_NAME or "").rstrip("/")
            if script_name and next_url.startswith("/") and not next_url.startswith(script_name):
                return f"{script_name}{next_url}"
            return next_url
        return reverse("home")


@auth_gate
def home(request):
    db = get_mongo_database()
    if request.user.is_authenticated:
        user_display_name = request.user.get_full_name() or request.user.get_username()
    else:
        user_display_name = "Invitado"
    context = {
        "project_name": "Educadata",
        "database_name": db.name,
        "default_cycle": latest_cycle(db),
        "user_display_name": user_display_name,
        "user_initial": (user_display_name.strip()[:1] or "U").upper(),
        "available_cycles": sorted(db.catalog_opciones.distinct("clave", {"tipo": "ciclo"})),
        "available_services": [
            {"code": code, "label": SERVICE_LABELS.get(code, code)}
            for code in sorted(db.catalog_opciones.distinct("clave", {"tipo": "sigla"}))
        ],
    }
    return render(request, "core/home.html", context)


@auth_gate
def map_data(request):
    db = get_mongo_database()
    cycle = request.GET.get("ciclo") or latest_cycle(db)
    plsep_population = db.plsep_poblacion_ciclo.find_one({"sigla": "PL-SEP", "ciclo": cycle}, {"_id": 0})

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
                        "docentes_m": {"$sum": {"$ifNull": ["$docentes_m", 0]}},
                        "docentes_h": {"$sum": {"$ifNull": ["$docentes_h", 0]}},
                        "planteles": {"$addToSet": "$plantel_cct"},
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
    entity_enrollment_totals = {}
    for row in service_rows:
        entity_key = row["_id"]["entidad_clave"]
        service_code = row["_id"]["sigla"]
        raw_enrollment = int(row.get("alumnos", 0) or 0)
        service_payload = {
            "code": service_code,
            "label": SERVICE_LABELS.get(service_code, service_code),
            "alumnos": raw_enrollment,
            "mujeres": int(row.get("mujeres", 0) or 0),
            "hombres": int(row.get("hombres", 0) or 0),
            "docentes": int(row.get("docentes", 0) or 0),
            "docentes_m": int(row.get("docentes_m", 0) or 0),
            "docentes_h": int(row.get("docentes_h", 0) or 0),
            "escuelas": 1 if service_code == "PL-SEP" and raw_enrollment > 0 else len(row.get("planteles", []) or []),
            "con_discapacidad": int((plsep_population or {}).get("con_discapacidad", 0) or 0) if service_code == "PL-SEP" else 0,
            "poblacion_indigena": int((plsep_population or {}).get("poblacion_indigena", 0) or 0) if service_code == "PL-SEP" else 0,
            "extranjeros_hombres": int((plsep_population or {}).get("extranjeros_hombres", 0) or 0) if service_code == "PL-SEP" else 0,
            "extranjeros_mujeres": int((plsep_population or {}).get("extranjeros_mujeres", 0) or 0) if service_code == "PL-SEP" else 0,
            "extranjeros_total": int((plsep_population or {}).get("extranjeros_total", 0) or 0) if service_code == "PL-SEP" else 0,
            "total_estudiantes": int((plsep_population or {}).get("total_estudiantes", 0) or 0) if service_code == "PL-SEP" else 0,
            "total_nacionales": int((plsep_population or {}).get("total_nacionales", 0) or 0) if service_code == "PL-SEP" else 0,
            "atributos_plsep_nacionales": service_code == "PL-SEP" and bool(plsep_population),
        }
        entity_service_totals.setdefault(entity_key, {})[service_code] = service_payload
        entity_enrollment_totals[entity_key] = entity_enrollment_totals.get(entity_key, 0) + service_payload["alumnos"]

        totals = service_totals.setdefault(
            service_code,
            {
                "code": service_code,
                "label": SERVICE_LABELS.get(service_code, service_code),
                "alumnos": 0,
                "mujeres": 0,
                "hombres": 0,
                "docentes": 0,
                "docentes_m": 0,
                "docentes_h": 0,
                "escuelas": 0,
                "con_discapacidad": 0,
                "poblacion_indigena": 0,
                "extranjeros_hombres": 0,
                "extranjeros_mujeres": 0,
                "extranjeros_total": 0,
                "total_estudiantes": 0,
                "total_nacionales": 0,
            },
        )
        if service_code == "PL-SEP":
            for field in ("alumnos", "mujeres", "hombres"):
                totals[field] += service_payload[field]
            if totals["docentes"] == 0:
                totals["docentes"] = service_payload["docentes"]
            if totals["docentes_m"] == 0:
                totals["docentes_m"] = service_payload["docentes_m"]
            if totals["docentes_h"] == 0:
                totals["docentes_h"] = service_payload["docentes_h"]
            if service_payload["alumnos"] > 0:
                totals["escuelas"] = 1
            totals["con_discapacidad"] = int((plsep_population or {}).get("con_discapacidad", 0) or 0)
            totals["poblacion_indigena"] = int((plsep_population or {}).get("poblacion_indigena", 0) or 0)
            totals["extranjeros_hombres"] = int((plsep_population or {}).get("extranjeros_hombres", 0) or 0)
            totals["extranjeros_mujeres"] = int((plsep_population or {}).get("extranjeros_mujeres", 0) or 0)
            totals["extranjeros_total"] = int((plsep_population or {}).get("extranjeros_total", 0) or 0)
            totals["total_estudiantes"] = int((plsep_population or {}).get("total_estudiantes", 0) or 0)
            totals["total_nacionales"] = int((plsep_population or {}).get("total_nacionales", 0) or 0)
            totals["atributos_plsep_nacionales"] = bool(plsep_population)
        else:
            for field in ("alumnos", "mujeres", "hombres", "docentes", "docentes_m", "docentes_h", "escuelas"):
                totals[field] += service_payload[field]

    payload_entities = {}
    for entity_key, entity in entity_lookup.items():
        payload_entities[entity["map_key"]] = {
            "entity_key": entity_key,
            "name": entity["name"],
            "indicators": indicator_docs_by_entity.get(entity_key, {}),
            "services": entity_service_totals.get(entity_key, {}),
        }

    national_indicators = get_national_indicators(db, cycle, indicator_docs, entity_enrollment_totals)

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
