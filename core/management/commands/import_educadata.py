import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from pymongo import UpdateOne

from educadata.mongo import get_mongo_database

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

INDICATOR_FIELDS = {
    "Cobertura": "cobertura",
    "Tasa de absorción": "tasa_absorcion",
    "Tasa de abandono escolar": "tasa_abandono",
    "Tasa de reprobación ": "tasa_reprobacion",
    "Tasa neta de escolarización ": "tasa_neta_escolarizacion",
    "Tasa de terminación ": "tasa_terminacion",
}


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = repair_mojibake(text)
    return " ".join(text.split())


def repair_mojibake(text):
    if not text:
        return text

    candidates = [text]
    repaired = decode_mojibake_bytes(text)
    if repaired:
        candidates.append(repaired)

    def badness(value):
        penalty_tokens = ("Ã", "Â", "Ð", "�", "\x81", "\x8d", "\x8f", "\x90", "\x9d")
        score = sum(value.count(token) for token in penalty_tokens)
        score += sum(1 for char in value if ord(char) < 32 and char not in ("\n", "\r", "\t"))
        return score

    return min(candidates, key=badness)


def decode_mojibake_bytes(text):
    raw = bytearray()
    for char in text:
        codepoint = ord(char)
        if codepoint < 256:
            raw.append(codepoint)
            continue
        try:
            raw.extend(char.encode("cp1252"))
        except UnicodeEncodeError:
            return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_int(value):
    text = normalize_text(value)
    if text == "":
        return None
    return int(float(text))


def parse_float(value):
    text = normalize_text(value)
    if text == "":
        return None
    return float(text)


def col_letters(cell_ref):
    letters = []
    for char in cell_ref:
        if char.isalpha():
            letters.append(char)
        else:
            break
    return "".join(letters)


def parse_shared_strings(workbook):
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for si in root.findall(f"{{{MAIN_NS}}}si"):
        text = "".join(node.text or "" for node in si.iterfind(f".//{{{MAIN_NS}}}t"))
        values.append(text)
    return values


def parse_workbook_relationships(workbook):
    root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    relationships = {}
    for rel in root.findall(f"{{{PKG_NS}}}Relationship"):
        relationships[rel.attrib["Id"]] = rel.attrib["Target"]
    return relationships


def parse_sheet_targets(workbook, rel_map):
    root = ET.fromstring(workbook.read("xl/workbook.xml"))
    sheets = []
    for sheet in root.findall(f".//{{{MAIN_NS}}}sheet"):
        target = rel_map[sheet.attrib[f"{{{REL_NS}}}id"]]
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        sheets.append((sheet.attrib["name"], target))
    return sheets


def iter_sheet_rows(workbook, sheet_path, shared_strings):
    root = ET.fromstring(workbook.read(sheet_path))
    for row in root.iterfind(f".//{{{MAIN_NS}}}row"):
        data = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = cell.attrib.get("r", "")
            col = col_letters(ref)
            cell_type = cell.attrib.get("t")
            value_node = cell.find(f"{{{MAIN_NS}}}v")
            if cell_type == "inlineStr":
                inline = cell.find(f"{{{MAIN_NS}}}is")
                value = "".join(node.text or "" for node in inline.iterfind(f".//{{{MAIN_NS}}}t")) if inline is not None else ""
            elif value_node is None:
                value = ""
            else:
                raw = value_node.text or ""
                if cell_type == "s":
                    value = shared_strings[int(raw)] if raw.isdigit() else raw
                else:
                    value = raw
            data[col] = value
        yield data


