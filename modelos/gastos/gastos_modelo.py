# ============================================================
# GASTOS_MODELO.PY — IA + Tendencia + Proyección + Auditoría
# ============================================================

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from datetime import datetime
from db import query_all_flat, exec_sql
from modelos.flujo_caja_proyectado.flujocaja_proyectado import (
    calcular_flujo_caja_proyectado_global,
)

# ============================================================
# UTILIDADES
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
    return float(np.sqrt(np.mean((y_real - y_pred) ** 2)))


# ============================================================
# PARÁMETROS IA Y TENDENCIA GASTOS
# ============================================================

def obtener_parametros_ia():
    rows = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS' AND Estado = 1
    """)

    params = {r[0]: safe_float(r[1]) for r in rows}

    return {
        "pct_train": params.get("IA_PORCENTAJE_ENTRENAMIENTO", 80) / 100,
        "pct_test": params.get("IA_PORCENTAJE_PRUEBA", 20) / 100,
        "factor_seguridad": params.get("IA_FACTOR_SEGURIDAD", 1.2),

        "pct_train_raw": params.get("IA_PORCENTAJE_ENTRENAMIENTO", 80),
        "pct_test_raw": params.get("IA_PORCENTAJE_PRUEBA", 20),
        "factor_seguridad_raw": params.get("IA_FACTOR_SEGURIDAD", 1.2)
    }


def obtener_meses_tendencia_gastos():
    rows = query_all_flat("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'GG_MESES_TENDENCIA_GASTOS'
          AND Categoria = 'IA_MODELOS_GASTOS'
    """)
    try:
        return int(rows[0][0]) if rows else 6
    except:
        return 6


