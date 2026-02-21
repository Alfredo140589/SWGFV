# =========================================================
# COMANDO: Importar CSV a tabla Irradiancia (según tu modelo real)
# Archivo: core/management/commands/import_irradiancia.py
#
# Uso:
#   python manage.py import_irradiancia data/irradiancia.csv
#   python manage.py import_irradiancia data/irradiancia.csv --clear
# =========================================================

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Irradiancia


def norm_key(s: str) -> str:
    """Normaliza headers del CSV para que coincidan con campos."""
    s = (s or "").strip().lower()
    # normalizaciones comunes
    s = s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    s = s.replace(" ", "_")
    s = s.replace("/", "_")
    s = s.replace("(", "").replace(")", "")
    s = s.replace("%", "")
    s = s.replace("__", "_")
    return s


def to_decimal(val, default=None):
    if val is None:
        return default
    s = str(val).strip()
    if s == "":
        return default
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


class Command(BaseCommand):
    help = "Importa el catálogo de irradiancia desde CSV. Upsert por ciudad+estado."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Ruta del CSV. Ej: data/irradiancia.csv")
        parser.add_argument("--clear", action="store_true", help="Borra la tabla antes de importar.")

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"]).resolve()
        if not csv_path.exists():
            raise CommandError(f"No existe el archivo: {csv_path}")

        if options["clear"]:
            deleted, _ = Irradiancia.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Tabla Irradiancia limpiada. Registros borrados: {deleted}"))

        # Campos reales existentes en tu modelo (por introspección)
        model_fields = {f.name for f in Irradiancia._meta.fields}

        created = 0
        updated = 0
        skipped = 0

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise CommandError("El CSV no tiene encabezados (headers). Revisa el archivo.")

            # Normaliza headers -> valores
            for raw_row in reader:
                row = {norm_key(k): v for k, v in raw_row.items()}

                # En tu modelo existen: ciudad, estado, region, tarifa, promedio, ene..dic, etc.
                ciudad = (row.get("ciudad") or "").strip()
                estado = (row.get("estado") or "").strip()

                if not ciudad:
                    skipped += 1
                    continue

                # Upsert por ciudad+estado (si no hay estado, igual intenta)
                qs = Irradiancia.objects.filter(ciudad__iexact=ciudad)
                if estado:
                    qs = qs.filter(estado__iexact=estado)
                obj = qs.first()

                # Construye kwargs sólo con campos que existan en el modelo
                kwargs = {}
                for key, value in row.items():
                    if key not in model_fields:
                        continue

                    # Convierte a decimal en campos numéricos típicos
                    if key in {"promedio", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"}:
                        kwargs[key] = to_decimal(value, default=Decimal("0"))
                    else:
                        kwargs[key] = (value or "").strip()

                # Asegura ciudad/estado aunque vengan vacíos en kwargs
                kwargs["ciudad"] = ciudad
                if "estado" in model_fields:
                    kwargs["estado"] = estado

                if obj:
                    for k, v in kwargs.items():
                        setattr(obj, k, v)
                    obj.save()
                    updated += 1
                else:
                    Irradiancia.objects.create(**kwargs)
                    created += 1

        self.stdout.write(self.style.SUCCESS("✅ Importación Irradiancia terminada"))
        self.stdout.write(f"Creado: {created} | Actualizado: {updated} | Omitidos: {skipped}")