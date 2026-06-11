# ============================================================
# FACTURASPROVEEDORES_MODELO.PY — Versión final
# ============================================================

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression



from db import query_all_flat
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


# ============================================================
# PARÁMETROS DEL MODELO
# ============================================================

def obtener_horizonte_vencimiento():
    """
    Horizonte para alertas de vencimiento (operativo).
    """
    row = query_all_flat("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'FP_HORIZONTE_VENCIMIENTO_FACTURAS'
    """)
    try:
        return int(row[0][0]) if row else 30
    except:
        return 30


def obtener_horizonte_proyeccion_pagos():
    """
    Horizonte para proyección/tendencia de pagos (predictivo).
    """
    row = query_all_flat("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'FP_HORIZONTE_PROYECCION_PAGOS_FACTURAS'
    """)
    try:
        return int(row[0][0]) if row else 7
    except:
        return 7

def obtener_meses_tendencia_pagos():
    """
    Número de meses históricos para calcular la tendencia de pagos.
    """
    row = query_all_flat("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'FP_MESES_TENDENCIA_PAGOS'
    """)
    try:
        return int(row[0][0]) if row else 7  # valor por defecto: 6 meses
    except:
        return 7


# ============================================================
# MAPEADOR COMÚN DE FACTURAS
# ============================================================

def _mapear_facturas_rows(rows, horizonte_ref=None):
    """
    Convierte filas crudas en diccionarios homogéneos.
    - horizonte_ref: solo se usa para marcar EsProximaVencer si se pasa.
    - Corrige el tema de días negativos para visualización: se guarda
      el valor real y el absoluto para la tabla.
    """
    facturas = []
    hoy = datetime.now().date()

    for r in rows:
        fecha_emision = r[4]
        fecha_venc = r[5]

        if isinstance(fecha_emision, datetime):
            fecha_emision = fecha_emision.date()
        if isinstance(fecha_venc, datetime):
            fecha_venc = fecha_venc.date()

        dias_al_venc = (fecha_venc - hoy).days if fecha_venc else 0
        dias_al_venc_abs = abs(dias_al_venc)

        es_vencida = dias_al_venc < 0 and r[7] != "Pagada"

        es_proxima = False
        if horizonte_ref is not None:
            es_proxima = 0 <= dias_al_venc <= horizonte_ref and r[7] != "Pagada"

        facturas.append({
            "Id": r[0],
            "NumeroFactura": r[1],
            "ProveedorId": r[2],
            "NombreProveedor": r[3] or "SIN PROVEEDOR",
            "FechaEmision": fecha_emision,
            "FechaVencimiento": fecha_venc,
            "MontoTotal": safe_float(r[6]),
            "EstadoFactura": r[7],
            "Estado": r[8],
            "DiasAlVencimiento": dias_al_venc,       # valor real (puede ser negativo)
            "DiasAlVencimientoAbs": dias_al_venc_abs, # para mostrar en tabla
            "EsVencida": es_vencida,
            "EsProximaVencer": es_proxima,
        })

    return facturas


# ============================================================
# CARGA DE FACTURAS
#   - GLOBAL: sin horizonte (datos reales BD)
#   - HORIZONTE: según parámetro (para proyección)
# ============================================================

def cargar_facturas_proveedores_global():
    """
    Carga TODAS las facturas activas (datos reales de BD, sin horizonte).
    """
    rows = query_all_flat("""
        SELECT 
            f.Id,
            f.NumeroFactura,
            f.ProveedorId,
            c.RazonSocial AS NombreProveedor,
            f.FechaFactura AS FechaEmision,
            f.FechaVencimiento,
            f.Total,
            f.EstadoFactura,
            f.Estado
        FROM FacturasProveedores f
        LEFT JOIN Clientes c 
            ON c.Id = f.ProveedorId
           AND c.TipoEntidad = 'PROVEEDOR'
        WHERE f.Estado = 1;
    """)
    return _mapear_facturas_rows(rows, horizonte_ref=None)


def cargar_facturas_proveedores_horizonte(horizonte_dias):
    hoy = datetime.now().date()
    fecha_inicio = hoy - timedelta(days=horizonte_dias)
    fecha_fin = hoy + timedelta(days=horizonte_dias)

    rows = query_all_flat("""
        SELECT 
            f.Id,
            f.NumeroFactura,
            f.ProveedorId,
            c.RazonSocial AS NombreProveedor,
            f.FechaFactura AS FechaEmision,
            f.FechaVencimiento,
            f.Total,
            f.EstadoFactura,
            f.Estado
        FROM FacturasProveedores f
        LEFT JOIN Clientes c 
            ON c.Id = f.ProveedorId
           AND c.TipoEntidad = 'PROVEEDOR'
        WHERE f.Estado = 1
          AND f.FechaFactura BETWEEN ? AND ?
    """, (fecha_inicio, fecha_fin))

    return _mapear_facturas_rows(rows, horizonte_ref=horizonte_dias)


# ============================================================
# CUENTAS POR PAGAR (DATOS REALES BD)
# ============================================================

def calcular_cuentas_por_pagar(facturas):
    """
    Clasifica facturas pendientes usando el horizonte operativo real:
    - vencido
    - próximo vencimiento (<= horizonte)
    - al día (> horizonte)
    """
    horizonte_proximo = obtener_horizonte_vencimiento()  # 30 días

    total = al_dia = proximo = vencido = 0.0

    for f in facturas:
        if f["EstadoFactura"] == "Pagada":
            continue  # NO entran en cuentas por pagar

        monto = f["MontoTotal"]

        if f["DiasAlVencimiento"] < 0:
            vencido += monto
        elif 0 <= f["DiasAlVencimiento"] <= horizonte_proximo:
            proximo += monto
        else:
            al_dia += monto

        total += monto

    return {
        "total": round(total, 2),
        "al_dia": round(al_dia, 2),
        "proximo_vencimiento": round(proximo, 2),
        "vencido": round(vencido, 2),
    }

# ============================================================
# ESTADOS GLOBALES (DATOS REALES BD)
# ============================================================

def obtener_estados_globales_facturas():
    """
    Calcula:
    - total_facturas_global
    - total_general_global (suma de montos)
    - conteo_estados_global = {Pagada, Pendiente, Anulada}
    """
    rows = query_all_flat("""
        SELECT 
            EstadoFactura,
            COUNT(*) AS Cantidad,
            SUM(Total) AS Monto
        FROM FacturasProveedores
        WHERE Estado = 1
        GROUP BY EstadoFactura;
    """)

    conteo = {
        "Pagada": 0,
        "Pendiente": 0,
        "Anulada": 0
    }

    total_facturas = 0
    total_general = 0.0

    for estado, cantidad, monto in rows:
        estado = estado or "Pendiente"
        if estado not in conteo:
            conteo[estado] = 0

        conteo[estado] += int(cantidad)
        total_facturas += int(cantidad)
        total_general += safe_float(monto)

    return {
        "total_facturas_global": total_facturas,
        "total_general_global": round(total_general, 2),
        "conteo_estados_global": conteo
    }


# ============================================================
# ALERTAS DE VENCIMIENTO (DATOS REALES BD)
# ============================================================

def calcular_alertas_vencimiento(facturas):
    """
    Calcula alertas de vencimiento sobre todas las facturas reales.
    Usa el parámetro FP_HORIZONTE_VENCIMIENTO_FACTURAS (operativo).
    Marca:
    - Próximas a vencer (<= horizonte)
    - Vencidas
    - Alto riesgo (vencidas > 30 días)
    """
    # Leer el horizonte desde la BD
    horizonte_proximo = obtener_horizonte_vencimiento()  # ← ahora usa el parámetro real (30 días)

    proximas, vencidas, alto_riesgo = [], [], []

    for f in facturas:
        if f["EstadoFactura"] == "Pagada":
            continue

        if f["EsVencida"]:
            vencidas.append(f)
            # Alto riesgo: vencidas con más de 30 días de atraso
            if abs(f["DiasAlVencimiento"]) > 30:
                alto_riesgo.append(f)
        elif 0 <= f["DiasAlVencimiento"] <= horizonte_proximo:
            proximas.append(f)

    total_alertas = len(proximas) + len(vencidas)

    return {
        "total_alertas": total_alertas,
        "proximas_vencer": proximas,
        "vencidas": vencidas,
        "alto_riesgo": alto_riesgo,
    }



# ============================================================
# CONCENTRACIÓN POR PROVEEDOR (GLOBAL)
# ============================================================

def calcular_concentracion_proveedores(facturas):
    montos = {}
    for f in facturas:
        prov = f["NombreProveedor"]
        montos[prov] = montos.get(prov, 0.0) + f["MontoTotal"]
    total = sum(montos.values()) or 1
    data = []
    for prov, monto in sorted(montos.items(), key=lambda x: x[1], reverse=True):
        data.append({
            "NombreProveedor": prov,
            "Monto": round(monto, 2),
            "Porcentaje": round((monto / total) * 100, 1),
        })
    return data


# ============================================================
# MONTOS POR PROVEEDOR (GLOBAL)
# ============================================================

def obtener_monto_por_proveedor():
    """
    Retorna una lista de diccionarios con:
    {
        "Proveedor": "Nombre del proveedor",
        "Monto": valor_total
    }
    Solo incluye entidades con TipoEntidad = 'PROVEEDOR'.
    """
    rows = query_all_flat("""
        SELECT 
            c.RazonSocial AS Proveedor,
            SUM(f.Total) AS Monto
        FROM FacturasProveedores f
        LEFT JOIN Clientes c 
            ON c.Id = f.ProveedorId
           AND c.TipoEntidad = 'PROVEEDOR'
        WHERE f.Estado = 1
        GROUP BY c.RazonSocial
        ORDER BY SUM(f.Total) DESC;
    """)

    resultado = []
    for r in rows:
        resultado.append({
            "Proveedor": r[0] or "SIN PROVEEDOR",
            "Monto": safe_float(r[1])
        })
    return resultado

# ============================================================
# REGRESIÓN LINEAL DE PAGOS (HISTÓRICO + PROYECCIÓN)
# ============================================================

def calcular_regresion_lineal_facturas(facturas):
    """
    Calcula tendencia y proyección de pagos usando TODO el histórico disponible.
    - Agrupa pagos por mes
    - Usa los últimos N meses (parámetro FP_MESES_TENDENCIA_PAGOS)
    - Genera proyección lineal para 3 meses futuros
    """

    meses_tendencia = obtener_meses_tendencia_pagos()  # parámetro configurable

    if not facturas:
        return {
            "proyeccion_pagos": 0,
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        }

    # Convertir a DataFrame
    df_raw = pd.DataFrame(facturas)
    df_raw["FechaEmision"] = pd.to_datetime(df_raw["FechaEmision"], errors="coerce")

    # 🔹 Tomar SOLO facturas pagadas (histórico real)
    df_pag = df_raw[df_raw["EstadoFactura"] == "Pagada"].copy()

    if df_pag.empty:
        return {
            "proyeccion_pagos": 0,
            "periodos": [],
            "montos_historicos": [],
            "montos_proyectados": [],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Sin datos suficientes",
        }

    # 🔹 Agrupar por mes (histórico completo)
    serie = (
        df_pag.groupby(df_pag["FechaEmision"].dt.to_period("M"))["MontoTotal"]
        .sum()
        .sort_index()
        .reset_index()
    )

    serie["FechaEmision"] = serie["FechaEmision"].dt.to_timestamp()
    serie = serie.tail(meses_tendencia)

    y_real = serie["MontoTotal"].values.astype(float)
    X = np.arange(len(y_real)).reshape(-1, 1)
    periodos_str = [p.strftime("%Y-%m") for p in serie["FechaEmision"]]

    # 🔹 Si solo hay un mes, generar tendencia mínima ascendente
    if len(y_real) < 2:
        incremento = y_real[0] * 0.05  # +5% mensual
        y_future = [y_real[0] + incremento * (i + 1) for i in range(3)]

        periodos_final = periodos_str + [
            (serie["FechaEmision"].iloc[-1] + pd.DateOffset(months=i + 1)).strftime("%Y-%m")
            for i in range(3)
        ]

        # 🔹 KPI de proyección: promedio de los meses futuros
        proyeccion_kpi = round(sum(y_future) / len(y_future), 2)

        return {
            "proyeccion_pagos": proyeccion_kpi,
            "periodos": periodos_final,
            "montos_historicos": [round(v, 2) for v in y_real],
            "montos_proyectados": [round(v, 2) for v in list(y_real) + y_future],
            "metricas": {"rmse": 0, "mape": 0, "r2": 0},
            "calidad": "Proyección estimada (1 mes histórico)",
        }

    # 🔹 Regresión lineal normal
    modelo = LinearRegression()
    modelo.fit(X, y_real)
    y_pred = modelo.predict(X)

    # 🔹 Proyección 3 meses futuros
    X_future = np.arange(len(y_real), len(y_real) + 3).reshape(-1, 1)
    y_future = modelo.predict(X_future)

    # 🔹 KPI de proyección: promedio de los tres meses futuros
    proyeccion_kpi = round(sum(y_future) / len(y_future), 2)
    if proyeccion_kpi < 0:
        proyeccion_kpi = 0  # evitar negativos en el KPI

    # Métricas
    rmse = float(np.sqrt(np.mean((y_real - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_real - y_pred) / y_real)) * 100)
    r2 = float(modelo.score(X, y_real))

    # Periodos futuros
    periodos_final = periodos_str + [
        (serie["FechaEmision"].iloc[-1] + pd.DateOffset(months=i + 1)).strftime("%Y-%m")
        for i in range(3)
    ]

    montos_proyectados = list(y_pred) + list(y_future)

    return {
        "proyeccion_pagos": proyeccion_kpi,  # ← solo afecta el KPI
        "periodos": periodos_final,
        "montos_historicos": [round(v, 2) for v in y_real],
        "montos_proyectados": [round(v, 2) for v in montos_proyectados],
        "metricas": {"rmse": rmse, "mape": mape, "r2": r2},
        "calidad": "Confiable" if r2 >= 0.6 else "Moderada",
    }



# ============================================================
# FLUJO DE CAJA PROYECTADO (LEE DE BD) — VERSIÓN CORREGIDA
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

    # ------------------------------------------------------------
    # Caso: No existe registro en BD
    # ------------------------------------------------------------
    if not row:
        flujo = 0.0
        pg, pf, pr = 40.0, 40.0, 20.0
        fecha = ""
        modo = "automatico"
    else:
        r = row[0]

        flujo = safe_float(r[0])
        pg = safe_float(r[1])   # YA VIENEN COMO PORCENTAJES (35.0, 25.0, 40.0)
        pf = safe_float(r[2])
        pr = safe_float(r[3])
        fecha = r[4]
        modo = r[5]

        # Corrección: evitar porcentajes inválidos
        if pg < 0: pg = 0
        if pf < 0: pf = 0
        if pr < 0: pr = 0

        # NO normalizar → ya vienen normalizados desde el cálculo automático

    # ------------------------------------------------------------
    # Calcular asignación proyectada
    # ------------------------------------------------------------
    asignacion = {
        "gastos_generales": round(flujo * (pg / 100), 2),
        "facturas_proveedores": round(flujo * (pf / 100), 2),
        "reserva": round(flujo * (pr / 100), 2),
    }

    # ------------------------------------------------------------
    # Retorno final compatible con dashboard
    # ------------------------------------------------------------
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
# KPIs PRINCIPALES PARA EL DASHBOARD
# ============================================================


def obtener_kpis_facturas_proveedores():

    facturas_global = cargar_facturas_proveedores_global()
    if not facturas_global:
        return None

    horizonte_venc = obtener_horizonte_vencimiento()
    horizonte_proy = obtener_horizonte_proyeccion_pagos()

    # 🔹 USAR TODO EL HISTÓRICO REAL PARA LA REGRESIÓN
    facturas_proyeccion = facturas_global

    # ============================
    # TOTALES GLOBALES
    # ============================
    globales = obtener_estados_globales_facturas()
    conteo_estados = globales["conteo_estados_global"]
    total_facturas_global = globales["total_facturas_global"]

    total_pagadas_monto = sum(
        f["MontoTotal"] for f in facturas_global if f["EstadoFactura"] == "Pagada"
    )

    # ============================
    # CUENTAS POR PAGAR
    # ============================
    cuentas = calcular_cuentas_por_pagar(facturas_global)
    obligacion_neta = cuentas["total"] - total_pagadas_monto

    # ============================
    # ALERTAS
    # ============================
    alertas = calcular_alertas_vencimiento(facturas_global)

    # ============================
    # CONCENTRACIÓN
    # ============================
    concentracion = calcular_concentracion_proveedores(facturas_global)

    # ============================
    # REGRESIÓN (HISTÓRICO REAL)
    # ============================
    regresion = calcular_regresion_lineal_facturas(facturas_proyeccion)

    # ============================
    # FLUJO DE CAJA PROYECTADO
    # ============================
    calcular_flujo_caja_proyectado_global()
    flujo = obtener_flujo_proyectado_desde_bd()

    # ============================
    # MONTOS POR PROVEEDOR
    # ============================
    monto_por_proveedor = obtener_monto_por_proveedor()

    # ============================
    # TABLA PENDIENTES
    # ============================
    lista_facturas_pendientes = [
        f for f in facturas_global if f["EstadoFactura"] == "Pendiente"
    ]

    # ============================
    # RETORNO FINAL
    # ============================
    return {

        # ---------------- KPIs PRINCIPALES ----------------
        "total_facturas": total_facturas_global,
        "facturas_pagadas": round(total_pagadas_monto, 2),

        "total_cuentas_por_pagar": cuentas["total"],
        "por_pagar_al_dia": cuentas["al_dia"],
        "por_pagar_proximo_vencimiento": cuentas["proximo_vencimiento"],
        "por_pagar_vencido": cuentas["vencido"],

        "obligacion_neta": round(obligacion_neta, 2),

        # 🔹 NUEVO KPI — PROYECCIÓN DE PAGOS
        "proyeccion_pagos": regresion["proyeccion_pagos"],

        # ---------------- GLOBALES ----------------
        "total_facturas_global": globales["total_facturas_global"],
        "total_general_global": globales["total_general_global"],
        "conteo_estados": conteo_estados,

        # ---------------- FLUJO DE CAJA ----------------
        "flujo_caja_proyectado": flujo["flujo_caja_proyectado"],
        "asignacion_proyectada": flujo["asignacion_proyectada"],
        "porcentaje_gastos": flujo["porcentaje_gastos"],
        "porcentaje_facturas": flujo["porcentaje_facturas"],
        "porcentaje_reserva": flujo["porcentaje_reserva"],
        "modo_flujo": flujo["modo_flujo"],
        "fecha_calculo_flujo": flujo["fecha_calculo_flujo"],

        # ---------------- ALERTAS ----------------
        "alertas_vencimiento": alertas["total_alertas"],
        "detalle_alertas": {
            "proximas_vencer": alertas["proximas_vencer"],
            "vencidas": alertas["vencidas"],
            "alto_riesgo": alertas["alto_riesgo"],
        },

        # ---------------- GRÁFICOS ----------------
        "concentracion_proveedores": concentracion,
        "monto_por_proveedor": monto_por_proveedor,
        "proyeccion_regresion": regresion,

        # ---------------- TABLA ----------------
        "lista_facturas": lista_facturas_pendientes,

        # ---------------- HORIZONTES ----------------
        "horizonte_vencimiento": horizonte_venc,
        "horizonte_proyeccion": horizonte_proy,
    }
