# ============================================================
# FACTURAS_PROVEEDORES_MODELO.PY — Versión con horizonte futuro
# ============================================================

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
from db import query_all_flat, exec_sql

# ============================================================
# FUNCIONES AUXILIARES
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


def calcular_rmse(y_real, y_pred):
    if len(y_real) < 2:
        return 0
    rmse = np.sqrt(np.mean((y_real - y_pred) ** 2))
    return float(round(rmse, 6))


def calcular_mape(y_real, y_pred):
    mask = y_real != 0
    if mask.sum() == 0:
        return 0.0
    mape = np.mean(np.abs((y_real[mask] - y_pred[mask]) / y_real[mask])) * 100
    return float(round(mape, 4))


def calcular_r2(y_real, y_pred):
    if len(y_real) < 2:
        return 0.0
    ss_res = np.sum((y_real - y_pred) ** 2)
    ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
    if ss_tot == 0:
        return 0.0
    return float(round(1 - ss_res / ss_tot, 6))


def evaluar_modelo(y_real, y_pred):
    return {
        "rmse": calcular_rmse(y_real, y_pred),
        "mape": calcular_mape(y_real, y_pred),
        "r2":   calcular_r2(y_real, y_pred),
    }


def clasificar_calidad(error, cantidad_datos):
    if cantidad_datos < 2:
        return "Sin datos suficientes"
    if error == 0:
        return "Sin datos suficientes"
    if 0.1 <= error <= 0.5:
        return "Confiable"
    if error <= 1.0:
        return "Precaución"
    if error <= 2.0:
        return "Inestable"
    return "No confiable"


def seleccionar_modelo_activo():
    rows = query_all_flat("""
        SELECT ValorParametro
        FROM ParametrosNegocio
        WHERE NombreParametro = 'IA_MODELO_ACTIVO'
          AND Categoria = 'IA_MODELOS_FACTURAS'
          AND Estado = 1
    """)
    if not rows:
        return False
    return str(rows[0][0]).strip() == "1"


# ============================================================
# 1. CARGA Y PREPARACIÓN DE DATOS
# ============================================================

def obtener_datos_facturas_proveedores():
    rows = query_all_flat("""
        SELECT
            FP.Id AS FacturaId,
            C.RazonSocial AS Proveedor,
            FP.NumeroFactura,
            CONVERT(date, FP.FechaFactura) AS FechaEmision,
            CONVERT(date, FP.FechaVencimiento) AS FechaVencimiento,
            FP.Total AS MontoTotal,
            FP.EstadoFactura AS Estado
        FROM FacturasProveedores FP
        INNER JOIN Clientes C ON FP.ProveedorId = C.Id
        WHERE FP.EstadoFactura IN ('Pagada', 'Pendiente', 'Anulada')
        ORDER BY FP.FechaFactura
    """)

    df = pd.DataFrame(list(rows), columns=[
        "FacturaId", "Proveedor", "NumeroFactura",
        "FechaEmision", "FechaVencimiento",
        "MontoTotal", "Estado"
    ])

    if not df.empty:
        df["MontoTotal"] = df["MontoTotal"].astype(float)
        df["FechaEmision"] = pd.to_datetime(df["FechaEmision"])
        df["FechaVencimiento"] = pd.to_datetime(df["FechaVencimiento"], errors="coerce")

    return df


def preparar_dataset_facturas(df):
    if df.empty:
        return df

    hoy = pd.Timestamp(datetime.now().date())

    df["DiasAlVencimiento"] = (df["FechaVencimiento"] - hoy).dt.days.fillna(0).astype(int)
    df["Antiguedad"] = (hoy - df["FechaEmision"]).dt.days.fillna(0).astype(int)
    df["EsVencida"] = (df["DiasAlVencimiento"] < 0) & (df["Estado"] == "Pendiente")
    df["PeriodoEmision"] = df["FechaEmision"].dt.to_period("M").astype(str)

    umbral_riesgo = safe_float(_leer_parametro(
        "FP_UMBRAL_RIESGO_MONTO", "IA_MODELOS_FACTURAS", 5_000_000
    ))
    df["RiesgoMonto"] = df["MontoTotal"] > umbral_riesgo

    dias_alerta = int(_leer_parametro(
        "FP_DIAS_ALERTA_VENCIMIENTO", "IA_MODELOS_FACTURAS", 7
    ))
    df["EsProximaVencer"] = (
        (df["DiasAlVencimiento"] >= 0) &
        (df["DiasAlVencimiento"] <= dias_alerta) &
        (df["Estado"] == "Pendiente")
    )

    return df


