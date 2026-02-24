import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Inversor, MicroInversor


class Command(BaseCommand):
    help = "Importa inversores y microinversores desde CSV (data/inversor.csv y data/microinversor.csv)"

    def handle(self, *args, **kwargs):
        # ✅ RUTAS PORTABLES (funciona en Windows, Linux y Render)
        base_dir = Path(settings.BASE_DIR)  # carpeta raíz del proyecto (donde está manage.py)
        inv_path = base_dir / "data" / "inversor.csv"
        micro_path = base_dir / "data" / "microinversor.csv"

        # Si tus CSV se guardaron con BOM (muy común en Excel), utf-8-sig lo maneja perfecto.
        enc = "utf-8-sig"

        # Validación rápida: si no existen, fallar con mensaje claro
        if not inv_path.exists():
            raise FileNotFoundError(f"No se encontró: {inv_path}  (asegúrate que exista data/inversor.csv en el servidor)")
        if not micro_path.exists():
            raise FileNotFoundError(f"No se encontró: {micro_path}  (asegúrate que exista data/microinversor.csv en el servidor)")

        def to_decimal(val):
            val = (val or "").strip().replace(",", ".")
            if not val:
                return None
            try:
                return Decimal(val)
            except Exception:
                return None

        def clean_text(val):
            return (val or "").strip()

        def to_int(val):
            val = (val or "").strip()
            return int(val) if val.isdigit() else None

        # =====================
        # IMPORTAR INVERSORES
        # =====================
        new_i = 0
        upd_i = 0

        with open(inv_path, encoding=enc, newline="") as f:
            reader = csv.DictReader(f)
            self.stdout.write(f"HEADERS INVERSOR: {reader.fieldnames}")

            for row in reader:
                marca = clean_text(row.get("Marca"))
                modelo = clean_text(row.get("Modelo"))
                potencia_w = to_decimal(row.get("Potencia"))  # ✅ W
                volt_nom = clean_text(row.get("Voltaje nominal"))

                if not marca or not modelo:
                    continue

                _, created = Inversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia_w": potencia_w,     # ✅ W
                        "voltaje_salida": volt_nom,   # texto (como lo tienes en tu modelo)
                    },
                )

                if created:
                    new_i += 1
                else:
                    upd_i += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"INVERSORES -> nuevos: {new_i} | actualizados: {upd_i} | total: {Inversor.objects.count()}"
            )
        )

        # =====================
        # IMPORTAR MICROINVERSORES
        # =====================
        new_m = 0
        upd_m = 0

        with open(micro_path, encoding=enc, newline="") as f:
            reader = csv.DictReader(f)
            self.stdout.write(f"HEADERS MICRO: {reader.fieldnames}")

            for row in reader:
                marca = clean_text(row.get("Marca"))
                modelo = clean_text(row.get("Modelo"))
                potencia_w = to_decimal(row.get("Potencia"))  # ✅ W
                canales = to_int(row.get("No mppt"))

                if not marca or not modelo:
                    continue

                _, created = MicroInversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia_w": potencia_w,  # ✅ W
                        "canales": canales,
                    },
                )

                if created:
                    new_m += 1
                else:
                    upd_m += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"MICRO -> nuevos: {new_m} | actualizados: {upd_m} | total: {MicroInversor.objects.count()}"
            )
        )

        self.stdout.write(self.style.SUCCESS("✅ Importación finalizada correctamente."))