def obtener_horizonte_proyeccion_gastos():
    rows = query_all_flat("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'GG_HORIZONTE_PROYECCION_GASTOS'
          AND Categoria = 'IA_MODELOS_GASTOS'
    """)
    try:
        return int(rows[0][0]) if rows else 1
    except:
        return 1


# ============================================================
# 1. CARGA DE DATOS DE GASTOS
# ============================================================

def obtener_datos_gastos():
    rows = query_all_flat("""
        SELECT 
            Id,
            CONVERT(date, Fecha),
            Descripcion,
            Valor,
            TipoRegistroId
        FROM GastosGenerales
        WHERE Estado = 1
        ORDER BY Fecha
    """)

    df = pd.DataFrame(list(rows), columns=[
        "Id", "Fecha", "Descripcion", "Valor", "TipoRegistroId"
    ])

    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        df["Valor"] = df["Valor"].astype(float)

    return df



def preparar_dataset_gastos():
    df = obtener_datos_gastos()
    if df.empty:
        return df

    df["Periodo"] = df["Fecha"].dt.to_period("M")
    df_agg = df.groupby("Periodo", as_index=False).agg(GastoTotal=("Valor", "sum"))
    df_agg["Fecha"] = df_agg["Periodo"].dt.to_timestamp()

    return df_agg


# ============================================================
# 1B. INGRESOS VS GASTOS POR MES
# ============================================================

def obtener_ingresos_vs_gastos_por_mes_todos():
    rows = query_all_flat("""
        WITH Ingresos AS (
            SELECT 
                YEAR(FechaEmision) AS Anio,
                MONTH(FechaEmision) AS Mes,
                SUM(TotalVenta) AS TotalIngresos
            FROM Ventas
            WHERE Estado = 1
            GROUP BY YEAR(FechaEmision), MONTH(FechaEmision)
        ),
        Gastos AS (
            SELECT 
                YEAR(Fecha) AS Anio,
                MONTH(Fecha) AS Mes,
                SUM(Valor) AS TotalGastos
            FROM GastosGenerales
            WHERE Estado = 1
            GROUP BY YEAR(Fecha), MONTH(Fecha)
        )
        SELECT 
            COALESCE(i.Anio, g.Anio) AS Anio,
            COALESCE(i.Mes, g.Mes) AS Mes,
            ISNULL(i.TotalIngresos, 0) AS Ingresos,
            ISNULL(g.TotalGastos, 0) AS Gastos
        FROM Ingresos i
        FULL OUTER JOIN Gastos g
            ON i.Anio = g.Anio AND i.Mes = g.Mes
        ORDER BY Anio, Mes;
    """)

    return [
        {"Anio": int(r[0]), "Mes": int(r[1]), "Ingresos": float(r[2]), "Gastos": float(r[3])}
        for r in rows
    ]


# ============================================================
# 2. MODELO IA (TRAIN/TEST)
# ============================================================

def dividir_dataset(df, pct_train):
    df = df.sort_values(["Periodo"])
    n = len(df)
    n_train = int(n * pct_train)
    return df.iloc[:n_train], df.iloc[n_train:]


def calcular_regresion_lineal_gastos_ia(df, params):
    if df.empty:
        return None

    df = df.copy()
    df["Anio"] = df["Fecha"].dt.year
    df["Mes"] = df["Fecha"].dt.month
    df["Trimestre"] = df["Fecha"].dt.quarter

    df_train, df_test = dividir_dataset(df, params["pct_train"])

    if len(df_train) < 2 or len(df_test) < 1:
        return None

    modelo = LinearRegression()
    modelo.fit(df_train[["Anio", "Mes", "Trimestre"]], df_train["GastoTotal"])

    y_pred = modelo.predict(df_test[["Anio", "Mes", "Trimestre"]])
    y_test = df_test["GastoTotal"]

    rmse = calcular_rmse(y_test.values, y_pred)
    r2 = modelo.score(df_test[["Anio", "Mes", "Trimestre"]], y_test)
    y_real_safe = np.where(y_test == 0, 1, y_test)
    mape = float(np.mean(np.abs((y_test - y_pred) / y_real_safe)) * 100)

    return {
        "rmse": rmse,
        "mape": mape,
        "r2": r2,
        "y_test": y_test.tolist(),
        "y_pred": y_pred.tolist(),
        "pct_train": params["pct_train_raw"],
        "pct_test": params["pct_test_raw"],
        "factor_seguridad": params["factor_seguridad_raw"]
    }


# ============================================================
# 3. REGRESIÓN SIMPLE — TENDENCIA + PROYECCIÓN
# ============================================================

def calcular_regresion_lineal_gastos(df):
    meses_tendencia = obtener_meses_tendencia_gastos()
    horizonte_proy = obtener_horizonte_proyeccion_gastos()

    if df.empty:
        return {
            "proyeccion_gastos": 0,
            "meses_tendencia": meses_tendencia,
            "horizonte_proyeccion": horizonte_proy,
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        }

    df = df.sort_values("Fecha")
    serie = df.tail(meses_tendencia)

    y_real = serie["GastoTotal"].values.astype(float)
    X = np.arange(len(y_real)).reshape(-1, 1)
    periodos_str = [p.strftime("%Y-%m") for p in serie["Fecha"]]

    if len(y_real) < 2:
        incremento = y_real[0] * 0.05
        y_future = [y_real[0] + incremento * (i + 1) for i in range(horizonte_proy)]
        proy = round(sum(y_future) / len(y_future), 2)

        return {
            "proyeccion_gastos": proy,
            "meses_tendencia": meses_tendencia,
            "horizonte_proyeccion": horizonte_proy,
            "periodos": periodos_str + [
                (serie["Fecha"].iloc[-1] + pd.DateOffset(months=i + 1)).strftime("%Y-%m")
                for i in range(horizonte_proy)
            ],
            "montos_historicos": [round(v, 2) for v in y_real],
            "montos_proyectados": [round(v, 2) for v in list(y_real) + y_future],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Proyección estimada (1 mes histórico)",
        }

    modelo = LinearRegression()
    modelo.fit(X, y_real)
    y_pred = modelo.predict(X)

    X_future = np.arange(len(y_real), len(y_real) + horizonte_proy).reshape(-1, 1)
    y_future = modelo.predict(X_future)

    proy = round(sum(y_future) / len(y_future), 2)
    if proy < 0:
        proy = 0

    rmse = calcular_rmse(y_real, y_pred)
    mape = float(np.mean(np.abs((y_real - y_pred) / y_real)) * 100)
    r2 = float(modelo.score(X, y_real))

    return {
        "proyeccion_gastos": proy,
        "meses_tendencia": meses_tendencia,
        "horizonte_proyeccion": horizonte_proy,
        "periodos": periodos_str + [
            (serie["Fecha"].iloc[-1] + pd.DateOffset(months=i + 1)).strftime("%Y-%m")
            for i in range(horizonte_proy)
        ],
        "montos_historicos": [round(v, 2) for v in y_real],
        "montos_proyectados": [round(v, 2) for v in list(y_pred) + list(y_future)],
        "metricas": {"rmse": rmse, "mape": mape, "r2": r2},
        "calidad": "Confiable" if r2 >= 0.6 else "Moderada",
    }


# ============================================================
# 4. PROYECCIÓN GASTOS — BD
# ============================================================

def obtener_proyeccion_gastos_desde_bd():
    row = query_all_flat("""
        SELECT TOP 1
            GastoProyectado,
            MesesTendencia,
            HorizonteProyeccion,
            RMSE,
            MAPE,
            R2,
            CalidadModelo,
            FechaCalculo,
            ModoCalculo
        FROM ProyeccionGastos
        ORDER BY Id DESC
    """)

    if not row:
        return None

    r = row[0]
    return {
        "GastoProyectado": safe_float(r[0]),
        "MesesTendencia": int(r[1]),
        "HorizonteProyeccion": int(r[2]),
        "RMSE": safe_float(r[3]),
        "MAPE": safe_float(r[4]),
        "R2": safe_float(r[5]),
        "CalidadModelo": r[6],
        "FechaCalculo": r[7],
        "ModoCalculo": r[8],
    }


def archivar_proyeccion_gastos(usuario_id=None):
    if usuario_id is None:
        usuario_id = 1  # valor por defecto

    row = query_all_flat("""
        SELECT TOP 1
            GastoProyectado,
            MesesTendencia,
            HorizonteProyeccion,
            RMSE,
            MAPE,
            R2,
            CalidadModelo,
            FechaCalculo
        FROM ProyeccionGastos
        ORDER BY Id DESC
    """)

    if not row:
        return

    r = row[0]

    exec_sql("""
        INSERT INTO AuditoriaProyeccionGastos (
            GastoProyectado,
            MesesTendencia,
            HorizonteProyeccion,
            RMSE,
            MAPE,
            R2,
            CalidadModelo,
            FechaCalculo,
            UsuarioId
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        safe_float(r[0]),
        int(r[1]),
        int(r[2]),
        safe_float(r[3]),
        safe_float(r[4]),
        safe_float(r[5]),
        r[6],
        r[7],
        usuario_id
    ))


