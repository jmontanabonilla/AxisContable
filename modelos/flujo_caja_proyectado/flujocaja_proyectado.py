# ============================================================
# FLUJO_CAJA_PROYECTADO.PY — Versión FINAL corregida
# ============================================================

from db import query_all_flat, exec_sql
from datetime import datetime
from flask import session

# ============================================================
# UTILIDAD
# ============================================================

def safe_float(value):
    try:
        if value is None:
            return 0.0
        s = str(value).strip()
        if s == "" or s.lower() == "nan":
            return 0.0
        return float(s)
    except:
        return 0.0


# ============================================================
# 1️⃣ OBTENER FLUJO REAL DESDE LA VISTA
# ============================================================

def obtener_flujo_caja_real():
    row = query_all_flat("""
        SELECT VentasReales, GastosReales, FacturasPagadas, FlujoCajaReal
        FROM vw_FlujoCajaReal_Global
    """)

    if not row:
        return {"ventas": 0, "gastos": 0, "pagadas": 0, "flujo_real": 0}

    r = row[0]
    return {
        "ventas": safe_float(r[0]),
        "gastos": safe_float(r[1]),
        "pagadas": safe_float(r[2]),
        "flujo_real": safe_float(r[3])
    }


# ============================================================
# 2️⃣ FUNCIÓN PRINCIPAL — FLUJO DE CAJA PROYECTADO GLOBAL
# ============================================================

def calcular_flujo_caja_proyectado_global():

    usuario_id = session.get("usuario_id", 1)

    # A. Obtener flujo real
    flujo_real = obtener_flujo_caja_real()
    ventas = flujo_real["ventas"]
    gastos = flujo_real["gastos"]
    pagadas = flujo_real["pagadas"]

    # B. Calcular porcentajes automáticos
    if ventas > 0:
        pct_gastos = min(gastos / ventas, 1.0)
        pct_facturas = min(pagadas / ventas, 1.0)

        suma = pct_gastos + pct_facturas
        if suma > 1:
            pct_gastos /= suma
            pct_facturas /= suma
            pct_reserva = 0
        else:
            pct_reserva = 1 - pct_gastos - pct_facturas
    else:
        pct_gastos, pct_facturas, pct_reserva = 0.35, 0.25, 0.40

    # Normalizar
    total = pct_gastos + pct_facturas + pct_reserva
    pct_gastos /= total
    pct_facturas /= total
    pct_reserva /= total

    pct_gastos_user = round(pct_gastos * 100, 1)
    pct_facturas_user = round(pct_facturas * 100, 1)
    pct_reserva_user = round(pct_reserva * 100, 1)

    # C. Cálculo corregido del flujo proyectado
    flujo_proyectado = round(
        max(0, ventas - gastos - pagadas) * pct_reserva,
        2
    )

    # D. Asignación
    asignacion = {
        "gastos_generales": round(flujo_proyectado * pct_gastos, 2),
        "facturas_proveedores": round(flujo_proyectado * pct_facturas, 2),
        "reserva": round(flujo_proyectado * pct_reserva, 2),
    }

    # E. Guardar en BD
    fila_actual = query_all_flat("SELECT TOP 1 Id FROM ProyeccionFlujoCaja ORDER BY Id DESC")

    if not fila_actual:
        exec_sql("""
            INSERT INTO ProyeccionFlujoCaja (
                FlujoCajaProyectado, PorcentajeGastos, PorcentajeFacturas, PorcentajeReserva,
                FechaCalculo, ModoCalculo, UltimaActualizacion
            )
            VALUES (?, ?, ?, ?, GETDATE(), 'automatico', GETDATE())
        """, (
            flujo_proyectado,
            pct_gastos_user,
            pct_facturas_user,
            pct_reserva_user
        ))
    else:
        id_actual = fila_actual[0][0]

        exec_sql("""
            INSERT INTO AuditoriaFlujoCaja (
                FlujoCajaProyectado, PorcentajeGastos, PorcentajeFacturas, PorcentajeReserva,
                FechaCalculo, ModoCalculo, UsuarioId
            )
            SELECT FlujoCajaProyectado, PorcentajeGastos, PorcentajeFacturas, PorcentajeReserva,
                   FechaCalculo, ModoCalculo, ?
            FROM ProyeccionFlujoCaja
            WHERE Id = ?
        """, (usuario_id, id_actual))

        exec_sql("""
            UPDATE ProyeccionFlujoCaja
            SET 
                FlujoCajaProyectado = ?,
                PorcentajeGastos = ?,
                PorcentajeFacturas = ?,
                PorcentajeReserva = ?,
                FechaCalculo = GETDATE(),
                ModoCalculo = 'automatico',
                UltimaActualizacion = GETDATE()
            WHERE Id = ?
        """, (
            flujo_proyectado,
            pct_gastos_user,
            pct_facturas_user,
            pct_reserva_user,
            id_actual
        ))

    # F. Retorno compatible con dashboard
    return {
        "flujo_caja_proyectado": flujo_proyectado,
        "porcentaje_gastos": pct_gastos_user,
        "porcentaje_facturas": pct_facturas_user,
        "porcentaje_reserva": pct_reserva_user,
        "asignacion_proyectada": asignacion,
        "fecha_calculo_flujo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "modo_flujo": "automatico"
    }