def _leer_parametro(nombre, categoria, default):
    rows = query_all_flat("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = ? AND Categoria = ? AND Estado = 1
    """, (nombre, categoria))
    if rows:
        return rows[0][0]
    return default


# ============================================================
# 2. MODELO REGRESIÓN LINEAL
# ============================================================

def calcular_regresion_lineal_facturas(df):
    df_pag = df[df["Estado"] == "Pagada"].copy()

    if df_pag.empty:
        return {
            "proyeccion_pagos": 0,
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        }

    serie = (
        df_pag.groupby("PeriodoEmision")["MontoTotal"]
        .sum()
        .sort_index()
        .reset_index()
    )

    y_real = serie["MontoTotal"].values.astype(float)
    X = np.arange(len(y_real)).reshape(-1, 1)

    if len(y_real) < 2:
        return {
            "proyeccion_pagos": float(y_real[0]) if len(y_real) == 1 else 0,
            "periodos": list(serie["PeriodoEmision"]),
            "montos_historicos": list(y_real),
            "montos_proyectados": list(y_real),
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        }

    modelo = LinearRegression()
    modelo.fit(X, y_real)

    y_pred = modelo.predict(X)
    proyeccion_pagos = max(0, float(modelo.predict([[len(y_real)]])[0]))
    metricas = evaluar_modelo(y_real, y_pred)
    calidad = clasificar_calidad(metricas["rmse"], len(y_real))

    return {
        "proyeccion_pagos": round(proyeccion_pagos, 2),
        "periodos": list(serie["PeriodoEmision"]),
        "montos_historicos": [round(v, 2) for v in y_real],
        "montos_proyectados": [round(v, 2) for v in y_pred],
        "metricas": metricas,
        "calidad": calidad,
    }


# ============================================================
# 3. KPIs Y ALERTAS — Cuentas por pagar
# ============================================================

def calcular_total_cuentas_por_pagar(df):
    pend = df[df["Estado"] == "Pendiente"]
    al_dia = pend[~pend["EsVencida"] & ~pend["EsProximaVencer"]]["MontoTotal"].sum()
    prox = pend[pend["EsProximaVencer"]]["MontoTotal"].sum()
    vencido = pend[pend["EsVencida"]]["MontoTotal"].sum()

    return {
        "total": round(float(pend["MontoTotal"].sum()), 2),
        "al_dia": round(float(al_dia), 2),
        "proximo_vencimiento": round(float(prox), 2),
        "vencido": round(float(vencido), 2),
    }


def generar_alertas_vencimiento(df):
    proximas = df[df["EsProximaVencer"]].to_dict(orient="records")
    vencidas = df[df["EsVencida"]].to_dict(orient="records")
    riesgo = df[df["RiesgoMonto"] & (df["Estado"] == "Pendiente")].to_dict(orient="records")

    total_alertas = len(set(
        [r["FacturaId"] for r in proximas] +
        [r["FacturaId"] for r in vencidas] +
        [r["FacturaId"] for r in riesgo]
    ))

    return {
        "total_alertas": total_alertas,
        "proximas_vencer": proximas,
        "vencidas": vencidas,
        "alto_riesgo_monto": riesgo,
    }


def calcular_concentracion_proveedores(df):
    pend = df[df["Estado"] == "Pendiente"]
    total = pend["MontoTotal"].sum()

    if total == 0:
        return {}

    por_prov = (
        pend.groupby("Proveedor")["MontoTotal"]
        .sum()
        .sort_values(ascending=False)
    )

    top_n = 5
    top = por_prov.head(top_n)
    otros = por_prov.iloc[top_n:].sum()

    concentracion = {p: round(float(m / total * 100), 1) for p, m in top.items()}
    if otros > 0:
        concentracion["Otros"] = round(float(otros / total * 100), 1)

    return concentracion


# ============================================================
# 4. FLUJO DE CAJA CON HORIZONTE FUTURO
# ============================================================

def calcular_flujo_caja_proyectado(df_facturas, ventas_proy, gastos_proy, modo="automatico", horizonte_futuro=7):
    hoy = datetime.now()
    limite = hoy + timedelta(days=horizonte_futuro)

    # Solo facturas que vencen dentro del horizonte
    df_futuro = df_facturas[df_facturas["FechaVencimiento"] <= limite]

    cuentas = calcular_total_cuentas_por_pagar(df_futuro)
    facturas_pendientes = cuentas["total"]

    flujo = safe_float(ventas_proy) - (safe_float(gastos_proy) + facturas_pendientes)

    # Corregir flujo negativo: se trunca a 0 para el dashboard
    flujo = round(max(flujo, 0), 2)

    if modo == "manual":
        pct_gastos_gen = safe_float(_leer_parametro("FP_PCT_GASTOS_GENERALES", "IA_MODELOS_FACTURAS", 0.35))
        pct_fact_prov = safe_float(_leer_parametro("FP_PCT_FACTURAS_PROV", "IA_MODELOS_FACTURAS", 0.25))
        pct_reserva = safe_float(_leer_parametro("FP_PCT_RESERVA", "IA_MODELOS_FACTURAS", 0.40))
    else:
        df_pag = df_facturas[df_facturas["Estado"] == "Pagada"]
        total_hist = safe_float(df_pag["MontoTotal"].sum())

        if total_hist > 0:
            pct_fact_prov = min(round(float(facturas_pendientes / total_hist), 4), 1.0)
        else:
            pct_fact_prov = 0.25

        pct_gastos_gen = round(safe_float(gastos_proy) / max(safe_float(ventas_proy), 1), 4)
        pct_reserva = max(round(1 - pct_gastos_gen - pct_fact_prov, 4), 0)

    monto_disponible = max(flujo, 0)

    asignacion = {
        "gastos_generales": round(monto_disponible * pct_gastos_gen, 2),
        "facturas_proveedores": round(monto_disponible * pct_fact_prov, 2),
        "reserva": round(monto_disponible * pct_reserva, 2),
    }

    return {
        "flujo_proyectado": flujo,
        "facturas_pendientes": facturas_pendientes,
        "asignacion": asignacion,
        "modo": modo,
        "horizonte_futuro": horizonte_futuro
    }


# ============================================================
# 5. CONSOLIDACIÓN FINAL
# ============================================================

def generar_resultados_facturas():
    # Parámetros IA generales
    parametros_ia = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS' AND Estado = 1
    """)
    params_ia = {p[0]: str(p[1]).strip() for p in parametros_ia}

    usar_proyeccion = int(params_ia.get("IA_USAR_PROYECCION", 0))
    horizonte_futuro = int(params_ia.get("IA_HORIZONTE_PROYECCION_FUTURO", 7))

    # Parámetros específicos facturas
    parametros_fp = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS_FACTURAS' AND Estado = 1
    """)
    params_fp = {p[0]: str(p[1]).strip() for p in parametros_fp}

    modo_flujo = params_fp.get("FP_MODO_FLUJO_CAJA", "automatico")
    ventas_proy = safe_float(params_fp.get("FP_VENTAS_PROYECTADAS", 0))
    gastos_proy = safe_float(params_fp.get("FP_GASTOS_PROYECTADOS", 0))
    modelo_activo = seleccionar_modelo_activo()

    # Carga de datos
    df_raw = obtener_datos_facturas_proveedores()
    if df_raw.empty:
        return _resultado_vacio()

    df = preparar_dataset_facturas(df_raw)

    # Modelo regresión
    resultado_regresion = {}
    if modelo_activo:
        resultado_regresion = calcular_regresion_lineal_facturas(df)
        flujo_caja_proyectado_ia = resultado_regresion.get("proyeccion_pagos", 0)
    else:
        flujo_caja_proyectado_ia = ventas_proy

    # Cuentas por pagar (sobre todo el dataset)
    cuentas = calcular_total_cuentas_por_pagar(df)

    # Flujo de caja real (sobre todo el saldo actual)
    flujo_real = round(
        safe_float(ventas_proy) - (safe_float(gastos_proy) + cuentas["total"]),
        2
    )

    # Flujo de caja proyectado (solo horizonte futuro)
    resultado_flujo = calcular_flujo_caja_proyectado(
        df_facturas=df,
        ventas_proy=ventas_proy if not modelo_activo else flujo_caja_proyectado_ia,
        gastos_proy=gastos_proy,
        modo=modo_flujo,
        horizonte_futuro=horizonte_futuro
    )

    # Alertas
    alertas = generar_alertas_vencimiento(df)

    # Concentración
    concentracion = calcular_concentracion_proveedores(df)

    # Conteo estados
    conteo_estados = df["Estado"].value_counts().to_dict()

    # Monto por proveedor (pendiente)
    monto_x_proveedor = (
        df[df["Estado"] == "Pendiente"]
        .groupby("Proveedor")["MontoTotal"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
        .rename(columns={"MontoTotal": "Monto"})
        .to_dict(orient="records")
    )

    # Tabla completa
    columnas_tabla = [
        "FacturaId", "NumeroFactura", "Proveedor",
        "FechaEmision", "FechaVencimiento", "MontoTotal",
        "Estado", "DiasAlVencimiento", "EsVencida",
        "EsProximaVencer", "RiesgoMonto"
    ]
    df_tabla = df[columnas_tabla].copy()
    df_tabla["FechaEmision"] = df_tabla["FechaEmision"].dt.strftime("%Y-%m-%d")
    df_tabla["FechaVencimiento"] = df_tabla["FechaVencimiento"].dt.strftime("%Y-%m-%d")
    lista_facturas = df_tabla.to_dict(orient="records")

    # Guardar proyección en BD (opcional)
    if usar_proyeccion and modelo_activo and resultado_regresion:
        rmse = resultado_regresion["metricas"]["rmse"]
        calidad = resultado_regresion["calidad"]
        proy = resultado_regresion["proyeccion_pagos"]

        exec_sql("""
            MERGE INTO ProyeccionFacturasProveedor AS target
            USING (SELECT 'global' AS Clave) AS source
            ON target.Clave = source.Clave
            WHEN MATCHED THEN
                UPDATE SET
                    PagoProyectado      = ?,
                    FlujoCajaProyect    = ?,
                    ErrorModelo         = ?,
                    CalidadModelo       = ?,
                    FechaProyeccion     = GETDATE(),
                    UltimaActualizacion = GETDATE()
            WHEN NOT MATCHED THEN
                INSERT (Clave, PagoProyectado, FlujoCajaProyect,
                        ErrorModelo, CalidadModelo, FechaProyeccion)
                VALUES ('global', ?, ?, ?, ?, GETDATE());
        """, (
            proy, resultado_flujo["flujo_proyectado"], rmse, calidad,
            proy, resultado_flujo["flujo_proyectado"], rmse, calidad,
        ))

    return {
        "total_facturas":                   int(len(df)),
        "total_cuentas_por_pagar":          cuentas["total"],
        "por_pagar_al_dia":                 cuentas["al_dia"],
        "por_pagar_proximo_vencimiento":    cuentas["proximo_vencimiento"],
        "por_pagar_vencido":                cuentas["vencido"],
        "flujo_caja_real":                  flujo_real,
        "flujo_caja_proyectado":            resultado_flujo["flujo_proyectado"],
        "asignacion_proyectada":            resultado_flujo["asignacion"],
        "modo_flujo":                       resultado_flujo["modo"],
        "alertas_vencimiento":              alertas["total_alertas"],
        "detalle_alertas":                  alertas,
        "concentracion_proveedores":        concentracion,
        "conteo_estados":                   conteo_estados,
        "monto_por_proveedor":              monto_x_proveedor,
        "modelo_activo":                    modelo_activo,
        "proyeccion_regresion":             resultado_regresion,
        "lista_facturas":                   lista_facturas,
        "horizonte_futuro":                 horizonte_futuro,
    }


def _resultado_vacio():
    return {
        "total_facturas":                   0,
        "total_cuentas_por_pagar":          0,
        "por_pagar_al_dia":                 0,
        "por_pagar_proximo_vencimiento":    0,
        "por_pagar_vencido":                0,
        "flujo_caja_real":                  0,
        "flujo_caja_proyectado":            0,
        "asignacion_proyectada":            {},
        "modo_flujo":                       "automatico",
        "alertas_vencimiento":              0,
        "detalle_alertas":                  {},
        "concentracion_proveedores":        {},
        "conteo_estados":                   {},
        "monto_por_proveedor":              [],
        "modelo_activo":                    False,
        "proyeccion_regresion":             {},
        "lista_facturas":                   [],
        "horizonte_futuro":                 7,
    }
