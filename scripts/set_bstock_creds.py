"""Guarda tus credenciales de B-Stock en Doppler (liquidation-tracker/prd).

La contraseña se pide en un prompt OCULTO: no se muestra, no queda en el
historial del terminal, y va directa de tu teclado a Doppler. Claude nunca la
ve. Puedes borrar este archivo después de usarlo.

Uso:  python set_bstock_creds.py
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


def main() -> int:
    print("== Credenciales B-Stock -> Doppler (liquidation-tracker/prd) ==")
    email = input("B-Stock email: ").strip()
    pwd = getpass.getpass("B-Stock password (no se muestra): ")
    if not email or not pwd:
        print("Email o contraseña vacíos, abortado.", file=sys.stderr)
        return 1
    print("Guardando en Doppler...")
    put("BSTOCK_USER", email)
    put("BSTOCK_PASS", pwd)
    print("Listo. Avisa a Claude para probar el login automático.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
