# ============================================================
# DASHBOARD GASTOS — Consolidación de KPIs y datasets
# ============================================================

from .gastos_modelo import generar_resultados_gastos


def generar_kpis_gastos():
    """
    Prepara todos los KPIs y datasets necesarios para el dashboard
    de gastos generales, con la misma estructura que inventarios.
    """

    r = generar_resultados_gastos(anio_dashboard=2026)

    # Si no hay datos, devolver estructura vacía
    if not r:
        return {
            "total_registros": 0,
            "gasto_total": 0,
            "promedio_mensual": 0,
            "presupuesto_proyectado": 0,
            "flujo_caja_real": 0,
            "pct_train": 0,
            "pct_test": 0,
            "factor_seguridad": 0,
            "gastos_por_mes": [],
            "ingresos_por_mes": [],
            "proyeccion_ia": []
        }

    df = r["df"]
    modelo = r["modelo"]
    presupuesto = r.get("presupuesto", {})
    flujo = r.get("flujo_caja", {})
    params = r.get("parametros_ia", {})
    ingresos_gastos_mes = r.get("ingresos_gastos_mes", [])

    # ============================
    # KPIs PRINCIPALES
    # ============================
    total_registros = len(df)
    gasto_total = sum(row.get("GastoTotal", 0) for row in df)
    promedio_mensual = gasto_total / total_registros if total_registros > 0 else 0

    # ============================
    # SERIES PARA GRÁFICOS
    # ============================

    # Gastos por mes (del dataset de gastos)
    gastos_por_mes = [
        {"Mes": f"{row['Mes']:02d}/{row['Anio']}", "Valor": row.get("GastoTotal", 0)}
        for row in df
    ]

    # Ingresos por mes (del dataset combinado)
    ingresos_por_mes = [
        {"Mes": f"{row['Mes']:02d}/{row['Anio']}", "Valor": row.get("Ingresos", 0)}
        for row in ingresos_gastos_mes
    ]

    # Proyección IA
    proyeccion_ia = [
        df[-1].get("GastoTotal", 0),
        presupuesto.get("PresupuestoSeguro", 0)
    ]

    # ============================
    # RETORNO FINAL (PLANO)
    # ============================
    return {
        "total_registros": total_registros,
        "gasto_total": gasto_total,
        "promedio_mensual": promedio_mensual,
        "presupuesto_proyectado": presupuesto.get("PresupuestoSeguro", 0),
        "flujo_caja_real": flujo.get("FlujoCajaReal", 0),

        # Parámetros IA
        "pct_train": params.get("pct_train", 0),
        "pct_test": params.get("pct_test", 0),
        "factor_seguridad": params.get("factor_seguridad", 0),

        # Series
        "gastos_por_mes": gastos_por_mes,
        "ingresos_por_mes": ingresos_por_mes,
        "proyeccion_ia": proyeccion_ia
    }
