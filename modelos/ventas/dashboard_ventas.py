# ============================================================
# DASHBOARD VENTAS — Consolidación de KPIs (VERSIÓN CORREGIDA)
# ============================================================

from .ventas_modelo import generar_resultados_ventas


# ============================================================
# ESTRUCTURA VACÍA
# ============================================================

def _resultado_vacio():
    return {
        "total_registros_ventas": 0,
        "total_ventas_brutas": 0,
        "total_registros_mes": 0,
        "ventas_mes": 0,
        "acumulado_anual": 0,
        "iva_recaudado": 0,
        "ingresos_netos": 0,
        "porcentaje_ingreso_neto": 0,
        "tendencia": "Sin datos",

        "top_clientes": [],
        "segmentacion_clientes": [],
        "productos_mas_vendidos": [],
        "medios_pago": [],

        "modelo": {
            "rmse": 0,
            "mape": 0,
            "r2": 0,
            "horizonte_meses": 1,
            "prediccion_ia": 0,
            "prediccion_futura": [],
            "prediccion_historica": []
        },

        "df_agg": [],
        "demanda_inventario": []
    }


# ============================================================
# GENERAR KPIs PARA EL DASHBOARD (VERSIÓN CORREGIDA)
# ============================================================

def generar_kpis_ventas():

    r = generar_resultados_ventas()

    if not r:
        return _resultado_vacio()

    # Modelo IA
    modelo = r.get("modelo", {}) or {}
    y_pred_futuro = modelo.get("y_pred_futuro", []) or []
    y_pred_hist = modelo.get("y_pred_hist", []) or []

    prediccion_ia = float(y_pred_futuro[0]) if y_pred_futuro else 0.0

    return {
        # ---------------- KPIs PRINCIPALES ----------------
        "total_registros_ventas": r.get("total_registros_ventas", 0),
        "total_ventas_brutas": r.get("total_ventas_brutas", 0),

        "total_registros_mes": r.get("total_registros_mes", 0),
        "ventas_mes": r.get("ventas_mes", 0),

        # ---------------- KPIs ANUALES ----------------
        "acumulado_anual": r.get("acumulado_anual", 0),
        "iva_recaudado": r.get("iva_recaudado", 0),
        "ingresos_netos": r.get("ingresos_netos", 0),
        "porcentaje_ingreso_neto": r.get("porcentaje_ingreso_neto", 0.81),
        "tendencia": r.get("tendencia", "Sin datos"),

        # ---------------- CLIENTES ----------------
        "top_clientes": r.get("top_clientes", []),
        "segmentacion_clientes": r.get("segmentacion_clientes", []),

        # ---------------- PRODUCTOS ----------------
        "productos_mas_vendidos": r.get("productos_mas_vendidos", []),

        # ---------------- MEDIOS DE PAGO ----------------
        "medios_pago": r.get("medios_pago", []),

        # ---------------- INVENTARIO ----------------
        "demanda_inventario": r.get("demanda_inventario", []),

        # ---------------- IA / MODELO ----------------
        "modelo": {
            "rmse": modelo.get("rmse", 0),
            "mape": modelo.get("mape", 0),
            "r2": modelo.get("r2", 0),
            "horizonte_meses": 1,
            "prediccion_ia": prediccion_ia,
            "prediccion_futura": y_pred_futuro,
            "prediccion_historica": y_pred_hist
        },

        # ---------------- SERIES PARA GRÁFICOS ----------------
        "df_agg": r.get("df_agg", [])
    }
