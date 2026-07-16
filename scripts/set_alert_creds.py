"""Guarda las credenciales de ALERTAS en Doppler (liquidation-tracker/prd).

Por qué existe: hoy en Doppler solo están BSTOCK_USER/BSTOCK_PASS. Como las
claves de email/WhatsApp faltan, `config.py` las lee con default "false" y las
alertas quedan **apagadas en silencio** — no fallan, simplemente no salen nunca.
Este script rellena ese hueco.

Los valores secretos se piden en un prompt OCULTO: no se muestran, no quedan en
el historial del terminal, y van directos de tu teclado a Doppler por stdin (no
por argv, así que tampoco aparecen en la lista de procesos). Claude nunca los ve.

Cada bloque es OPCIONAL: deja el primer campo en blanco para saltártelo. Solo se
activa (`*_ALERTS_ENABLED=true`) el canal que rellenes.

Uso:  python scripts/set_alert_creds.py

Los valores que faltan suelen estar en el `.env` del PC de casa, si aún existe:
  C:\\Users\\guill\\Claude\\liquidation-auction-tracker\\.env
Si no, se regeneran: la App Password de Gmail en la cuenta de Google, y la API
key de CallMeBot pidiéndosela otra vez al bot por WhatsApp.
"""
import getpass
import subprocess
import sys

PROJECT, CONFIG = "liquidation-tracker", "prd"


def put(name: str, value: str) -> None:
    # El valor va por stdin (no por argv) → no aparece en la lista de procesos
    # ni en el historial. capture_output=True evita que Doppler lo imprima.
    r = subprocess.run(
        ["doppler", "secrets", "set", name, "--project", PROJECT, "--config", CONFIG],
        input=value, text=True, capture_output=True,
    )
    if r.returncode != 0:
        print(f"ERROR guardando {name}:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"  {name}  guardado OK")


def email_block() -> bool:
    print("\n-- Alertas por EMAIL (Gmail) --")
    user = input("  SMTP_USERNAME (tu Gmail; vacío = saltar): ").strip()
    if not user:
        print("  saltado.")
        return False
    pwd = getpass.getpass("  SMTP_PASSWORD (App Password de Gmail, no se muestra): ")
    if not pwd:
        print("  sin contraseña, bloque saltado.", file=sys.stderr)
        return False
    to = input("  EMAIL_RECIPIENTS (destinatarios, separados por comas): ").strip()
    if not to:
        print("  sin destinatarios, bloque saltado.", file=sys.stderr)
        return False
    put("SMTP_USERNAME", user)
    put("SMTP_PASSWORD", pwd)
    put("EMAIL_RECIPIENTS", to)
    put("EMAIL_ALERTS_ENABLED", "true")
    return True


def whatsapp_block() -> bool:
    print("\n-- Alertas por WHATSAPP (CallMeBot) --")
    phone = input("  CALLMEBOT_PHONE (formato +34600111222; vacío = saltar): ").strip()
    if not phone:
        print("  saltado.")
        return False
    key = getpass.getpass("  CALLMEBOT_APIKEY (no se muestra): ")
    if not key:
        print("  sin apikey, bloque saltado.", file=sys.stderr)
        return False
    put("CALLMEBOT_PHONE", phone)
    put("CALLMEBOT_APIKEY", key)
    put("WHATSAPP_ALERTS_ENABLED", "true")
    return True


def call_block() -> bool:
    print("\n-- Alertas por LLAMADA (CallMeBot vía Telegram) --")
    user = input("  CALLMEBOT_TELEGRAM_USER (@usuario o +34...; vacío = saltar): ").strip()
    if not user:
        print("  saltado.")
        return False
    put("CALLMEBOT_TELEGRAM_USER", user)
    put("CALL_ALERTS_ENABLED", "true")
    return True


def main() -> int:
    print(f"== Credenciales de alertas -> Doppler ({PROJECT}/{CONFIG}) ==")
    print("Deja el primer campo de un bloque en blanco para saltarlo.")

    done = [
        ("email", email_block()),
        ("whatsapp", whatsapp_block()),
        ("llamada", call_block()),
    ]
    activos = [name for name, ok in done if ok]

    print()
    if not activos:
        print("No se configuró ningún canal. Las alertas siguen apagadas.")
        return 1
    print(f"Canales activados: {', '.join(activos)}")
    print("Comprueba que el proceso las ve:")
    print("  doppler run -- python -c \"from liquidation_tracker.config import EmailConfig; print(EmailConfig.from_env().enabled)\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
