# app.py
# ---------------- IMPORTS ----------------
from flask import Flask, render_template, request, redirect, url_for, session, flash, g,make_response, jsonify, Response, abort
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from db import get_connection, query_one, query_all, exec_sql
from utils import restar_meses,numero_a_letras,rango_mes,format_date_for_input,generar_cufe_simulado,generar_qr_base64
from dateutil.relativedelta import relativedelta
from weasyprint import HTML
from num2words import num2words
from decimal import Decimal
from modelos.inventarios.inventarios_modelo import generar_resultados_inventario
from modelos.inventarios.dasboard_inventarios import generar_kpis_inventario
import hashlib
import math
import functools
import pdfkit
import locale
import os
import locale
import os
import webbrowser
from threading import Timer
import webbrowser
from threading import Timer


# ---------------- CONFIG ----------------
app = Flask(__name__)
app.secret_key = "monbo_secret_key_very_secret"
firma_rel_path = 'static/img/firma.png'


# ---------------- SESSION ----------------
@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = {
            "id": session.get("user_id"),
            "usuario": session.get("usuario"),
            "rol_id": session.get("rol_id"),
            "rol_nombre": session.get("rol_nombre")
        }


# ---------------- SESSION ----------------
@app.before_request
def load_user():
    g.user = None
    if "user_id" in session:
        g.user = {
            "id": session.get("user_id"),
            "usuario": session.get("usuario"),
            "rol_id": session.get("rol_id"),
            "rol_nombre": session.get("rol_nombre")
        }

# ---------------- PERMISSIONS ----------------
def has_permission(rol_id, modulo, accion):
    if not rol_id:
        return False
    col_map = {"ver": "Ver", "crear": "Crear", "editar": "Editar"}
    col = col_map.get(accion.lower(), "Ver")
    row = query_one(
        f"""
        SELECT {col}
        FROM Permisos p
        JOIN Modulos m ON p.ModuloId = m.Id
        WHERE p.RolId = ? AND m.Nombre = ? AND p.Estado = 1
        """,
        (rol_id, modulo.upper())
    )
    return bool(row and int(row[0]) == 1)


def require_permission(modulo, accion="ver"):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login_page"))
            if not has_permission(session.get("rol_id"), modulo, accion):
                menu = get_menu_for_role(session.get("rol_id"))
                return render_template("no_permission.html", modulo=modulo, menu=menu), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def get_menu_for_role(rol_id):
    if not rol_id:
        return {}
    rows = query_all("""
        SELECT m.Nombre, p.Ver, p.Crear, p.Editar
        FROM Permisos p
        JOIN Modulos m ON p.ModuloId = m.Id
        WHERE p.RolId = ? AND p.Estado = 1
    """, (rol_id,))
    return {r[0]: {"ver": bool(r[1]), "crear": bool(r[2]), "editar": bool(r[3])} for r in rows}


# ---------------- ROUTES: LOGIN ----------------
@app.route("/", methods=["GET"])
def login_page():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    usuario = request.form.get("usuario")
    clave = request.form.get("clave")

    row = query_one("""
        SELECT Id, ContrasenaHash, RolId, Estado
        FROM Usuarios
        WHERE Usuario = ? COLLATE Latin1_General_CS_AS
    """, (usuario,))

    if not row or int(row[3]) != 1 or not check_password_hash(row[1], clave):
        return render_template("login.html", error="Credenciales inválidas")

    rol_name_row = query_one("SELECT NombreRol FROM Roles WHERE Id = ?", (row[2],))
    rol_name = rol_name_row[0] if rol_name_row else "SinRol"

    # 👇 Guardamos user_id y también usuario_id como alias
    session.update({
        "user_id": row[0],
        "usuario_id": row[0],   # alias para que GastosGenerales funcione
        "usuario": usuario,
        "rol_id": row[2],
        "rol_nombre": rol_name
    })

    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()  # elimina toda la sesión
    flash("Has cerrado sesión correctamente.", "success")
    return redirect(url_for("login_page"))  # redirige al login

# ---------------- ROUTES: DASHBOARD ----------------
@app.route("/dashboard", endpoint="dashboard")
def dashboard_page():
    if "rol_id" not in session:
        flash("Debes iniciar sesión.", "danger")
        return redirect(url_for("login"))

    # Menú dinámico
    rows = query_all("""
        SELECT m.Id, m.Nombre, m.Descripcion, m.Ruta
        FROM Permisos p
        JOIN Modulos m ON p.ModuloId = m.Id
        WHERE p.RolId = ? AND p.Ver = 1 AND p.Estado = 1 AND m.Estado = 1
        ORDER BY m.Id ASC
    """, (session["rol_id"],))
    modulos = [{"id": r[0], "nombre": r[1], "descripcion": r[2], "ruta": r[3]} for r in rows]

    hoy = datetime.today()
    periodo_default = hoy.strftime("%Y-%m")
    periodo_str = (request.args.get("periodo") or periodo_default).strip()

    try:
        anio, mes = map(int, periodo_str.split("-"))
    except Exception:
        anio, mes = hoy.year, hoy.month
        periodo_str = f"{anio:04d}-{mes:02d}"

    # Parámetro: meses visibles en dashboard
    row_param = query_one("""
        SELECT ValorParametro 
        FROM ParametrosNegocio 
        WHERE NombreParametro = 'MESES_DASHBOARD_VISIBLES' AND Estado = 1
    """)
    try:
        meses_rango = int(row_param[0]) if row_param and str(row_param[0]).isdigit() else 12
    except Exception:
        meses_rango = 12

    # Lista de períodos disponibles
    base = datetime(hoy.year, hoy.month, 1)
    periodos_disponibles = []
    for i in range(meses_rango):
        ref = base - relativedelta(months=i)
        valor = ref.strftime("%Y-%m")
        nombre = ref.strftime("%B %Y")
        periodos_disponibles.append({"valor": valor, "nombre": nombre})

    # --- Datos mensuales desde las vistas ---
    datos_ventas = [0] * 12
    rows_ventas = query_all("""
        SELECT Mes, TotalVentas
        FROM vw_ReporteMensualVentas
        WHERE Anio = ?
    """, (anio,))
    for r in rows_ventas:
        datos_ventas[r[0] - 1] = float(r[1])

    datos_gastos = [0] * 12
    rows_gastos = query_all("""
        SELECT Mes, TotalGastado
        FROM vw_ReporteMensualGastos
        WHERE Anio = ?
    """, (anio,))
    for r in rows_gastos:
        datos_gastos[r[0] - 1] = float(r[1])

    datos_facturas = [0] * 12
    rows_facturas = query_all("""
        SELECT Mes, TotalFacturado
        FROM vw_ReporteMensualFacturas
        WHERE Anio = ?
    """, (anio,))
    for r in rows_facturas:
        datos_facturas[r[0] - 1] = float(r[1])

    # Totales del mes seleccionado
    totales = {
        "ventas": datos_ventas[mes-1],
        "gastos": datos_gastos[mes-1],
        "facturas": datos_facturas[mes-1]
    }

    # Labels de meses
    meses_labels = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]

    return render_template("dashboard.html",
                           modulos=modulos,
                           periodo_seleccionado=periodo_str,
                           periodos_disponibles=periodos_disponibles,
                           totales=totales,
                           meses=meses_labels,
                           datos_ventas=datos_ventas,
                           datos_gastos=datos_gastos,
                           datos_facturas=datos_facturas)



# ---------------- ROUTE: CAMBIAR CONTRASEÑA ----------------

@app.route("/cambiar_contrasena", methods=["GET", "POST"])
def cambiar_contrasena():
    if g.user is None:
        return redirect(url_for("login_page"))

    menu = get_menu_for_role(session["rol_id"])

    if request.method == "POST":
        nueva = request.form.get("nueva")
        confirmar = request.form.get("confirmar")

        if not nueva or not confirmar:
            return render_template("cambiar_contrasena.html", menu=menu, error="Debe ingresar la nueva contraseña y confirmarla")

        if nueva != confirmar:
            return render_template("cambiar_contrasena.html", menu=menu, error="Las contraseñas no coinciden")

        # Guardar nueva contraseña (hash seguro)
        pw_hash = generate_password_hash(nueva)
        exec_sql("""
            UPDATE Usuarios
            SET ContrasenaHash = ?
            WHERE Id = ?
        """, (pw_hash, session["user_id"]))

        return redirect(url_for("dashboard"))

    return render_template("cambiar_contrasena.html", menu=menu)


# ---------------- ROUTES: USUARIOS ----------------
@app.route("/usuarios", methods=["GET", "POST"])
@require_permission("USUARIOS", "ver")
def usuarios_page():
    menu = get_menu_for_role(session["rol_id"])
    roles = query_all("SELECT Id, NombreRol FROM Roles WHERE Estado = 1 ORDER BY NombreRol")

    if request.method == "POST":
        usuario = (request.form.get("usuario") or "").strip()
        clave = (request.form.get("clave") or "").strip()
        rol_nombre = (request.form.get("rol") or "").strip()

        rol_id_row = query_one("SELECT Id FROM Roles WHERE NombreRol = ?", (rol_nombre,))
        rol_id = rol_id_row[0] if rol_id_row else None

        if not usuario or not clave or not rol_id:
            flash("Todos los campos son obligatorios.", "danger")
        else:
            # Validación de duplicados
            exists = query_one("SELECT Id FROM Usuarios WHERE Usuario = ? AND Estado = 1", (usuario,))
            if exists:
                flash("Ya existe un usuario con ese nombre.", "danger")
            else:
                pw_hash = generate_password_hash(clave)
                exec_sql(
                    "INSERT INTO Usuarios (Usuario, ContrasenaHash, RolId, Estado, FechaCreacion) VALUES (?, ?, ?, 1, GETDATE())",
                    (usuario, pw_hash, rol_id)
                )
                flash("Usuario creado correctamente.", "success")

        return redirect(url_for("usuarios_page"))

    # --- Paginación y búsqueda ---
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "1=1"
    params = []

    if search:
        where += " AND (u.Usuario LIKE ? OR r.NombreRol LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    total_row = query_one(f"""
        SELECT COUNT(*) FROM Usuarios u
        JOIN Roles r ON u.RolId = r.Id
        WHERE {where}
    """, tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    rows = query_all(f"""
        SELECT u.Id, u.Usuario, r.NombreRol,
           (CASE WHEN r.NombreRol = 'Admin' THEN 1 ELSE 0 END) AS EsAdmin,
           u.Estado, u.FechaCreacion
        FROM Usuarios u
        JOIN Roles r ON u.RolId = r.Id
        WHERE {where}
        ORDER BY u.Id ASC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    usuarios = [{
        "id": r[0],
        "usuario": r[1],
        "rol": r[2],
        "admin": bool(r[3]),
        "estado": "Activo" if str(r[4]) in ("1", "True") else "Inactivo",
        "fecha_creacion": r[5].strftime("%Y-%m-%d %H:%M") if r[5] else ""
    } for r in rows]


    #  Aquí ya sin indentación extra
    return render_template(
        "usuarios.html",
        menu=menu,
        usuarios=usuarios,
        roles=roles,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total
    )

@app.route("/usuarios/inactivar/<int:usuario_id>", methods=["POST"])
@require_permission("USUARIOS", "editar")
def inactivar_usuario(usuario_id):
    exec_sql("UPDATE Usuarios SET Estado = 0 WHERE Id = ?", (usuario_id,))
    flash("Usuario inactivado correctamente.", "warning")
    return redirect(url_for("usuarios_page", page=request.args.get("page", 1), search=request.args.get("search", "")))


@app.route("/usuarios/editar/<int:usuario_id>", methods=["POST"])
@require_permission("USUARIOS", "editar")
def editar_usuario(usuario_id):
    usuario = (request.form.get("usuario_edit") or "").strip()
    rol_nombre = (request.form.get("rol_edit") or "").strip()
    rol_id_row = query_one("SELECT Id FROM Roles WHERE NombreRol = ?", (rol_nombre,))
    rol_id = rol_id_row[0] if rol_id_row else None

    if not usuario or not rol_id:
        flash("Todos los campos son obligatorios.", "danger")
    else:
        # Validación de duplicados (excluyendo el mismo usuario)
        exists = query_one("SELECT Id FROM Usuarios WHERE Usuario = ? AND Estado = 1 AND Id <> ?", (usuario, usuario_id))
        if exists:
            flash("Ya existe otro usuario con ese nombre.", "danger")
        else:
            exec_sql("UPDATE Usuarios SET Usuario = ?, RolId = ? WHERE Id = ?", (usuario, rol_id, usuario_id))
            flash("Usuario actualizado correctamente.", "success")

    return redirect(url_for("usuarios_page", page=request.args.get("page", 1), search=request.args.get("search", "")))


