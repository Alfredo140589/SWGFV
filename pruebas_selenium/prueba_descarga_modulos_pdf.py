from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

# Valores de prueba
PROYECTO = "prueba dimensionamiento"
TIPO_FACTURACION = "Bimestral (6 consumos)"
IRRADIANCIA = "León - Guanajuato (Prom: 5.79)"
EFICIENCIA = "0.80"
PANEL = "Canadian - CS6.2-66TB-620 (620.00 W)"

CONSUMOS = {
    "consumo_ene": "1000.0",
    "consumo_feb": "1050.0",
    "consumo_mar": "1200.0",
    "consumo_abr": "1100.0",
    "consumo_may": "1300.0",
    "consumo_jun": "1250.0",
}

CARPETA_DESCARGAS = Path.cwd() / "descargas_prueba_modulos"


def limpiar_descargas():
    CARPETA_DESCARGAS.mkdir(exist_ok=True)
    for archivo in CARPETA_DESCARGAS.iterdir():
        if archivo.is_file():
            archivo.unlink()


def esperar_pdf(timeout=30):
    inicio = time.time()
    while time.time() - inicio < timeout:
        archivos = list(CARPETA_DESCARGAS.glob("*.pdf"))
        temporales = list(CARPETA_DESCARGAS.glob("*.crdownload"))
        if archivos and not temporales:
            return archivos[0]
        time.sleep(1)
    raise TimeoutError("No se descargó el PDF")


def click_seguro(driver, elemento):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)
    time.sleep(1)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)


def main():
    limpiar_descargas()

    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": str(CARPETA_DESCARGAS.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta")

        # 2. Login
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve captcha manualmente y entra al sistema...")

        wait_largo = WebDriverWait(driver, 120)

        # 3. Abrir menú Dimensionamiento
        menu_dimensionamiento = wait_largo.until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Dimensionamiento')]"))
        )
        menu_dimensionamiento.click()
        print("Menú Dimensionamiento encontrado")
        time.sleep(1)

        # 4. Entrar al submenú cálculo de módulos
        submenu_modulos = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@href, 'modulos') or contains(., 'Cálculo de módulos') or contains(., 'módulos') or contains(., 'Módulos')]"
                )
            )
        )
        submenu_modulos.click()
        print("Entró al módulo de cálculo de módulos")
        time.sleep(2)

        # 5. Seleccionar proyecto
        select_proyecto = wait.until(
            EC.presence_of_element_located((By.XPATH, "//select[1]"))
        )
        Select(select_proyecto).select_by_visible_text(PROYECTO)
        print(f"Proyecto seleccionado: {PROYECTO}")
        time.sleep(1)

        # 6. Tipo de facturación
        select_facturacion = wait.until(
            EC.presence_of_element_located((By.ID, "tipoFacturacion"))
        )
        Select(select_facturacion).select_by_visible_text(TIPO_FACTURACION)
        print(f"Tipo de facturación seleccionado: {TIPO_FACTURACION}")
        time.sleep(1)

        # 7. Irradiancia
        select_irradiancia = wait.until(
            EC.presence_of_element_located((By.ID, "irradianciaSelect"))
        )
        Select(select_irradiancia).select_by_visible_text(IRRADIANCIA)
        print(f"Irradiancia seleccionada: {IRRADIANCIA}")
        time.sleep(1)

        # 8. Eficiencia
        select_eficiencia = wait.until(
            EC.presence_of_element_located((By.ID, "eficienciaSelect"))
        )
        Select(select_eficiencia).select_by_visible_text(EFICIENCIA)
        print(f"Eficiencia seleccionada: {EFICIENCIA}")
        time.sleep(1)

        # 9. Panel
        select_panel = wait.until(
            EC.presence_of_element_located((By.ID, "panelSelect"))
        )
        Select(select_panel).select_by_visible_text(PANEL)
        print(f"Panel seleccionado: {PANEL}")
        time.sleep(1)

        # 10. Llenar consumos
        for campo_name, valor in CONSUMOS.items():
            campo = wait.until(
                EC.presence_of_element_located((By.NAME, campo_name))
            )
            campo.clear()
            campo.send_keys(valor)
            print(f"{campo_name} = {valor}")

        # 11. Calcular
        boton_calcular = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@type='submit' and @name='action' and @value='calcular']")
            )
        )
        click_seguro(driver, boton_calcular)
        print("Botón Calcular presionado")
        time.sleep(3)

        # 12. Validar resultados
        body_text = driver.find_element(By.TAG_NAME, "body").text
        assert (
            "Número de módulos" in body_text
            or "Potencia total instalada" in body_text
            or "Descargar PDF" in body_text
        ), "No aparecieron resultados del cálculo"

        print("Resultados del cálculo visibles")

        # 13. Descargar PDF
        boton_pdf = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(., 'Descargar PDF') and contains(@href, '/pdf/')]")
            )
        )
        click_seguro(driver, boton_pdf)
        print("Descargando PDF...")

        archivo_pdf = esperar_pdf(timeout=30)
        print(f"PDF descargado: {archivo_pdf.name}")

        assert archivo_pdf.exists(), "El PDF no existe"

        print("✅ PRUEBA EXITOSA (HU017 - cálculo de módulos + descarga PDF)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()