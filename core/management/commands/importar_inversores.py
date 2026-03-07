import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Inversor, MicroInversor


class Command(BaseCommand):
    help = "Importa inversores y microinversores desde CSV (data/inversor.csv y data/microinversor.csv)"

    def handle(self, *args, **kwargs):
        base_dir = Path(settings.BASE_DIR)
        inv_path = base_dir / "data" / "inversor.csv"
        micro_path = base_dir / "data" / "microinversor.csv"

        enc = "utf-8-sig"

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

                if not marca or not modelo:
                    continue

                potencia = to_decimal(row.get("Potencia"))
                corriente_entrada = to_decimal(row.get("Corriente de entrada"))
                corriente_salida = to_decimal(row.get("Corriente de salida"))
                voltaje_arranque = to_decimal(row.get("Voltaje de arranque"))
                voltaje_maximo_entrada = to_decimal(row.get("Voltaje máximo de entrada"))
                no_mppt = to_int(row.get("No mppt"))
                no_fases = to_int(row.get("No fases"))
                voltaje_nominal = clean_text(row.get("Voltaje nominal"))

                _, created = Inversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia": potencia,
                        "corriente_entrada": corriente_entrada,
                        "corriente_salida": corriente_salida,
                        "voltaje_arranque": voltaje_arranque,
                        "voltaje_maximo_entrada": voltaje_maximo_entrada,
                        "no_mppt": no_mppt,
                        "no_fases": no_fases,
                        "voltaje_nominal": voltaje_nominal,
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

                if not marca or not modelo:
                    continue

                potencia = to_decimal(row.get("Potencia"))
                corriente_entrada = to_decimal(row.get("Corriente de entrada"))
                corriente_salida = to_decimal(row.get("Corriente de salida"))
                voltaje_arranque = to_decimal(row.get("Voltaje de arranque"))
                voltaje_maximo_entrada = to_decimal(row.get("Voltaje máximo de entrada"))
                no_mppt = to_int(row.get("No mppt"))
                no_fases = to_int(row.get("No fases"))
                voltaje_nominal = clean_text(row.get("Voltaje nominal"))

                _, created = MicroInversor.objects.update_or_create(
                    marca=marca,
                    modelo=modelo,
                    defaults={
                        "potencia": potencia,
                        "corriente_entrada": corriente_entrada,
                        "corriente_salida": corriente_salida,
                        "voltaje_arranque": voltaje_arranque,
                        "voltaje_maximo_entrada": voltaje_maximo_entrada,
                        "no_mppt": no_mppt,
                        "no_fases": no_fases,
                        "voltaje_nominal": voltaje_nominal,
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