# ---------------- ROUTES: PERMISOS ----------------
@app.route("/permisos", methods=["GET", "POST"])
@require_permission("PERMISOS", "ver")
def permisos_page():
    menu = get_menu_for_role(session["rol_id"])
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    per_page = 10

    roles = query_all("SELECT Id, NombreRol FROM Roles")
    modulos = query_all("SELECT Id, Nombre FROM Modulos WHERE Estado = 1")

    if request.method == "POST":
        rol_id = request.form.get("rol_id")
        modulo_id = request.form.get("modulo")
        ver = 1 if request.form.get("ver") else 0
        crear = 1 if request.form.get("crear") else 0
        editar = 1 if request.form.get("editar") else 0

        exists = query_one(
            "SELECT Id FROM Permisos WHERE RolId = ? AND ModuloId = ? AND Estado = 1",
            (rol_id, modulo_id)
        )
        if exists:
            flash("Ya existe un permiso para ese rol y módulo.", "danger")
        elif rol_id and modulo_id:
            exec_sql(
                "INSERT INTO Permisos (RolId, ModuloId, Ver, Crear, Editar, Estado) VALUES (?, ?, ?, ?, ?, 1)",
                (rol_id, modulo_id, ver, crear, editar)
            )
            flash("Permiso asignado correctamente.", "success")
        return redirect(url_for("permisos_page"))

    # Conteo total
    total = query_one("""
        SELECT COUNT(*)
        FROM Permisos p
        JOIN Roles r ON p.RolId = r.Id
        JOIN Modulos m ON p.ModuloId = m.Id
        WHERE p.Estado = 1
          AND (r.NombreRol LIKE ? OR m.Nombre LIKE ?)
    """, (f"%{search}%", f"%{search}%"))[0]

    total_pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page

    rows = query_all("""
        SELECT p.Id, r.NombreRol, m.Nombre, p.Ver, p.Crear, p.Editar
        FROM Permisos p
        JOIN Roles r ON p.RolId = r.Id
        JOIN Modulos m ON p.ModuloId = m.Id
        WHERE p.Estado = 1
          AND (r.NombreRol LIKE ? OR m.Nombre LIKE ?)
        ORDER BY p.Id ASC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, (f"%{search}%", f"%{search}%", offset, per_page))

    permisos = [
        {"id": r[0], "rol": r[1], "modulo": r[2],
         "ver": bool(r[3]), "crear": bool(r[4]), "editar": bool(r[5])}
        for r in rows
    ]

    return render_template("permisos.html", menu=menu, roles=roles, modulos=modulos,
                           permisos=permisos, total=total, page=page,
                           total_pages=total_pages, search=search)


@app.route("/permisos/editar/<int:permiso_id>", methods=["POST"])
@require_permission("PERMISOS", "editar")
def editar_permiso(permiso_id):
    ver = 1 if request.form.get("ver_edit") else 0
    crear = 1 if request.form.get("crear_edit") else 0
    editar = 1 if request.form.get("editar_edit") else 0

    exec_sql("""
        UPDATE Permisos
        SET Ver = ?, Crear = ?, Editar = ?
        WHERE Id = ?
    """, (ver, crear, editar, permiso_id))

    flash("Permiso actualizado correctamente.", "success")
    return redirect(url_for("permisos_page"))


@app.route("/permisos/inactivar/<int:permiso_id>", methods=["POST"])
@require_permission("PERMISOS", "editar")
def inactivar_permiso(permiso_id):
    exec_sql("UPDATE Permisos SET Estado = 0 WHERE Id = ?", (permiso_id,))
    flash("Permiso inactivado correctamente.", "warning")
    return redirect(url_for("permisos_page"))

# ---------------- ROUTES: VENTAS MANUALES----------------
@app.route("/ventas", methods=["GET", "POST"])
@require_permission("VENTAS", "ver")
def ventas_page():
    menu = get_menu_for_role(session["rol_id"])
    hoy = datetime.today()

    # Límites de fecha del mes actual
    inicio_mes = hoy.replace(day=1)
    if hoy.month == 12:
        siguiente_mes = hoy.replace(year=hoy.year + 1, month=1, day=1)
    else:
        siguiente_mes = hoy.replace(month=hoy.month + 1, day=1)
    fin_mes = siguiente_mes - timedelta(days=1)

    inicio_mes_str = inicio_mes.strftime("%Y-%m-%d")
    fin_mes_str = fin_mes.strftime("%Y-%m-%d")

    # Parámetro para permitir meses anteriores en ventas (1/0)
    row_param = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'PERMITIR_FECHAS_ANTERIORES_VENTAS' AND Estado = 1
    """)
    permitir_anteriores_ventas = int(row_param[0]) if row_param else 0

    periodo_str = request.args.get("periodo", hoy.strftime("%Y-%m"))
    año, mes = map(int, periodo_str.split("-"))
    search = request.args.get("search", "").strip()
    pagina_actual = int(request.args.get("page", 1))
    ventas_por_pagina = 10

    # --- Registrar nueva venta ---
    if request.method == "POST":
        fecha_emision = request.form.get("fecha_emision")
        total_venta = request.form.get("total_venta")
        medio_pago = request.form.get("medio_pago")
        prefijo = request.form.get("tipo_factura")
        usuario_id = session.get("user_id")

        if not fecha_emision or not total_venta or not medio_pago or not prefijo:
            flash("Todos los campos son obligatorios.", "warning")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        # Validación de fecha
        try:
            fecha_emision_dt = datetime.strptime(fecha_emision, "%Y-%m-%d")
        except (ValueError, TypeError):
            flash("Formato de fecha inválido.", "danger")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        # Bloquear meses futuros
        if fecha_emision_dt > fin_mes:
            flash("No se permite registrar ventas en meses futuros.", "danger")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        # Bloquear meses anteriores si el parámetro está en 0
        if permitir_anteriores_ventas == 0 and fecha_emision_dt < inicio_mes:
            flash("No se permite registrar ventas en meses anteriores.", "danger")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        # Validación de total
        total_venta = float(total_venta.replace(",", ".")) if total_venta else 0.0
        if total_venta <= 0:
            flash("El total de venta debe ser mayor a cero.", "warning")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        # IVA y cálculos
        row_iva = query_one("""
            SELECT ValorParametro FROM ParametrosNegocio
            WHERE NombreParametro = 'IVA_POR_DEFECTO' AND Estado = 1
        """)
        iva_porcentaje = float(row_iva[0]) if row_iva and row_iva[0] else 19.0
        impuestos = round(total_venta * (iva_porcentaje / 100), 2)
        subtotal = round(total_venta - impuestos, 2)

        # Consecutivo
        row_consecutivo = query_one("""
            SELECT Id, ValorActual, NumeroFinal FROM Consecutivos
            WHERE Prefijo = ? AND Estado = 1
        """, (prefijo,))
        if not row_consecutivo:
            flash("No se encontró configuración de consecutivo.", "danger")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        consecutivo_id, valor_actual, numero_final = int(row_consecutivo[0]), int(row_consecutivo[1]), int(row_consecutivo[2])
        if valor_actual >= numero_final:
            flash("Se alcanzó el límite de consecutivos.", "danger")
            return redirect(url_for("ventas_page", periodo=periodo_str))

        numero_documento = valor_actual + 1
        exec_sql("UPDATE Consecutivos SET ValorActual = ? WHERE Prefijo = ? AND Estado = 1",
                 (numero_documento, prefijo))

        # Insertar venta
        exec_sql("""
            INSERT INTO Ventas (
                NumeroDocumento, Prefijo, FechaEmision, HoraEmision,
                Subtotal, Impuestos, TotalVenta, MedioPago,
                UsuarioCreaId, FechaCreacion, Estado, ConsecutivoId, VentaCerrada
            ) VALUES (?, ?, ?, GETDATE(), ?, ?, ?, ?, ?, GETDATE(), 1, ?, 0)
        """, (numero_documento, prefijo, fecha_emision,
              subtotal, impuestos, total_venta, medio_pago, usuario_id, consecutivo_id))

        flash("Venta registrada exitosamente.", "success")
        return redirect(url_for("ventas_page", periodo=periodo_str))

    # --- Filtro de ventas del mes ---
    filtro = "WHERE Estado = 1 AND MONTH(FechaEmision) = ? AND YEAR(FechaEmision) = ?"
    params = [mes, año]
    if search:
        filtro += " AND (CAST(NumeroDocumento AS NVARCHAR) LIKE ?)"
        params += [f"%{search}%"]

    todas_las_ventas = query_all(f"""
        SELECT Id,
               Prefijo AS prefijo,
               NumeroDocumento AS numero_documento,
               CONVERT(varchar(10), FechaEmision, 120) AS fecha_emision, -- YYYY-MM-DD
               Subtotal AS subtotal,
               Impuestos AS impuestos,
               TotalVenta AS total_venta,
               ISNULL(VentaCerrada,0) AS venta_cerrada,
               FechaCreacion AS fecha_creacion
        FROM Ventas
        {filtro}
        ORDER BY FechaCreacion DESC
    """, params)

    total_registros = len(todas_las_ventas)
    total_paginas = math.ceil(total_registros / ventas_por_pagina)
    inicio = (pagina_actual - 1) * ventas_por_pagina
    ventas = todas_las_ventas[inicio:inicio + ventas_por_pagina]

    # --- Indicadores dinámicos por año ---
    row_uvt = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'TOPE_UVT_FE' AND Estado = 1
    """)
    tope_uvt = float(row_uvt[0]) if row_uvt and row_uvt[0] else 1300.0

    nombre_uvt_parametro = f"UVT{año}_PESOS"
    row_uvt_pesos = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = ? AND Estado = 1
    """, (nombre_uvt_parametro,))
    uvt_pesos = float(row_uvt_pesos[0]) if row_uvt_pesos and row_uvt_pesos[0] else 0.0

    tope_anual_pesos = round(tope_uvt * uvt_pesos, 2)
    tope_mensual = round(tope_anual_pesos / 12, 2)

    row_ventas_mes = query_one("""
        SELECT SUM(TotalVenta) FROM Ventas
        WHERE Estado = 1 AND MONTH(FechaEmision) = ? AND YEAR(FechaEmision) = ?
    """, (mes, año))
    ventas_mes = float(row_ventas_mes[0]) if row_ventas_mes and row_ventas_mes[0] else 0.0

    row_gastos_mes = query_one("""
        SELECT SUM(Valor) FROM GastosGenerales
        WHERE Estado = 1 AND MONTH(Fecha) = ? AND YEAR(Fecha) = ?
    """, (mes, año))
    gastos_mes = float(row_gastos_mes[0]) if row_gastos_mes and row_gastos_mes[0] else 0.0

    # Suma de facturas de proveedores del mes
    row_facturas_mes = query_one("""
        SELECT SUM(Total) FROM FacturasProveedores
        WHERE Estado = 1 AND MONTH(FechaFactura) = ? AND YEAR(FechaFactura) = ?
    """, (mes, año))
    facturas_mes = float(row_facturas_mes[0]) if row_facturas_mes and row_facturas_mes[0] else 0.0

    diferencia = tope_mensual - ventas_mes - gastos_mes - facturas_mes

    # Meses visibles para el selector
    row_visible_meses = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'REGISTRO_MESES_VENTAS_VISIBLES' AND Estado = 1
    """)
    visible_meses = int(row_visible_meses[0]) if row_visible_meses and row_visible_meses[0] else 12

    base = datetime(hoy.year, hoy.month, 1)
    meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    periodos_disponibles = []
    for i in range(visible_meses):
        dt = restar_meses(base, i)
        valor = f"{dt.year:04d}-{dt.month:02d}"
        nombre = f"{meses_es[dt.month - 1]} {dt.year}"
        periodos_disponibles.append({"valor": valor, "nombre": nombre})

    consecutivos = query_all("SELECT Prefijo, Tipo FROM Consecutivos WHERE Estado = 1")

    # --- RETURN: render de la página de ventas ---
    return render_template("ventas.html",
        menu=menu,
        periodo_seleccionado=periodo_str,
        ventas=ventas,
        consecutivos=consecutivos,
        tope_anual_pesos=tope_anual_pesos,
        tope_mensual=tope_mensual,
        ventas_mes=ventas_mes,
        gastos_mes=gastos_mes,
        facturas_mes=facturas_mes,
        diferencia=diferencia,
        total_registros=total_registros,
        pagina_actual=pagina_actual,
        total_paginas=total_paginas,
        search=search,
        periodos_disponibles=periodos_disponibles,
        inicio_mes_str=inicio_mes_str,
        fin_mes_str=fin_mes_str,
        permitir_anteriores=permitir_anteriores_ventas
    )

@app.route("/ventas/inactivar/<int:venta_id>", methods=["POST"])
@require_permission("VENTAS", "editar")
def inactivar_venta(venta_id):
    exec_sql("UPDATE Ventas SET Estado = 0 WHERE Id = ?", (venta_id,))
    flash("Venta inactivada correctamente.", "success")
    return redirect(url_for("ventas_page", periodo=request.args.get("periodo")))

# ---------------- ROUTE: EDITAR VENTA NORMAL ----------------
@app.route("/ventas/<int:venta_id>/editar", methods=["GET", "POST"])
@require_permission("VENTAS", "editar")
def editar_venta_normal(venta_id):
    from utils import rango_mes, format_date_for_input
    from datetime import datetime

    menu = get_menu_for_role(session["rol_id"])
    venta = query_one("""
        SELECT Id, FechaEmision, TotalVenta, MedioPago, ISNULL(VentaCerrada,0) as VentaCerrada
        FROM Ventas
        WHERE Id = ?
    """, (venta_id,))

    if not venta:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for("ventas_page"))

    fecha_emision_actual = venta[1]
    año, mes = fecha_emision_actual.year, fecha_emision_actual.month
    fecha_inicio, fecha_fin = rango_mes(año, mes)
    fecha_emision_str = format_date_for_input(fecha_emision_actual)

    if request.method == "POST":
        accion = request.form.get("accion")

        # Si se presiona "Cerrar venta"
        if accion == "cerrar":
            exec_sql("""
                UPDATE Ventas
                SET VentaCerrada = 1
                WHERE Id = ?
            """, (venta_id,))
            flash("La venta fue cerrada correctamente. Ya no se puede modificar.", "success")
            return redirect(url_for("editar_venta_normal", venta_id=venta_id))

        # Caso normal: editar datos
        fecha_emision = request.form.get("fecha_emision")
        total_venta = float(request.form.get("total_venta"))
        medio_pago = request.form.get("medio_pago")

        fecha_dt = datetime.strptime(fecha_emision, "%Y-%m-%d")
        if fecha_dt < datetime.strptime(fecha_inicio, "%Y-%m-%d") or fecha_dt > datetime.strptime(fecha_fin, "%Y-%m-%d"):
            flash(f"La fecha de emisión debe estar dentro de {fecha_inicio} y {fecha_fin}.", "warning")
            return redirect(url_for("editar_venta_normal", venta_id=venta_id))

        row_iva = query_one("""
            SELECT ValorParametro FROM ParametrosNegocio
            WHERE NombreParametro = 'IVA_POR_DEFECTO' AND Estado = 1
        """)
        iva_porcentaje = float(row_iva[0]) if row_iva and row_iva[0] else 19.0
        impuestos = round(total_venta * (iva_porcentaje / 100), 2)
        subtotal = round(total_venta - impuestos, 2)

        exec_sql("""
            UPDATE Ventas
            SET FechaEmision=?, TotalVenta=?, Subtotal=?, Impuestos=?, MedioPago=?
            WHERE Id=?
        """, (fecha_emision, total_venta, subtotal, impuestos, medio_pago, venta_id))

        flash("Venta actualizada correctamente.", "success")
        return redirect(url_for("ventas_page"))

    return render_template("editar_venta_normal.html",
                       menu=menu,
                       venta={
                           "Id": venta[0],
                           "FechaEmision": venta[1],
                           "TotalVenta": venta[2],
                           "MedioPago": venta[3],
                           "VentaCerrada": venta[4]
                       },
                       fecha_inicio=fecha_inicio,
                       fecha_fin=fecha_fin,
                       fecha_emision_str=fecha_emision_str)

## codigo
# ---------------- ROUTE: DETALLE DE VENTA ver (CUENTA DE COBRO) ----------------
@app.route("/detalleventa/<int:venta_id>")
def detalleventa_cuenta_cobro(venta_id):
    # Venta
    venta_row = query_one("""
        SELECT Id, NumeroDocumento, FechaEmision, TotalVenta, ISNULL(VentaCerrada,0)
        FROM dbo.Ventas
        WHERE Id = ?
    """, (venta_id,))
    if not venta_row:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for("ventas_page"))

    venta = {
        "Id": venta_row[0],
        "NumeroDocumento": venta_row[1],
        "FechaEmision": venta_row[2],
        "TotalVenta": Decimal(venta_row[3] or 0),   # conversión a Decimal
        "VentaCerrada": bool(venta_row[4])
    }

    # Detalles con ClienteId
    detalles_rows = query_all("""
        SELECT Id, NumeroLinea, Descripcion, Cantidad, ValorUnitario, ValorTotalUnitario, ClienteId
        FROM dbo.DetalleVenta
        WHERE VentaId = ? AND Estado = 1
        ORDER BY NumeroLinea ASC
    """, (venta_id,))
    detalles = [{
        "Id": d[0], "NumeroLinea": d[1], "Descripcion": d[2],
        "Cantidad": d[3], "ValorUnitario": d[4],
        "ValorTotalUnitario": d[5], "ClienteId": d[6]
    } for d in detalles_rows]

    # Cliente de la venta = cliente de la primera línea activa
    cliente_id = detalles[0]["ClienteId"] if detalles else None
    cliente = None
    if cliente_id:
        c = query_one("""
            SELECT Id, RazonSocial, NumeroIdentificacion, Direccion, Telefono, Correo
            FROM dbo.Clientes
            WHERE Id = ? AND Estado = 1
        """, (cliente_id,))
        if c:
            cliente = {
                "Id": c[0],
                "RazonSocial": c[1],
                "NumeroIdentificacion": c[2],
                "Direccion": c[3],
                "Telefono": c[4],
                "Correo": c[5]
            }

    # Subtotal de ítems
    subtotal_actual = sum(Decimal(d["ValorTotalUnitario"] or 0) for d in detalles)

    # Menú dinámico según rol
    menu = get_menu_for_role(session.get("rol_id", 1))

    return render_template(
        "detalleventa.html",
        menu=menu,
        venta=venta,
        cliente=cliente,
        detalles=detalles,
        subtotal_actual=subtotal_actual,
        cliente_id=cliente_id
    )


# ---------------- ROUTE: EDITAR Total de la Venta (CUENTA DE COBRO) ----------------
@app.route("/detalleventa/<int:venta_id>/editar", methods=["POST"])
def editar_venta_cuenta_cobro(venta_id):
    # Buscar la venta
    venta = query_one("SELECT Id FROM dbo.Ventas WHERE Id = ?", (venta_id,))
    if not venta:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for("ventas_page"))

    # Capturar datos del formulario
    fecha_emision = request.form.get("fecha_emision_edit")
    total_venta = request.form.get("total_venta_edit")

    # Actualizar la venta
    exec_sql("""
        UPDATE dbo.Ventas
        SET FechaEmision = ?, TotalVenta = ?
        WHERE Id = ?
    """, (fecha_emision, total_venta, venta_id))

    flash("Venta actualizada correctamente.", "success")
    return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))


# ---------------- ROUTE: ADICIONAR DETALLE items ----------------
@app.route("/detalleventa/<int:venta_id>/add", methods=["POST"])
def detalleventa_page(venta_id):
    venta = query_one("""
        SELECT Id, NumeroDocumento, FechaEmision, TotalVenta, ISNULL(VentaCerrada,0)
        FROM dbo.Ventas
        WHERE Id = ?
    """, (venta_id,))
    if not venta:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for("ventas_cuentacobro_page"))

    if bool(venta[4]):
        flash("La venta ya está cerrada.", "warning")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    detalles = query_all("""
        SELECT Id, NumeroLinea, ValorTotalUnitario, ClienteId
        FROM dbo.DetalleVenta
        WHERE VentaId = ? AND Estado = 1
        ORDER BY NumeroLinea ASC
    """, (venta_id,))
    subtotal_actual = sum(Decimal(d[2] or 0) for d in detalles)
    total_venta = Decimal(venta[3])
    cliente_id_actual = detalles[0][3] if detalles else None

    descripcion = request.form.get("descripcion")
    cantidad = Decimal(request.form.get("cantidad"))
    valor_unitario = Decimal(request.form.get("valor_unitario"))
    numero_identificacion = request.form.get("numero_identificacion")
    valor_total = cantidad * valor_unitario

    # Buscar cliente por NúmeroIdentificacion
    cliente = query_one("""
        SELECT Id
        FROM dbo.Clientes
        WHERE NumeroIdentificacion = ? AND Estado = 1
    """, (numero_identificacion,))
    if not cliente:
        flash("Cliente no encontrado o inactivo.", "danger")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))
    nuevo_cliente_id = cliente[0]

    # Regla: si ya hay líneas, no permitir cambiar el cliente
    if cliente_id_actual is not None and nuevo_cliente_id != cliente_id_actual:
        flash("Ya hay líneas registradas. No puedes cambiar el cliente de la venta.", "danger")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Validación subtotal
    if subtotal_actual + valor_total > total_venta:
        flash("El total de las líneas supera el TotalVenta. No se guardó la línea.", "danger")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Insertar detalle
    exec_sql("""
        INSERT INTO dbo.DetalleVenta (
            VentaId, NumeroLinea, Descripcion, Cantidad, ValorUnitario,
            ValorTotalUnitario, ClienteId, Estado
        ) VALUES (
            ?, (SELECT ISNULL(MAX(NumeroLinea), 0) + 1 FROM dbo.DetalleVenta WHERE VentaId = ?),
            ?, ?, ?, ?, ?, 1
        )
    """, (venta_id, venta_id, descripcion, cantidad, valor_unitario, valor_total, nuevo_cliente_id))

    flash("Detalle registrado correctamente.", "success")
    return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))


# ---------------- ROUTE: EDITAR DETALLE items ----------------
@app.route("/detalle/editar/<int:detalle_id>/<int:venta_id>", methods=["GET", "POST"])
def editar_detalle(detalle_id, venta_id):
    detalle = query_one("""
        SELECT Id, Descripcion, Cantidad, ValorUnitario
        FROM dbo.DetalleVenta
        WHERE Id = ?
    """, (detalle_id,))
    if not detalle:
        flash("Detalle no encontrado.", "danger")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    venta = query_one("SELECT ISNULL(VentaCerrada,0) FROM dbo.Ventas WHERE Id = ?", (venta_id,))
    if venta and bool(venta[0]):
        flash("La venta está cerrada. No puedes editar detalles.", "warning")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    if request.method == "POST":
        descripcion = request.form.get("descripcion")
        cantidad = Decimal(request.form.get("cantidad"))
        valor_unitario = Decimal(request.form.get("valor_unitario"))
        valor_total = cantidad * valor_unitario

        exec_sql("""
            UPDATE dbo.DetalleVenta
            SET Descripcion = ?, Cantidad = ?, ValorUnitario = ?, ValorTotalUnitario = ?
            WHERE Id = ?
        """, (descripcion, cantidad, valor_unitario, valor_total, detalle_id))

        flash("Detalle actualizado correctamente.", "success")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    return render_template("editar_detalle.html", detalle={
        "Id": detalle[0],
        "Descripcion": detalle[1],
        "Cantidad": detalle[2],
        "ValorUnitario": detalle[3]
    }, venta_id=venta_id)

# ---------------- ROUTE: INACTIVAR DETALLE ----------------
@app.route("/detalle/inactivar/<int:detalle_id>/<int:venta_id>", methods=["POST"])
def inactivar_detalle(detalle_id, venta_id):
    exec_sql("UPDATE dbo.DetalleVenta SET Estado = 0 WHERE Id = ?", (detalle_id,))
    flash("Detalle inactivado.", "success")
    return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

@app.route("/api/clientes/<numero_identificacion>")
def api_get_cliente(numero_identificacion):
    cliente = query_one("""
        SELECT Id, RazonSocial, NumeroIdentificacion, Direccion, Telefono, Correo
        FROM dbo.Clientes
        WHERE NumeroIdentificacion = ? AND Estado = 1
    """, (numero_identificacion,))
    if not cliente:
        return jsonify({"error": "Cliente no encontrado"}), 404

    return jsonify({
        "Id": cliente[0],
        "RazonSocial": cliente[1],
        "NumeroIdentificacion": cliente[2],
        "Direccion": cliente[3],
        "Telefono": cliente[4],
        "Correo": cliente[5]
    })


# ---------------- ROUTE: CERRAR VENTA cuenta de cobro ----------------
@app.route("/ventas/<int:venta_id>/cerrar", methods=["POST"])
def cerrar_venta(venta_id):
    venta = query_one("SELECT Id, TotalVenta FROM dbo.Ventas WHERE Id = ?", (venta_id,))
    if not venta:
        flash("Venta no encontrada.", "danger")
        return redirect(url_for("ventas_page"))

    # Traer ítems activos de la venta
    detalles = query_all("SELECT ValorTotalUnitario FROM dbo.DetalleVenta WHERE VentaId = ? AND Estado = 1", (venta_id,))
    if not detalles:
        flash("No puede cerrar la venta sin ítems registrados.", "warning")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Calcular subtotal actual
    subtotal_actual = sum(Decimal(d[0] or 0) for d in detalles)
    totalVenta = Decimal(venta[1] or 0)

    # Validar que el subtotal coincida con el total de la venta
    if subtotal_actual != totalVenta:
        flash("No puede cerrar la venta: el total de ítems debe ser igual al Total de la venta.", "warning")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Marcar la venta como cerrada
    exec_sql("UPDATE dbo.Ventas SET VentaCerrada = 1 WHERE Id = ?", (venta_id,))
    flash("Venta cerrada correctamente.", "success")

    return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))


# ---------------- ROUTE: GENERAR PDF CUENTA DE COBRO ----------------
@app.route("/detalleventa/pdf/<int:venta_id>")
def generar_pdf_cuenta_cobro(venta_id):
    # Datos del propietario
    parametros = query_all("""
        SELECT NombreParametro, ValorParametro
        FROM ParametrosNegocio
        WHERE Estado = 1 AND Categoria = 'EMPRENDIMIENTO_PROPIETARIO'
    """)
    propietario = {p[0]: p[1] for p in parametros}

    # Información de la venta
    venta = query_one("""
        SELECT V.Id, V.NumeroDocumento, V.FechaEmision, V.TotalVenta, ISNULL(V.VentaCerrada,0), C.Tipo
        FROM Ventas V
        LEFT JOIN Consecutivos C ON V.ConsecutivoId = C.Id
        WHERE V.Id = ?
    """, (venta_id,))

    # Detalles de la venta
    detalles = query_all("""
        SELECT Descripcion, Cantidad, ValorUnitario, ValorTotalUnitario
        FROM DetalleVenta
        WHERE VentaId = ?
    """, (venta_id,))

    # 🔒 Validación: no permitir imprimir si no hay ítems
    if not detalles:
        flash("No puede generar PDF: la venta no tiene ítems registrados.", "warning")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Cliente receptor (JOIN directo, sin restricción de estado)
    cliente = query_one("""
        SELECT TOP 1 C.RazonSocial, C.NumeroIdentificacion, C.Direccion, C.Telefono, C.Correo
        FROM DetalleVenta D
        INNER JOIN Clientes C ON D.ClienteId = C.Id
        WHERE D.VentaId = ? AND D.ClienteId IS NOT NULL
    """, (venta_id,))

    if not cliente:
        flash("Debes seleccionar un cliente antes de imprimir la cuenta de cobro.", "danger")
        return redirect(url_for("detalleventa_cuenta_cobro", venta_id=venta_id))

    # Fecha en español
    try:
        locale.setlocale(locale.LC_TIME, 'es_CO.UTF-8')
    except:
        locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')

    fecha_emision = datetime.strptime(str(venta[2]), "%Y-%m-%d")
    fecha_formateada = fecha_emision.strftime("%d de %B de %Y")

    # Totales
    total = sum(d[3] for d in detalles)
    total_letras = numero_a_letras(int(total))

    # Formatear consecutivo CUENTA_COBRO
    numero_doc = venta[1]
    tipo_doc = venta[5]
    if tipo_doc and tipo_doc.upper() == "CUENTA_COBRO":
        numero_doc = f"{int(numero_doc):04d}"

    # Renderizar plantilla
    html = render_template("pdf_cuenta_cobro.html",
                           propietario=propietario,
                           firma_rel_path=firma_rel_path,
                           cliente={
                               "RazonSocial": cliente[0],
                               "NumeroIdentificacion": cliente[1],
                               "Direccion": cliente[2],
                               "Telefono": cliente[3],
                               "Correo": cliente[4]
                           },
                           venta={
                               "Id": venta[0],
                               "NumeroDocumento": numero_doc,
                               "FechaEmision": fecha_formateada,
                               "TotalVenta": venta[3],
                               "VentaCerrada": venta[4],
                               "Tipo": tipo_doc
                           },
                           detalles=[{
                               "Descripcion": d[0],
                               "Cantidad": d[1],
                               "ValorUnitario": d[2],
                               "ValorTotalUnitario": d[3]
                           } for d in detalles],
                           total=total,
                           total_letras=total_letras)

    # Generar PDF
    pdf = HTML(string=html, base_url=app.root_path).write_pdf()
    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"inline; filename=cuenta_cobro_{venta_id}.pdf"
    return response

# ---------------- ROUTE: Distribucion ----------------
@app.route("/distribucion", methods=["GET", "POST"])
def distribucion_principal():
    anio = datetime.now().year
    mes = datetime.now().month

    if request.method == "POST":
        anio = int(request.form.get("anio"))
        mes = int(request.form.get("mes"))
        ventas_pct = Decimal(request.form.get("ventas_pct"))
        gastos_pct = Decimal(request.form.get("gastos_pct"))
        facturas_pct = Decimal(request.form.get("facturas_pct"))

        if ventas_pct + gastos_pct + facturas_pct != 100:
            flash("Los porcentajes deben sumar 100%.", "danger")
            return redirect(request.url)

        exec_sql("""
            MERGE DistribucionMensual AS target
            USING (SELECT ? AS Anio, ? AS Mes) AS source
            ON target.Anio = source.Anio AND target.Mes = source.Mes
            WHEN MATCHED THEN
                UPDATE SET PorcentajeVentas = ?, PorcentajeGastos = ?, PorcentajeFacturas = ?
            WHEN NOT MATCHED THEN
                INSERT (Anio, Mes, PorcentajeVentas, PorcentajeGastos, PorcentajeFacturas)
                VALUES (?, ?, ?, ?, ?);
        """, (anio, mes, ventas_pct, gastos_pct, facturas_pct,
              anio, mes, ventas_pct, gastos_pct, facturas_pct))

        flash("Distribución guardada correctamente.", "success")
        return redirect(url_for("distribucion_principal"))

    # Obtener UVT del año
    parametros = query_one("""
        SELECT ValorParametro
        FROM ParametrosNegocio
        WHERE NombreParametro = ? AND Estado = 1
    """, (f"UVT{anio}_PESOS",))

    if not parametros:
        flash(f"No se encontró UVT para el año {anio}.", "danger")
        return redirect(url_for("index"))

    uvt_valor = Decimal(parametros[0])
    tope_mensual = uvt_valor * Decimal("292")

    distribucion = query_one("""
        SELECT PorcentajeVentas, PorcentajeGastos, PorcentajeFacturas
        FROM DistribucionMensual
        WHERE Anio = ? AND Mes = ?
    """, (anio, mes))

    valores = None
    if distribucion:
        valores = {
            "ventas": tope_mensual * distribucion[0] / 100,
            "gastos": tope_mensual * distribucion[1] / 100,
            "facturas": tope_mensual * distribucion[2] / 100
        }

    return render_template("distribucion.html",
                           anio=anio, mes=mes,
                           tope_mensual=tope_mensual,
                           distribucion=distribucion,
                           valores=valores)


# ---------------- ROUTES: CLIENTES / PROVEEDORES ----------------
@app.route("/clientes", methods=["GET", "POST"])
@require_permission("CLIENTES", "ver")
def clientes_page():
    menu = get_menu_for_role(session["rol_id"])

    # Tipo de entidad seleccionado (CLIENTE o PROVEEDOR)
    tipo_entidad = (request.args.get("tipo_entidad") or "CLIENTE").upper()

    # ---------------- CREAR CLIENTE / PROVEEDOR ----------------
    if request.method == "POST":
        tipo_id = (request.form.get("tipo_identificacion_id") or "").strip()
        numero_id = (request.form.get("numero_identificacion") or "").strip()
        razon = (request.form.get("razon_social") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        direccion = (request.form.get("direccion") or "").strip()
        departamento = (request.form.get("departamento") or "").strip()
        ciudad = (request.form.get("municipio") or "").strip()
        correo = (request.form.get("correo") or "").strip()
        tipo_entidad_form = (request.form.get("tipo_entidad") or "CLIENTE").upper()
        usuario_id = session.get("user_id")

        if tipo_id and numero_id and razon and usuario_id:

            # Validar duplicado por tipo + identificación
            existe = query_one("""
                SELECT Id, Estado 
                FROM Clientes 
                WHERE TipoIdentificacionId = ? 
                  AND NumeroIdentificacion = ?
                  AND TipoEntidad = ?
            """, (tipo_id, numero_id, tipo_entidad_form))

            if existe:
                if existe[1] == 1:
                    flash(f"El {tipo_entidad_form.lower()} ya existe y está activo.", "danger")
                else:
                    flash(f"El {tipo_entidad_form.lower()} ya existe pero está inactivo.", "warning")
            else:
                exec_sql("""
                    INSERT INTO Clientes (
                        TipoIdentificacionId, NumeroIdentificacion, RazonSocial, Telefono, Direccion,
                        Departamento, Municipio, Correo, FechaCreacion, UsuarioCreaId, Estado, TipoEntidad
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), ?, 1, ?)
                """, (tipo_id, numero_id, razon, telefono, direccion, departamento, ciudad, correo, usuario_id, tipo_entidad_form))

                flash(f"{tipo_entidad_form.capitalize()} creado exitosamente.", "success")
        else:
            flash("Los campos Tipo de identificación, Número y Razón Social son obligatorios.", "danger")

        return redirect(url_for("clientes_page", tipo_entidad=tipo_entidad))

    # ---------------- LISTADO + BÚSQUEDA + PAGINACIÓN ----------------
    page_size = 10
    page = int(request.args.get("page", 1)) if request.args.get("page") else 1
    if page < 1: page = 1
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "c.Estado = 1 AND c.TipoEntidad = ?"
    params = [tipo_entidad]

    if search:
        where += " AND (c.RazonSocial LIKE ? OR c.NumeroIdentificacion LIKE ? OR c.Correo LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])

    total_row = query_one(f"SELECT COUNT(*) FROM Clientes c WHERE {where}", tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    rows = query_all(f"""
        SELECT c.Id, t.Descripcion, c.NumeroIdentificacion, c.RazonSocial, c.Telefono, c.Direccion,
               c.Departamento, c.Municipio, c.Correo, c.FechaCreacion, c.TipoIdentificacionId, c.TipoEntidad
        FROM Clientes c
        JOIN TipoIdentificacion t ON c.TipoIdentificacionId = t.Id
        WHERE {where}
        ORDER BY c.Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    clientes = [{
        "id": r[0],
        "tipo_identificacion": r[1],
        "numero_identificacion": r[2],
        "razon_social": r[3],
        "telefono": r[4],
        "direccion": r[5],
        "departamento": r[6],
        "municipio": r[7],
        "correo": r[8],
        "fecha_creacion": r[9],
        "tipo_identificacion_id": r[10],
        "tipo_entidad": r[11]
    } for r in rows]

    tipos_identificacion = query_all("SELECT Id, Descripcion FROM TipoIdentificacion WHERE Estado=1 ORDER BY Descripcion ASC")
    tipos_identificacion = [{"id": r[0], "descripcion": r[1]} for r in tipos_identificacion]

    return render_template(
        "clientes.html",
        menu=menu,
        clientes=clientes,
        tipos_identificacion=tipos_identificacion,
        tipo_entidad=tipo_entidad,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total
    )


