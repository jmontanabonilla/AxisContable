# ============================================================
# INVENTARIOS_MODELO.PY — Regresión Lineal con Proyección IA
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
    """Error RMSE del modelo."""
    if len(y_real) < 2:
        return 0
    return float(np.sqrt(np.mean((y_real - y_pred) ** 2)))


def clasificar_calidad(error):
    """Clasifica el modelo según el error."""
    if error == 0:
        return "Sin datos"
    if error < 0.5:
        return "Bueno"
    if error < 1.0:
        return "Aceptable"
    if error < 2.0:
        return "Regular"
    return "Malo"


# ============================================================
# 1. CARGA DE DATOS
# ============================================================

def obtener_datos_ventas():
    rows = query_all_flat("""
        SELECT dv.InventarioId,
               CONVERT(date, v.FechaEmision),
               SUM(dv.Cantidad)
        FROM DetalleVenta dv
        INNER JOIN Ventas v ON dv.VentaId = v.Id
        WHERE dv.Estado = 1 AND dv.InventarioId IS NOT NULL
        GROUP BY dv.InventarioId, CONVERT(date, v.FechaEmision)
        ORDER BY dv.InventarioId, CONVERT(date, v.FechaEmision)
    """)

    df = pd.DataFrame(list(rows), columns=["InventarioId", "Fecha", "CantidadDia"])
    if not df.empty:
        df["CantidadDia"] = df["CantidadDia"].astype(float)
        df["Fecha"] = pd.to_datetime(df["Fecha"])
    return df


def obtener_datos_inventario():
    rows = query_all_flat("""
        SELECT I.Id,
               I.NombreProducto,
               I.Costo,
               I.TieneFechaVencimiento,
               ISNULL(SUM(D.Cantidad), 0) AS StockActual,
               C.NombreCategoria,
               MIN(D.FechaVencimiento) AS FechaVencimiento
        FROM Inventarios I
        LEFT JOIN DetalleInventario D
               ON I.Id = D.InventarioId AND D.Estado = 1
        LEFT JOIN CategoriasInventario C
               ON I.CategoriaInventarioId = C.Id
        WHERE I.Estado = 1
        GROUP BY I.Id, I.NombreProducto, I.Costo, I.TieneFechaVencimiento, C.NombreCategoria
    """)

    df = pd.DataFrame(list(rows), columns=[
        "InventarioId",
        "NombreProducto",
        "Costo",
        "TieneFechaVencimiento",
        "StockActual",
        "Categoria",
        "FechaVencimiento"
    ])

    if not df.empty:
        df["StockActual"] = df["StockActual"].astype(float)
        df["Costo"] = df["Costo"].astype(float)
        df["TieneFechaVencimiento"] = df["TieneFechaVencimiento"].astype(int)
        df["FechaVencimiento"] = pd.to_datetime(df["FechaVencimiento"], errors="coerce")

    return df


def preparar_dataset_inventario():
    df_v = obtener_datos_ventas()
    df_i = obtener_datos_inventario()

    if df_v.empty:
        df_i["CantidadDia"] = 0.0
        return df_i

    df = pd.merge(df_i, df_v, on="InventarioId", how="left")
    df["CantidadDia"] = df["CantidadDia"].fillna(0)
    return df


# ============================================================
# 2. CONSOLIDACIÓN FINAL
# ============================================================

