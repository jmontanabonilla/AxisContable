# ============================================================
# VENTAS_MODELO.PY — Dashboard de Ventas + IA + Clientes
# ============================================================

import pandas as pd
import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score

from db import query_all_flat, query_one, exec_sql


# ============================================================
# AUXILIARES
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
    
def safe_numeric_sql(value, default=0.0):
    """
    Convierte valores IA a números seguros para SQL Server.
    Evita NaN, inf, overflow y valores fuera de rango.
    """
    try:
        if value is None:
            return default

        v = float(value)

        if np.isnan(v) or np.isinf(v):
            return default

        # Limitar valores extremos para evitar overflow SQL
        if v > 1e12:
            return 1e12
        if v < -1e12:
            return -1e12

        return v
    except:
        return default



# ============================================================
# 1. CARGA DE DATOS
# ============================================================

def obtener_datos_ventas():
    """
    Ventas + DetalleVenta:
    - Ventas: Id, FechaEmision, TotalVenta, MedioPago, Estado
    - DetalleVenta: VentaId, ClienteId, InventarioId, Cantidad, ValorTotalUnitario, Estado
    """
    rows = query_all_flat("""
        SELECT 
            v.Id,
            v.FechaEmision,
            dv.ClienteId,
            v.TotalVenta,
            v.MedioPago,
            dv.InventarioId,
            dv.Cantidad,
            dv.ValorTotalUnitario
        FROM Ventas v
        INNER JOIN DetalleVenta dv ON dv.VentaId = v.Id
        WHERE v.Estado = 1
          AND dv.Estado = 1
        ORDER BY v.FechaEmision
    """)

    df = pd.DataFrame(list(rows), columns=[
        "VentaId", "Fecha", "ClienteId", "TotalVenta",
        "MedioPago", "InventarioId", "Cantidad", "ValorTotalUnitario"
    ])

    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        df["Anio"] = df["Fecha"].dt.year
        df["Mes"] = df["Fecha"].dt.month
        df["Dia"] = df["Fecha"].dt.day
        df["Trimestre"] = df["Fecha"].dt.quarter
        df["TotalVenta"] = df["TotalVenta"].astype(float)
        df["Cantidad"] = df["Cantidad"].astype(float)
        df["ValorTotalUnitario"] = df["ValorTotalUnitario"].astype(float)

    return df


def obtener_datos_clientes():
    rows = query_all_flat("""
        SELECT 
            c.Id AS ClienteId,
            c.RazonSocial AS Nombre,
            COUNT(dv.Id) AS Frecuencia,
            SUM(dv.ValorTotalUnitario) AS MontoTotal,
            MAX(v.FechaEmision) AS UltimaCompra
        FROM Clientes c
        LEFT JOIN DetalleVenta dv 
            ON dv.ClienteId = c.Id AND dv.Estado = 1
        LEFT JOIN Ventas v
            ON v.Id = dv.VentaId AND v.Estado = 1
        WHERE c.TipoEntidad = 'CLIENTE'
        GROUP BY c.Id, c.RazonSocial
    """)

    df = pd.DataFrame(list(rows), columns=[
        "ClienteId", "Nombre", "Frecuencia", "MontoTotal", "UltimaCompra"
    ])

    if not df.empty:
        df["UltimaCompra"] = pd.to_datetime(df["UltimaCompra"])
        df["Frecuencia"] = df["Frecuencia"].astype(int)
        df["MontoTotal"] = df["MontoTotal"].astype(float)

    return df


def preparar_dataset_ventas():
    df_ventas = obtener_datos_ventas()
    df_clientes = obtener_datos_clientes()

    if df_ventas.empty:
        return df_ventas, pd.DataFrame(), df_clientes

    df = df_ventas.copy()

    # Periodo mensual
    df["Periodo"] = df["Fecha"].dt.to_period("M")

    # Agregación mensual (CORREGIDO)
    df_agg = df.groupby("Periodo", as_index=False).agg(
        VentasMes=("ValorTotalUnitario", "sum")
    )

    # Period → string
    df_agg["Periodo"] = df_agg["Periodo"].astype(str)

    # Fecha real para gráfico
    df_agg["Fecha"] = pd.to_datetime(df_agg["Periodo"])

    # Variables IA
    df_agg["Anio"] = df_agg["Fecha"].dt.year
    df_agg["Mes"] = df_agg["Fecha"].dt.month
    df_agg["Trimestre"] = df_agg["Fecha"].dt.quarter

    return df, df_agg, df_clientes