# ---------------- INACTIVAR ----------------
@app.route("/clientes/inactivar/<int:cliente_id>", methods=["POST"])
@require_permission("CLIENTES", "editar")
def inactivar_cliente(cliente_id):
    exec_sql("UPDATE Clientes SET Estado = 0 WHERE Id = ?", (cliente_id,))
    flash("Registro inactivado correctamente.", "warning")
    return redirect(url_for("clientes_page", page=request.args.get("page", 1), search=request.args.get("search", ""), tipo_entidad=request.args.get("tipo_entidad", "CLIENTE")))


# ---------------- EDITAR ----------------
@app.route("/clientes/editar/<int:cliente_id>", methods=["POST"])
@require_permission("CLIENTES", "editar")
def editar_cliente(cliente_id):
    tipo_id = (request.form.get("tipo_identificacion_edit") or "").strip()
    numero_id = (request.form.get("numero_identificacion_edit") or "").strip()
    razon = (request.form.get("razon_social_edit") or "").strip()
    telefono = (request.form.get("telefono_edit") or "").strip()
    direccion = (request.form.get("direccion_edit") or "").strip()
    departamento = (request.form.get("departamento_edit") or "").strip()
    ciudad = (request.form.get("municipio_edit") or "").strip()
    correo = (request.form.get("correo_edit") or "").strip()
    tipo_entidad = (request.form.get("tipo_entidad_edit") or "CLIENTE").upper()

    if tipo_id and numero_id and razon:

        # Validación duplicado por tipo + identificación
        existe = query_one("""
            SELECT Id FROM Clientes
            WHERE TipoIdentificacionId = ? 
              AND NumeroIdentificacion = ? 
              AND TipoEntidad = ?
              AND Id <> ?
        """, (tipo_id, numero_id, tipo_entidad, cliente_id))

        if existe:
            flash(f"Ya existe otro {tipo_entidad.lower()} con ese tipo y número de identificación.", "danger")
        else:
            exec_sql("""
                UPDATE Clientes
                SET TipoIdentificacionId=?, NumeroIdentificacion=?, RazonSocial=?, Telefono=?, Direccion=?,
                    Departamento=?, Municipio=?, Correo=?, TipoEntidad=?
                WHERE Id=?
            """, (tipo_id, numero_id, razon, telefono, direccion, departamento, ciudad, correo, tipo_entidad, cliente_id))

            flash(f"{tipo_entidad.capitalize()} actualizado correctamente.", "success")
    else:
        flash("Los campos Tipo de identificación, Número y Razón Social son obligatorios.", "danger")

    return redirect(url_for("clientes_page", page=request.args.get("page", 1), search=request.args.get("search", ""), tipo_entidad=tipo_entidad))


# ---------------- ROUTES: PARÁMETROS DE NEGOCIO ----------------

