from pathlib import Path
import zipfile

from django.core.management.base import BaseCommand, CommandError

from educadata.mongo import get_mongo_database

from .import_educadata import (
    iter_sheet_rows,
    normalize_text,
    parse_int,
    parse_shared_strings,
    parse_sheet_targets,
    parse_workbook_relationships,
)


class Command(BaseCommand):
    help = "Importa la población PL-SEP por ciclo (discapacidad y hablantes de lenguas indígenas) hacia MongoDB."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Ruta absoluta al archivo XLSX.")
        parser.add_argument("--dry-run", action="store_true", help="Analiza y resume sin escribir en MongoDB.")
        parser.add_argument("--drop", action="store_true", help="Elimina la colección destino antes de importar.")

    def handle(self, *args, **options):
        source_path = Path(options["path"]).expanduser()
        if not source_path.exists():
            raise CommandError(f"No existe el archivo: {source_path}")

        db = get_mongo_database()
        collection = db.plsep_poblacion_ciclo

        if options["drop"] and not options["dry_run"]:
            collection.drop()

        documents = {}

        with zipfile.ZipFile(source_path) as workbook:
            shared_strings = parse_shared_strings(workbook)
            rel_map = parse_workbook_relationships(workbook)
            sheets = parse_sheet_targets(workbook, rel_map)

            for _, sheet_path in sheets:
                rows = iter_sheet_rows(workbook, sheet_path, shared_strings)
                header_row = next(rows, None)
                if not header_row:
                    continue

                header_map = {value: col for col, value in header_row.items()}
                normalized_headers = {normalize_text(key): value for key, value in header_map.items()}
                if not {"sigla", "CICLO", "Total de Estudiantes"}.issubset(normalized_headers):
                    continue

                for row in rows:
                    sigla = normalize_text(row.get(normalized_headers["sigla"], ""))
                    ciclo = normalize_text(row.get(normalized_headers["CICLO"], ""))
                    if not sigla or not ciclo:
                        continue

                    documents[(sigla, ciclo)] = {
                        "sigla": sigla,
                        "ciclo": ciclo,
                        "total_estudiantes": parse_int(row.get(normalized_headers["Total de Estudiantes"], "")) or 0,
                        "total_nacionales": parse_int(row.get(normalized_headers.get("Total nacionales"), "")) or 0,
                        "extranjeros_hombres": parse_int(row.get(normalized_headers.get("Extranjeros Hombres"), "")) or 0,
                        "extranjeros_mujeres": parse_int(row.get(normalized_headers.get("Extranjeros Mujeres"), "")) or 0,
                        "extranjeros_total": parse_int(row.get(normalized_headers.get("Extranjeros Total"), "")) or 0,
                        "con_discapacidad": parse_int(row.get(normalized_headers.get("Estudiantes con Discapacidad"), "")) or 0,
                        "poblacion_indigena": parse_int(row.get(normalized_headers.get("Hablantes de lenguas indígenas"), "")) or 0,
                    }

        if not options["dry_run"] and documents:
            operations = []
            for document in documents.values():
                operations.append(
                    {
                        "filter": {"sigla": document["sigla"], "ciclo": document["ciclo"]},
                        "replacement": document,
                    }
                )
            for operation in operations:
                collection.replace_one(operation["filter"], operation["replacement"], upsert=True)
            collection.create_index([("sigla", 1), ("ciclo", 1)], unique=True)

        self.stdout.write(self.style.SUCCESS("Importacion PL-SEP analizada correctamente."))
        self.stdout.write(f"Archivo: {source_path}")
        self.stdout.write(f"Registros detectados: {len(documents)}")
        for document in sorted(documents.values(), key=lambda item: item["ciclo"]):
            self.stdout.write(
                f'{document["ciclo"]}: total={document["total_estudiantes"]}, '
                f'discapacidad={document["con_discapacidad"]}, indigena={document["poblacion_indigena"]}'
            )