class Command(BaseCommand):
    help = "Importa la base Educadata desde un archivo XLSX hacia MongoDB."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Ruta absoluta al archivo XLSX.")
        parser.add_argument("--dry-run", action="store_true", help="Analiza y resume sin escribir en MongoDB.")
        parser.add_argument("--drop", action="store_true", help="Elimina las colecciones destino antes de importar.")

    def handle(self, *args, **options):
        source_path = Path(options["path"]).expanduser()
        if not source_path.exists():
            raise CommandError(f"No existe el archivo: {source_path}")

        db = get_mongo_database()
        collections = [
            "catalog_entidades",
            "catalog_municipios",
            "catalog_localidades",
            "catalog_opciones",
            "planteles",
            "matricula_plantel_ciclo",
            "indicadores_entidad_ciclo",
        ]

        if options["drop"] and not options["dry_run"]:
            for name in collections:
                db[name].drop()

        counters = {name: 0 for name in collections}
        option_sets = {
            "control": set(),
            "modalidad": set(),
            "subcontrol": set(),
            "nivel": set(),
            "subnivel": set(),
            "sigla": set(),
            "ciclo": set(),
        }
        entity_indicator_seen = set()
        entities = {}
        municipalities = {}
        localities = {}
        planteles = {}
        matricula = {}
        indicators = {}

        with zipfile.ZipFile(source_path) as workbook:
            shared_strings = parse_shared_strings(workbook)
            rel_map = parse_workbook_relationships(workbook)
            sheets = parse_sheet_targets(workbook, rel_map)

            for sheet_name, sheet_path in sheets:
                if sheet_name == "Diccionario":
                    continue

                rows = iter_sheet_rows(workbook, sheet_path, shared_strings)
                header_row = next(rows, None)
                if not header_row:
                    continue

                header_map = {value: col for col, value in header_row.items()}

                for row in rows:
                    entity_key = normalize_text(row.get(header_map["ENTIDAD"], ""))
                    entity_name = normalize_text(row.get(header_map["C_NOM_ENT"], ""))
                    municipality_key = normalize_text(row.get(header_map["CV_MUN"], ""))
                    municipality_name = normalize_text(row.get(header_map["C_NOM_MUN"], ""))
                    locality_key = normalize_text(row.get(header_map["CV_LOC"], ""))
                    locality_name = normalize_text(row.get(header_map["C_NOM_LOC"], ""))
                    plantel_cct = normalize_text(row.get(header_map["PLANTEL"], ""))
                    school_key = normalize_text(row.get(header_map["ESCUELA"], ""))
                    school_name = normalize_text(row.get(header_map["NOMESCUELA"], ""))
                    control = normalize_text(row.get(header_map["CONTROL"], ""))
                    modalidad = normalize_text(row.get(header_map["MODALIDAD"], ""))
                    subcontrol = normalize_text(row.get(header_map["C_SUBCONTROL"], ""))
                    nivel = normalize_text(row.get(header_map["NIVEL"], ""))
                    subnivel = normalize_text(row.get(header_map["SUBNIVEL"], ""))
                    sigla = normalize_text(row.get(header_map["sigla"], ""))
                    ciclo = normalize_text(row.get(header_map["CICLO"], ""))

                    option_sets["control"].add(control)
                    option_sets["modalidad"].add(modalidad)
                    option_sets["subcontrol"].add(subcontrol)
                    option_sets["nivel"].add(nivel)
                    option_sets["subnivel"].add(subnivel)
                    option_sets["sigla"].add(sigla)
                    option_sets["ciclo"].add(ciclo)

                    if entity_key:
                        entities[entity_key] = {"clave": entity_key, "nombre": entity_name}

                    if municipality_key:
                        municipalities[(entity_key, municipality_key)] = {
                            "entidad_clave": entity_key,
                            "clave": municipality_key,
                            "nombre": municipality_name,
                        }

                    if locality_key:
                        localities[(entity_key, municipality_key, locality_key)] = {
                            "entidad_clave": entity_key,
                            "municipio_clave": municipality_key,
                            "clave": locality_key,
                            "nombre": locality_name,
                        }

                    plantel_key = plantel_cct or school_key
                    planteles[plantel_key] = {
                        "plantel_cct": plantel_key,
                        "escuela_clave": school_key,
                        "nombre": school_name,
                        "entidad_clave": entity_key,
                        "municipio_clave": municipality_key or None,
                        "localidad_clave": locality_key or None,
                        "control": control,
                        "modalidad": modalidad,
                        "subcontrol": subcontrol,
                        "nivel": nivel,
                        "subnivel": subnivel,
                        "sigla": sigla,
                    }

                    matricula[(plantel_key, ciclo)] = {
                        "plantel_cct": plantel_key,
                        "ciclo": ciclo,
                        "escuelas": parse_int(row.get(header_map["ESCUELAS"], "")),
                        "alumnos": parse_int(row.get(header_map["ALUMNOS"], "")),
                        "mujeres": parse_int(row.get(header_map["MUJERES"], "")),
                        "hombres": parse_int(row.get(header_map["HOMBRES"], "")),
                        "docentes": parse_int(row.get(header_map["DOCENTES"], "")),
                        "docentes_m": parse_int(row.get(header_map["DOCENTES_M"], "")),
                        "docentes_h": parse_int(row.get(header_map["DOCENTES_H"], "")),
                    }

                    indicator_key = (entity_key, ciclo)
                    if indicator_key not in entity_indicator_seen:
                        entity_indicator_seen.add(indicator_key)
                        indicator_doc = {
                            "entidad_clave": entity_key,
                            "ciclo": ciclo,
                        }
                        for source_name, target_name in INDICATOR_FIELDS.items():
                            indicator_doc[target_name] = parse_float(row.get(header_map[source_name], ""))

                        indicators[indicator_key] = indicator_doc

        if not options["dry_run"]:
            self.bulk_upsert(db.catalog_entidades, "clave", entities.values())
            counters["catalog_entidades"] = len(entities)

            self.bulk_upsert(
                db.catalog_municipios,
                ["entidad_clave", "clave"],
                municipalities.values(),
            )
            counters["catalog_municipios"] = len(municipalities)

            self.bulk_upsert(
                db.catalog_localidades,
                ["entidad_clave", "municipio_clave", "clave"],
                localities.values(),
            )
            counters["catalog_localidades"] = len(localities)

            self.bulk_upsert(db.planteles, "plantel_cct", planteles.values())
            counters["planteles"] = len(planteles)

            self.bulk_upsert(
                db.matricula_plantel_ciclo,
                ["plantel_cct", "ciclo"],
                matricula.values(),
            )
            counters["matricula_plantel_ciclo"] = len(matricula)

            self.bulk_upsert(
                db.indicadores_entidad_ciclo,
                ["entidad_clave", "ciclo"],
                indicators.values(),
            )
            counters["indicadores_entidad_ciclo"] = len(indicators)

            option_ops = []
            for option_type, values in option_sets.items():
                for value in values:
                    if not value:
                        continue
                    option_ops.append(
                        UpdateOne(
                            {"tipo": option_type, "clave": value},
                            {"$set": {"tipo": option_type, "clave": value, "valor": value}},
                            upsert=True,
                        )
                    )
            if option_ops:
                db.catalog_opciones.bulk_write(option_ops, ordered=False)
                counters["catalog_opciones"] = len(option_ops)
            self.ensure_indexes(db)

        self.stdout.write(self.style.SUCCESS("Importacion analizada correctamente."))
        self.stdout.write(f"Archivo: {source_path}")
        self.stdout.write(f"Ciclos procesados: {len(option_sets['ciclo'])}")
        self.stdout.write(f"Indicadores entidad-ciclo detectados: {len(entity_indicator_seen)}")
        for name, count in counters.items():
            self.stdout.write(f"{name}: {count}")

    def bulk_upsert(self, collection, keys, documents):
        docs = list(documents)
        if not docs:
            return
        if isinstance(keys, str):
            keys = [keys]
        operations = []
        for doc in docs:
            selector = {key: doc.get(key) for key in keys}
            operations.append(UpdateOne(selector, {"$set": doc}, upsert=True))
        collection.bulk_write(operations, ordered=False)

    def ensure_indexes(self, db):
        db.catalog_entidades.create_index("clave", unique=True)
        db.catalog_municipios.create_index([("entidad_clave", 1), ("clave", 1)], unique=True)
        db.catalog_localidades.create_index(
            [("entidad_clave", 1), ("municipio_clave", 1), ("clave", 1)],
            unique=True,
        )
        db.catalog_opciones.create_index([("tipo", 1), ("clave", 1)], unique=True)
        db.planteles.create_index("plantel_cct", unique=True)
        db.planteles.create_index("escuela_clave", unique=True, sparse=True)
        db.matricula_plantel_ciclo.create_index([("plantel_cct", 1), ("ciclo", 1)], unique=True)
        db.indicadores_entidad_ciclo.create_index([("entidad_clave", 1), ("ciclo", 1)], unique=True)
