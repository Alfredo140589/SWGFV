from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

# CAMBIA este nombre por el nombre EXACTO del proyecto
PROYECTO = "prueba dimensionamiento"

CARPETA_DESCARGAS = Path.cwd() / "descargas_prueba_dimensionamiento"


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

        # 4. Entrar al submenú Dimensionamiento
        submenu_dimensionamiento = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@href, 'dimensionamiento') or contains(., 'Dimensionamiento')]"
                )
            )
        )
        submenu_dimensionamiento.click()
        print("Entró al módulo de dimensionamiento")
        time.sleep(2)

        # 5. Seleccionar proyecto
        select_proyecto = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//label[contains(., 'Selecciona el proyecto')]/following::select[1]"
                )
            )
        )

        combo_proyecto = Select(select_proyecto)

        print("Opciones disponibles en el combo de proyecto:")
        for opcion in combo_proyecto.options:
            print(f"- '{opcion.text.strip()}'")

        combo_proyecto.select_by_visible_text(PROYECTO)
        print(f"Proyecto seleccionado: {PROYECTO}")
        time.sleep(2)

        # 6. Verificar que aparece el botón Descargar PDF
        boton_pdf = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//a[contains(., 'Descargar PDF')] | "
                    "//button[contains(., 'Descargar PDF')] | "
                    "//a[contains(@href, '.pdf')]"
                )
            )
        )
        print("Botón Descargar PDF visible")

        # 7. Descargar PDF
        click_seguro(driver, boton_pdf)
        print("Descargando PDF...")

        archivo_pdf = esperar_pdf(timeout=30)
        print(f"PDF descargado: {archivo_pdf.name}")

        # 8. Validación final
        assert archivo_pdf.exists(), "El PDF no existe"

        print("✅ PRUEBA EXITOSA (HU018 - descarga PDF de dimensionamiento)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()