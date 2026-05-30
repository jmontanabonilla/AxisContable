# ============================================================
# GASTOS_MODELO.PY — Regresión Lineal con Proyección IA
# ============================================================

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from datetime import datetime
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


# ============================================================
# 1. CARGA DE DATOS DE GASTOS
# ============================================================

def obtener_datos_gastos():
    rows = query_all_flat("""
        SELECT 
            CONVERT(date, Fecha),
            Descripcion,
            Valor,
            TipoRegistroId
        FROM GastosGenerales
        WHERE Estado = 1
        ORDER BY Fecha
    """)

    df = pd.DataFrame(list(rows), columns=[
        "Fecha", "Descripcion", "Valor", "TipoRegistroId"
    ])

    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        df["Valor"] = df["Valor"].astype(float)

    return df


def preparar_dataset_gastos():
    df = obtener_datos_gastos()

    if df.empty:
        return df

    df["Anio"] = df["Fecha"].dt.year
    df["Mes"] = df["Fecha"].dt.month
    df["Trimestre"] = df["Fecha"].dt.quarter

    df["TipoGasto"] = df["TipoRegistroId"].map({
        1: "Fijo",
        2: "Bimestral",
        3: "Anual",
        4: "Variable"
    })

    df_agg = df.groupby(["Anio", "Mes", "Trimestre"], as_index=False) \
               .agg(GastoTotal=("Valor", "sum"))

    return df_agg


# ============================================================
# 1B. INGRESOS VS GASTOS POR MES (PARA DASHBOARD)
# ============================================================

def obtener_ingresos_vs_gastos_por_mes(anio=None):
    """
    Devuelve una lista de dicts:
    [
      {"Anio": 2026, "Mes": 5, "Ingresos": 579761.0, "Gastos": 3500000.0},
      ...
    ]
    combinando Ventas e GastosGenerales con FULL OUTER JOIN por año/mes.
    """
    if anio is None:
        anio = datetime.today().year

    rows = query_all_flat(f"""
        WITH Ingresos AS (
            SELECT 
                YEAR(FechaEmision) AS Anio,
                MONTH(FechaEmision) AS Mes,
                SUM(TotalVenta) AS TotalIngresos
            FROM Ventas
            WHERE Estado = 1
              AND YEAR(FechaEmision) = ?
            GROUP BY YEAR(FechaEmision), MONTH(FechaEmision)
        ),
        Gastos AS (
            SELECT 
                YEAR(Fecha) AS Anio,
                MONTH(Fecha) AS Mes,
                SUM(Valor) AS TotalGastos
            FROM GastosGenerales
            WHERE Estado = 1
              AND YEAR(Fecha) = ?
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
    """, (anio, anio))

    resultados = []
    for r in rows:
        resultados.append({
            "Anio": int(r[0]),
            "Mes": int(r[1]),
            "Ingresos": float(r[2]),
            "Gastos": float(r[3])
        })
    return resultados


# ============================================================
# 2. PARÁMETROS IA DESDE BD
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

        # Valores crudos para mostrar en dashboard
        "pct_train_raw": params.get("IA_PORCENTAJE_ENTRENAMIENTO", 80),
        "pct_test_raw": params.get("IA_PORCENTAJE_PRUEBA", 20),
        "factor_seguridad_raw": params.get("IA_FACTOR_SEGURIDAD", 1.2)
    }


# ============================================================
# 3. MODELO PREDICTIVO
# ============================================================

def dividir_dataset(df, pct_train):
    df = df.sort_values(["Anio", "Mes"])
    n = len(df)
    n_train = int(n * pct_train)

    df_train = df.iloc[:n_train]
    df_test = df.iloc[n_train:]

    return df_train, df_test


def calcular_regresion_lineal_gastos(df, params):
    pct_train = params["pct_train"]

    df_train, df_test = dividir_dataset(df, pct_train)

    if len(df_train) < 2 or len(df_test) < 1:
        return None

    X_train = df_train[["Anio", "Mes", "Trimestre"]]
    y_train = df_train["GastoTotal"]

    X_test = df_test[["Anio", "Mes", "Trimestre"]]
    y_test = df_test["GastoTotal"]

    modelo = LinearRegression()
    modelo.fit(X_train, y_train)

    y_pred = modelo.predict(X_test)

    rmse = calcular_rmse(y_test.values, y_pred)
    r2 = modelo.score(X_test, y_test)

    y_real_safe = np.where(y_test == 0, 1, y_test)
    mape = float(np.mean(np.abs((y_test - y_pred) / y_real_safe)) * 100)

    # Solo devolver valores numéricos y listas, no el objeto modelo
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
# 4. PRESUPUESTO Y PROYECCIÓN
# ============================================================

def calcular_presupuesto_optimo(df, factor_seguridad):
    if df.empty:
        return None

    df = df.sort_values(["Anio", "Mes"])
    ultimo = df.iloc[-1]

    anio = int(ultimo["Anio"])
    mes = int(ultimo["Mes"]) + 1
    if mes > 12:
        mes = 1
        anio += 1

    trimestre = (mes - 1) // 3 + 1

    # Promedio simple como proyección base
    promedio_gasto = df["GastoTotal"].mean()
    presupuesto = promedio_gasto * factor_seguridad

    return {
        "Anio": anio,
        "Mes": mes,
        "Trimestre": trimestre,
        "Proyeccion": float(promedio_gasto),
        "PresupuestoSeguro": float(presupuesto)
    }


# ============================================================
# 5. FLUJO DE CAJA REAL (placeholder)
# ============================================================

def calcular_flujo_caja_real():
    rows = query_all_flat("""
        SELECT 0 AS Ventas, 0 AS Gastos, 0 AS Facturas
    """)

    ventas, gastos, facturas = rows[0]

    return {
        "Ventas": ventas,
        "Gastos": gastos,
        "Facturas": facturas,
        "FlujoCajaReal": ventas - (gastos + facturas)
    }


# ============================================================
# 6. CONSOLIDACIÓN FINAL
# ============================================================

def generar_resultados_gastos(anio_dashboard=None):
    """
    Devuelve todo lo necesario para el dashboard:
    - df: dataset de gastos agregados
    - modelo: info de regresión
    - presupuesto: proyección y presupuesto seguro
    - flujo_caja: placeholder
    - parametros_ia: parámetros crudos
    - ingresos_gastos_mes: ingresos vs gastos por mes (para la gráfica)
    """
    params = obtener_parametros_ia()

    df = preparar_dataset_gastos()
    if df.empty:
        return {}

    if anio_dashboard is None:
        anio_dashboard = datetime.today().year

    modelo_info = calcular_regresion_lineal_gastos(df, params)
    presupuesto = calcular_presupuesto_optimo(df, params["factor_seguridad"])
    flujo_caja = calcular_flujo_caja_real()
    ingresos_gastos_mes = obtener_ingresos_vs_gastos_por_mes(anio_dashboard)

    return {
        "df": df.to_dict(orient="records"),
        "modelo": modelo_info,
        "presupuesto": presupuesto,
        "flujo_caja": flujo_caja,
        "parametros_ia": {
            "pct_train": params["pct_train_raw"],
            "pct_test": params["pct_test_raw"],
            "factor_seguridad": params["factor_seguridad_raw"]
        },
        "ingresos_gastos_mes": ingresos_gastos_mes
    }