# ============================================================
# 1.1 COMPLETAR MESES DEL AÑO ACTUAL
# ============================================================
def completar_meses_del_anio(df_agg):
    """
    Completa el dataset de ventas con todos los meses del año del primer registro.
    Garantiza que exista la columna VentasMes y las variables IA.
    """
    if df_agg.empty:
        return pd.DataFrame()

    # Tomar el año del primer registro (no el actual)
    anio_base = int(df_agg["Fecha"].dt.year.min())
    meses = range(1, 13)

    df_base = pd.DataFrame({
        "Periodo": [f"{anio_base}-{m:02d}" for m in meses],
        "Fecha": [datetime(anio_base, m, 1) for m in meses]
    })

    df_agg = df_agg.copy()
    df_agg["Fecha"] = pd.to_datetime(df_agg["Fecha"], errors="coerce")

    df_agg_filtrado = df_agg[df_agg["Fecha"].dt.year == anio_base].copy()

    df_completo = df_base.merge(
        df_agg_filtrado[["Periodo", "VentasMes"]],
        on="Periodo",
        how="left"
    )

    df_completo["VentasMes"] = df_completo["VentasMes"].fillna(0.0)
    df_completo["Anio"] = anio_base
    df_completo["Mes"] = list(meses)
    df_completo["Trimestre"] = [(m - 1) // 3 + 1 for m in meses]

    return df_completo







# ============================================================
# 2. PARÁMETROS IA
# ============================================================

def _leer_parametros_ia():
    """
    Lee parámetros desde monbo.dbo.ParametrosNegocio para IA_MODELOS_VENTAS
    """
    rows = query_all_flat("""
        SELECT NombreParametro, ValorParametro
        FROM monbo.dbo.ParametrosNegocio
        WHERE Categoria = 'IA_MODELOS_VENTAS'
    """)
    if not rows:
        return {}
    p = {}
    for nombre, valor in rows:
        try:
            p[str(nombre)] = float(valor)
        except:
            continue
    return p


# ============================================================
# 3. MODELO PREDICTIVO — RANDOM FOREST (1 MES FUTURO)
# ============================================================

def calcular_random_forest_ventas(df_agg):

    # Si no hay datos, retornar estructura vacía
    if df_agg.empty:
        return {
            "modelo": None,
            "rmse": 0.0,
            "mape": 0.0,
            "r2": 0.0,
            "y_pred": [],
            "y_pred_hist": [],
            "y_pred_futuro": [],
            "horizonte": 1
        }

    # ============================
    # LEER PARÁMETROS IA
    # ============================
    params = _leer_parametros_ia()

    meses_tendencia = int(params.get("VV_MESES_TENDENCIA_VENTAS", 6))
    horizonte_meses = int(params.get("VV_HORIZONTE_PROYECCION_VENTAS", 1))

    # ============================
    # TOMAR SOLO LOS ÚLTIMOS N MESES
    # ============================
    if len(df_agg) > meses_tendencia:
        df_agg_modelo = df_agg.tail(meses_tendencia).copy()
    else:
        df_agg_modelo = df_agg.copy()

    # ============================
    # VARIABLES IA
    # ============================
    X = df_agg_modelo[["Anio", "Mes", "Trimestre"]]
    y = df_agg_modelo["VentasMes"]

    modelo = RandomForestRegressor(n_estimators=200, random_state=42)
    modelo.fit(X, y)

    y_pred_hist = modelo.predict(X)

    # ============================
    # PROYECCIÓN DINÁMICA
    # ============================
    ultimo = df_agg_modelo.iloc[-1]

    anio_futuro = int(ultimo["Anio"])
    mes_futuro = int(ultimo["Mes"]) + horizonte_meses

    # Ajustar año si se pasa de diciembre
    while mes_futuro > 12:
        mes_futuro -= 12
        anio_futuro += 1

    trimestre_futuro = ((mes_futuro - 1) // 3) + 1

    X_futuro = pd.DataFrame(
        [[anio_futuro, mes_futuro, trimestre_futuro]],
        columns=["Anio", "Mes", "Trimestre"]
    )

    y_pred_futuro = modelo.predict(X_futuro)

    # ============================
    # MÉTRICAS
    # ============================
    y_pred_total = np.concatenate([y_pred_hist, y_pred_futuro])

    rmse = np.sqrt(mean_squared_error(y, y_pred_hist))
    mape = np.mean(np.abs((y - y_pred_hist) / np.where(y == 0, 1, y))) * 100
    r2 = r2_score(y, y_pred_hist)

    return {
        "modelo": modelo,
        "rmse": float(rmse),
        "mape": float(mape),
        "r2": float(r2),
        "y_pred": y_pred_total.tolist(),
        "y_pred_hist": y_pred_hist.tolist(),
        "y_pred_futuro": y_pred_futuro.tolist(),
        "horizonte": horizonte_meses
    }


# ============================================================
# 4. CÁLCULOS DESCRIPTIVOS
# ============================================================

def calcular_tendencia_ventas(df_agg):
    if df_agg.empty or len(df_agg) < 2:
        return "Sin datos"

    serie = df_agg["VentasMes"].values

    if serie[-1] > serie[-2]:
        return "Creciente"
    elif serie[-1] < serie[-2]:
        return "Decreciente"
    else:
        return "Estable"


def calcular_top_clientes(df_clientes, top=10):
    if df_clientes.empty:
        return []
    df = df_clientes.sort_values("MontoTotal", ascending=False)
    return df.head(top).to_dict(orient="records")


def calcular_productos_mas_vendidos(top=15):
    rows = query_all_flat("""
        SELECT 
            i.NombreProducto,
            SUM(dv.Cantidad) AS Unidades,
            SUM(dv.ValorTotalUnitario) AS Total
        FROM DetalleVenta dv
        INNER JOIN Ventas v ON v.Id = dv.VentaId
        INNER JOIN Inventarios i ON i.Id = dv.InventarioId
        WHERE v.Estado = 1
          AND dv.Estado = 1
        GROUP BY i.NombreProducto
        ORDER BY Total DESC
    """)

    if not rows:
        return []

    df = pd.DataFrame(list(rows), columns=["Producto", "Unidades", "Total"])
    df["Unidades"] = df["Unidades"].astype(float)
    df["Total"] = df["Total"].astype(float)

    df = df.sort_values("Total", ascending=False).head(top)

    return [
        {
            "Producto": r["Producto"],
            "Unidades": int(round(r["Unidades"])),
            "Total": int(round(r["Total"]))
        }
        for _, r in df.iterrows()
    ]


def calcular_medios_pago():
    rows = query_all_flat("""
        SELECT 
            MedioPago,
            COUNT(*) AS Cantidad,
            SUM(TotalVenta) AS Total
        FROM Ventas
        WHERE Estado = 1
        GROUP BY MedioPago
    """)

    return [
        {
            "MedioPago": r[0],
            "Cantidad": int(r[1]),
            "Total": int(round(safe_float(r[2])))
        }
        for r in rows
    ]


def segmentar_clientes(df_clientes):
    if df_clientes.empty:
        return []

    segmentos = []

    for _, row in df_clientes.iterrows():
        freq = int(row["Frecuencia"] or 0)

        if freq >= 10:
            seg = "Frecuente"
        elif freq >= 3:
            seg = "Ocasional"
        else:
            seg = "Inactivo"

        segmentos.append({
            "ClienteId": row["ClienteId"],
            "Nombre": row["Nombre"],
            "Segmento": seg,
            "MontoTotal": safe_float(row["MontoTotal"])
        })

    return segmentos


def obtener_demanda_inventario_resumen(top=10):
    rows = query_all_flat("""
        SELECT 
            i.NombreProducto,
            p.DemandaEsperada,
            p.StockOptimo,
            p.CalidadModelo
        FROM dbo.ProyeccionInventario p
        INNER JOIN dbo.Inventarios i ON i.Id = p.InventarioId
        WHERE p.TipoProyeccion = 'Futuro'
    """)

    if not rows:
        return []

    df = pd.DataFrame(list(rows), columns=[
        "Producto", "DemandaEsperada", "StockOptimo", "CalidadModelo"
    ])

    df["DemandaEsperada"] = df["DemandaEsperada"].astype(float)
    df["StockOptimo"] = df["StockOptimo"].astype(float)

    df = df.sort_values("DemandaEsperada", ascending=False)

    return df.head(top).to_dict(orient="records")


# ============================================================
# 5. GUARDADO DE PROYECCIONES EN BD
# ============================================================

def guardar_proyeccion_ventas(modelo, tendencia, ventas_pred):
    ventas_pred = safe_numeric_sql(ventas_pred)
    rmse = safe_numeric_sql(modelo.get("rmse"))
    mape = safe_numeric_sql(modelo.get("mape"))
    r2 = safe_numeric_sql(modelo.get("r2"))

    existente = query_one("SELECT TOP 1 Id FROM ProyeccionVentas ORDER BY Id DESC")

    if existente:
        exec_sql("""
            UPDATE ProyeccionVentas
            SET VentasProyectadas = ?, RMSE = ?, MAPE = ?, R2 = ?, Tendencia = ?,
                FechaCalculo = GETDATE(), UltimaActualizacion = GETDATE(),
                ModoCalculo = 'automatico'
            WHERE Id = ?
        """, [
            ventas_pred,
            rmse,
            mape,
            r2,
            tendencia,
            existente[0]
        ])
    else:
        exec_sql("""
            INSERT INTO ProyeccionVentas
            (VentasProyectadas, RMSE, MAPE, R2, Tendencia, FechaCalculo, ModoCalculo)
            VALUES (?, ?, ?, ?, ?, GETDATE(), 'automatico')
        """, [
            ventas_pred,
            rmse,
            mape,
            r2,
            tendencia
        ])



def guardar_auditoria_proyeccion_ventas(modelo, tendencia, ventas_pred, usuario_id=1):
    ventas_pred = safe_numeric_sql(ventas_pred)
    rmse = safe_numeric_sql(modelo.get("rmse"))
    mape = safe_numeric_sql(modelo.get("mape"))
    r2 = safe_numeric_sql(modelo.get("r2"))

    exec_sql("""
        INSERT INTO AuditoriaProyeccionVentas
        (VentasProyectadas, RMSE, MAPE, R2, Tendencia, FechaCalculo, ModoCalculo, UsuarioId)
        VALUES (?, ?, ?, ?, ?, GETDATE(), 'automatico', ?)
    """, [
        ventas_pred,
        rmse,
        mape,
        r2,
        tendencia,
        usuario_id
    ])



# ============================================================
# 6. CONSOLIDACIÓN FINAL (1 MES FUTURO + ÚLTIMOS 12 MESES)
# ============================================================

def generar_resultados_ventas():
    df, df_agg, df_clientes = preparar_dataset_ventas()

    if df is None or df.empty:
        return {}

    params = _leer_parametros_ia()
    porcentaje_ingreso_neto = float(params.get("VV_PORCENTAJE_INGRESO_NETO", 0.81))

    # Asegurar columna Fecha
    if "Fecha" in df.columns:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
    elif "FechaEmision" in df.columns:
        df["Fecha"] = pd.to_datetime(df["FechaEmision"])

    # Determinar año base según los datos
    anio_base = int(df["Fecha"].dt.year.min())

    # Completar meses del año base
    df_agg_completo = completar_meses_del_anio(df_agg)
    df_agg_completo["Anio"] = anio_base

    # Modelo IA
    modelo = calcular_random_forest_ventas(df_agg_completo)
    tendencia = calcular_tendencia_ventas(df_agg_completo)
    y_pred_hist = modelo.get("y_pred_hist", [])
    y_pred_futuro = modelo.get("y_pred_futuro", [])

    # Cálculos auxiliares
    top_clientes = calcular_top_clientes(df_clientes)
    segmentacion = segmentar_clientes(df_clientes)
    productos = calcular_productos_mas_vendidos()
    medios_pago = calcular_medios_pago()
    demanda_inventario = obtener_demanda_inventario_resumen()

    # KPIs principales
    df_ventas_unicas = df.drop_duplicates(subset=["VentaId"])
    total_ventas_brutas = float(df_ventas_unicas["TotalVenta"].sum())
    ingresos_netos = total_ventas_brutas * porcentaje_ingreso_neto
    total_registros_ventas = query_one("SELECT COUNT(*) FROM Ventas WHERE Estado = 1")[0]

    # Serialización para gráficas
    df_agg_serializable = df_agg_completo.copy()
    df_agg_serializable["Fecha"] = pd.to_datetime(df_agg_serializable["Fecha"])
    df_agg_serializable["Periodo"] = df_agg_serializable["Periodo"].astype(str)

    # Inicializar columna de predicción IA vacía
    df_agg_serializable["PrediccionIA"] = [None] * len(df_agg_serializable)

    # Asignar predicciones históricas
    if y_pred_hist:
        n_hist = len(y_pred_hist)
        df_agg_serializable.loc[len(df_agg_serializable) - n_hist:, "PrediccionIA"] = y_pred_hist

    # Mes futuro (solo 1)
    if y_pred_futuro:
        ultima_fecha = df_agg_serializable["Fecha"].max()
        fecha_siguiente = ultima_fecha + pd.DateOffset(months=1)

        df_futuro = pd.DataFrame({
            "Periodo": [f"{fecha_siguiente.year}-{fecha_siguiente.month:02d}"],
            "Fecha": [fecha_siguiente],
            "VentasMes": [np.nan],
            "Anio": [fecha_siguiente.year],
            "Mes": [fecha_siguiente.month],
            "Trimestre": [(fecha_siguiente.month - 1) // 3 + 1],
            "PrediccionIA": [y_pred_futuro[0]]
        })

        df_agg_serializable = pd.concat([df_agg_serializable, df_futuro], ignore_index=True)

    # Ordenar y mantener todos los meses del año base + futuro
    df_agg_serializable = df_agg_serializable.sort_values("Fecha")
    df_agg_serializable["Fecha"] = df_agg_serializable["Fecha"].dt.strftime("%Y-%m-%d")

    # Guardar proyección IA
    ventas_pred = safe_numeric_sql(y_pred_futuro[0] if y_pred_futuro else 0.0)

    guardar_proyeccion_ventas(modelo, tendencia, ventas_pred)
    guardar_auditoria_proyeccion_ventas(modelo, tendencia, ventas_pred)

    # Retorno final
    return {
        "ventas_mes": float(df_agg_serializable["VentasMes"].sum()),
        "total_registros_mes": int(len(df_agg_serializable)),
        "acumulado_anual": float(df_agg_serializable["VentasMes"].sum()),
        "iva_recaudado": total_ventas_brutas * 0.19,
        "ingresos_netos": ingresos_netos,
        "total_ventas_brutas": total_ventas_brutas,
        "total_registros_ventas": int(total_registros_ventas),
        "porcentaje_ingreso_neto": porcentaje_ingreso_neto,
        "tendencia": tendencia,
        "top_clientes": top_clientes,
        "segmentacion_clientes": segmentacion,
        "productos_mas_vendidos": productos,
        "medios_pago": medios_pago,
        "demanda_inventario": demanda_inventario,
        "modelo": modelo,
        "df_agg": df_agg_serializable.to_dict(orient="records")
    }