@app.route("/parametros", methods=["GET", "POST"])
@require_permission("PARAMETROS_NEGOCIO", "ver")
def parametros_page():
    if g.user is None:
        return redirect(url_for("login_page"))

    menu = get_menu_for_role(session["rol_id"])

    # Crear nuevo parámetro
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        valor = (request.form.get("valor") or "").strip()
        categoria = (request.form.get("categoria") or "").strip()

        if not nombre or not valor or not categoria:
            flash("Todos los campos son obligatorios.", "danger")
            return redirect(url_for("parametros_page"))

        # Validación de duplicados por nombre
        existe = query_one("""
            SELECT Id FROM ParametrosNegocio
            WHERE NombreParametro = ? AND Estado = 1
        """, (nombre,))
        if existe:
            flash("Ya existe un parámetro activo con ese nombre.", "warning")
            return redirect(url_for("parametros_page"))

        # Inserción
        exec_sql("""
            INSERT INTO ParametrosNegocio (NombreParametro, ValorParametro, Categoria, FechaActualizacion, Estado)
            VALUES (?, ?, ?, GETDATE(), 1)
        """, (nombre, valor, categoria))
        flash("Parámetro creado exitosamente.", "success")
        return redirect(url_for("parametros_page"))

    # --- Paginación y búsqueda ---
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "Estado = 1"
    params = []

    if search:
        where += " AND (NombreParametro LIKE ? OR ValorParametro LIKE ? OR Categoria LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])

    # Total de registros
    total_row = query_one(f"SELECT COUNT(*) FROM ParametrosNegocio WHERE {where}", tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    # Listado paginado
    rows = query_all(
        f"""
        SELECT Id, NombreParametro, ValorParametro, Categoria, FechaActualizacion
        FROM ParametrosNegocio
        WHERE {where}
        ORDER BY FechaActualizacion DESC, Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """,
        tuple(params + [offset, page_size])
    )

    parametros = [
        {
            "id": r[0],
            "nombre": r[1],
            "valor": r[2],
            "categoria": r[3],
            "fecha": r[4].strftime("%Y-%m-%d %H:%M") if r[4] else ""
        }
        for r in rows
    ]

    return render_template(
        "parametros.html",
        menu=menu,
        parametros=parametros,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total
    )


# ---------------- ROUTES: PARÁMETROS - EDICIÓN ----------------
@app.route("/parametros/editar/<int:parametro_id>", methods=["POST"])
@require_permission("PARAMETROS_NEGOCIO", "editar")
def editar_parametro(parametro_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    nombre = (request.form.get("nombre_edit") or "").strip()
    valor = (request.form.get("valor_edit") or "").strip()
    categoria = (request.form.get("categoria_edit") or "").strip()

    if not nombre or not valor or not categoria:
        flash("Todos los campos son obligatorios.", "danger")
        return redirect(url_for("parametros_page", page=request.args.get("page", 1), search=request.args.get("search", "")))

    # Validación de duplicados por nombre
    existe = query_one("""
        SELECT Id FROM ParametrosNegocio
        WHERE NombreParametro = ? AND Estado = 1 AND Id <> ?
    """, (nombre, parametro_id))
    if existe:
        flash("Ya existe otro parámetro activo con ese nombre.", "warning")
        return redirect(url_for("parametros_page", page=request.args.get("page", 1), search=request.args.get("search", "")))

    exec_sql("""
        UPDATE ParametrosNegocio
        SET NombreParametro = ?, ValorParametro = ?, Categoria = ?, FechaActualizacion = GETDATE()
        WHERE Id = ?
    """, (nombre, valor, categoria, parametro_id))

    flash("Parámetro actualizado correctamente.", "success")
    return redirect(url_for("parametros_page", page=request.args.get("page", 1), search=request.args.get("search", "")))


# ---------------- ROUTES: PARÁMETROS - INACTIVACIÓN ----------------
@app.route("/parametros/inactivar/<int:parametro_id>", methods=["POST"])
@require_permission("PARAMETROS_NEGOCIO", "inactivar")
def inactivar_parametro(parametro_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    exec_sql("UPDATE ParametrosNegocio SET Estado = 0 WHERE Id = ?", (parametro_id,))
    flash("Parámetro inactivado correctamente.", "success")
    return redirect(url_for("parametros_page", page=request.args.get("page", 1), search=request.args.get("search", "")))



# ---------------- ROUTES: TIPO IDENTIFICACION ----------------
@app.route("/tipos_identificacion", methods=["GET", "POST"])
@require_permission("TIPO_IDENTIFICACION", "ver")
def tipos_identificacion_page():
    menu = get_menu_for_role(session["rol_id"])

    if request.method == "POST":
        codigo = request.form.get("codigo")
        descripcion = request.form.get("descripcion")
        if codigo and descripcion:
            exec_sql("""
                INSERT INTO TipoIdentificacion (Codigo, Descripcion, Estado)
                VALUES (?, ?, 1)
            """, (codigo.strip(), descripcion.strip()))

    # Paginación y búsqueda
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "Estado = 1"
    params = []

    if search:
        where += " AND (Codigo LIKE ? OR Descripcion LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    total_row = query_one(f"""
        SELECT COUNT(*) FROM TipoIdentificacion
        WHERE {where}
    """, tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    rows = query_all(f"""
        SELECT Id, Codigo, Descripcion
        FROM TipoIdentificacion
        WHERE {where}
        ORDER BY Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    tipos = [{"id": r[0], "codigo": r[1], "descripcion": r[2]} for r in rows]

    return render_template("tipos_identificacion.html", menu=menu, tipos=tipos,
                           page=page, total_pages=total_pages, search=search, total=total)

@app.route("/tipos_identificacion/inactivar/<int:tipo_id>", methods=["POST"])
@require_permission("TIPO_IDENTIFICACION", "editar")
def inactivar_tipo_identificacion(tipo_id):
    exec_sql("UPDATE TipoIdentificacion SET Estado = 0 WHERE Id = ?", (tipo_id,))
    return redirect(url_for("tipos_identificacion_page", page=request.args.get("page", 1), search=request.args.get("search", "")))

@app.route("/tipos_identificacion/editar/<int:tipo_id>", methods=["POST"])
@require_permission("TIPO_IDENTIFICACION", "editar")
def editar_tipo_identificacion(tipo_id):
    codigo = (request.form.get("codigo_edit") or "").strip()
    descripcion = (request.form.get("descripcion_edit") or "").strip()

    if codigo and descripcion:
        exec_sql(
            "UPDATE TipoIdentificacion SET Codigo = ?, Descripcion = ? WHERE Id = ?",
            (codigo, descripcion, tipo_id)
        )

    return redirect(url_for(
        "tipos_identificacion_page",
        page=request.args.get("page", 1),
        search=request.args.get("search", "")
    ))

# ---------------- ROUTES: CATEGORIAS GASTO ----------------

@app.route("/categorias_gasto", methods=["GET"])
@require_permission("CATEGORIAS_GASTO", "ver")
def categorias_gasto_page():
    menu = get_menu_for_role(session["rol_id"])

    # Paginación
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    # Búsqueda (strip)
    search = (request.args.get("search") or "").strip()
    where = "Estado = 1"
    params = []

    if search:
        where += " AND Nombre LIKE ?"
        like = f"%{search}%"
        params.append(like)

    total_row = query_one(f"""
        SELECT COUNT(*) FROM CategoriasGasto
        WHERE {where}
    """, tuple(params))
    total = int(total_row[0]) if total_row else 0

    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    rows = query_all(f"""
        SELECT Id, Nombre, EsUnicaMes
        FROM CategoriasGasto
        WHERE {where}
        ORDER BY Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    categorias = [{"id": r[0], "nombre": r[1], "es_unica": bool(r[2])} for r in rows]

    return render_template(
        "categorias_gasto.html",
        menu=menu,
        categorias=categorias,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total
    )


@app.route("/categorias_gasto/crear", methods=["POST"])
@require_permission("CATEGORIAS_GASTO", "crear")
def crear_categoria_gasto():
    nombre = (request.form.get("nombre") or "").strip()
    es_unica_str = (request.form.get("es_unica_mes") or "").strip()
    es_unica = 1 if es_unica_str == "1" else 0

    # Validación de campos
    if not nombre:
        flash("El nombre de la categoría es obligatorio.", "danger")
        return redirect(url_for("categorias_gasto_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", "")))

    # Validación de duplicados (Nombre, activo, case-insensitive)
    existe = query_one("""
        SELECT Id FROM CategoriasGasto
        WHERE Estado = 1 AND LOWER(Nombre) = LOWER(?)
    """, (nombre,))
    if existe:
        flash("Ya existe una categoría activa con ese nombre.", "danger")
        return redirect(url_for("categorias_gasto_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", "")))

    # Inserción (PRG)
    exec_sql("""
        INSERT INTO CategoriasGasto (Nombre, Estado, EsUnicaMes)
        VALUES (?, 1, ?)
    """, (nombre, es_unica))
    flash("Categoría creada correctamente.", "success")
    return redirect(url_for("categorias_gasto_page",
                            page=request.args.get("page", 1),
                            search=request.args.get("search", "")))


@app.route("/categorias_gasto/inactivar/<int:cat_id>", methods=["POST"])
@require_permission("CATEGORIAS_GASTO", "editar")
def inactivar_categoria_gasto(cat_id):
    exec_sql("UPDATE CategoriasGasto SET Estado = 0 WHERE Id = ?", (cat_id,))
    flash("Categoría inactivada correctamente.", "warning")
    return redirect(url_for("categorias_gasto_page",
                            page=request.args.get("page", 1),
                            search=request.args.get("search", "")))


@app.route("/categorias_gasto/editar/<int:cat_id>", methods=["POST"])
@require_permission("CATEGORIAS_GASTO", "editar")
def editar_categoria_gasto(cat_id):
    nombre = (request.form.get("nombre_edit") or "").strip()
    es_unica_str = (request.form.get("es_unica_mes_edit") or "").strip()
    es_unica = 1 if es_unica_str == "1" else 0

    if not nombre:
        flash("El nombre de la categoría es obligatorio.", "danger")
        return redirect(url_for("categorias_gasto_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", "")))

    # Validación de duplicados (excluye el mismo Id)
    existe = query_one("""
        SELECT Id FROM CategoriasGasto
        WHERE Estado = 1 AND LOWER(Nombre) = LOWER(?) AND Id <> ?
    """, (nombre, cat_id))
    if existe:
        flash("Ya existe otra categoría activa con ese nombre.", "danger")
        return redirect(url_for("categorias_gasto_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", "")))

    # Actualización
    exec_sql("""
        UPDATE CategoriasGasto
        SET Nombre = ?, EsUnicaMes = ?
        WHERE Id = ?
    """, (nombre, es_unica, cat_id))
    flash("Categoría actualizada correctamente.", "success")
    return redirect(url_for("categorias_gasto_page",
                            page=request.args.get("page", 1),
                            search=request.args.get("search", "")))


# ---------------- ROUTES: MODULOS ----------------
@app.route("/modulos", methods=["GET", "POST"])
@require_permission("MODULOS", "ver")
def modulos_page():
    menu = get_menu_for_role(session["rol_id"])

    if request.method == "POST":
        nombre = request.form.get("nombre")
        descripcion = request.form.get("descripcion")
        ruta = request.form.get("ruta")

        if nombre and descripcion and ruta:
            # Validar duplicados
            existe = query_one(
                "SELECT Id FROM Modulos WHERE Nombre = ? OR Ruta = ?",
                (nombre.strip(), ruta.strip())
            )
            if existe:
                flash("Ya existe un módulo con ese nombre o ruta.", "danger")
                return redirect(url_for("modulos_page")) # <-- redirigir siempre
            else:
                exec_sql("""
                    INSERT INTO Modulos (Nombre, Descripcion, Ruta, Estado)
                    VALUES (?, ?, ?, 1)
                """, (nombre.strip(), descripcion.strip(), ruta.strip()))
                flash("Módulo creado correctamente.", "success")
                return redirect(url_for("modulos_page"))

    # Paginación y búsqueda
    page_size = 10
    page = int(request.args.get("page", 1))
    offset = (page - 1) * page_size
    search = (request.args.get("search") or "").strip()

    where = "Estado = 1"
    params = []
    if search:
        where += " AND (Nombre LIKE ? OR Descripcion LIKE ? OR Ruta LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])

    total_row = query_one(f"SELECT COUNT(*) FROM Modulos WHERE {where}", tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    rows = query_all(f"""
        SELECT Id, Nombre, Descripcion, Ruta
        FROM Modulos
        WHERE {where}
        ORDER BY Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    modulos = [{"id": r[0], "nombre": r[1], "descripcion": r[2], "ruta": r[3]} for r in rows]

    return render_template("modulos.html", menu=menu, modulos=modulos,
                           page=page, total_pages=total_pages, search=search, total=total)


@app.route("/modulos/inactivar/<int:modulo_id>", methods=["POST"])
@require_permission("MODULOS", "editar")
def inactivar_modulo(modulo_id):
    exec_sql("UPDATE Modulos SET Estado = 0 WHERE Id = ?", (modulo_id,))
    flash("Módulo inactivado correctamente.", "warning")
    return redirect(url_for("modulos_page"))


@app.route("/modulos/editar/<int:modulo_id>", methods=["POST"])
@require_permission("MODULOS", "editar")
def editar_modulo(modulo_id):
    nombre = request.form.get("nombre_edit")
    descripcion = request.form.get("descripcion_edit")
    ruta = request.form.get("ruta_edit")

    if nombre and descripcion and ruta:
        # Validar duplicados excluyendo el mismo Id
        existe = query_one(
            "SELECT Id FROM Modulos WHERE (Nombre = ? OR Ruta = ?) AND Id != ?",
            (nombre.strip(), ruta.strip(), modulo_id)
        )
        if existe:
            flash("El nombre o la ruta ya están en uso por otro módulo.", "danger")
            return redirect(url_for("modulos_page"))
        else:
            exec_sql("""
                UPDATE Modulos
                SET Nombre = ?, Descripcion = ?, Ruta = ?
                WHERE Id = ?
            """, (nombre.strip(), descripcion.strip(), ruta.strip(), modulo_id))
            flash("Módulo actualizado correctamente.", "success")
            return redirect(url_for("modulos_page"))

    return redirect(url_for("modulos_page"))


# ---------------- ROUTES: GASTOS GENERALES ----------------
@app.route("/gastos_generales", methods=["GET", "POST"])
@require_permission("GASTOS", "ver")
def gastos_generales_page():
    menu = get_menu_for_role(session["rol_id"])
    hoy = datetime.today()

    # Período seleccionado
    periodo_default = hoy.strftime("%Y-%m")
    periodo_str = (request.args.get("periodo") or request.form.get("periodo") or periodo_default).strip()
    try:
        año, mes = map(int, periodo_str.split("-"))
    except Exception:
        año, mes = hoy.year, hoy.month
        periodo_str = f"{año:04d}-{mes:02d}"

    # Límites del mes
    inicio_mes = datetime(año, mes, 1)
    fin_mes = datetime(año, 12, 31) if mes == 12 else datetime(año, mes + 1, 1) - timedelta(days=1)
    inicio_mes_str = inicio_mes.strftime("%Y-%m-%d")
    fin_mes_str = fin_mes.strftime("%Y-%m-%d")

    # Parámetro: permitir meses anteriores
    row_param_ant = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'PERMITIR_FECHAS_ANTERIORES_GASTOS' AND Estado = 1
    """)
    permitir_anteriores_gastos = int(row_param_ant[0]) if row_param_ant else 0

    # ---------------- CREAR GASTO ----------------
    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        tipo_registro_id = request.form.get("tipo_registro_id")
        descripcion = (request.form.get("descripcion") or "").strip()
        categoria_id = request.form.get("categoria_id")
        valor = (request.form.get("valor") or "").strip()
        usuario_id = session.get("user_id")

        if not fecha or not tipo_registro_id or not descripcion or not categoria_id or not valor:
            flash("Todos los campos son obligatorios.", "danger")
            return redirect(url_for("gastos_generales_page", periodo=periodo_str))

        # Validación fecha
        try:
            fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
        except:
            flash("Formato de fecha inválido.", "danger")
            return redirect(url_for("gastos_generales_page", periodo=periodo_str))

        if fecha_dt > fin_mes:
            flash("No se permite registrar gastos en meses futuros.", "danger")
            return redirect(url_for("gastos_generales_page", periodo=periodo_str))

        if permitir_anteriores_gastos == 0 and fecha_dt < inicio_mes:
            flash("No se permite registrar gastos en meses anteriores.", "danger")
            return redirect(url_for("gastos_generales_page", periodo=periodo_str))

        # Validación categoría única por mes
        row_cat = query_one("SELECT EsUnicaMes FROM CategoriasGasto WHERE Id = ?", (categoria_id,))
        es_unica = bool(row_cat[0]) if row_cat else False

        if es_unica:
            existe = query_one("""
                SELECT Id FROM GastosGenerales
                WHERE Estado = 1 AND CategoriaId = ? AND MONTH(Fecha) = ? AND YEAR(Fecha) = ?
            """, (categoria_id, mes, año))
            if existe:
                flash("Ya existe un gasto registrado en esta categoría para este mes.", "danger")
                return redirect(url_for("gastos_generales_page", periodo=periodo_str))

        # INSERT
        exec_sql("""
            INSERT INTO GastosGenerales (Fecha, TipoRegistroId, Descripcion, CategoriaId, Valor, UsuarioId, FechaRegistro, Estado)
            VALUES (?, ?, ?, ?, ?, ?, GETDATE(), 1)
        """, (fecha, tipo_registro_id, descripcion, categoria_id, valor, usuario_id))

        flash("Gasto registrado exitosamente.", "success")
        return redirect(url_for("gastos_generales_page", periodo=periodo_str))

    # ---------------- PERÍODOS DISPONIBLES ----------------
    row_param = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'MESES_GASTOS_VISIBLES' AND Estado = 1
    """)
    meses_rango = int(row_param[0]) if row_param and str(row_param[0]).isdigit() else 12

    base = datetime(hoy.year, hoy.month, 1)
    periodos_disponibles = [{
        "valor": (base - timedelta(days=30 * i)).strftime("%Y-%m"),
        "nombre": (base - timedelta(days=30 * i)).strftime("%B %Y")
    } for i in range(meses_rango)]

    # ---------------- LISTADO + BÚSQUEDA + PAGINACIÓN ----------------
    page_size = 10
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "g.Estado = 1 AND MONTH(g.Fecha) = ? AND YEAR(g.Fecha) = ?"
    params = [mes, año]

    if search:
        where += " AND (g.Descripcion LIKE ? OR c.Nombre LIKE ? OR tr.NombreTipo LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])

    total_row = query_one(f"""
        SELECT COUNT(*)
        FROM GastosGenerales g
        JOIN CategoriasGasto c ON g.CategoriaId = c.Id
        JOIN TiposRegistro tr ON g.TipoRegistroId = tr.Id
        WHERE {where}
    """, tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    rows = query_all(f"""
        SELECT g.Id, g.Fecha, tr.NombreTipo, g.Descripcion, c.Nombre, g.Valor, g.FechaRegistro,
               g.CategoriaId, g.TipoRegistroId
        FROM GastosGenerales g
        JOIN CategoriasGasto c ON g.CategoriaId = c.Id
        JOIN TiposRegistro tr ON g.TipoRegistroId = tr.Id
        WHERE {where}
        ORDER BY g.Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    gastos = [{
        "id": r[0],
        "fecha": r[1].strftime("%Y-%m-%d"),
        "tipo_registro": r[2],
        "descripcion": r[3],
        "categoria": r[4],
        "valor": float(r[5]),
        "fecha_registro": r[6].strftime("%Y-%m-%d %H:%M"),
        "categoria_id": r[7],
        "tipo_registro_id": r[8]
    } for r in rows]

    categorias = [{
        "id": r[0],
        "nombre": r[1],
        "es_unica": bool(r[2])
    } for r in query_all("SELECT Id, Nombre, EsUnicaMes FROM CategoriasGasto WHERE Estado = 1 ORDER BY Nombre ASC")]

    tipos_registro = query_all("""
        SELECT Id, NombreTipo
        FROM TiposRegistro
        WHERE Estado = 1
        ORDER BY NombreTipo ASC
    """)

    # Total del mes
    row_total_mes = query_one("""
        SELECT ISNULL(SUM(Valor),0)
        FROM GastosGenerales
        WHERE Estado = 1 AND MONTH(Fecha) = ? AND YEAR(Fecha) = ?
    """, (mes, año))
    total_mes = float(row_total_mes[0]) if row_total_mes else 0.0

    return render_template(
        "gastos_generales.html",
        menu=menu,
        gastos=gastos,
        categorias=categorias,
        tipos_registro=tipos_registro,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total,
        periodo_seleccionado=periodo_str,
        total_mes=total_mes,
        periodos_disponibles=periodos_disponibles,
        inicio_mes_str=inicio_mes_str,
        fin_mes_str=fin_mes_str,
        permitir_anteriores=permitir_anteriores_gastos
    )


# ---------------- INACTIVAR ----------------
@app.route("/gastos_generales/inactivar/<int:gasto_id>", methods=["POST"])
@require_permission("GASTOS", "editar")
def inactivar_gasto_general(gasto_id):
    exec_sql("UPDATE GastosGenerales SET Estado = 0 WHERE Id = ?", (gasto_id,))
    flash("Gasto inactivado.", "warning")
    return redirect(url_for(
        "gastos_generales_page",
        page=request.args.get("page", 1),
        search=request.args.get("search", ""),
        periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))
    ))


# ---------------- EDITAR ----------------
@app.route("/gastos_generales/editar/<int:gasto_id>", methods=["POST"])
@require_permission("GASTOS", "editar")
def editar_gasto_general(gasto_id):
    periodo_default = datetime.today().strftime("%Y-%m")
    periodo_str = (request.args.get("periodo") or periodo_default).strip()
    año, mes = map(int, periodo_str.split("-"))

    inicio_mes = datetime(año, mes, 1)
    fin_mes = datetime(año, 12, 31) if mes == 12 else datetime(año, mes + 1, 1) - timedelta(days=1)

    fecha = request.form.get("fecha_edit")
    tipo_registro_id = request.form.get("tipo_registro_id_edit")
    descripcion = request.form.get("descripcion_edit")
    categoria_id = request.form.get("categoria_id_edit")
    valor = request.form.get("valor_edit")

    if not fecha or not tipo_registro_id or not descripcion or not categoria_id or not valor:
        flash("Todos los campos son obligatorios.", "danger")
        return redirect(url_for("gastos_generales_page", periodo=periodo_str))

    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
    if not (inicio_mes <= fecha_dt <= fin_mes):
        flash("La fecha debe estar dentro del período seleccionado.", "danger")
        return redirect(url_for("gastos_generales_page", periodo=periodo_str))

    # Validación categoría única
    row_cat = query_one("SELECT EsUnicaMes FROM CategoriasGasto WHERE Id = ?", (categoria_id,))
    es_unica = bool(row_cat[0]) if row_cat else False

    if es_unica:
        existe = query_one("""
            SELECT Id FROM GastosGenerales
            WHERE Estado = 1 AND CategoriaId = ? AND MONTH(Fecha) = ? AND YEAR(Fecha) = ? AND Id <> ?
        """, (categoria_id, mes, año, gasto_id))
        if existe:
            flash("Ya existe otro gasto en esta categoría para este mes.", "danger")
            return redirect(url_for("gastos_generales_page", periodo=periodo_str))

    exec_sql("""
        UPDATE GastosGenerales
        SET Fecha = ?, TipoRegistroId = ?, Descripcion = ?, CategoriaId = ?, Valor = ?, FechaRegistro = GETDATE()
        WHERE Id = ?
    """, (fecha, tipo_registro_id, descripcion, categoria_id, valor, gasto_id))

    flash("Gasto actualizado correctamente.", "success")
    return redirect(url_for("gastos_generales_page", periodo=periodo_str))


# ---------------- ROUTES: PROVEEDORES ----------------
@app.route("/proveedores", methods=["GET", "POST"])
@require_permission("PROVEEDORES", "ver")
def proveedores_page():
    menu = get_menu_for_role(session["rol_id"])
    hoy = datetime.today()

    # Límites del mes actual
    inicio_mes = hoy.replace(day=1)
    fin_mes = (inicio_mes + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    inicio_mes_str = inicio_mes.strftime("%Y-%m-%d")
    fin_mes_str = fin_mes.strftime("%Y-%m-%d")

    # Parámetro para permitir meses anteriores
    row_param = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'PERMITIR_FECHAS_ANTERIORES_PROVEEDORES' AND Estado = 1
    """)
    permitir_anteriores_prov = int(row_param[0]) if row_param else 0

    periodo_str = request.args.get("periodo", hoy.strftime("%Y-%m"))
    año, mes = map(int, periodo_str.split("-"))
    search = request.args.get("search", "").strip()
    pagina_actual = int(request.args.get("page", 1))
    registros_por_pagina = 10

    # ---------------- REGISTRAR NUEVA FACTURA ----------------
    if request.method == "POST":
        proveedor_id = request.form.get("proveedor_id")
        numero_factura = request.form.get("numero_factura")
        fecha_factura = request.form.get("fecha_factura")
        fecha_vencimiento = request.form.get("fecha_vencimiento")
        estado_factura = request.form.get("estado_factura", "Pendiente")
        total = request.form.get("total")
        descripcion = request.form.get("descripcion")
        usuario_id = session.get("user_id")

        # Validaciones básicas
        if not proveedor_id or not numero_factura or not fecha_factura or not total:
            flash("Todos los campos son obligatorios.", "warning")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        # Validar fecha factura
        try:
            fecha_dt = datetime.strptime(fecha_factura, "%Y-%m-%d")
        except:
            flash("Formato de fecha inválido.", "danger")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        # Validar fecha vencimiento
        try:
            venc_dt = datetime.strptime(fecha_vencimiento, "%Y-%m-%d")
        except:
            flash("Formato de fecha de vencimiento inválido.", "danger")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        if venc_dt <= fecha_dt:
            flash("La fecha de vencimiento debe ser posterior a la fecha de emisión.", "danger")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        # Validar total
        total = float(total.replace(",", ".")) if total else 0.0
        if total <= 0:
            flash("El total debe ser mayor a cero.", "warning")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        # Validar duplicado
        existe = query_one("""
            SELECT Id FROM FacturasProveedores
            WHERE ProveedorId = ? AND NumeroFactura = ? AND Estado = 1
        """, (proveedor_id, numero_factura))
        if existe:
            flash("Ya existe una factura activa con ese número para este proveedor.", "danger")
            return redirect(url_for("proveedores_page", periodo=periodo_str))

        # Validar estado inicial (solo Pendiente o Pagada)
        if estado_factura not in ["Pendiente", "Pagada"]:
            estado_factura = "Pendiente"

        # Insertar factura
        exec_sql("""
            INSERT INTO FacturasProveedores (
                ProveedorId, NumeroFactura, FechaFactura, FechaVencimiento,
                Total, Descripcion, EstadoFactura,
                UsuarioId, FechaRegistro, Estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), 1)
        """, (
            proveedor_id, numero_factura, fecha_factura, fecha_vencimiento,
            total, descripcion, estado_factura, usuario_id
        ))

        flash("Factura de proveedor registrada exitosamente.", "success")
        return redirect(url_for("proveedores_page", periodo=periodo_str))

    # ---------------- LISTADO Y FILTROS ----------------
    facturas = query_all(f"""
        SELECT FP.Id,
               FP.ProveedorId,
               C.RazonSocial AS Proveedor,
               FP.NumeroFactura,
               CONVERT(varchar(10), FP.FechaFactura, 120) AS FechaFactura,
               CONVERT(varchar(10), FP.FechaVencimiento, 120) AS FechaVencimiento,
               FP.Total,
               FP.Descripcion,
               FP.EstadoFactura,
               FP.FechaRegistro
        FROM FacturasProveedores FP
        JOIN Clientes C ON FP.ProveedorId = C.Id
        WHERE C.TipoEntidad = 'PROVEEDOR'
          AND FP.Estado = 1
          AND MONTH(FP.FechaFactura) = ? AND YEAR(FP.FechaFactura) = ?
        ORDER BY FP.FechaRegistro DESC
    """, [mes, año])

    total_registros = len(facturas)
    total_paginas = math.ceil(total_registros / registros_por_pagina)
    inicio = (pagina_actual - 1) * registros_por_pagina
    facturas_pagina = facturas[inicio:inicio + registros_por_pagina]

    # ---------------- TOTAL FACTURADO DEL MES ----------------
    row_suma = query_one("""
        SELECT ISNULL(SUM(Total), 0)
        FROM FacturasProveedores
        WHERE Estado = 1
          AND EstadoFactura <> 'Anulada'
          AND MONTH(FechaFactura) = ? AND YEAR(FechaFactura) = ?
    """, (mes, año))
    suma_mes = float(row_suma[0]) if row_suma else 0.0

    # ---------------- SELECTOR DE PERÍODOS ----------------
    row_visible_meses = query_one("""
        SELECT ValorParametro FROM ParametrosNegocio
        WHERE NombreParametro = 'REGISTRO_MESES_PROVEEDORES_VISIBLES' AND Estado = 1
    """)
    visible_meses = int(row_visible_meses[0]) if row_visible_meses and row_visible_meses[0] else 12

    base = datetime(hoy.year, hoy.month, 1)
    meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    periodos_disponibles = []
    for i in range(visible_meses):
        dt = base - timedelta(days=30 * i)
        valor = f"{dt.year:04d}-{dt.month:02d}"
        nombre = f"{meses_es[dt.month - 1]} {dt.year}"
        periodos_disponibles.append({"valor": valor, "nombre": nombre})

    # ---------------- LISTA DE PROVEEDORES ----------------
    proveedores = query_all("""
        SELECT Id, RazonSocial, NumeroIdentificacion
        FROM Clientes
        WHERE Estado = 1 AND TipoEntidad = 'PROVEEDOR'
        ORDER BY RazonSocial ASC
    """)

    return render_template("proveedores.html",
        menu=menu,
        periodo_seleccionado=periodo_str,
        facturas=facturas_pagina,
        proveedores=proveedores,
        total_registros=total_registros,
        pagina_actual=pagina_actual,
        total_paginas=total_paginas,
        search=search,
        periodos_disponibles=periodos_disponibles,
        inicio_mes_str=inicio_mes_str,
        fin_mes_str=fin_mes_str,
        permitir_anteriores=permitir_anteriores_prov,
        suma_mes=suma_mes
    )


# ---------------- INACTIVAR ----------------
@app.route("/proveedores/inactivar/<int:factura_id>", methods=["POST"])
@require_permission("PROVEEDORES", "editar")
def inactivar_factura(factura_id):
    exec_sql("UPDATE FacturasProveedores SET Estado = 0 WHERE Id = ?", (factura_id,))
    flash("Factura inactivada correctamente.", "warning")
    return redirect(url_for("proveedores_page",
                            page=request.args.get("page", 1),
                            search=request.args.get("search", "")))


# ---------------- EDITAR ----------------
@app.route("/proveedores/editar/<int:factura_id>", methods=["POST"])
@require_permission("PROVEEDORES", "editar")
def editar_factura(factura_id):
    proveedor_id = request.form.get("proveedor_id_edit")
    numero_factura = request.form.get("numero_factura_edit")
    fecha_factura = request.form.get("fecha_factura_edit")
    fecha_vencimiento = request.form.get("fecha_vencimiento_edit")
    estado_factura = request.form.get("estado_factura_edit")
    total = request.form.get("total_edit")
    descripcion = request.form.get("descripcion_edit")

    # Validar campos obligatorios
    if not proveedor_id or not numero_factura or not fecha_factura or not fecha_vencimiento:
        flash("Proveedor, número de factura, fecha de emisión y vencimiento son obligatorios.", "danger")
        return redirect(url_for("proveedores_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", ""),
                                periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))))

    # Validar fechas
    try:
        fecha_dt = datetime.strptime(fecha_factura, "%Y-%m-%d")
        venc_dt = datetime.strptime(fecha_vencimiento, "%Y-%m-%d")
    except:
        flash("Formato de fecha inválido.", "danger")
        return redirect(url_for("proveedores_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", ""),
                                periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))))

    if venc_dt <= fecha_dt:
        flash("La fecha de vencimiento debe ser posterior a la fecha de emisión.", "danger")
        return redirect(url_for("proveedores_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", ""),
                                periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))))

    # Normalizar total
    total = float((total or "0").replace(",", "."))

    # Validar duplicado de número de factura
    existe = query_one("""
        SELECT Id FROM FacturasProveedores
        WHERE NumeroFactura = ? AND Estado = 1 AND Id <> ?
    """, (numero_factura, factura_id))
    if existe:
        flash("Ya existe otra factura activa con ese número.", "danger")
        return redirect(url_for("proveedores_page",
                                page=request.args.get("page", 1),
                                search=request.args.get("search", ""),
                                periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))))

    # Validar estado (en edición sí se permite Anulada)
    if estado_factura not in ["Pendiente", "Pagada", "Anulada"]:
        estado_factura = "Pendiente"

    # Actualizar factura
    exec_sql("""
        UPDATE FacturasProveedores
        SET ProveedorId = ?, NumeroFactura = ?, FechaFactura = ?, FechaVencimiento = ?,
            Total = ?, Descripcion = ?, EstadoFactura = ?
        WHERE Id = ?
    """, (
        proveedor_id,
        numero_factura,
        fecha_dt.strftime("%Y-%m-%d"),
        venc_dt.strftime("%Y-%m-%d"),
        total,
        descripcion,
        estado_factura,
        factura_id
    ))

    flash("Factura actualizada correctamente.", "success")
    return redirect(url_for("proveedores_page",
                            page=request.args.get("page", 1),
                            search=request.args.get("search", ""),
                            periodo=request.args.get("periodo", datetime.today().strftime("%Y-%m"))))




