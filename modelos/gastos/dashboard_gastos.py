# ============================================================
# DASHBOARD GASTOS — Consolidación de KPIs y datasets
# ============================================================

from .gastos_modelo import generar_resultados_gastos


def _resultado_vacio():
    return {
        "total_registros": 0,
        "gasto_total": 0,
        "gasto_mes_actual": 0,
        "presupuesto_proyectado": 0,
        "flujo_caja_proyectado": 0,
        "proyeccion_gastos": 0,

        # Parámetros IA
        "pct_train": 0,
        "pct_test": 0,
        "factor_seguridad": 0,

        # Series
        "gastos_por_mes": [],
        "ingresos_por_mes": [],
        "proyeccion_ia": [],
        "tendencia_gastos": {
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        },
    }


def generar_kpis_gastos(anio_dashboard=None):
    """
    Prepara todos los KPIs y datasets necesarios para el dashboard
    de gastos generales, alineado con el dashboard de ventas.
    """

    r = generar_resultados_gastos(anio_dashboard=anio_dashboard)

    if not r:
        return _resultado_vacio()

    # ============================
    # Datos base del modelo
    # ============================
    df = r.get("df", [])
    modelo = r.get("modelo")
    presupuesto = r.get("presupuesto", {})
    flujo = r.get("flujo_caja", {})
    params = r.get("parametros_ia", {})
    ingresos_gastos_mes = r.get("ingresos_gastos_mes", [])
    tendencia = r.get("tendencia_gastos", {})

    # ============================
    # KPIs PRINCIPALES
    # ============================
    total_registros = int(r.get("total_registros_gastos", 0))
    gasto_total = float(r.get("total_gastos", 0.0))
    gasto_mes_actual = float(r.get("gasto_mes_actual", 0.0))
    total_registros_mes = int(r.get("total_registros_mes", 0))

    # Proyección de gastos (tendencia IA)
    proyeccion_gastos = tendencia.get("proyeccion_gastos", 0)

    # Presupuesto próximo mes (IA + parámetro base)
    presupuesto_proyectado = float(r.get("presupuesto_proximo_mes", 0.0))

    # Flujo de caja proyectado
    flujo_caja_proyectado = float(flujo.get("flujo_caja_proyectado", 0.0))

    # ============================
    # SERIES PARA GRÁFICOS
    # ============================

    # Gastos por mes (dataset agregado mensual)
    gastos_por_mes = []
    for row in df:
        fecha = row.get("Fecha")
        etiqueta = fecha[:7] if isinstance(fecha, str) else fecha.strftime("%Y-%m")
        gastos_por_mes.append({
            "Mes": etiqueta,
            "Valor": row.get("GastoTotal", 0)
        })

    # Ingresos por mes (dataset combinado)
    ingresos_por_mes = [
        {
            "Mes": f"{row['Anio']}-{row['Mes']:02d}",
            "Valor": row.get("Ingresos", 0)
        }
        for row in ingresos_gastos_mes
    ]

    # Proyección IA (último gasto vs presupuesto seguro)
    ultimo_gasto = df[-1].get("GastoTotal", 0) if df else 0
    proyeccion_ia = [
        ultimo_gasto,
        presupuesto.get("PresupuestoSeguro", 0)
    ]

    # ============================
    # RETORNO FINAL
    # ============================
    return {
        # KPIs
        "total_registros": total_registros,
        "gasto_total": gasto_total,
        "gasto_mes_actual": gasto_mes_actual,
        "total_registros_mes": total_registros_mes,
        "presupuesto_proyectado": presupuesto_proyectado,
        "flujo_caja_proyectado": flujo_caja_proyectado,
        "proyeccion_gastos": proyeccion_gastos,

        # Parámetros IA
        "pct_train": params.get("pct_train", 0),
        "pct_test": params.get("pct_test", 0),
        "factor_seguridad": params.get("factor_seguridad", 0),

        # Series para gráficas
        "gastos_por_mes": gastos_por_mes,
        "ingresos_por_mes": ingresos_por_mes,
        "proyeccion_ia": proyeccion_ia,

        # Tendencia completa
        "tendencia_gastos": tendencia,
    }
