import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import TablaConductoresAWGConReactancia


class Command(BaseCommand):
    help = "Importa tabla de conductores AWG con reactancia desde CSV (data/tabla_conductores_awg_con_reactancia.csv)"

    def handle(self, *args, **kwargs):
        base_dir = Path(settings.BASE_DIR)
        csv_path = base_dir / "data" / "tabla_conductores_awg_con_reactancia.csv"
        enc = "utf-8-sig"

        if not csv_path.exists():
            raise FileNotFoundError(
                f"No se encontró: {csv_path} (asegúrate que exista data/tabla_conductores_awg_con_reactancia.csv)"
            )

        def to_decimal(val):
            val = (val or "").strip().replace(",", ".")
            if not val:
                return None
            try:
                return Decimal(val)
            except Exception:
                return None

        def to_int(val):
            val = (val or "").strip()
            return int(val) if val.isdigit() else None

        nuevos = 0
        actualizados = 0

        with open(csv_path, encoding=enc, newline="") as f:
            reader = csv.DictReader(f)
            self.stdout.write(f"HEADERS TABLA AWG: {reader.fieldnames}")

            for row in reader:
                calibre_awg = to_int(row.get("calibre_awg"))
                area_transversal = to_decimal(row.get("area_transversal"))
                resistencia_cc = to_decimal(row.get("resistencia_cc"))
                resistencia_ca = to_decimal(row.get("resistencia_ca"))
                reactancia = to_decimal(row.get("reactancia"))

                if calibre_awg is None:
                    continue

                _, created = TablaConductoresAWGConReactancia.objects.update_or_create(
                    calibre_awg=calibre_awg,
                    defaults={
                        "area_transversal": area_transversal,
                        "resistencia_cc": resistencia_cc,
                        "resistencia_ca": resistencia_ca,
                        "reactancia": reactancia,
                    },
                )

                if created:
                    nuevos += 1
                else:
                    actualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Importación finalizada. Nuevos: {nuevos} | Actualizados: {actualizados} | Total: {TablaConductoresAWGConReactancia.objects.count()}"
            )
        )