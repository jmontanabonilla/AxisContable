/* ==========================================
   CREAR BASE DE DATOS
========================================== */
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'AxisContable')
BEGIN
    CREATE DATABASE AxisContable;
END;
GO

USE AxisContable;
GO

/* ==========================================
   ROLES Y USUARIOS
========================================== */
CREATE TABLE Roles (
    Id INT IDENTITY PRIMARY KEY,
    NombreRol VARCHAR(50) NOT NULL,
    Estado BIT NOT NULL DEFAULT 1
);
GO

CREATE TABLE Usuarios (
    Id INT IDENTITY PRIMARY KEY,
    Usuario VARCHAR(50) NOT NULL UNIQUE,
    ContrasenaHash VARCHAR(255) NOT NULL,
    RolId INT NOT NULL,
    FechaCreacion DATETIME2 DEFAULT SYSDATETIME(),
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_Usuarios_Roles FOREIGN KEY (RolId) REFERENCES Roles(Id)
);
GO

/* ==========================================
   PERMISOS
========================================== */
CREATE TABLE Permisos (
    Id INT IDENTITY PRIMARY KEY,
    RolId INT NOT NULL,
    Modulo VARCHAR(100) NOT NULL,
    Ver BIT NOT NULL DEFAULT 0,
    Crear BIT NOT NULL DEFAULT 0,
    Editar BIT NOT NULL DEFAULT 0,
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_Permisos_Rol FOREIGN KEY (RolId) REFERENCES Roles(Id)
);
GO

/* ==========================================
   TABLAS PARAM�TRICAS
========================================== */
CREATE TABLE ParametrosNegocio (
    Id INT IDENTITY PRIMARY KEY,
    NombreParametro VARCHAR(100) NOT NULL,
    ValorParametro VARCHAR(500) NOT NULL,
    Categoria VARCHAR(100),
    FechaActualizacion DATETIME2 DEFAULT SYSDATETIME(),
    Estado BIT NOT NULL DEFAULT 1
);
GO

CREATE TABLE TipoDocumentoDIAN (
    Id INT IDENTITY PRIMARY KEY,
    Codigo VARCHAR(10) NOT NULL,
    Descripcion VARCHAR(100) NOT NULL,
    Estado BIT NOT NULL DEFAULT 1
);
GO

CREATE TABLE TipoIdentificacion (
    Id INT IDENTITY PRIMARY KEY,
    Codigo VARCHAR(10) NOT NULL,
    Descripcion VARCHAR(100) NOT NULL,
    Estado BIT NOT NULL DEFAULT 1
);
GO

CREATE TABLE CategoriasGasto (
    Id INT IDENTITY PRIMARY KEY,
    Nombre VARCHAR(100) NOT NULL,
    Estado BIT NOT NULL DEFAULT 1
);
GO

CREATE TABLE Consecutivos (
    Id INT IDENTITY PRIMARY KEY,
    Tipo VARCHAR(50) NOT NULL,
    Prefijo VARCHAR(20) NOT NULL,
    ValorActual BIGINT NOT NULL,
    NumeroInicial BIGINT,
    NumeroFinal BIGINT,
    FechaActualizacion DATETIME2 DEFAULT SYSDATETIME(),
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT UQ_Consecutivos UNIQUE (Tipo, Prefijo)
);
GO

/* ==========================================
   CLIENTES
========================================== */
CREATE TABLE Clientes (
    Id BIGINT IDENTITY PRIMARY KEY,
    TipoIdentificacionId INT NOT NULL,
    NumeroIdentificacion VARCHAR(50) NOT NULL,
    RazonSocial VARCHAR(255),
    Telefono VARCHAR(50),
    Direccion VARCHAR(255),
    Departamento VARCHAR(100),
    Municipio VARCHAR(100),
    Correo VARCHAR(255),
    FechaCreacion DATETIME2 DEFAULT SYSDATETIME(),
    UsuarioCreaId INT NULL,
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_Clientes_TipoIdentificacion 
        FOREIGN KEY (TipoIdentificacionId) REFERENCES TipoIdentificacion(Id),
    CONSTRAINT FK_Clientes_Usuario 
        FOREIGN KEY (UsuarioCreaId) REFERENCES Usuarios(Id)
);
GO

