# ============================================================
# DASHBOARD INVENTARIOS — Consolidación de KPIs y datasets
# ============================================================

from .inventarios_modelo import generar_resultados_inventario


def generar_kpis_inventario():
    """
    Prepara todos los KPIs y datasets necesarios para el dashboard
    de inventarios, incluyendo:
    - KPIs principales
    - Stock por categoría (TOP 10)
    - Rotación de productos (TOP 10)
    - Días estimados de stock
    - Alertas de inventario (TOP 5)
    - Vencimientos por mes
    - Parámetros dinámicos (margen, días alerta vencimiento)
    - Dataset completo de rotación (incluye StockOptimo y DemandaEsperada)
    """

    r = generar_resultados_inventario()

    # Si no hay datos, devolver estructura vacía
    if not r:
        return {
            "total_productos": 0,
            "total_activos": 0,
            "valor_inventario": 0,
            "margen_potencial": 0,
            "margen_potencial_factor": 0,
            "alertas_stock_bajo": 0,
            "por_vencer": 0,
            "dias_alerta_venc": 0,
            "stock_por_categoria": {},
            "top_rotacion": [],
            "dias_stock": [],
            "top_alertas": [],
            "vencimientos_por_mes": {},
            "rotacion_productos": []
        }

    # Retornar KPIs completos
    return {
        # ---------------- KPIs PRINCIPALES ----------------
        "total_productos": r["total_productos"],
        "total_activos": r["total_activos"],
        "valor_inventario": r["valor_inventario"],
        "margen_potencial": r["margen_potencial"],
        "margen_potencial_factor": r["margen_potencial_factor"],

        # ---------------- ALERTAS Y VENCIMIENTOS ----------------
        "alertas_stock_bajo": r["alertas_stock_bajo"],
        "por_vencer": r["por_vencer"],
        "dias_alerta_venc": r["dias_alerta_venc"],

        # ---------------- GRÁFICOS / DATASETS ----------------
        "stock_por_categoria": r["stock_por_categoria"],     # TOP 10
        "top_rotacion": r["top_rotacion"],                   # TOP 10 por demanda futura
        "dias_stock": r["dias_stock"],                       # Lista completa
        "top_alertas": r["top_alertas"],                     # TOP 5 por criticidad
        "vencimientos_por_mes": r["vencimientos_por_mes"],   # Conteo por mes

        # ---------------- DATASET DETALLADO ----------------
        # Incluye por producto:
        # StockActual, StockOptimo, DemandaFutura, ErrorModelo, CalidadModelo, etc.
        "rotacion_productos": r["rotacion_productos"]
    }