def generar_resultados_inventario():

    # ---------------- PARÁMETROS IA ----------------
    parametros_ia = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS' AND Estado = 1
    """)

    params_ia = {p[0]: str(p[1]).strip() for p in parametros_ia}

    usar_proyeccion = int(params_ia.get("IA_USAR_PROYECCION", 0))
    horizonte_futuro = int(params_ia.get("IA_HORIZONTE_PROYECCION_FUTURO", 7))
    factor_seguridad = safe_float(params_ia.get("IA_FACTOR_SEGURIDAD", 1.0))

    # ---------------- PARÁMETROS INVENTARIOS ----------------
    parametros_inv = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS_INVENTARIOS' AND Estado = 1
    """)

    params_inv = {p[0]: str(p[1]).strip() for p in parametros_inv}

    margen_potencial_factor = safe_float(params_inv.get("IA_MARGEN_POTENCIAL", 0.7))
    dias_alerta_venc = int(params_inv.get("INV_DIAS_ALERTA_VENCIMIENTO", 30))

    # ---------------- CARGAR DATOS ----------------
    df = preparar_dataset_inventario()
    if df.empty:
        return {}

    resultados = []

    # ---------------- ENTRENAR Y PROYECTAR ----------------
    for inv_id, grupo in df.groupby("InventarioId"):

        y_real = grupo["CantidadDia"].values.astype(float)

        # CASO 1: Sin datos suficientes
        if len(y_real) < 2:
            demanda_futura = 0
            error_modelo = 0
            calidad = "Sin datos"
            metodo = "Regla"
            stock_optimo = 0

        else:
            # Entrenar regresión
            X = np.arange(len(y_real)).reshape(-1, 1)
            modelo = LinearRegression()
            modelo.fit(X, y_real)

            demanda_dia = float(modelo.predict([[len(y_real)]])[0])
            demanda_futura = demanda_dia * horizonte_futuro if usar_proyeccion else 0

            # Error RMSE
            y_pred = modelo.predict(X)
            error_modelo = calcular_rmse(y_real, y_pred)

            # Clasificación
            calidad = clasificar_calidad(error_modelo)

            # Selección del método
            if calidad == "Malo":
                metodo = "Regla"
                stock_optimo = round(grupo["StockActual"].iloc[0] * factor_seguridad)
            else:
                metodo = "IA"
                stock_optimo = max(0, round(demanda_futura * factor_seguridad))

        resultados.append({
            "InventarioId": inv_id,
            "NombreProducto": grupo["NombreProducto"].iloc[0],
            "Categoria": grupo["Categoria"].iloc[0],
            "StockActual": safe_float(grupo["StockActual"].iloc[0]),
            "Costo": safe_float(grupo["Costo"].iloc[0]),
            "DemandaFutura": safe_float(demanda_futura),
            "StockOptimo": safe_float(stock_optimo),
            "ErrorModelo": safe_float(error_modelo),
            "CalidadModelo": calidad,
            "MetodoProyeccion": metodo
        })

    df_final = pd.DataFrame(resultados)

    # ---------------- GUARDAR PROYECCIÓN ----------------
    if usar_proyeccion and not df_final.empty:
        for _, row in df_final.iterrows():
            exec_sql("""
                MERGE INTO ProyeccionInventario AS target
                USING (SELECT ? AS InventarioId) AS source
                ON target.InventarioId = source.InventarioId
                WHEN MATCHED THEN
                    UPDATE SET StockOptimo = ?, FechaProyeccion = GETDATE(),
                               MetodoIA = ?, ErrorModelo = ?, Tendencia = ?,
                               DemandaEsperada = ?, FactorSeguridad = ?,
                               TipoProyeccion = 'Futuro', UltimaActualizacion = GETDATE()
                WHEN NOT MATCHED THEN
                    INSERT (InventarioId, StockOptimo, FechaProyeccion, MetodoIA,
                            ErrorModelo, Tendencia, DemandaEsperada, FactorSeguridad, TipoProyeccion)
                    VALUES (?, ?, GETDATE(), ?, ?, ?, ?, ?, 'Futuro');
            """, (
                int(row["InventarioId"]),
                safe_float(row["StockOptimo"]),
                row["MetodoProyeccion"],
                safe_float(row["ErrorModelo"]),
                row["CalidadModelo"],
                safe_float(row["DemandaFutura"]),
                safe_float(factor_seguridad),

                int(row["InventarioId"]),
                safe_float(row["StockOptimo"]),
                row["MetodoProyeccion"],
                safe_float(row["ErrorModelo"]),
                row["CalidadModelo"],
                safe_float(row["DemandaFutura"]),
                safe_float(factor_seguridad)
            ))

    # ---------------- KPIs ----------------
    valor_inventario = float((df_final["StockActual"] * df_final["Costo"]).sum())
    margen_potencial = valor_inventario * margen_potencial_factor

    total_activos = int((df_final["StockActual"] > 0).sum())

    # Productos por vencer
    hoy = datetime.now()
    limite = hoy + timedelta(days=dias_alerta_venc)

    df_venc = df[
        (df["TieneFechaVencimiento"] == 1) &
        (df["FechaVencimiento"].notna()) &
        (df["FechaVencimiento"] <= limite)
    ]

    por_vencer = len(df_venc)

    # Días estimados de stock
    df_final["DiasStock"] = df_final.apply(
        lambda r: round(r["StockActual"] / r["DemandaFutura"], 1)
        if r["DemandaFutura"] > 0 else None,
        axis=1
    )

    # Top alertas
    df_final["Criticidad"] = df_final["StockActual"] - df_final["StockOptimo"]
    top_alertas = df_final.sort_values("Criticidad").head(5).to_dict(orient="records")

    # Vencimientos por mes
    df_v = df[
        (df["TieneFechaVencimiento"] == 1) &
        (df["FechaVencimiento"].notna())
    ].copy()

    if not df_v.empty:
        df_v["Mes"] = df_v["FechaVencimiento"].dt.to_period("M").astype(str)
        vencimientos_por_mes = df_v.groupby("Mes")["InventarioId"].count().to_dict()
    else:
        vencimientos_por_mes = {}

    # Stock por categoría
    stock_por_categoria = (
        df_final.groupby("Categoria")["StockActual"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .to_dict()
    )

    # Rotación
    top_rotacion = df_final.sort_values("DemandaFutura", ascending=False).head(10).to_dict(orient="records")

    return {
        "total_productos": int(len(df_final)),
        "total_activos": total_activos,
        "valor_inventario": round(valor_inventario, 2),
        "margen_potencial": round(margen_potencial, 2),
        "margen_potencial_factor": margen_potencial_factor,
        "alertas_stock_bajo": int((df_final["StockActual"] < df_final["StockOptimo"]).sum()),
        "por_vencer": por_vencer,
        "dias_alerta_venc": dias_alerta_venc,
        "stock_por_categoria": stock_por_categoria,
        "rotacion_productos": df_final.to_dict(orient="records"),
        "top_rotacion": top_rotacion,
        "dias_stock": df_final[["NombreProducto", "DiasStock"]].to_dict(orient="records"),
        "top_alertas": top_alertas,
        "vencimientos_por_mes": vencimientos_por_mes
    }
