import csv
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from core.models import Conductor


class Command(BaseCommand):
    help = "Importa/actualiza la tabla 'conductores' desde SWGFV/data/conductores.csv"

    def handle(self, *args, **options):
        base_dir = getattr(settings, "BASE_DIR", None)
        if not base_dir:
            self.stderr.write("❌ No se encontró BASE_DIR en settings.")
            return

        csv_path = os.path.join(base_dir, "data", "conductores.csv")
        if not os.path.exists(csv_path):
            self.stderr.write(f"❌ No existe el archivo: {csv_path}")
            return

        updated = 0
        created = 0

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            required_cols = [
                "id_conductor", "calibre_cable",
                "tubo_1/2_pulgada", "tubo_3/4_pulgada", "tubo_1_pulgada",
                "tubo_1_1/4_pulgada", "tubo_1_1/2_pulgada",
                "tubo_2_pulgada", "tubo_2_1/2_pulgada",
            ]
            for col in required_cols:
                if col not in reader.fieldnames:
                    self.stderr.write(f"❌ Falta columna en CSV: {col}")
                    return

            for row in reader:
                try:
                    pk = int(str(row["id_conductor"]).strip())
                except Exception:
                    continue

                def to_int(x):
                    try:
                        return int(str(x).strip())
                    except Exception:
                        return 0

                defaults = {
                    "calibre_cable": (row.get("calibre_cable") or "").strip(),
                    "tubo_1_2_pulgada": to_int(row.get("tubo_1/2_pulgada")),
                    "tubo_3_4_pulgada": to_int(row.get("tubo_3/4_pulgada")),
                    "tubo_1_pulgada": to_int(row.get("tubo_1_pulgada")),
                    "tubo_1_1_4_pulgada": to_int(row.get("tubo_1_1/4_pulgada")),
                    "tubo_1_1_2_pulgada": to_int(row.get("tubo_1_1/2_pulgada")),
                    "tubo_2_pulgada": to_int(row.get("tubo_2_pulgada")),
                    "tubo_2_1_2_pulgada": to_int(row.get("tubo_2_1/2_pulgada")),
                }

                obj, was_created = Conductor.objects.update_or_create(
                    id_conductor=pk,
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(f"✅ Importación conductores lista. Creados: {created} | Actualizados: {updated}")