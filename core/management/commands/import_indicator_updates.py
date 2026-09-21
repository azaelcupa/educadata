import re
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from pymongo import UpdateOne

from core.management.commands.import_educadata import (
    INDICATOR_FIELDS,
    iter_sheet_rows,
    normalize_text,
    parse_float,
    parse_shared_strings,
    parse_sheet_targets,
    parse_workbook_relationships,
)
from educadata.mongo import get_mongo_database

VALID_CYCLE_RE = re.compile(r"^\d{4}-\d{4}$")
ENTITY_SHEET_HEADERS = {
    "Cobertura": "Cobertura Total",
}


class Command(BaseCommand):
    help = "Importa indicadores nacionales y por entidad desde un XLSX de actualizacion."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Ruta absoluta al archivo XLSX.")
        parser.add_argument("--dry-run", action="store_true", help="Analiza y resume sin escribir en MongoDB.")

    def handle(self, *args, **options):
        source_path = Path(options["path"]).expanduser()
        if not source_path.exists():
            raise CommandError(f"No existe el archivo: {source_path}")

        db = get_mongo_database()
        entity_indicators = {}
        national_indicators = {}
        cycles = set()

        with zipfile.ZipFile(source_path) as workbook:
            shared_strings = parse_shared_strings(workbook)
            rel_map = parse_workbook_relationships(workbook)
            sheets = dict(parse_sheet_targets(workbook, rel_map))

            self.load_national_sheet(
                workbook,
                sheets.get("Indicadores Nacionales"),
                shared_strings,
                national_indicators,
                cycles,
            )
            self.load_entity_sheet(
                workbook,
                sheets.get("Indicadores x entidad"),
                shared_strings,
                entity_indicators,
                cycles,
            )

        if not options["dry_run"]:
            self.bulk_upsert(
                db.indicadores_nacionales_ciclo,
                ["ciclo"],
                national_indicators.values(),
            )
            self.bulk_upsert(
                db.indicadores_entidad_ciclo,
                ["entidad_clave", "ciclo"],
                entity_indicators.values(),
            )
            self.bulk_upsert(
                db.catalog_opciones,
                ["tipo", "clave"],
                (
                    {"tipo": "ciclo", "clave": cycle, "valor": cycle}
                    for cycle in sorted(cycles)
                ),
            )
            db.indicadores_nacionales_ciclo.create_index([("ciclo", 1)], unique=True)
            db.indicadores_entidad_ciclo.create_index([("entidad_clave", 1), ("ciclo", 1)], unique=True)
            db.catalog_opciones.create_index([("tipo", 1), ("clave", 1)], unique=True)

        self.stdout.write(self.style.SUCCESS("Importacion de indicadores analizada correctamente."))
        self.stdout.write(f"Archivo: {source_path}")
        self.stdout.write(f"Ciclos detectados: {len(cycles)}")
        self.stdout.write(f"Indicadores nacionales: {len(national_indicators)}")
        self.stdout.write(f"Indicadores por entidad: {len(entity_indicators)}")

    def load_national_sheet(self, workbook, sheet_path, shared_strings, target, cycles):
        if not sheet_path:
            raise CommandError("No se encontro la hoja 'Indicadores Nacionales'.")

        rows = iter_sheet_rows(workbook, sheet_path, shared_strings)
        header_row = None
        header_map = None
        for row in rows:
            values = {col: normalize_text(value) for col, value in row.items()}
            if "CICLO" in values.values():
                header_row = values
                header_map = {value: col for col, value in header_row.items()}
                break
        if not header_map:
            raise CommandError("No se encontro el encabezado de la hoja 'Indicadores Nacionales'.")

        for row in rows:
            cycle = normalize_text(row.get(header_map["CICLO"], ""))
            if not VALID_CYCLE_RE.match(cycle):
                continue
            cycles.add(cycle)
            doc = {"ciclo": cycle}
            for source_name, target_name in INDICATOR_FIELDS.items():
                normalized_source = normalize_text(source_name)
                source_key = f"{normalized_source} nacional" if normalized_source != "Cobertura" else "Cobertura nacional"
                doc[target_name] = parse_float(row.get(header_map[source_key], ""))
            target[cycle] = doc

    def load_entity_sheet(self, workbook, sheet_path, shared_strings, target, cycles):
        if not sheet_path:
            raise CommandError("No se encontro la hoja 'Indicadores x entidad'.")

        rows = iter_sheet_rows(workbook, sheet_path, shared_strings)
        header_row = next(rows, None)
        if not header_row:
            raise CommandError("La hoja 'Indicadores x entidad' esta vacia.")
        header_map = {normalize_text(value): col for col, value in header_row.items()}

        for row in rows:
            entity_key = normalize_text(row.get(header_map["ENTIDAD"], ""))
            cycle = normalize_text(row.get(header_map["CICLO"], ""))
            if not entity_key or not VALID_CYCLE_RE.match(cycle):
                continue

            cycles.add(cycle)
            doc = {
                "entidad_clave": entity_key,
                "ciclo": cycle,
            }
            for source_name, target_name in INDICATOR_FIELDS.items():
                source_key = ENTITY_SHEET_HEADERS.get(source_name, source_name)
                doc[target_name] = parse_float(row.get(header_map[normalize_text(source_key)], ""))
            target[(entity_key, cycle)] = doc

    def bulk_upsert(self, collection, keys, documents):
        docs = list(documents)
        if not docs:
            return
        operations = []
        for doc in docs:
            selector = {key: doc.get(key) for key in keys}
            operations.append(UpdateOne(selector, {"$set": doc}, upsert=True))
        collection.bulk_write(operations, ordered=False)