# ---------------- ROUTES: Consecutivos ----------------

@app.route("/consecutivos", methods=["GET", "POST"])
@require_permission("CONSECUTIVOS", "ver")
def consecutivos_page():
    menu = get_menu_for_role(session["rol_id"])

    if request.method == "POST":
        tipo = (request.form.get("tipo") or "").strip()
        prefijo = (request.form.get("prefijo") or "").strip()
        valor_actual = (request.form.get("valor_actual") or "").strip()
        numero_inicial = request.form.get("numero_inicial")
        numero_final = request.form.get("numero_final")

        if not tipo or not prefijo or not valor_actual:
            flash("Todos los campos son obligatorios.", "danger")
            return redirect(url_for("consecutivos_page"))

        # Validación duplicados
        existe = query_one("""
            SELECT Id FROM Consecutivos
            WHERE Tipo = ? AND Prefijo = ? AND Estado = 1
        """, (tipo, prefijo))
        if existe:
            flash("Ya existe un consecutivo activo con ese tipo y prefijo.", "warning")
            return redirect(url_for("consecutivos_page"))

        # Formato especial para CUENTA_COBRO
        if tipo.upper() == "CUENTA_COBRO":
            valor_actual_fmt = str(valor_actual).zfill(4)
        else:
            valor_actual_fmt = int(valor_actual)

        exec_sql("""
            INSERT INTO Consecutivos (Tipo, Prefijo, ValorActual, NumeroInicial, NumeroFinal, FechaActualizacion, Estado)
            VALUES (?, ?, ?, ?, ?, GETDATE(), 1)
        """, (
            tipo, prefijo,
            valor_actual_fmt,
            int(numero_inicial) if numero_inicial else None,
            int(numero_final) if numero_final else None
        ))
        flash("Consecutivo registrado exitosamente.", "success")
        return redirect(url_for("consecutivos_page"))

    # --- Búsqueda y paginación ---
    search = (request.args.get("search") or "").strip()
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1: page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    where = "Estado = 1"
    params = []
    if search:
        where += " AND (Tipo LIKE ? OR Prefijo LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    total_row = query_one(f"SELECT COUNT(*) FROM Consecutivos WHERE {where}", tuple(params))
    total = int(total_row[0]) if total_row else 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    rows = query_all(f"""
        SELECT Id, Tipo, Prefijo, ValorActual, NumeroInicial, NumeroFinal, FechaActualizacion
        FROM Consecutivos
        WHERE {where}
        ORDER BY Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    consecutivos = [{
        "id": r[0],
        "tipo": r[1],
        "prefijo": r[2],
        "valor_actual": r[3],
        "numero_inicial": r[4],
        "numero_final": r[5],
        "fecha_actualizacion": r[6].strftime("%Y-%m-%d %H:%M") if r[6] else ""
    } for r in rows]

    return render_template("consecutivos.html",
        menu=menu,
        consecutivos=consecutivos,
        search=search,
        page=page,
        total_pages=total_pages,
        total=total
    )


@app.route("/consecutivos/inactivar/<int:consecutivo_id>", methods=["POST"])
@require_permission("CONSECUTIVOS", "editar")
def inactivar_consecutivo(consecutivo_id):
    exec_sql("UPDATE Consecutivos SET Estado = 0 WHERE Id = ?", (consecutivo_id,))
    flash("Consecutivo inactivado.", "info")
    return redirect(url_for("consecutivos_page"))


@app.route("/consecutivos/editar/<int:consecutivo_id>", methods=["POST"])
@require_permission("CONSECUTIVOS", "editar")
def editar_consecutivo(consecutivo_id):
    tipo = (request.form.get("tipo_edit") or "").strip()
    prefijo = (request.form.get("prefijo_edit") or "").strip()
    valor_actual = (request.form.get("valor_actual_edit") or "").strip()
    numero_inicial = request.form.get("numero_inicial_edit")
    numero_final = request.form.get("numero_final_edit")

    if not tipo or not prefijo or not valor_actual:
        flash("Todos los campos son obligatorios.", "danger")
        return redirect(url_for("consecutivos_page"))

    # Validación duplicados
    existe = query_one("""
        SELECT Id FROM Consecutivos
        WHERE Tipo = ? AND Prefijo = ? AND Estado = 1 AND Id <> ?
    """, (tipo, prefijo, consecutivo_id))
    if existe:
        flash("Ya existe otro consecutivo activo con ese tipo y prefijo.", "warning")
        return redirect(url_for("consecutivos_page"))

    # Formato especial para CUENTA_COBRO
    if tipo.upper() == "CUENTA_COBRO":
        valor_actual_fmt = str(valor_actual).zfill(4)
    else:
        valor_actual_fmt = int(valor_actual)

    exec_sql("""
        UPDATE Consecutivos
        SET Tipo=?, Prefijo=?, ValorActual=?, NumeroInicial=?, NumeroFinal=?, FechaActualizacion=GETDATE()
        WHERE Id=?
    """, (
        tipo, prefijo,
        valor_actual_fmt,
        int(numero_inicial) if numero_inicial else None,
        int(numero_final) if numero_final else None,
        consecutivo_id
    ))
    flash("Consecutivo actualizado.", "success")
    return redirect(url_for("consecutivos_page"))


# ---------------- ROUTES: Notas Creditos ----------------

@app.route("/ventas/notas_credito")
def notas_credito():
    return render_template("notas_credito.html")

# ---------------- RUTA: CAJA REGISTRADORA ----------------

@app.route("/ventas/caja_registradora", methods=["GET"])
def caja_registradora():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Tipos de documento activos (usa 'Tipo' en lugar de 'TipoDocumento')
    tipos_documento = query_all("""
        SELECT Id, Tipo, Prefijo, ValorActual
        FROM dbo.Consecutivos
        WHERE Estado = 1
        ORDER BY Tipo
    """)

    #  Categorías con productos activos
    categorias = query_all("""
        SELECT c.Id,
               c.NombreCategoria,
               c.ColorVisual,
               c.Icono,
               COUNT(i.Id) AS total_productos
        FROM dbo.CategoriasInventario AS c
        LEFT JOIN dbo.Inventarios AS i
            ON i.CategoriaInventarioId = c.Id
            AND ISNULL(i.Estado,0) = 1
        GROUP BY c.Id, c.NombreCategoria, c.ColorVisual, c.Icono
        ORDER BY c.NombreCategoria
    """)

    # Medios de pago disponibles
    medios_pago = ["EFECTIVO", "DEBITO", "CREDITO", "TRANSFERENCIA", "OTRO"]

    return render_template(
        "caja_registradora.html",
        tipos_documento=tipos_documento,
        categorias=categorias,
        medios_pago=medios_pago,
        iva_default=19
    )


# ---------------- RUTA: PRODUCTOS POR CATEGORÍA ----------------

@app.route("/ventas/caja_registradora/productos/<int:categoria_id>")
def productos_por_categoria(categoria_id):
    productos = query_all("""
        SELECT I.Id, I.NombreProducto, I.Costo AS Precio,
               ISNULL(SUM(D.Cantidad), 0) AS Cantidad
        FROM Inventarios I
        LEFT JOIN DetalleInventario D ON I.Id = D.InventarioId AND D.Estado = 1
        WHERE I.CategoriaInventarioId = ? AND I.Estado = 1
        GROUP BY I.Id, I.NombreProducto, I.Costo
        ORDER BY I.NombreProducto
    """, (categoria_id,))

    data = [
        {
            "id": p[0],
            "nombre": p[1],
            "precio": p[2],
            "cantidad": p[3]
        }
        for p in productos
    ]
    return jsonify(data)

@app.route("/api/producto/<codigo_barras>")
def buscar_producto_por_codigo(codigo_barras):
    producto = query_one("""
        SELECT i.Id, i.NombreProducto, i.Costo,
               ISNULL(SUM(d.Cantidad), 0) AS CantidadDisponible
        FROM dbo.Inventarios AS i
        LEFT JOIN dbo.DetalleInventario AS d ON d.InventarioId = i.Id AND d.Estado = 1
        WHERE i.CodigoBarras = ? AND i.Estado = 1
        GROUP BY i.Id, i.NombreProducto, i.Costo
    """, (codigo_barras,))
    
    if not producto:
        return jsonify({"error": "Producto no encontrado"}), 404

    return jsonify({
        "Id": producto[0],
        "NombreProducto": producto[1],
        "Costo": producto[2],
        "CantidadDisponible": producto[3]
    })

# ---------------- RUTA: REGISTRAR VENTA ----------------
@app.route("/ventas/caja_registradora/registrar", methods=["POST"])
def registrar_venta_caja():
    if "user_id" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada"}), 401

    data = request.get_json()
    tipo_documento_id = data.get("tipo_documento_id")
    medio_pago = (data.get("medio_pago") or "").strip()
    cliente_identificacion = (data.get("cliente_identificacion") or "").strip()
    iva_porcentaje = Decimal(str(data.get("iva_porcentaje", 0)))
    valor_recibido = Decimal(str(data.get("valor_recibido", 0)))
    items = data.get("items", [])

    # ---------------- VALIDACIONES ----------------
    if not items:
        return jsonify({"ok": False, "msg": "No hay productos en la venta"}), 400
    if not medio_pago:
        return jsonify({"ok": False, "msg": "Debe seleccionar un medio de pago"}), 400
    if not cliente_identificacion:
        return jsonify({"ok": False, "msg": "Debe ingresar la identificación del cliente"}), 400
    if valor_recibido <= 0:
        return jsonify({"ok": False, "msg": "Debe ingresar el valor recibido"}), 400

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # ---------------- 1️⃣ Consecutivo ----------------
        cursor.execute("""
            SELECT Id, Tipo, Prefijo, ValorActual
            FROM dbo.Consecutivos
            WHERE Id = ? AND Estado = 1
        """, (tipo_documento_id,))
        consec = cursor.fetchone()
        if not consec:
            return jsonify({"ok": False, "msg": "Consecutivo no encontrado o inactivo"}), 400

        consec_id = consec[0]
        tipo_doc = consec[1].upper()
        prefijo = consec[2]
        numero_actual = consec[3] + 1

        es_documento_equivalente = (tipo_doc == "DOCUMENTO_EQUIVALENTE")
        es_factura_electronica = (tipo_doc == "FACTURA_ELECTRONICA")

        # ---------------- 2️⃣ Cliente ----------------
        cursor.execute("""
            SELECT Id FROM dbo.Clientes
            WHERE NumeroIdentificacion = ?
        """, (cliente_identificacion,))
        row = cursor.fetchone()
        if not row:
            return jsonify({
                "ok": False,
                "msg": "Cliente no encontrado. Regístrelo en el módulo de clientes antes de continuar."
            }), 400
        cliente_id = row[0]

        # ---------------- 3️⃣ Validar stock ----------------
        for it in items:
            inventario_id = it["inventario_id"]
            cantidad_solicitada = Decimal(str(it["cantidad"]))

            cursor.execute("""
                SELECT ISNULL(SUM(Cantidad), 0)
                FROM dbo.DetalleInventario
                WHERE InventarioId = ? AND ISNULL(Estado,0) = 1
            """, (inventario_id,))
            total_disponible = cursor.fetchone()[0]

            if total_disponible < cantidad_solicitada:
                return jsonify({
                    "ok": False,
                    "msg": f"Stock insuficiente para '{it['nombre']}'. Disponible: {total_disponible}, solicitado: {cantidad_solicitada}"
                }), 400

        # ---------------- 4️⃣ Totales ----------------
        subtotal = sum(Decimal(str(it["cantidad"])) * Decimal(str(it["precio"])) for it in items)
        impuestos = (subtotal * iva_porcentaje / Decimal("100")).quantize(Decimal("1"))
        total = subtotal + impuestos

        if valor_recibido < total:
            return jsonify({
                "ok": False,
                "msg": "El valor recibido no puede ser menor al total a pagar."
            }), 400

        # ---------------- 5️⃣ Validación Documento Equivalente ----------------
        if es_documento_equivalente and total > Decimal("200000"):
            return jsonify({
                "ok": False,
                "msg": "El Documento Equivalente solo aplica para ventas hasta $200.000 COP."
            }), 400

        # ---------------- 6️⃣ Simulación DIAN (solo FE) ----------------
        if es_factura_electronica:
            hoy = datetime.now()

            venta_temp = {
                "Subtotal": subtotal,
                "Impuestos": impuestos,
                "TotalVenta": total,
                "NumeroDocumento": numero_actual,
                "IdentificacionEmpresa": "SIMULADO",
                "FechaEmision": hoy.date()
            }

            # CUFE simulado desde utils.py
            cufe = generar_cufe_simulado(venta_temp, items)

            # QR simulado desde utils.py
            qr_data = f"https://dian.gov.co/qr?cufe={cufe}"
            qr_base64 = generar_qr_base64(qr_data)

            ambiente = "PRUEBAS"
            numero_resolucion = "SIM-2024-0001"
            fecha_resolucion = hoy.date()

        else:
            cufe = None
            qr_base64 = None
            ambiente = None
            numero_resolucion = None
            fecha_resolucion = None

        # ---------------- 7️⃣ Insertar venta ----------------
        hoy = datetime.now()
        cursor.execute("""
            INSERT INTO dbo.Ventas
                (NumeroDocumento, Prefijo, FechaEmision, HoraEmision,
                 Subtotal, Impuestos, TotalVenta, MedioPago,
                 UsuarioCreaId, FechaCreacion, Estado,
                 ConsecutivoId, VentaCerrada, TotalPagar,
                 CUFE, QR, AmbienteDIAN, NumeroResolucion, FechaResolucion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            numero_actual, prefijo, hoy.date(), hoy.time(),
            subtotal, impuestos, total, medio_pago,
            session["user_id"], hoy,
            1, consec_id, 1, valor_recibido,
            cufe, qr_base64, ambiente, numero_resolucion, fecha_resolucion
        ))

        cursor.execute("SELECT @@IDENTITY")
        venta_id = cursor.fetchone()[0]

        # ---------------- 8️⃣ Insertar detalle + FIFO ----------------
        for idx, it in enumerate(items, start=1):
            cantidad = Decimal(str(it["cantidad"]))
            precio = Decimal(str(it["precio"]))
            total_linea = cantidad * precio
            inventario_id = it["inventario_id"]

            cursor.execute("""
                INSERT INTO dbo.DetalleVenta
                    (VentaId, NumeroLinea, Descripcion,
                     Cantidad, ValorUnitario, ValorTotalUnitario,
                     Estado, InventarioId, ClienteId)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                venta_id, idx, it["nombre"],
                cantidad, precio, total_linea,
                1, inventario_id, cliente_id
            ))

            # FIFO
            cursor.execute("""
                SELECT Id, Cantidad
                FROM dbo.DetalleInventario
                WHERE InventarioId = ? AND ISNULL(Estado,0) = 1
                ORDER BY
                    CASE WHEN FechaVencimiento IS NULL THEN 1 ELSE 0 END,
                    FechaVencimiento ASC
            """, (inventario_id,))
            lotes = cursor.fetchall()

            cantidad_restante = cantidad
            for lote_id, disponible in lotes:
                if cantidad_restante <= 0:
                    break

                disponible = Decimal(str(disponible))
                if disponible >= cantidad_restante:
                    cursor.execute("""
                        UPDATE dbo.DetalleInventario
                        SET Cantidad = Cantidad - ?
                        WHERE Id = ?
                    """, (cantidad_restante, lote_id))
                    cantidad_restante = Decimal("0")
                else:
                    cursor.execute("""
                        UPDATE dbo.DetalleInventario
                        SET Cantidad = 0
                        WHERE Id = ?
                    """, (lote_id,))
                    cantidad_restante -= disponible

        # ---------------- 9️⃣ Actualizar consecutivo ----------------
        cursor.execute("""
            UPDATE dbo.Consecutivos
            SET ValorActual = ?
            WHERE Id = ?
        """, (numero_actual, consec_id))

        conn.commit()

        return jsonify({
            "ok": True,
            "venta_id": venta_id,
            "prefijo": prefijo,
            "numero": numero_actual,
            "tipo_documento": tipo_doc,
            "total": float(total),
            "valor_recibido": float(valor_recibido),
            "cambio": float(valor_recibido - total),
            "iva": float(impuestos),
            "cufe": cufe,
            "qr": qr_base64
        })

    except Exception as e:
        conn.rollback()
        error_msg = str(e)

        # Detectar error de consecutivo duplicado
        if "UQ_Ventas" in error_msg or "duplicate key" in error_msg:
            return jsonify({
                "ok": False,
                "msg": "El consecutivo ya fue utilizado. Actualice el consecutivo en la tabla Consecutivos."
            }), 400

        # Otros errores
        return jsonify({"ok": False, "msg": error_msg}), 500

    finally:
        conn.close()




# ---------------------- HISTORIAL DE VENTAS ----------------------
@app.route("/ventas/historial")
def historial_ventas():
    if "user_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    page = int(request.args.get("page", 1))
    per_page = 10
    offset = (page - 1) * per_page

    # Consulta base con JOIN para obtener el tipo de documento
    base_query = """
        FROM dbo.Ventas v
        JOIN dbo.Consecutivos c ON c.Id = v.ConsecutivoId
        WHERE ISNULL(v.Estado,0) = 1
    """

    params = []
    if search:
        base_query += " AND (CAST(v.NumeroDocumento AS VARCHAR) LIKE ? OR v.Prefijo LIKE ?)"
        params = [f"%{search}%", f"%{search}%"]

    # Total de registros
    total_registros = query_one(f"SELECT COUNT(*) {base_query}", params)[0]
    total_pages = (total_registros + per_page - 1) // per_page

    # Consulta paginada
    ventas = query_all(f"""
        SELECT 
            v.Id,
            v.FechaEmision,
            v.Prefijo,
            v.NumeroDocumento,
            v.TotalVenta,
            v.MedioPago,
            v.Estado,
            c.Tipo AS TipoDocumento
        {base_query}
        ORDER BY v.FechaEmision DESC, v.Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, params + [offset, per_page])

    return render_template(
        "historial_ventas.html",
        ventas=ventas,
        page=page,
        total_pages=total_pages,
        total_registros=total_registros,
        search=search
    )



