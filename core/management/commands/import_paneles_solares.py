import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import PanelSolar


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _get(row: dict, *possible_keys, default=""):
    norm_map = {_norm(k): v for k, v in row.items()}
    for k in possible_keys:
        nk = _norm(k)
        if nk in norm_map:
            return (norm_map[nk] or "").strip()
    return default


def _to_int(val: str):
    val = (val or "").strip()
    if val == "":
        return None
    return int(float(val))  # por si viene "1.0"


def _to_float(val: str):
    val = (val or "").strip().replace(",", ".")
    if val == "":
        return None
    return float(val)


class Command(BaseCommand):
    help = "Importa paneles solares desde CSV hacia la tabla PanelSolar."

    def add_arguments(self, parser):
        # ✅ opcional: si no lo das, usa data/paneles_solares.csv
        parser.add_argument("csv_path", nargs="?", type=str, default="", help="Ruta al CSV (ej: data/paneles_solares.csv)")
        parser.add_argument("--clear", action="store_true", help="Borra la tabla antes de importar")

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        default_path = base_dir / "data" / "paneles_solares.csv"

        csv_arg = (options.get("csv_path") or "").strip()
        csv_path = (base_dir / csv_arg).resolve() if csv_arg else default_path.resolve()

        if not csv_path.exists():
            raise CommandError(f"No existe el archivo: {csv_path}")

        if options["clear"]:
            deleted, _ = PanelSolar.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Tabla PanelSolar limpiada. Registros borrados: {deleted}"))

        created = 0
        updated = 0
        skipped = 0

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise CommandError("El CSV no tiene encabezados (fila 1).")

            for i, row in enumerate(reader, start=2):
                id_modulo = _to_int(_get(row, "PK Id_modulo", "PK_Id_modulo", "id_modulo", "Id_modulo"))
                marca = _get(row, "Marca")
                modelo = _get(row, "Modelo")
                potencia = _to_float(_get(row, "Potencia"))
                voc = _to_float(_get(row, "Voc"))
                isc = _to_float(_get(row, "Isc"))
                vmp = _to_float(_get(row, "Vmp"))
                imp = _to_float(_get(row, "Imp"))

                if id_modulo is None:
                    skipped += 1
                    self.stdout.write(self.style.WARNING(f"Fila {i}: sin PK Id_modulo -> omitida"))
                    continue

                if not marca or not modelo:
                    skipped += 1
                    self.stdout.write(self.style.WARNING(f"Fila {i}: sin Marca/Modelo -> omitida"))
                    continue

                _, was_created = PanelSolar.objects.update_or_create(
                    id_modulo=id_modulo,
                    defaults={
                        "marca": marca,
                        "modelo": modelo,
                        "potencia": potencia,
                        "voc": voc,
                        "isc": isc,
                        "vmp": vmp,
                        "imp": imp,
                    },
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS("✅ Importación Paneles Solares terminada"))
        self.stdout.write(f"Creado: {created} | Actualizado: {updated} | Omitidos: {skipped}")