CREATE UNIQUE INDEX UQ_Clientes 
ON Clientes(TipoIdentificacionId, NumeroIdentificacion);
GO

/* ==========================================
   VENTAS
========================================== */
CREATE TABLE Ventas (
    Id BIGINT IDENTITY PRIMARY KEY,
    NumeroDocumento VARCHAR(50) NOT NULL,
    Prefijo VARCHAR(20) NOT NULL,
    FechaEmision DATE NOT NULL,
    HoraEmision TIME NOT NULL,
    Subtotal DECIMAL(14,2) NOT NULL,
    Impuestos DECIMAL(14,2) NOT NULL DEFAULT 0,
    TotalVenta DECIMAL(14,2) NOT NULL,
    MedioPago VARCHAR(50),
    UsuarioCreaId INT NULL,
    FechaCreacion DATETIME2 DEFAULT SYSDATETIME(),
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_Ventas_Usuario FOREIGN KEY (UsuarioCreaId) REFERENCES Usuarios(Id)
);
GO

CREATE UNIQUE INDEX UQ_Ventas
ON Ventas(Prefijo, NumeroDocumento);
GO

/* ==========================================
   DETALLE VENTA
========================================== */
CREATE TABLE DetalleVenta (
    Id BIGINT IDENTITY PRIMARY KEY,
    VentaId BIGINT NOT NULL,
    NumeroLinea INT NOT NULL,
    Descripcion VARCHAR(255) NOT NULL,
    Cantidad DECIMAL(10,2) NOT NULL,
    ValorUnitario DECIMAL(14,2) NOT NULL,
    Subtotal DECIMAL(14,2) NOT NULL,
    Total DECIMAL(14,2) NOT NULL,

    ClienteId BIGINT NULL,
    TipoDocumentoId INT NULL,
    CUFE_CUDE VARCHAR(255),
    FechaEnvioDIAN DATETIME2,

    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_DetalleVenta_Venta 
        FOREIGN KEY (VentaId) REFERENCES Ventas(Id) ON DELETE CASCADE,
    CONSTRAINT FK_DetalleVenta_Cliente 
        FOREIGN KEY (ClienteId) REFERENCES Clientes(Id),
    CONSTRAINT FK_DetalleVenta_TipoDoc 
        FOREIGNUTINE FOREIGN KEY (TipoDocumentoId) REFERENCES TipoDocumentoDIAN(Id)
);
GO

CREATE UNIQUE INDEX UQ_DetalleVenta
ON DetalleVenta (VentaId, NumeroLinea);
GO

/* ==========================================
   GASTOS GENERALES
========================================== */
CREATE TABLE GastosGenerales (
    Id BIGINT IDENTITY PRIMARY KEY,
    Fecha DATE NOT NULL,
    TipoRegistro VARCHAR(50) NOT NULL,
    Descripcion VARCHAR(255) NOT NULL,
    CategoriaId INT NULL,
    Valor DECIMAL(14,2) NOT NULL,
    UsuarioId INT NOT NULL,
    FechaRegistro DATETIME2 DEFAULT SYSDATETIME(),
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_Gastos_Categoria FOREIGN KEY (CategoriaId) REFERENCES CategoriasGasto(Id),
    CONSTRAINT FK_Gastos_Usuario FOREIGN KEY (UsuarioId) REFERENCES Usuarios(Id)
);
GO

/* ==========================================
   FACTURAS DE PROVEEDORES (CON AUTOMATIZACION)
========================================== */
CREATE TABLE FacturasProveedores (
    Id BIGINT IDENTITY PRIMARY KEY,
    ProveedorNombre VARCHAR(255) NOT NULL,
    TipoIdentificacionId INT NULL,
    NumeroIdentificacion VARCHAR(50) NULL,
    NumeroFactura VARCHAR(50) NOT NULL,
    FechaFactura DATE NOT NULL,
    FechaRegistro DATETIME2 DEFAULT SYSDATETIME(),
    Subtotal DECIMAL(14,2) NOT NULL,
    Impuestos DECIMAL(14,2) DEFAULT 0,
    Total DECIMAL(14,2) NOT NULL,
    MedioPago VARCHAR(50),
    UsuarioId INT NOT NULL,

    -- CAMPOS NUEVOS PARA AUTOMATIZACI�N
    OrigenRegistro VARCHAR(50) NOT NULL DEFAULT 'MANUAL',  -- MANUAL / CORREO / N8N
    EmailOrigen VARCHAR(255) NULL,

    Estado BIT NOT NULL DEFAULT 1,

    CONSTRAINT FK_Proveedor_TipoIdent 
        FOREIGN KEY (TipoIdentificacionId) REFERENCES TipoIdentificacion(Id),
    CONSTRAINT FK_Proveedor_Usuario 
        FOREIGN KEY (UsuarioId) REFERENCES Usuarios(Id)
);
GO

