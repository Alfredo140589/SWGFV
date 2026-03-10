import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import GlosarioConcepto


class Command(BaseCommand):
    help = "Importa y sincroniza conceptos del glosario desde data/glosario_fotovoltaico_extendido_swgfv.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default="data/glosario_fotovoltaico_extendido_swgfv.csv",
            help="Ruta relativa al proyecto del archivo CSV a importar."
        )

    def handle(self, *args, **options):
        relative_path = options["file"]
        csv_path = Path(settings.BASE_DIR) / relative_path

        if not csv_path.exists():
            raise CommandError(f"No se encontró el archivo CSV: {csv_path}")

        creados = 0
        actualizados = 0
        eliminados = 0

        nombres_csv = set()
        filas_validas = []

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            columnas_esperadas = {"termino", "definicion", "formula", "categoria"}
            columnas_encontradas = set(reader.fieldnames or [])

            if not columnas_esperadas.issubset(columnas_encontradas):
                raise CommandError(
                    f"El CSV no tiene las columnas esperadas. "
                    f"Esperadas: {sorted(columnas_esperadas)} | "
                    f"Encontradas: {sorted(columnas_encontradas)}"
                )

            for row in reader:
                termino = (row.get("termino") or "").strip()
                definicion = (row.get("definicion") or "").strip()
                formula = (row.get("formula") or "").strip()
                categoria = (row.get("categoria") or "").strip()

                if not termino or not definicion:
                    continue

                nombres_csv.add(termino)
                filas_validas.append({
                    "termino": termino,
                    "definicion": definicion,
                    "formula": formula,
                    "categoria": categoria,
                })

        # =====================================================
        # 1) Eliminar registros que ya no están en el CSV
        # =====================================================
        qs_eliminar = GlosarioConcepto.objects.exclude(nombre_concepto__in=nombres_csv)
        eliminados = qs_eliminar.count()
        qs_eliminar.delete()

        # =====================================================
        # 2) Crear o actualizar registros del CSV
        # =====================================================
        for fila in filas_validas:
            obj, creado = GlosarioConcepto.objects.update_or_create(
                nombre_concepto=fila["termino"],
                defaults={
                    "descripcion": fila["definicion"],
                    "formula": fila["formula"],
                    "categoria": fila["categoria"],
                }
            )

            if creado:
                creados += 1
            else:
                actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"Sincronización completada. Creados: {creados} | Actualizados: {actualizados} | Eliminados: {eliminados}"
        ))