# ---------------- ROUTE: GENERAR PDF DOCUMENTO EQUIVALENTE POS ----------------

@app.route("/documento_equivalente/pdf/<int:venta_id>")
def generar_pdf_documento_equivalente(venta_id):

    # ============================
    # 1. CONSULTAR CABECERA
    # ============================
    sql_cabecera = """
        SELECT 
            v.Id,
            v.NumeroDocumento,
            v.Prefijo,
            v.FechaEmision,
            v.HoraEmision,
            v.Subtotal,
            v.Impuestos,
            v.TotalVenta,
            v.MedioPago,
            v.TotalPagar,

            cli.RazonSocial,
            cli.NumeroIdentificacion,
            cli.Correo,
            cli.Telefono,
            cli.Direccion,
            cli.Municipio,
            cli.Departamento,

            td.Codigo AS CodigoDIAN,
            td.Descripcion AS TipoDIAN,

            pn.NombreEmprendimiento,
            pn.NombreEmpresario,
            pn.IdentificacionEmpresa,
            pn.DireccionEmpresa,
            pn.CorreoEmpresa,
            pn.CiudadEmpresa,
            pn.RegimenFiscal,
            pn.ResponsabilidadDIAN

        FROM Ventas v
        LEFT JOIN Consecutivos con ON v.ConsecutivoId = con.Id
        LEFT JOIN TipoDocumentoDIAN td ON con.TipoDocumentoDIANId = td.Id

        OUTER APPLY (
            SELECT TOP 1
                c.RazonSocial,
                c.NumeroIdentificacion,
                c.Correo,
                c.Telefono,
                c.Direccion,
                c.Municipio,
                c.Departamento
            FROM DetalleVenta dv
            LEFT JOIN Clientes c ON dv.ClienteId = c.Id
            WHERE dv.VentaId = v.Id
        ) cli

        CROSS APPLY (
            SELECT
                MAX(CASE WHEN NombreParametro = 'NOMBRE NEGOCIO' THEN ValorParametro END) AS NombreEmprendimiento,
                MAX(CASE WHEN NombreParametro = 'NOMBRE' THEN ValorParametro END) AS NombreEmpresario,
                MAX(CASE WHEN NombreParametro = 'IDENTIFICACION' THEN ValorParametro END) AS IdentificacionEmpresa,
                MAX(CASE WHEN NombreParametro = 'DIRECCION' THEN ValorParametro END) AS DireccionEmpresa,
                MAX(CASE WHEN NombreParametro = 'CORREO' THEN ValorParametro END) AS CorreoEmpresa,
                MAX(CASE WHEN NombreParametro = 'CIUDAD' THEN ValorParametro END) AS CiudadEmpresa,
                MAX(CASE WHEN NombreParametro = 'RegimenFiscal' THEN ValorParametro END) AS RegimenFiscal,
                MAX(CASE WHEN NombreParametro = 'ResponsabilidadDIAN' THEN ValorParametro END) AS ResponsabilidadDIAN
            FROM ParametrosNegocio
        ) pn

        WHERE v.Id = ?
    """

    row = query_one(sql_cabecera, (venta_id,))
    if not row:
        abort(404, "Venta no encontrada.")

    # Convertir pyodbc.Row → dict
    venta = {col[0]: row[i] for i, col in enumerate(row.cursor_description)}

    # Validación DIAN
    if venta["CodigoDIAN"] != "DEQ":
        abort(400, "Este documento no es un Documento Equivalente POS.")

    # ============================
    # 2. CONSULTAR DETALLE
    # ============================
    sql_detalle = """
        SELECT Descripcion, Cantidad, ValorUnitario, ValorTotalUnitario
        FROM DetalleVenta
        WHERE VentaId = ?
        ORDER BY NumeroLinea
    """

    rows = query_all(sql_detalle, (venta_id,))
    if not rows:
        abort(400, "La venta no tiene ítems registrados.")

    detalles = [
        {col[0]: r[i] for i, col in enumerate(r.cursor_description)}
        for r in rows
    ]

    # ============================
    # 3. FORMATEAR FECHA
    # ============================
    try:
        locale.setlocale(locale.LC_TIME, 'es_CO.UTF-8')
    except:
        locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')

    fecha = datetime.strptime(str(venta["FechaEmision"]), "%Y-%m-%d")
    fecha_formateada = fecha.strftime("%d de %B de %Y")

    # ============================
    # 4. RENDERIZAR PDF
    # ============================
    html = render_template(
        "pdf_documento_equivalente.html",
        venta=venta,
        detalles=detalles,
        fecha_formateada=fecha_formateada
    )

    pdf = HTML(string=html, base_url=app.root_path).write_pdf()

    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = (
        f"inline; filename=documento_equivalente_{venta_id}.pdf"
    )

    return response