/* ==========================================
   REPORTES DIAN
========================================== */
CREATE TABLE ReportesDian (
    Id BIGINT IDENTITY PRIMARY KEY,
    VentaId BIGINT NOT NULL,
    NumeroDocumentoDIAN VARCHAR(200),
    XML_Enviado NVARCHAR(MAX),
    XML_Respuesta NVARCHAR(MAX),
    CodigoRespuesta VARCHAR(50),
    MensajeRespuesta NVARCHAR(1000),
    FechaEnvio DATETIME2 DEFAULT SYSDATETIME(),
    FechaRespuesta DATETIME2,
    Estado BIT NOT NULL DEFAULT 1,
    CONSTRAINT FK_ReportesDian 
        FOREIGN KEY (VentaId) REFERENCES Ventas(Id) ON DELETE CASCADE
);
GO

/* ==========================================
   VISTAS
========================================== */
CREATE VIEW vw_ReporteDiarioVentas AS
SELECT 
    FechaEmision,
    COUNT(*) AS NumeroTransacciones,
    SUM(TotalVenta) AS TotalVendido
FROM Ventas
WHERE Estado = 1
GROUP BY FechaEmision;
GO

/* ==========================================
   TRIGGERS DE PROTECCI�N
========================================== */
CREATE TRIGGER TRG_NoDelete_Ventas
ON Ventas
INSTEAD OF DELETE
AS
BEGIN
    RAISERROR('No se permite borrar. Use Estado = 0.',16,1);
END;
GO

CREATE TRIGGER TRG_NoDelete_Clientes
ON Clientes
INSTEAD OF DELETE
AS
BEGIN
    RAISERROR('No se permite borrar. Use Estado = 0.',16,1);
END;
GO

CREATE TRIGGER TRG_NoDelete_Gastos
ON GastosGenerales
INSTEAD OF DELETE
AS
BEGIN
    RAISERROR('No se permite borrar. Use Estado = 0.',16,1);
END;
GO


CREATE TABLE DetalleVenta (
    Id BIGINT IDENTITY PRIMARY KEY,
    VentaId BIGINT NOT NULL,
    NumeroLinea INT NOT NULL,
    Descripcion VARCHAR(255) NOT NULL,
    Cantidad DECIMAL(10,2) NOT NULL,
    ValorUnitario DECIMAL(14,2) NOT NULL,
    Subtotal DECIMAL(14,2) NOT NULL,
    Total DECIMAL(14,2) NOT NULL,

    ClienteId BIGINT NULL,
    TipoDocumentoId INT NULL,
    CUFE_CUDE VARCHAR(255) NULL,
    FechaEnvioDIAN DATETIME2 NULL,

    Estado BIT NOT NULL DEFAULT 1,

    CONSTRAINT FK_DetalleVenta_Venta 
        FOREIGN KEY (VentaId) REFERENCES Ventas(Id) ON DELETE CASCADE,

    CONSTRAINT FK_DetalleVenta_Cliente 
        FOREIGN KEY (ClienteId) REFERENCES Clientes(Id),

    CONSTRAINT FK_DetalleVenta_TipoDoc 
        FOREIGN KEY (TipoDocumentoId) REFERENCES TipoDocumentoDIAN(Id)
);
GO

CREATE UNIQUE INDEX UQ_DetalleVenta
ON DetalleVenta (VentaId, NumeroLinea);
GO

SELECT * 
FROM sys.tables 
WHERE name IN ('Ventas', 'DetalleVenta');