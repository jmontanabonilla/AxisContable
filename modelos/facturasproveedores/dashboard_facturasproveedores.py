# ============================================================
# DASHBOARD FACTURAS PROVEEDORES — Consolidación de KPIs
# ============================================================

# ============================================================
# DASHBOARD FACTURAS PROVEEDORES — Consolidación de KPIs
# ============================================================

from .facturasproveedores_modelo import generar_resultados_facturas


def generar_kpis_facturas_proveedores():
    """
    Prepara todos los KPIs y datasets necesarios para el dashboard
    de facturas proveedores:
    - KPIs de cuentas por pagar
    - Flujo de caja real y proyectado
    - Alertas de vencimiento y riesgo por monto
    - Concentración por proveedor
    - Conteo de estados (Pagada, Pendiente, Anulada)
    - Top proveedores por monto pendiente
    - Dataset completo de facturas
    - Parámetros IA (horizonte, modelo activo, métricas)
    """

    r = generar_resultados_facturas()

    # Si no hay datos, devolver estructura vacía
    if not r:
        return {
            # KPIs principales
            "total_facturas": 0,
            "total_cuentas_por_pagar": 0,
            "por_pagar_al_dia": 0,
            "por_pagar_proximo_vencimiento": 0,
            "por_pagar_vencido": 0,

            # Flujo de caja
            "flujo_caja_real": 0,
            "flujo_caja_proyectado": 0,
            "asignacion_proyectada": {},
            "modo_flujo": "automatico",

            # Alertas
            "alertas_vencimiento": 0,
            "detalle_alertas": {},

            # Concentración
            "concentracion_proveedores": {},

            # Gráficas
            "conteo_estados": {},
            "monto_por_proveedor": [],

            # IA
            "modelo_activo": False,
            "proyeccion_regresion": {},

            # Dataset completo
            "lista_facturas": [],

            # Parámetros
            "horizonte_futuro": 7,
        }

    # Retornar KPIs completos
    return {
        # ---------------- KPIs PRINCIPALES ----------------
        "total_facturas": r["total_facturas"],
        "total_cuentas_por_pagar": r["total_cuentas_por_pagar"],
        "por_pagar_al_dia": r["por_pagar_al_dia"],
        "por_pagar_proximo_vencimiento": r["por_pagar_proximo_vencimiento"],
        "por_pagar_vencido": r["por_pagar_vencido"],

        # ---------------- FLUJO DE CAJA ----------------
        "flujo_caja_real": r["flujo_caja_real"],
        "flujo_caja_proyectado": r["flujo_caja_proyectado"],
        "asignacion_proyectada": r["asignacion_proyectada"],
        "modo_flujo": r["modo_flujo"],

        # ---------------- ALERTAS ----------------
        "alertas_vencimiento": r["alertas_vencimiento"],
        "detalle_alertas": r["detalle_alertas"],

        # ---------------- CONCENTRACIÓN ----------------
        "concentracion_proveedores": r["concentracion_proveedores"],

        # ---------------- GRÁFICAS / DATASETS ----------------
        "conteo_estados": r["conteo_estados"],
        "monto_por_proveedor": r["monto_por_proveedor"],

        # ---------------- IA / MACHINE LEARNING ----------------
        "modelo_activo": r["modelo_activo"],
        "proyeccion_regresion": r["proyeccion_regresion"],

        # ---------------- DATASET DETALLADO ----------------
        "lista_facturas": r["lista_facturas"],

        # ---------------- PARÁMETROS ----------------
        "horizonte_futuro": r["horizonte_futuro"],
    }
