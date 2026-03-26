from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

PROYECTO = "Prueba corrección"
TEMPERATURA = "70"
FACTOR_POTENCIA = "1"


def main():
    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 20)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta correctamente")

        # 2. Llenar login
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve el captcha manualmente y entra al sistema...")

        # Esperar hasta que aparezca el menú Cálculos
        wait_largo = WebDriverWait(driver, 120)

        menu_calculos = wait_largo.until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Cálculos')]"))
        )

        # 3. Abrir menú Cálculos
        menu_calculos.click()
        print("Menú Cálculos encontrado")
        time.sleep(1)

        # 4. Abrir submenú Caída de tensión
        submenu_caida = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href, '/calculos/caida-tension') or contains(., 'Caída de tensión')]")
            )
        )
        submenu_caida.click()
        print("Entró al módulo de caída de tensión")
        time.sleep(2)

        # 5. Seleccionar proyecto
        select_proyecto = wait.until(
            EC.presence_of_element_located((By.XPATH, "//select"))
        )
        Select(select_proyecto).select_by_visible_text(PROYECTO)
        print(f"Proyecto seleccionado: {PROYECTO}")
        time.sleep(2)

        # 6. Llenar temperatura
        campo_temperatura = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[contains(@name,'temperatura')]"))
        )
        campo_temperatura.clear()
        campo_temperatura.send_keys(TEMPERATURA)
        print(f"Temperatura capturada: {TEMPERATURA}")

        # 7. Llenar factor de potencia
        campo_fp = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[contains(@name,'factor')]"))
        )
        campo_fp.clear()
        campo_fp.send_keys(FACTOR_POTENCIA)
        print(f"Factor de potencia capturado: {FACTOR_POTENCIA}")

        # 8. Clic en calcular (VERSIÓN CORREGIDA)
        boton_calcular = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[contains(., 'Calcular') or contains(@class,'btn-success')]")
            )
        )

        # Scroll hacia el botón
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton_calcular)
        time.sleep(1)

        # Intentar click normal, si falla usar JS
        try:
            boton_calcular.click()
        except Exception:
            driver.execute_script("arguments[0].click();", boton_calcular)

        print("Botón calcular presionado")
        time.sleep(2)

        # 9. Validar resultado
        body_text = driver.find_element(By.TAG_NAME, "body").text

        assert "Correcto" in body_text, "No apareció el texto 'Correcto' en la página"

        print("✅ Prueba exitosa: se realizó el cálculo correctamente")

        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()