# ---------------- ROUTE: GENERAR PDF Facturas Ventas ----------------
@app.route("/factura_electronica/pdf/<int:venta_id>")
def generar_pdf_factura_electronica(venta_id):

    # ============================
    # 1. CONSULTAR CABECERA
    # ============================
    sql = """
        SELECT 
            v.Id,
            v.NumeroDocumento,
            v.Prefijo,
            v.FechaEmision,
            v.HoraEmision,
            v.Subtotal,
            v.Impuestos,
            v.TotalVenta,
            v.MedioPago,
            v.TotalPagar,

            v.CUFE,
            v.QR,
            v.AmbienteDIAN,
            v.NumeroResolucion,
            v.FechaResolucion,

            cli.RazonSocial,
            cli.NumeroIdentificacion,
            cli.Correo,
            cli.Telefono,
            cli.Direccion,
            cli.Municipio,
            cli.Departamento,

            pn.NombreEmprendimiento,
            pn.NombreEmpresario,
            pn.IdentificacionEmpresa,
            pn.DireccionEmpresa,
            pn.CorreoEmpresa,
            pn.CiudadEmpresa,
            pn.RegimenFiscal,
            pn.ResponsabilidadDIAN

        FROM Ventas v

        OUTER APPLY (
            SELECT TOP 1
                c.RazonSocial,
                c.NumeroIdentificacion,
                c.Correo,
                c.Telefono,
                c.Direccion,
                c.Municipio,
                c.Departamento
            FROM DetalleVenta dv
            LEFT JOIN Clientes c ON dv.ClienteId = c.Id
            WHERE dv.VentaId = v.Id
        ) cli

        CROSS APPLY (
            SELECT
                MAX(CASE WHEN NombreParametro = 'NOMBRE NEGOCIO' THEN ValorParametro END) AS NombreEmprendimiento,
                MAX(CASE WHEN NombreParametro = 'NOMBRE' THEN ValorParametro END) AS NombreEmpresario,
                MAX(CASE WHEN NombreParametro = 'IDENTIFICACION' THEN ValorParametro END) AS IdentificacionEmpresa,
                MAX(CASE WHEN NombreParametro = 'DIRECCION' THEN ValorParametro END) AS DireccionEmpresa,
                MAX(CASE WHEN NombreParametro = 'CORREO' THEN ValorParametro END) AS CorreoEmpresa,
                MAX(CASE WHEN NombreParametro = 'CIUDAD' THEN ValorParametro END) AS CiudadEmpresa,
                MAX(CASE WHEN NombreParametro = 'RegimenFiscal' THEN ValorParametro END) AS RegimenFiscal,
                MAX(CASE WHEN NombreParametro = 'ResponsabilidadDIAN' THEN ValorParametro END) AS ResponsabilidadDIAN
            FROM ParametrosNegocio
        ) pn

        WHERE v.Id = ?
    """

    row = query_one(sql, (venta_id,))
    if not row:
        abort(404, "Factura no encontrada.")

    venta = {desc[0]: row[i] for i, desc in enumerate(row.cursor_description)}

    # Validar que sea factura electrónica
    if not venta["CUFE"]:
        abort(400, "Esta venta no es una Factura Electrónica.")

    # ============================
    # 2. CONSULTAR DETALLE
    # ============================
    rows = query_all("""
        SELECT Descripcion, Cantidad, ValorUnitario, ValorTotalUnitario
        FROM DetalleVenta
        WHERE VentaId = ?
        ORDER BY NumeroLinea
    """, (venta_id,))

    detalles = [
        {desc[0]: r[i] for i, desc in enumerate(r.cursor_description)}
        for r in rows
    ]

    # ============================
    # 3. FORMATEAR FECHA
    # ============================
    try:
        locale.setlocale(locale.LC_TIME, 'es_CO.UTF-8')
    except:
        locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')

    fecha = datetime.strptime(str(venta["FechaEmision"]), "%Y-%m-%d")
    fecha_formateada = fecha.strftime("%d de %B de %Y")

    # ============================
    # 4. RENDERIZAR HTML
    # ============================
    html = render_template(
        "pdf_factura_electronica.html",
        venta=venta,
        detalles=detalles,
        fecha_formateada=fecha_formateada
    )

    # ============================
    # 5. GENERAR PDF
    # ============================
    pdf = HTML(string=html, base_url=app.root_path).write_pdf()

    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = (
        f"inline; filename=factura_electronica_{venta_id}.pdf"
    )

    return response

