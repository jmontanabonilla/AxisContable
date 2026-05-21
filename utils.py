# utils.py
from datetime import datetime
from num2words import num2words
import calendar
import hashlib
import qrcode
import base64
from io import BytesIO


# ============================================================
# 1. FECHAS
# ============================================================

def restar_meses(fecha: datetime, n: int) -> datetime:
    """
    Retrocede n meses desde la fecha dada.
    Corrige el bug de que al seleccionar enero se saltaba a febrero del año anterior.
    """
    año = fecha.year
    mes = fecha.month - n
    while mes <= 0:
        mes += 12
        año -= 1
    return datetime(año, mes, 1)


def rango_mes(año: int, mes: int):
    """
    Retorna el primer y último día del mes en formato YYYY-MM-DD.
    """
    fecha_inicio = datetime(año, mes, 1)
    ultimo_dia = calendar.monthrange(año, mes)[1]
    fecha_fin = datetime(año, mes, ultimo_dia)
    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")


def format_date_for_input(fecha: datetime) -> str:
    """
    Convierte un datetime a formato YYYY-MM-DD para inputs HTML.
    """
    return fecha.strftime("%Y-%m-%d")


# ============================================================
# 2. NÚMEROS A LETRAS
# ============================================================

def numero_a_letras(valor: int) -> str:
    """
    Convierte un número entero a letras en español y lo devuelve en mayúsculas
    con la leyenda PESOS M/CTE.
    """
    return num2words(valor, lang='es').upper() + " PESOS M/CTE"


def numero_a_letras_con_centavos(valor: float) -> str:
    """
    Convierte un valor con centavos a letras:
    Ej: 12345.67 → DOCE MIL TRESCIENTOS CUARENTA Y CINCO PESOS 67/100 MCTE
    """
    entero = int(valor)
    centavos = int(round((valor - entero) * 100))
    texto = num2words(entero, lang='es').upper()
    return f"{texto} PESOS {centavos:02d}/100 MCTE"


def formatear_valor(valor: float) -> str:
    """
    Formatea un número con separador de miles.
    """
    return f"{valor:,.0f}".replace(",", ".")


# ============================================================
# 3. CUFE SIMULADO (NO DIAN)
# ============================================================

def generar_cufe_simulado(venta: dict, detalles: list) -> str:
    """
    Genera un CUFE simulado usando SHA-256.
    NO es válido para DIAN, pero sirve para pruebas internas.
    """
    base = (
        str(venta.get("Subtotal", "")) +
        str(venta.get("Impuestos", "")) +
        str(venta.get("TotalVenta", "")) +
        str(venta.get("NumeroDocumento", "")) +
        str(venta.get("IdentificacionEmpresa", "")) +
        str(venta.get("FechaEmision", "")) +
        str(datetime.now())
    )

    return hashlib.sha256(base.encode()).hexdigest().upper()


# ============================================================
# 4. QR BASE64
# ============================================================

def generar_qr_base64(data: str) -> str:
    """
    Genera un código QR en base64 para incrustar en PDFs o HTML.
    """
    qr = qrcode.QRCode(
        version=1,
        box_size=4,
        border=2
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
