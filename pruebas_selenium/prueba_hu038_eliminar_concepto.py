from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import time


URL = "http://127.0.0.1:8000/"
USUARIO = "alfredo.arias@fortiapv.com"
PASSWORD = "Admin123*"

CONCEPTO_A_ELIMINAR = "Concepto prueba 2"


def click_seguro(driver, elemento):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elemento)
    time.sleep(1)
    try:
        elemento.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elemento)


def abrir_menu_recursos(driver, wait, actions):
    menu_recursos = wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[normalize-space()='Recursos']")
        )
    )

    actions.move_to_element(menu_recursos).perform()
    time.sleep(1)

    try:
        menu_recursos.click()
    except Exception:
        driver.execute_script("arguments[0].click();", menu_recursos)

    print("Menú Recursos encontrado")
    time.sleep(1)


def main():
    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 20)
    wait_largo = WebDriverWait(driver, 120)
    actions = ActionChains(driver)

    try:
        # 1. Abrir sistema
        driver.get(URL)
        print("Página abierta")

        # 2. Login automático
        usuario = wait.until(EC.presence_of_element_located((By.ID, "usuario")))
        password = wait.until(EC.presence_of_element_located((By.ID, "password")))

        usuario.clear()
        usuario.send_keys(USUARIO)
        password.clear()
        password.send_keys(PASSWORD)

        print("Resuelve captcha manualmente y presiona el botón ingresar...")

        # 3. Esperar ingreso manual
        wait_largo.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[normalize-space()='Recursos']")
            )
        )

        # 4. Abrir Recursos
        abrir_menu_recursos(driver, wait, actions)

        # 5. Entrar a Modificación de Concepto
        submenu_modificacion_concepto = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[normalize-space()='Modificación de Concepto']")
            )
        )
        click_seguro(driver, submenu_modificacion_concepto)
        print("Entró al módulo Modificación de Concepto")
        time.sleep(2)

        # 6. Confirmar vista
        print(f"URL actual: {driver.current_url}")

        titulo = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//h2[contains(., 'Modificación de Conceptos')]")
            )
        )
        print(f"Título detectado: {titulo.text}")

        # 7. Buscar el concepto a eliminar
        caja_busqueda_nombre = wait.until(
            EC.presence_of_element_located((By.NAME, "nombre"))
        )
        caja_busqueda_nombre.clear()
        caja_busqueda_nombre.send_keys(CONCEPTO_A_ELIMINAR)
        print(f"Texto capturado en búsqueda: {CONCEPTO_A_ELIMINAR}")

        boton_buscar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='searchFormModConceptos']//button[@type='submit' and @name='action' and @value='search']")
            )
        )
        click_seguro(driver, boton_buscar)
        print("Botón Buscar presionado")
        time.sleep(2)

        body_text = driver.find_element(By.TAG_NAME, "body").text
        assert CONCEPTO_A_ELIMINAR in body_text, \
            f"No apareció el concepto '{CONCEPTO_A_ELIMINAR}' en los resultados"

        print("✅ Concepto encontrado correctamente")

        # 8. Pulsar Seleccionar
        boton_seleccionar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(., 'Seleccionar')]")
            )
        )
        click_seguro(driver, boton_seleccionar)
        print("Botón Seleccionar presionado")
        time.sleep(2)

        # 9. Pulsar Eliminar
        boton_eliminar = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'Eliminar')]")
            )
        )
        click_seguro(driver, boton_eliminar)
        print("Botón Eliminar presionado")
        time.sleep(2)

        # 10. Confirmar alerta si existe
        try:
            alerta = WebDriverWait(driver, 5).until(EC.alert_is_present())
            print(f"Alerta detectada: {alerta.text}")
            alerta.accept()
            print("Alerta de confirmación aceptada")
            time.sleep(2)
        except Exception:
            print("No apareció alerta de confirmación, se continúa con la validación")

        # 11. Ir a Recursos > Conceptos para validar que ya no exista
        abrir_menu_recursos(driver, wait, actions)

        submenu_conceptos = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(@href, '/recursos/conceptos/')]")
            )
        )
        click_seguro(driver, submenu_conceptos)
        print("Entró al módulo Glosario de Conceptos")
        time.sleep(2)

        print(f"URL actual: {driver.current_url}")

        # 12. Buscar el concepto eliminado
        caja_busqueda = wait.until(
            EC.presence_of_element_located((By.NAME, "q"))
        )
        caja_busqueda.clear()
        caja_busqueda.send_keys(CONCEPTO_A_ELIMINAR)
        print(f"Texto capturado en búsqueda final: {CONCEPTO_A_ELIMINAR}")

        boton_buscar_concepto = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='searchFormConceptos']//button[@type='submit']")
            )
        )
        click_seguro(driver, boton_buscar_concepto)
        print("Botón Buscar presionado para validación final")
        time.sleep(2)

        body_text = driver.find_element(By.TAG_NAME, "body").text

        assert CONCEPTO_A_ELIMINAR not in body_text, \
            f"El concepto '{CONCEPTO_A_ELIMINAR}' todavía aparece en la búsqueda final"

        print("✅ El concepto ya no aparece en la consulta final")
        print("✅ PRUEBA EXITOSA (HU038 - eliminación de concepto)")
        time.sleep(5)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()