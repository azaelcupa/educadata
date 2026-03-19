from django.core.management.base import BaseCommand
from pymongo import UpdateOne

from educadata.mongo import get_mongo_database
from .import_educadata import repair_mojibake


TARGET_COLLECTIONS = [
    "catalog_entidades",
    "catalog_municipios",
    "catalog_localidades",
    "catalog_opciones",
    "planteles",
    "matricula_plantel_ciclo",
    "indicadores_entidad_ciclo",
]


def repair_value(value):
    if isinstance(value, str):
        return repair_mojibake(value)
    if isinstance(value, list):
        return [repair_value(item) for item in value]
    if isinstance(value, dict):
        return {key: repair_value(item) for key, item in value.items()}
    return value


class Command(BaseCommand):
    help = "Repara textos con mojibake en las colecciones de Educadata."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Analiza cambios sin escribir en MongoDB.")

    def handle(self, *args, **options):
        db = get_mongo_database()
        dry_run = options["dry_run"]
        total_docs_changed = 0

        for collection_name in TARGET_COLLECTIONS:
            collection = db[collection_name]
            operations = []
            changed_docs = 0

            for doc in collection.find({}):
                doc_id = doc["_id"]
                repaired = repair_value(doc)
                if repaired != doc:
                    changed_docs += 1
                    if not dry_run:
                        repaired.pop("_id", None)
                        operations.append(UpdateOne({"_id": doc_id}, {"$set": repaired}))
                        if len(operations) >= 1000:
                            collection.bulk_write(operations, ordered=False)
                            operations = []

            if operations and not dry_run:
                collection.bulk_write(operations, ordered=False)

            total_docs_changed += changed_docs
            self.stdout.write(f"{collection_name}: {changed_docs} documentos corregibles")

        mode = "Analisis" if dry_run else "Reparacion"
        self.stdout.write(self.style.SUCCESS(f"{mode} completada. Documentos afectados: {total_docs_changed}"))
