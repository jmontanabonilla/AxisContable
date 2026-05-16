import getpass
from werkzeug.security import generate_password_hash
from db import get_connection

def ensure_roles_exist(conn):
    cur = conn.cursor()
    cur.execute("IF NOT EXISTS (SELECT 1 FROM Roles WHERE NombreRol = 'Admin') INSERT INTO Roles(NombreRol, Estado) VALUES ('Admin',1);")
    cur.execute("IF NOT EXISTS (SELECT 1 FROM Roles WHERE NombreRol = 'Usuario') INSERT INTO Roles(NombreRol, Estado) VALUES ('Usuario',1);")
    cur.execute("IF NOT EXISTS (SELECT 1 FROM Roles WHERE NombreRol = 'Ventas') INSERT INTO Roles(NombreRol, Estado) VALUES ('Ventas',1);")
    conn.commit()

def create_admin(usuario, password):
    pw_hash = generate_password_hash(password)
    conn = get_connection()
    try:
        ensure_roles_exist(conn)
        cur = conn.cursor()
        cur.execute("SELECT TOP 1 Id FROM Roles WHERE NombreRol = 'Admin';")
        rol_admin_id = cur.fetchone()[0]
        cur.execute("SELECT Id FROM Usuarios WHERE Usuario = ?;", (usuario,))
        if cur.fetchone():
            print(f"El usuario '{usuario}' ya existe.")
            return
        cur.execute("INSERT INTO Usuarios (Usuario, ContrasenaHash, RolId, Estado) VALUES (?, ?, ?, 1);", (usuario, pw_hash, rol_admin_id))
        conn.commit()
        print(f"Usuario '{usuario}' creado correctamente con rol Admin.")
    finally:
        conn.close()

if __name__ == "__main__":
    print("Creación de usuario admin para Monbo")
    usuario = input("Usuario (por defecto 'admin'): ").strip() or "admin"
    password = getpass.getpass("Contraseña para el usuario admin: ")
    password2 = getpass.getpass("Confirme la contraseña: ")
    if password != password2:
        print("Las contraseñas no coinciden.")
    elif len(password) < 6:
        print("Usa una contraseña de al menos 6 caracteres.")
    else:
        create_admin(usuario, password)