def guardar_proyeccion_gastos(proy):
    exec_sql("""
        INSERT INTO ProyeccionGastos (
            GastoProyectado,
            MesesTendencia,
            HorizonteProyeccion,
            RMSE,
            MAPE,
            R2,
            CalidadModelo,
            ModoCalculo
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'automatico')
    """, (
        proy["proyeccion_gastos"],
        proy["meses_tendencia"],
        proy["horizonte_proyeccion"],
        proy["metricas"]["rmse"],
        proy["metricas"]["mape"],
        proy["metricas"]["r2"],
        proy["calidad"]
    ))


# ============================================================
# 5. PRESUPUESTO Y PROYECCIÓN (USANDO FACTOR DE SEGURIDAD IA)
# ============================================================

def calcular_presupuesto_optimo(df, factor_seguridad):
    """
    Calcula el presupuesto del próximo mes basado en:
    - Promedio histórico de gastos
    - Factor de seguridad IA (ej: 1.2 = +20%)
    - Parámetro base definido por el usuario (GG_PRESUPUESTO_BASE)
    """

    # ============================
    # 1️⃣ Leer parámetro base del usuario
    # ============================
    row = query_all_flat("""
        SELECT ValorParametro
        FROM ParametrosNegocio
        WHERE NombreParametro = 'GG_PRESUPUESTO_BASE'
          AND Categoria = 'IA_MODELOS_GASTOS'
          AND Estado = 1
    """)

    presupuesto_base = safe_float(row[0][0]) if row else 0.0

    # ============================
    # 2️⃣ Si no hay datos, retornar base
    # ============================
    if df.empty:
        return {
            "Anio": None,
            "Mes": None,
            "Trimestre": None,
            "Proyeccion": 0,
            "PresupuestoSeguro": presupuesto_base
        }

    df = df.sort_values("Fecha")

    # ============================
    # 3️⃣ Promedio histórico mensual
    # ============================
    promedio_gasto = df["GastoTotal"].mean()

    # ============================
    # 4️⃣ Presupuesto seguro = promedio * factor IA + base usuario
    # ============================
    presupuesto = (promedio_gasto * factor_seguridad) + presupuesto_base

    # ============================
    # 5️⃣ Calcular próximo mes
    # ============================
    ultimo = df.iloc[-1]
    anio = int(ultimo["Fecha"].year)
    mes = int(ultimo["Fecha"].month) + 1

    if mes > 12:
        mes = 1
        anio += 1

    trimestre = (mes - 1) // 3 + 1

    # ============================
    # 6️⃣ Retorno final
    # ============================
    return {
        "Anio": anio,
        "Mes": mes,
        "Trimestre": trimestre,
        "Proyeccion": float(promedio_gasto),
        "PresupuestoSeguro": float(presupuesto)
    }


# ============================================================
# 6. FLUJO DE CAJA PROYECTADO
# ============================================================

