import csv
from decimal import Decimal
from django.core.management.base import BaseCommand
from core.models import Inversor, MicroInversor


class Command(BaseCommand):
    help = "Importa inversores y microinversores desde CSV"

    def handle(self, *args, **kwargs):
        INV_PATH = r"C:\Users\alfre\Desktop\Escuela\2026-1\SWGFV\data\inversor.csv"
        MICRO_PATH = r"C:\Users\alfre\Desktop\Escuela\2026-1\SWGFV\data\microinversor.csv"
        ENC = "utf-8-sig"

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
        new_i = upd_i = 0

        with open(INV_PATH, encoding=ENC, newline="") as f:
            reader = csv.DictReader(f)
            self.stdout.write(f"HEADERS INVERSOR: {reader.fieldnames}")

            for row in reader:
                marca = clean_text(row.get("Marca"))
                modelo = clean_text(row.get("Modelo"))
                potencia_w = to_decimal(row.get("Potencia"))
                volt_nom = clean_text(row.get("Voltaje nominal"))

                if not marca or not modelo:
                    continue

                obj, created = Inversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia_w": potencia_w,
                        "voltaje_salida": volt_nom,
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
        new_m = upd_m = 0

        with open(MICRO_PATH, encoding=ENC, newline="") as f:
            reader = csv.DictReader(f)
            self.stdout.write(f"HEADERS MICRO: {reader.fieldnames}")

            for row in reader:
                marca = clean_text(row.get("Marca"))
                modelo = clean_text(row.get("Modelo"))
                potencia_w = to_decimal(row.get("Potencia"))
                canales = to_int(row.get("No mppt"))

                if not marca or not modelo:
                    continue

                obj, created = MicroInversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia_w": potencia_w,
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