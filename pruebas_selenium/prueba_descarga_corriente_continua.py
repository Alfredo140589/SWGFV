from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from pathlib import Path
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

PROYECTO = "Prueba corrección"

CARPETA_DESCARGAS = Path.cwd() / "descargas_prueba_cc"


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


def ir_a_corriente_continua(driver, wait, wait_largo):
    menu = wait_largo.until(
        EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Cálculos')]"))
    )
    menu.click()
    time.sleep(1)

    submenu = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(@href, 'corriente-continua') or contains(., 'Corriente')]")
        )
    )
    submenu.click()
    time.sleep(2)


def seleccionar_proyecto(driver, wait):
    select = wait.until(
        EC.presence_of_element_located((By.XPATH, "//select"))
    )
    Select(select).select_by_visible_text(PROYECTO)
    time.sleep(2)


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

        usuario.send_keys(USUARIO)
        password.send_keys(PASSWORD)

        print("Resuelve captcha...")
        wait_largo = WebDriverWait(driver, 120)

        # 3. Navegación
        ir_a_corriente_continua(driver, wait, wait_largo)
        seleccionar_proyecto(driver, wait)

        print("Resultados visibles")

        # 4. Descargar PDF
        boton_pdf = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(., 'Descargar PDF')]")
            )
        )

        click_seguro(driver, boton_pdf)
        print("Descargando PDF...")

        archivo = esperar_pdf()

        print(f"PDF descargado: {archivo.name}")

        # 5. Validación final
        assert archivo.exists(), "El PDF no existe"

        print("✅ PRUEBA EXITOSA (HU021 - PDF)")

        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()