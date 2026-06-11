# ============================================================
# DASHBOARD FACTURAS PROVEEDORES — Consolidación de KPIs
# ============================================================

from .facturasproveedores_modelo import obtener_kpis_facturas_proveedores


# ============================================================
# ESTRUCTURA VACÍA (para evitar errores cuando no hay datos)
# ============================================================

def _resultado_vacio():
    return {
        # ---------------- KPIs PRINCIPALES ----------------
        "total_facturas": 0,
        "facturas_pagadas": 0,

        "total_cuentas_por_pagar": 0,
        "por_pagar_al_dia": 0,
        "por_pagar_proximo_vencimiento": 0,
        "por_pagar_vencido": 0,

        "obligacion_neta": 0,

        # 🔹 NUEVO KPI — PROYECCIÓN DE PAGOS
        "proyeccion_pagos": 0,

        # ---------------- GLOBALES ----------------
        "total_facturas_global": 0,
        "total_general_global": 0,
        "conteo_estados": {
            "Pagada": 0,
            "Pendiente": 0,
            "Anulada": 0
        },

        # ---------------- FLUJO DE CAJA ----------------
        "flujo_caja_proyectado": 0,
        "asignacion_proyectada": {
            "gastos_generales": 0,
            "facturas_proveedores": 0,
            "reserva": 0
        },
        "porcentaje_gastos": 0,
        "porcentaje_facturas": 0,
        "porcentaje_reserva": 0,
        "modo_flujo": "automatico",
        "fecha_calculo_flujo": "",

        # ---------------- ALERTAS ----------------
        "alertas_vencimiento": 0,
        "detalle_alertas": {
            "proximas_vencer": [],
            "vencidas": [],
            "alto_riesgo": []
        },

        # ---------------- CONCENTRACIÓN ----------------
        "concentracion_proveedores": {},

        # ---------------- GRÁFICAS ----------------
        "monto_por_proveedor": [],
        "proyeccion_regresion": {
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "calidad": "Sin datos"
        },

        # ---------------- TABLA ----------------
        "lista_facturas": [],

        # ---------------- HORIZONTES ----------------
        "horizonte_vencimiento": 0,
        "horizonte_proyeccion": 0,
    }


def generar_kpis_facturas_proveedores():

    r = obtener_kpis_facturas_proveedores()

    if not r:
        return _resultado_vacio()

    return {
        # ---------------- KPIs PRINCIPALES ----------------
        "total_facturas": r["total_facturas_global"],
        "facturas_pagadas": r["facturas_pagadas"],

        "total_cuentas_por_pagar": r["total_cuentas_por_pagar"],
        "por_pagar_al_dia": r["por_pagar_al_dia"],
        "por_pagar_proximo_vencimiento": r["por_pagar_proximo_vencimiento"],
        "por_pagar_vencido": r["por_pagar_vencido"],

        "obligacion_neta": r["obligacion_neta"],

        # 🔹 NUEVO KPI — PROYECCIÓN DE PAGOS (TARJETA)
        "proyeccion_pagos": r["proyeccion_pagos"],

        # ---------------- GLOBALES ----------------
        "total_facturas_global": r["total_facturas_global"],
        "total_general_global": r["total_general_global"],
        "conteo_estados": r["conteo_estados"],

        # ---------------- FLUJO DE CAJA ----------------
        "flujo_caja_proyectado": r["flujo_caja_proyectado"],
        "asignacion_proyectada": r["asignacion_proyectada"],
        "porcentaje_gastos": r["porcentaje_gastos"],
        "porcentaje_facturas": r["porcentaje_facturas"],
        "porcentaje_reserva": r["porcentaje_reserva"],
        "modo_flujo": r["modo_flujo"],
        "fecha_calculo_flujo": r["fecha_calculo_flujo"],

        # ---------------- ALERTAS ----------------
        "alertas_vencimiento": r["alertas_vencimiento"],
        "detalle_alertas": r["detalle_alertas"],

        # ---------------- CONCENTRACIÓN ----------------
        "concentracion_proveedores": r["concentracion_proveedores"],

        # ---------------- GRÁFICAS ----------------
        "monto_por_proveedor": r["monto_por_proveedor"],
        "proyeccion_regresion": r["proyeccion_regresion"],

        # ---------------- TABLA ----------------
        "lista_facturas": r["lista_facturas"],

        # ---------------- HORIZONTES ----------------
        "horizonte_vencimiento": r["horizonte_vencimiento"],
        "horizonte_proyeccion": r["horizonte_proyeccion"],
    }