def obtener_flujo_proyectado_desde_bd():
    row = query_all_flat("""
        SELECT TOP 1 
            FlujoCajaProyectado,
            PorcentajeGastos,
            PorcentajeFacturas,
            PorcentajeReserva,
            FechaCalculo,
            ModoCalculo
        FROM ProyeccionFlujoCaja
        ORDER BY Id DESC
    """)

    if not row:
        flujo = 0.0
        pg, pf, pr = 40.0, 40.0, 20.0
        fecha = ""
        modo = "automatico"
    else:
        r = row[0]
        flujo = safe_float(r[0])
        pg = safe_float(r[1])
        pf = safe_float(r[2])
        pr = safe_float(r[3])
        fecha = r[4]
        modo = r[5]

    asignacion = {
        "gastos_generales": round(flujo * (pg / 100), 2),
        "facturas_proveedores": round(flujo * (pf / 100), 2),
        "reserva": round(flujo * (pr / 100), 2),
    }

    return {
        "flujo_caja_proyectado": flujo,
        "porcentaje_gastos": pg,
        "porcentaje_facturas": pf,
        "porcentaje_reserva": pr,
        "fecha_calculo_flujo": fecha,
        "modo_flujo": modo,
        "asignacion_proyectada": asignacion
    }


# ============================================================
#  CONSOLIDACIÓN FINAL PARA DASHBOARD
# ============================================================

def generar_resultados_gastos(anio_dashboard=None):
    params = obtener_parametros_ia()
    df_agg = preparar_dataset_gastos()

    if df_agg.empty:
        return {}

    # ============================
    # IA (train/test)
    # ============================
    modelo_ia = calcular_regresion_lineal_gastos_ia(df_agg, params)

    # ============================
    # Presupuesto seguro (IA + parámetro base)
    # ============================
    presupuesto = calcular_presupuesto_optimo(df_agg, params["factor_seguridad"])

    # ============================
    # Flujo de caja proyectado
    # ============================
    calcular_flujo_caja_proyectado_global()
    flujo = obtener_flujo_proyectado_desde_bd()

    # ============================
    # Ingresos vs gastos por mes
    # ============================
    if anio_dashboard is None:
        ingresos_gastos_mes = obtener_ingresos_vs_gastos_por_mes_todos()
    else:
        ingresos_gastos_mes = obtener_ingresos_vs_gastos_por_mes_todos(anio_dashboard)

    # ============================
    # Tendencia y proyección de gastos
    # ============================
    tendencia = calcular_regresion_lineal_gastos(df_agg)

    # ============================
    # Persistencia en BD (auditoría + proyección)
    # ============================
    archivar_proyeccion_gastos()
    guardar_proyeccion_gastos(tendencia)

    # ============================
    # KPIs PRINCIPALES PARA DASHBOARD
    # ============================
    df_detalle = obtener_datos_gastos()
    total_registros_gastos = len(df_detalle)
    total_gastos = float(df_detalle["Valor"].sum()) if not df_detalle.empty else 0.0

    # ============================
    # Gasto del mes actual
    # ============================
    hoy = datetime.now()
    mes_actual = hoy.month
    anio_actual = hoy.year

    if not df_detalle.empty:
        df_mes = df_detalle[
            (df_detalle["Fecha"].dt.month == mes_actual) &
            (df_detalle["Fecha"].dt.year == anio_actual)
        ]

        # Total de gasto del mes
        gasto_mes_actual = float(df_mes["Valor"].sum()) if not df_mes.empty else 0.0

        # Total de registros del mes (únicos)
        total_registros_mes = int(len(df_mes.drop_duplicates(subset=["Id"]))) if not df_mes.empty else 0
    else:
        gasto_mes_actual = 0.0
        total_registros_mes = 0

    # ============================
    # Presupuesto próximo mes (seguro)
    # ============================
    presupuesto_proximo_mes = float(presupuesto["PresupuestoSeguro"])

    # ============================
    # Retorno final
    # ============================
    return {
        "df": df_agg.to_dict(orient="records"),
        "modelo": modelo_ia,
        "presupuesto": presupuesto,
        "flujo_caja": flujo,

        "parametros_ia": {
            "pct_train": params["pct_train_raw"],
            "pct_test": params["pct_test_raw"],
            "factor_seguridad": params["factor_seguridad_raw"]
        },

        "ingresos_gastos_mes": ingresos_gastos_mes,
        "tendencia_gastos": tendencia,

        "total_registros_gastos": int(total_registros_gastos),
        "total_gastos": total_gastos,
        "gasto_mes_actual": gasto_mes_actual,
        "total_registros_mes": total_registros_mes,
        "presupuesto_proximo_mes": presupuesto_proximo_mes
    }