# ---------------- ROUTES: INVENTARIOS ----------------
@app.route("/inventarios", methods=["GET", "POST"])
@require_permission("INVENTARIOS", "ver")
def inventarios_page():

    # ---------------- VALIDAR LOGIN ----------------
    if g.user is None:
        return redirect(url_for("login_page"))

    # ---------------- MENÚ POR ROL ----------------
    menu = get_menu_for_role(session["rol_id"])

    # ---------------- CARGAR CATEGORÍAS ----------------
    categorias = query_all("""
        SELECT Id, NombreCategoria
        FROM CategoriasInventario
        WHERE Estado = 1
        ORDER BY NombreCategoria
    """)

    # ============================================================
    # 🔥 LEER SWITCH IA_USAR_PROYECCION DESDE BD
    # ============================================================
    row_switch = query_one("""
        SELECT ValorParametro 
        FROM ParametrosNegocio
        WHERE NombreParametro = 'IA_USAR_PROYECCION'
    """)
    usar_proyeccion = int(row_switch[0]) if row_switch else 0

    # ============================================================
    # 🔥 ACTUALIZAR SWITCH IA_USAR_PROYECCION (POST AJAX)
    # ============================================================
    if request.method == "POST" and "switch_ia" in request.form:

        valor = 1 if request.form.get("valor") == "1" else 0

        exec_sql("""
            UPDATE ParametrosNegocio
            SET ValorParametro = ?, FechaActualizacion = GETDATE()
            WHERE NombreParametro = 'IA_USAR_PROYECCION'
        """, (valor,))

        flash("Proyección IA actualizada.", "success")
        return redirect(url_for("inventarios_page"))

    # ============================================================
    # 🔥 CREAR NUEVO PRODUCTO
    # ============================================================
    if request.method == "POST" and request.form.get("nombre"):

        nombre = request.form.get("nombre").strip()
        sku = request.form.get("sku").strip()
        codigo_barras = request.form.get("codigo_barras").strip()
        costo = request.form.get("costo").strip()
        categoria_id = request.form.get("categoria_id")
        tiene_fecha_vencimiento = 1 if request.form.get("tieneFechaVencimiento") else 0

        # Validaciones
        if not nombre or not sku or not codigo_barras or not costo or not categoria_id:
            flash("Todos los campos son obligatorios.", "danger")
            return redirect(url_for("inventarios_page"))

        if query_one("SELECT Id FROM Inventarios WHERE SKU = ? AND Estado = 1", (sku,)):
            flash("El SKU ya existe.", "warning")
            return redirect(url_for("inventarios_page"))

        if query_one("SELECT Id FROM Inventarios WHERE CodigoBarras = ? AND Estado = 1", (codigo_barras,)):
            flash("El código de barras ya existe.", "warning")
            return redirect(url_for("inventarios_page"))

        exec_sql("""
            INSERT INTO Inventarios 
            (NombreProducto, SKU, CodigoBarras, Costo, Estado, FechaCreacion, UsuarioId, 
             CategoriaInventarioId, TieneFechaVencimiento)
            VALUES (?, ?, ?, ?, 1, GETDATE(), ?, ?, ?)
        """, (nombre, sku, codigo_barras, costo, session["usuario_id"], categoria_id, tiene_fecha_vencimiento))

        flash("Producto registrado correctamente.", "success")
        return redirect(url_for("inventarios_page"))

    # ============================================================
    # 🔍 BÚSQUEDA Y PAGINACIÓN
    # ============================================================
    page_size = 10
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * page_size

    search = (request.args.get("search") or "").strip()
    where = "I.Estado = 1"
    params = []

    if search:
        where += " AND (I.NombreProducto LIKE ? OR I.SKU LIKE ? OR I.CodigoBarras LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]

    total_productos = query_one(
        f"SELECT COUNT(*) FROM Inventarios I WHERE {where}", tuple(params)
    )[0]

    total_pages = max(1, (total_productos + page_size - 1) // page_size)

    total_stock = query_one(f"""
        SELECT ISNULL(SUM(D.Cantidad), 0)
        FROM Inventarios I
        LEFT JOIN DetalleInventario D ON I.Id = D.InventarioId AND D.Estado = 1
        WHERE {where}
    """, tuple(params))[0]

    # ============================================================
    # 📦 CONSULTA PRINCIPAL (PAGINADA EN SQL)
    # ============================================================
    rows = query_all(
        f"""
        SELECT I.Id, I.SKU, I.NombreProducto,
               C.NombreCategoria,
               ISNULL(SUM(D.Cantidad), 0) AS Stock,
               I.Costo,
               I.FechaCreacion,
               I.TieneFechaVencimiento
        FROM Inventarios I
        LEFT JOIN CategoriasInventario C ON I.CategoriaInventarioId = C.Id
        LEFT JOIN DetalleInventario D ON I.Id = D.InventarioId AND D.Estado = 1
        WHERE {where}
        GROUP BY I.Id, I.SKU, I.NombreProducto, C.NombreCategoria, 
                 I.Costo, I.FechaCreacion, I.TieneFechaVencimiento
        ORDER BY I.FechaCreacion DESC, I.Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """,
        tuple(params + [offset, page_size])
    )

    # ============================================================
    # 🧠 EJECUTAR MODELO DE INVENTARIOS (REGRESIÓN LINEAL)
    # ============================================================
    resultados = generar_resultados_inventario()

    # Si por alguna razón viene vacío, evitar error
    rotacion = resultados.get("rotacion_productos", []) if resultados else []
    mapa_resultados = {r["InventarioId"]: r for r in rotacion}

    # ============================================================
    # 🧩 ARMAR LISTA FINAL DE PRODUCTOS PARA EL HTML
    # ============================================================
    productos = []
    for r in rows:
        inv_id = r[0]
        stock = int(r[4]) if r[4] is not None else 0
        costo = float(r[5]) if r[5] is not None else 0.0

        datos_ia = mapa_resultados.get(inv_id, {})

        calidad = datos_ia.get("CalidadModelo", "Sin datos")
        error = datos_ia.get("ErrorModelo", 0)
        stock_optimo = int(datos_ia.get("StockOptimo", 0))

        # ---------------- PROYECCIÓN IA (TEXTO SIMPLE) ----------------
        if usar_proyeccion == 0:
            proyeccion_ia = "Sin proyección"
        else:
            if calidad == "Sin datos":
                proyeccion_ia = "Sin datos"
            elif calidad == "Malo":
                proyeccion_ia = "Sin proyección"
            else:
                proyeccion_ia = f"{stock_optimo} unidades"

        # ---------------- VALORACIÓN (TEXTO) ----------------
        # Puedes dejarla tal cual o mapearla a algo más visual
        valoracion = calidad

        productos.append({
            "id": inv_id,
            "sku": r[1],
            "nombre": r[2],
            "categoria": r[3],
            "stock": stock,
            "stock_optimo": stock_optimo,
            "costo": costo,
            "precio_total": costo * stock,
            "fecha_creacion": r[6].strftime("%Y-%m-%d %H:%M") if r[6] else "",
            "tiene_fecha_vencimiento": bool(r[7]),

            # Campos para la vista
            "proyeccion_ia": proyeccion_ia,
            "valoracion": valoracion,
            "error_modelo": error
        })

    # ============================================================
    # 📄 RENDERIZAR PLANTILLA
    # ============================================================
    return render_template(
        "inventarios.html",
        menu=menu,
        productos=productos,
        page=page,
        total_pages=total_pages,
        total_productos=total_productos,
        total_stock=total_stock,
        search=search,
        categorias=categorias,
        usar_proyeccion=usar_proyeccion
    )



# ---------------- INACTIVAR PRODUCTO ----------------
@app.route("/inventarios/inactivar/<int:inventario_id>", methods=["POST"])
@require_permission("INVENTARIOS", "inactivar")
def inactivar_inventario(inventario_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    exec_sql("UPDATE Inventarios SET Estado = 0 WHERE Id = ?", (inventario_id,))
    flash("Producto inactivado correctamente.", "success")
    return redirect(url_for("inventarios_page"))


# ---------------- EDITAR PRODUCTO ----------------
@app.route("/inventarios/editar/<int:inventario_id>", methods=["GET", "POST"])
@require_permission("INVENTARIOS", "editar")
def editar_inventario(inventario_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    # Capturar parámetros de paginación y búsqueda
    page = request.args.get("page", 1)
    search = request.args.get("search", "")

    menu = get_menu_for_role(session["rol_id"])

    # Obtener datos del producto actual
    row = query_one("""
        SELECT I.Id, I.NombreProducto, I.SKU, I.CodigoBarras, I.Costo,
               C.NombreCategoria AS CategoriaNombre, I.CategoriaInventarioId,
               I.TieneFechaVencimiento
        FROM Inventarios I
        LEFT JOIN CategoriasInventario C ON I.CategoriaInventarioId = C.Id
        WHERE I.Id = ? AND I.Estado = 1
    """, (inventario_id,))

    if not row:
        flash("Producto no encontrado o inactivo.", "warning")
        return redirect(url_for("inventarios_page", page=page, search=search))

    producto = {
        "id": row[0],
        "nombre": row[1],
        "sku": row[2],
        "codigo_barras": row[3],
        "costo": row[4],
        "categoria_nombre": row[5],
        "categoria_id": row[6],
        "tiene_fecha_vencimiento": bool(row[7])
    }

    categorias = query_all("""
        SELECT Id, NombreCategoria
        FROM CategoriasInventario
        WHERE Estado = 1
        ORDER BY NombreCategoria
    """)

    # ---------------- NAVEGACIÓN ENTRE REGISTROS ----------------
    prev_row = query_one("""
        SELECT TOP 1 Id FROM Inventarios
        WHERE Id < ? AND Estado = 1
        ORDER BY Id DESC
    """, (inventario_id,))
    next_row = query_one("""
        SELECT TOP 1 Id FROM Inventarios
        WHERE Id > ? AND Estado = 1
        ORDER BY Id ASC
    """, (inventario_id,))

    prev_id = prev_row[0] if prev_row else None
    next_id = next_row[0] if next_row else None

    # ---------------- ACTUALIZACIÓN DE PRODUCTO ----------------
    if request.method == "POST":
        nombre = (request.form.get("nombre_edit") or "").strip()
        sku = (request.form.get("sku_edit") or "").strip()
        codigo_barras = (request.form.get("codigo_barras_edit") or "").strip()
        costo = (request.form.get("costo_edit") or "").strip()
        categoria_id = request.form.get("categoria_edit")

        if not nombre or not sku or not codigo_barras or not costo or not categoria_id:
            flash("Todos los campos del producto son obligatorios.", "danger")
            return redirect(url_for("editar_inventario", inventario_id=inventario_id, page=page, search=search))

        existe_sku = query_one("""
            SELECT Id FROM Inventarios
            WHERE SKU = ? AND Id <> ? AND Estado = 1
        """, (sku, inventario_id))
        if existe_sku:
            flash("El SKU ya existe en otro producto.", "warning")
            return redirect(url_for("editar_inventario", inventario_id=inventario_id, page=page, search=search))

        existe_cb = query_one("""
            SELECT Id FROM Inventarios
            WHERE CodigoBarras = ? AND Id <> ? AND Estado = 1
        """, (codigo_barras, inventario_id))
        if existe_cb:
            flash("El código de barras ya existe en otro producto.", "warning")
            return redirect(url_for("editar_inventario", inventario_id=inventario_id, page=page, search=search))

        exec_sql("""
            UPDATE Inventarios
            SET NombreProducto = ?, SKU = ?, CodigoBarras = ?, Costo = ?, 
                CategoriaInventarioId = ?, FechaCreacion = GETDATE()
            WHERE Id = ?
        """, (nombre, sku, codigo_barras, costo, categoria_id, inventario_id))

        flash("Producto actualizado correctamente.", "success")
        return redirect(url_for("inventarios_page", page=page, search=search))

    # ---------------- LOTES ----------------
    lotes = query_all("""
        SELECT Id, NumeroLote, Cantidad, FechaVencimiento, Estado
        FROM DetalleInventario
        WHERE InventarioId = ? AND Estado = 1
        ORDER BY FechaVencimiento
    """, (inventario_id,))

    total_stock = sum(int(l[2] or 0) for l in lotes)

    # ---------------- RENDER ----------------
    return render_template(
        "editar_inventario.html",
        menu=menu,
        producto=producto,
        lotes=lotes,
        total_stock=total_stock,
        categorias=categorias,
        page=page,
        search=search,
        prev_id=prev_id,
        next_id=next_id
    )



# ---------------- AGREGAR LOTE ----------------
@app.route("/inventarios/agregar_lote/<int:inventario_id>", methods=["POST"])
@require_permission("INVENTARIOS", "crear")
def agregar_lote(inventario_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    numero_lote = (request.form.get("numero_lote") or "").strip()
    cantidad = (request.form.get("cantidad") or "").strip()
    fecha_vencimiento = (request.form.get("fecha_vencimiento") or "").strip()

    if not numero_lote or not cantidad:
        flash("El número de lote y la cantidad son obligatorios.", "danger")
        return redirect(url_for("editar_inventario", inventario_id=inventario_id))

    # Validar regla de vencimiento según el producto
    inv_row = query_one("SELECT TieneFechaVencimiento FROM Inventarios WHERE Id = ?", (inventario_id,))
    if inv_row and inv_row[0] == 1 and not fecha_vencimiento:
        flash("Este producto requiere fecha de vencimiento para sus lotes.", "danger")
        return redirect(url_for("editar_inventario", inventario_id=inventario_id))

    existe_lote = query_one("""
        SELECT Id FROM DetalleInventario
        WHERE InventarioId = ? AND NumeroLote = ? AND Estado = 1
    """, (inventario_id, numero_lote))
    if existe_lote:
        flash("El número de lote ya existe para este producto.", "warning")
        return redirect(url_for("editar_inventario", inventario_id=inventario_id))

    fecha_final = fecha_vencimiento if fecha_vencimiento else None

    exec_sql("""
        INSERT INTO DetalleInventario 
        (InventarioId, NumeroLote, Cantidad, FechaVencimiento, Estado, UsuarioId)
        VALUES (?, ?, ?, ?, 1, ?)
    """, (inventario_id, numero_lote, cantidad, fecha_final, session["usuario_id"]))

    flash("Lote agregado correctamente.", "success")
    return redirect(url_for("editar_inventario", inventario_id=inventario_id))


# ---------------- EDITAR LOTE ----------------
@app.route("/inventarios/editar_lote/<int:lote_id>/<int:inventario_id>", methods=["GET", "POST"])
@require_permission("INVENTARIOS", "editar")
def editar_lote(lote_id, inventario_id):
    if g.user is None:
        return redirect(url_for("login_page"))

    if request.method == "GET":
        lote = query_one("""
            SELECT Id, NumeroLote, Cantidad, FechaVencimiento
            FROM DetalleInventario
            WHERE Id = ? AND Estado = 1
        """, (lote_id,))

        if not lote:
            return jsonify({"error": "Lote no encontrado"}), 404

        lote_json = {
            "id": lote[0],
            "numero_lote": lote[1],
            "cantidad": lote[2],
            "fecha_vencimiento": lote[3].strftime("%Y-%m-%d") if hasattr(lote[3], "strftime") else lote[3]
        }
        return jsonify(lote_json)

    numero_lote = (request.form.get("numero_lote_edit") or "").strip()
    cantidad = (request.form.get("cantidad_edit") or "").strip()
    fecha_vencimiento = (request.form.get("fecha_vencimiento_edit") or "").strip()

    if not numero_lote or not cantidad or not fecha_vencimiento:
        flash("Todos los campos del lote son obligatorios.", "danger")
        return redirect(url_for("editar_inventario", inventario_id=inventario_id))

    existe_lote = query_one("""
        SELECT Id FROM DetalleInventario
        WHERE InventarioId = ? AND NumeroLote = ? AND Id <> ? AND Estado = 1
    """, (inventario_id, numero_lote, lote_id))
    if existe_lote:
        flash("Ya existe otro lote con ese número para este producto.", "warning")
        return redirect(url_for("editar_inventario", inventario_id=inventario_id))

    exec_sql("""
        UPDATE DetalleInventario
        SET NumeroLote = ?, Cantidad = ?, FechaVencimiento = ?
        WHERE Id = ?
    """, (numero_lote, cantidad, fecha_vencimiento, lote_id))

    flash("Lote actualizado correctamente.", "success")
    return redirect(url_for("editar_inventario", inventario_id=inventario_id))




# ---------------- ROUTES: CATEGORIAS INVENTARIO ----------------

@app.route("/categorias_inventario", methods=["GET"])
@require_permission("CATEGORIAS_INVENTARIO", "ver")
def categorias_inventario_page():
    menu = get_menu_for_role(session["rol_id"])

    # Paginación
    page_size = 10
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    offset = (page - 1) * page_size

    # Búsqueda
    search = (request.args.get("search") or "").strip()
    where = "Estado = 1"
    params = []

    if search:
        where += " AND NombreCategoria LIKE ?"
        params.append(f"%{search}%")

    # Total registros
    total_row = query_one(f"""
        SELECT COUNT(*) FROM CategoriasInventario
        WHERE {where}
    """, tuple(params))
    total = int(total_row[0]) if total_row else 0

    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size

    # Obtener categorías
    rows = query_all(f"""
        SELECT Id, NombreCategoria, ColorVisual, Icono
        FROM CategoriasInventario
        WHERE {where}
        ORDER BY Id DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    categorias = [
        {
            "id": r[0],
            "nombre": r[1],
            "color": r[2],
            "icono": r[3]
        }
        for r in rows
    ]

    return render_template(
        "categorias_inventario.html",
        menu=menu,
        categorias=categorias,
        page=page,
        total_pages=total_pages,
        search=search,
        total=total
    )


@app.route("/categorias_inventario/crear", methods=["POST"])
@require_permission("CATEGORIAS_INVENTARIO", "crear")
def crear_categoria_inventario():
    nombre = (request.form.get("nombre") or "").strip()
    color = (request.form.get("color") or "").strip()
    icono = (request.form.get("icono") or "").strip()

    if not nombre:
        flash("El nombre de la categoría es obligatorio.", "danger")
        return redirect(url_for("categorias_inventario_page"))

    # Validación duplicados
    existe = query_one("""
        SELECT Id FROM CategoriasInventario
        WHERE Estado = 1 AND LOWER(NombreCategoria) = LOWER(?)
    """, (nombre,))
    if existe:
        flash("Ya existe una categoría activa con ese nombre.", "danger")
        return redirect(url_for("categorias_inventario_page"))

    exec_sql("""
        INSERT INTO CategoriasInventario (NombreCategoria, ColorVisual, Icono, Estado, UsuarioId)
        VALUES (?, ?, ?, 1, ?)
    """, (nombre, color, icono, session["usuario_id"]))

    flash("Categoría creada correctamente.", "success")
    return redirect(url_for("categorias_inventario_page"))


@app.route("/categorias_inventario/inactivar/<int:cat_id>", methods=["POST"])
@require_permission("CATEGORIAS_INVENTARIO", "editar")
def inactivar_categoria_inventario(cat_id):
    exec_sql("""
        UPDATE CategoriasInventario
        SET Estado = 0
        WHERE Id = ?
    """, (cat_id,))

    flash("Categoría inactivada correctamente.", "warning")
    return redirect(url_for("categorias_inventario_page"))


@app.route("/categorias_inventario/editar/<int:cat_id>", methods=["POST"])
@require_permission("CATEGORIAS_INVENTARIO", "editar")
def editar_categoria_inventario(cat_id):
    nombre = (request.form.get("nombre_edit") or "").strip()
    color = (request.form.get("color_edit") or "").strip()
    icono = (request.form.get("icono_edit") or "").strip()

    if not nombre:
        flash("El nombre de la categoría es obligatorio.", "danger")
        return redirect(url_for("categorias_inventario_page"))

    # Validación duplicados
    existe = query_one("""
        SELECT Id FROM CategoriasInventario
        WHERE Estado = 1 AND LOWER(NombreCategoria) = LOWER(?) AND Id <> ?
    """, (nombre, cat_id))
    if existe:
        flash("Ya existe otra categoría activa con ese nombre.", "danger")
        return redirect(url_for("categorias_inventario_page"))

    exec_sql("""
        UPDATE CategoriasInventario
        SET NombreCategoria = ?, ColorVisual = ?, Icono = ?
        WHERE Id = ?
    """, (nombre, color, icono, cat_id))

    flash("Categoría actualizada correctamente.", "success")
    return redirect(url_for("categorias_inventario_page"))


# ---------------- ROUTES: Tipos de Registro ----------------

@app.route("/tipos_registro", methods=["GET", "POST"])
@require_permission("TIPOS_REGISTRO", "ver")
def tipos_registro_page():

    if g.user is None:
        return redirect(url_for("login_page"))

    menu = get_menu_for_role(session["rol_id"])

    # Parámetros
    search = (request.args.get("search") or "").strip()
    page_size = 10
    page = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * page_size

    # Filtro
    where = "Estado = 1"
    params = []

    if search:
        where += " AND NombreTipo LIKE ?"
        params.append(f"%{search}%")

    # Total registros
    total_tipos = query_one(
        f"SELECT COUNT(*) FROM TiposRegistro WHERE {where}",
        tuple(params)
    )[0]

    total_pages = max(1, (total_tipos + page_size - 1) // page_size)

    # Consulta principal
    tipos = query_all(f"""
        SELECT Id, NombreTipo, Descripcion, UsuarioId, FechaCreacion, Estado
        FROM TiposRegistro
        WHERE {where}
        ORDER BY FechaCreacion DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """, tuple(params + [offset, page_size]))

    # Crear nuevo tipo
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        usuario_id = session["user_id"]

        if not nombre:
            flash("El nombre del tipo de registro es obligatorio.", "danger")
            return redirect(url_for("tipos_registro_page"))

        # 🔸 Validar unicidad
        existe = query_one("SELECT COUNT(*) FROM TiposRegistro WHERE NombreTipo = ? AND Estado = 1", (nombre,))[0]
        if existe > 0:
            flash("Ya existe un tipo de registro con ese nombre.", "warning")
            return redirect(url_for("tipos_registro_page"))

        exec_sql("""
            INSERT INTO TiposRegistro (NombreTipo, Descripcion, UsuarioId)
            VALUES (?, ?, ?)
        """, (nombre, descripcion, usuario_id))

        flash("Tipo de registro creado correctamente.", "success")
        return redirect(url_for("tipos_registro_page"))

    return render_template(
        "tipos_registro.html",
        menu=menu,
        tipos=tipos,
        total_tipos=total_tipos,
        total_pages=total_pages,
        page=page,
        search=search
    )


# ---------------- EDITAR TIPO DE REGISTRO (POST desde modal) ----------------
@app.route("/tipos_registro/editar/<int:tipo_id>", methods=["POST"])
@require_permission("TIPOS_REGISTRO", "editar")
def editar_tipo_registro(tipo_id):

    page = request.args.get("page", 1)
    search = request.args.get("search", "")

    nombre = (request.form.get("nombre_edit") or "").strip()
    descripcion = (request.form.get("descripcion_edit") or "").strip()

    if not nombre:
        flash("El nombre del tipo de registro es obligatorio.", "danger")
        return redirect(url_for("tipos_registro_page", page=page, search=search))

    # 🔸 Validar unicidad (excluyendo el mismo registro)
    existe = query_one("SELECT COUNT(*) FROM TiposRegistro WHERE NombreTipo = ? AND Id <> ? AND Estado = 1", (nombre, tipo_id))[0]
    if existe > 0:
        flash("Ya existe otro tipo de registro con ese nombre.", "warning")
        return redirect(url_for("tipos_registro_page", page=page, search=search))

    exec_sql("""
        UPDATE TiposRegistro
        SET NombreTipo = ?, Descripcion = ?
        WHERE Id = ?
    """, (nombre, descripcion, tipo_id))

    flash("Tipo de registro actualizado correctamente.", "success")
    return redirect(url_for("tipos_registro_page", page=page, search=search))


# ---------------- INACTIVAR TIPO DE REGISTRO ----------------
@app.route("/tipos_registro/inactivar/<int:tipo_id>", methods=["POST"])
@require_permission("TIPOS_REGISTRO", "inactivar")
def inactivar_tipo_registro(tipo_id):

    page = request.args.get("page", 1)
    search = request.args.get("search", "")

    exec_sql("""
        UPDATE TiposRegistro
        SET Estado = 0
        WHERE Id = ?
    """, (tipo_id,))

    flash("Tipo de registro inactivado correctamente.", "success")
    return redirect(url_for("tipos_registro_page", page=page, search=search))




# ---------------- ROUTES: Dashboard Ventas ----------------

@app.route("/ventas/dashboard")
def dashboard_ventas():
    return render_template("dashboard_ventas.html")

# ---------------- ROUTES: Dashboard Gastos ----------------

@app.route("/gastos/dashboard")
def dashboard_gastos():
    return render_template("dashboard_gastos.html")

# ---------------- ROUTES: Dashboard Inventarios ----------------

@app.route("/inventarios/dashboard")
@require_permission("INVENTARIOS", "ver")
def dashboard_inventarios():

    kpis = generar_kpis_inventario()

    return render_template(
        "dashboard_inventarios.html",
        kpis=kpis
    )

# ---------------- ROUTES: Dashboard Facturas Proveedores ----------------
@app.route("/facturas_proveedores/dashboard")
def dashboard_facturas_proveedores():
    return render_template("dashboard_facturas_proveedores.html")




# --------------- run ----------------
if __name__ == "__main__":
   app.run(host="0.0.0.0", port=5000, debug=True)

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == "__main__":
    Timer(1, open_browser).start()
    app.run(host="0.0.0.0", port=5000, debug=True)