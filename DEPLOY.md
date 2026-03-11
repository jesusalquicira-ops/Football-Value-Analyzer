# 🚀 Despliegue en Streamlit Cloud — Guía paso a paso

## Lo que necesitas
- Cuenta en GitHub (gratis): https://github.com
- Cuenta en Streamlit Cloud (gratis): https://share.streamlit.io

---

## Paso 1 — Subir el proyecto a GitHub

### Instalar Git (si no lo tienes)
Descarga desde: https://git-scm.com/download/win

### En PowerShell, desde la carpeta del proyecto:
```powershell
cd C:\Users\nofea\OneDrive\Escritorio\football_value_analyzer\football_value

# Inicializar repositorio
git init
git add .
git commit -m "Football Value Analyzer - initial commit"
```

### Crear repositorio en GitHub:
1. Ve a https://github.com/new
2. Nombre: `football-value-analyzer`
3. Privado ✅ (para proteger tu código)
4. NO inicialices con README
5. Clic en **Create repository**

### Conectar y subir:
```powershell
git remote add origin https://github.com/TU_USUARIO/football-value-analyzer.git
git branch -M main
git push -u origin main
```

---

## Paso 2 — Desplegar en Streamlit Cloud

1. Ve a https://share.streamlit.io
2. Clic en **"New app"**
3. Conecta tu cuenta de GitHub
4. Selecciona:
   - **Repository:** `TU_USUARIO/football-value-analyzer`
   - **Branch:** `main`
   - **Main file path:** `app.py`
5. Clic en **"Deploy!"**

---

## Paso 3 — Configurar las API Keys (IMPORTANTE)

Las keys NO van en el código — van en los Secrets de Streamlit:

1. En tu app desplegada, clic en **"⋮" (tres puntos)** → **Settings**
2. Clic en **"Secrets"**
3. Pega exactamente esto (con tus keys reales):

```toml
ODDS_API_KEY = "tu_key_real_aqui"
FOOTBALL_DATA_KEY = "tu_key_real_aqui"
```

4. Clic en **"Save"** — la app se reiniciará automáticamente

---

## Paso 4 — Verificar

Tu app estará disponible en:
```
https://TU_USUARIO-football-value-analyzer-app-XXXXX.streamlit.app
```

Puedes acceder desde cualquier dispositivo — PC, móvil, tablet.

---

## Actualizar la app en el futuro

Cada vez que hagas cambios locales:
```powershell
git add .
git commit -m "descripción del cambio"
git push
```
Streamlit Cloud se actualiza automáticamente en ~30 segundos.

---

## ⚠️ Límites del plan gratuito de Streamlit Cloud

| Recurso | Límite |
|---|---|
| Apps activas | 1 |
| RAM | 1 GB |
| CPU | Compartida |
| Tiempo activo | Ilimitado |
| Se "duerme" tras | 7 días sin visitas |

Para reactivarla después de que se duerma, solo visita la URL y espera ~30 segundos.

---

## ⚠️ Sobre el caché en la nube

En Streamlit Cloud el sistema de archivos es temporal — el caché se borra
cada vez que la app se reinicia. Esto significa más llamadas a las APIs,
pero con los TTLs configurados (stats: 6h, cuotas: 10min) no agotarás
los límites gratuitos en uso normal.
