# utils.py
from datetime import datetime
from num2words import num2words
import calendar


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

    

def numero_a_letras(valor: int) -> str:
    """
    Convierte un número a letras en español y lo devuelve en mayúsculas
    con la leyenda PESOS M/CTE.
    """
    return num2words(valor, lang='es').upper() + " PESOS M/CTE"

def rango_mes(año: int, mes: int):
    
    fecha_inicio = datetime(año, mes, 1)
    ultimo_dia = calendar.monthrange(año, mes)[1]
    fecha_fin = datetime(año, mes, ultimo_dia)
    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")


def format_date_for_input(fecha: datetime) -> str:
  
    return fecha.strftime("%Y-%m-%d")



