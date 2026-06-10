import os
import json
import re
import time
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any, List

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# ==========================================
# 1. CREDENCIALES (DESDE VARIABLES DE ENTORNO)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# Credenciales de Google desde variable de entorno
google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if not google_creds_json:
    raise Exception("❌ GOOGLE_CREDENTIALS no encontrada en variables de entorno")

# Convertir string JSON a diccionario
creds_dict = json.loads(google_creds_json)

groq_client = Groq(api_key=GROQ_API_KEY)

# ==========================================
# 2. CONEXIÓN GOOGLE SHEETS
# ==========================================
def conectar_google_sheets():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    return client

print("🔄 Conectando a Google Sheets...")
client = conectar_google_sheets()
spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
inventario_sheet = spreadsheet.worksheet("Inventario")
ventas_sheet = spreadsheet.worksheet("Ventas")
compras_sheet = spreadsheet.worksheet("Compras")
print("✅ Conectado a Google Sheets")

# ==========================================
# 3. FUNCIÓN PARA CONVERTIR NÚMEROS
# ==========================================
def convertir_numero(valor):
    if valor is None or valor == "":
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        texto = valor.strip()
        texto = texto.replace('S/.', '').replace('S/', '').replace('$', '').replace(' ', '')
        if ',' in texto:
            texto = texto.replace(',', '.')
        numeros = re.findall(r'(\d+(?:\.\d+)?)', texto)
        if numeros:
            try:
                return float(numeros[0])
            except:
                return 0.0
    return 0.0

# ==========================================
# 4. FUNCIONES DE PRODUCTOS
# ==========================================
def obtener_productos():
    try:
        data = inventario_sheet.get_all_values()
        productos = {}
        for idx, row in enumerate(data[1:], start=2):
            if len(row) > 1 and row[1] and str(row[1]).strip():
                nombre = str(row[1]).strip()
                productos[nombre] = {
                    "fila": idx,
                    "codigo": row[0] if len(row) > 0 else "",
                    "precio": convertir_numero(row[4] if len(row) > 4 else "0"),
                    "costo": convertir_numero(row[3] if len(row) > 3 else "0"),
                    "stock_actual": float(row[8]) if len(row) > 8 and row[8] else 0,
                }
        return productos
    except Exception as e:
        print(f"Error: {e}")
        return {}

# ==========================================
# 5. FUNCIONES DE VENTAS Y GANANCIAS
# ==========================================
def obtener_ventas(periodo: str = "dia") -> List[Dict]:
    try:
        data = ventas_sheet.get_all_values()
        ventas = []
        ahora = datetime.now()
        
        if periodo == "dia":
            limite = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        elif periodo == "semana":
            limite = ahora - timedelta(days=7)
        elif periodo == "mes":
            limite = ahora - timedelta(days=30)
        elif periodo == "anio":
            limite = ahora - timedelta(days=365)
        else:
            limite = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        
        for row in data[1:]:
            if len(row) < 5:
                continue
            fecha_str = row[0] if row[0] else ""
            if not fecha_str:
                continue
            try:
                fecha = datetime.strptime(fecha_str, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
                except:
                    continue
            if fecha < limite:
                continue
            if periodo == "dia" and fecha.date() != ahora.date():
                continue
            
            producto = row[2] if len(row) > 2 else "Desconocido"
            cantidad = float(row[3]) if len(row) > 3 and row[3] else 0
            total = convertir_numero(row[5] if len(row) > 5 else "0")
            ganancia = convertir_numero(row[6] if len(row) > 6 else "0")
            
            if total > 0:
                ventas.append({
                    "fecha": fecha,
                    "producto": producto,
                    "cantidad": cantidad,
                    "total": total,
                    "ganancia": ganancia,
                })
        return ventas
    except Exception as e:
        print(f"Error: {e}")
        return []

def calcular_ganancias(periodo: str = "dia") -> Dict:
    ventas = obtener_ventas(periodo)
    return {
        "total_ventas": sum(v["total"] for v in ventas),
        "total_ganancia": sum(v["ganancia"] for v in ventas),
        "num_ventas": len(ventas),
    }

def top_productos_mas_vendidos(periodo: str = "dia", limite: int = 5) -> List[Dict]:
    ventas = obtener_ventas(periodo)
    producto_cantidad = {}
    for venta in ventas:
        p = venta["producto"]
        producto_cantidad[p] = producto_cantidad.get(p, 0) + venta["cantidad"]
    top = []
    for p, cant in sorted(producto_cantidad.items(), key=lambda x: x[1], reverse=True)[:limite]:
        top.append({"producto": p, "cantidad": cant})
    return top

def top_productos_mas_ganancia(periodo: str = "dia", limite: int = 5) -> List[Dict]:
    ventas = obtener_ventas(periodo)
    producto_ganancia = {}
    for venta in ventas:
        p = venta["producto"]
        producto_ganancia[p] = producto_ganancia.get(p, 0) + venta["ganancia"]
    top = []
    for p, gan in sorted(producto_ganancia.items(), key=lambda x: x[1], reverse=True)[:limite]:
        top.append({"producto": p, "ganancia": gan})
    return top

def formatear_periodo(periodo: str) -> str:
    return {"dia": "hoy", "semana": "esta semana", "mes": "este mes", "anio": "este año"}.get(periodo, "hoy")

# ==========================================
# 6. FUNCIONES DE NEGOCIO
# ==========================================
def agregar_nuevo_producto(nombre: str, costo: float, precio: float, stock: int) -> Tuple[bool, str]:
    try:
        productos = obtener_productos()
        if nombre in productos:
            return False, f"❌ '{nombre}' ya existe"
        
        all_data = inventario_sheet.get_all_values()
        ultimo_codigo = len([r for r in all_data[1:] if r and r[0] and str(r[0]).isdigit()]) + 1
        
        inventario_sheet.append_row([
            ultimo_codigo, nombre, "General",
            costo, precio, stock,
            0, 0, stock, 5, precio - costo, "NORMAL"
        ])
        time.sleep(0.5)
        return True, f"✅ NUEVO PRODUCTO: {nombre}\n💰 Precio: S/{precio:.2f}\n📊 Stock: {stock}"
    except Exception as e:
        return False, f"❌ Error: {e}"

def actualizar_stock(producto: str, cantidad: int, es_venta: bool, costo=None, precio_venta=None) -> Tuple[bool, str]:
    try:
        productos = obtener_productos()
        
        # Buscar producto exacto
        producto_real = None
        for nombre in productos:
            if nombre.lower() == producto.lower():
                producto_real = nombre
                break
        
        # Si no existe y es compra con precios, crear producto
        if not producto_real and not es_venta and costo is not None and precio_venta is not None:
            return agregar_nuevo_producto(producto, costo, precio_venta, cantidad)
        
        if not producto_real:
            return False, f"❌ Producto '{producto}' no existe.\n\n💡 Para crearlo: `compre {cantidad} {producto}, costo X, venta Y`"
        
        info = productos[producto_real]
        stock_actual = info["stock_actual"]
        precio_unitario = precio_venta if precio_venta is not None else info["precio"]
        costo_unitario = costo if costo is not None else info["costo"]
        
        if es_venta:
            if precio_unitario <= 0:
                return False, f"❌ '{producto_real}' NO TIENE PRECIO."
            if stock_actual < cantidad:
                return False, f"❌ Stock insuficiente. Solo hay {int(stock_actual)}"
            
            nuevo_stock = stock_actual - cantidad
            total = cantidad * precio_unitario
            ganancia = cantidad * (precio_unitario - costo_unitario)
            
            ventas_sheet.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                info["codigo"], producto_real, cantidad,
                precio_unitario, total, ganancia, "Telegram Bot", "EFECTIVO"
            ])
            inventario_sheet.update_cell(info["fila"], 9, nuevo_stock)
            return True, f"✅ VENTA: {cantidad} {producto_real}\n💰 Total: S/{total:.2f}\n📊 Stock: {int(nuevo_stock)}"
        else:
            nuevo_stock = stock_actual + cantidad
            compras_sheet.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                info["codigo"], producto_real, cantidad,
                cantidad * info["costo"], "Telegram Bot", "Compra"
            ])
            inventario_sheet.update_cell(info["fila"], 9, nuevo_stock)
            mensaje = f"✅ COMPRA: +{cantidad} {producto_real}\n📊 Stock: {int(nuevo_stock)}"
            if precio_venta and precio_venta > 0:
                inventario_sheet.update_cell(info["fila"], 5, precio_venta)
                mensaje += f"\n💰 Precio: S/{precio_venta:.2f}"
            return True, mensaje
    except Exception as e:
        return False, f"❌ Error: {e}"

# ==========================================
# 7. GROQ - CEREBRO ÚNICO
# ==========================================
def interpretar_con_groq(mensaje: str) -> Dict[str, Any]:
    try:
        productos = obtener_productos()
        lista_productos = ", ".join([f'"{p}"' for p in productos.keys()]) if productos else "ninguno"
        
        prompt = f"""
Eres el asistente de inventario de Lúmin Shop. Tu tarea es entender el mensaje del usuario y devolver SOLO un JSON.

PRODUCTOS EXISTENTES EN EL INVENTARIO: {lista_productos}

REGLAS IMPORTANTES:
1. Si el usuario quiere COMPRAR o AGREGAR stock (usa palabras como "compre", "compré", "traje", "llegaron"): accion = "COMPRA"
2. Si el usuario quiere VENDER (usa palabras como "vendí", "vendió", "se vendió", "se vendieron"): accion = "VENTA"
3. Si pregunta por PRECIO o STOCK de un producto: accion = "CONSULTA"
4. Si pregunta por GANANCIAS: accion = "REPORTE_GANANCIAS"
5. Si pregunta qué se VENDIÓ MÁS: accion = "TOP_VENTAS"
6. Si pregunta qué producto dio MÁS GANANCIA: accion = "TOP_GANANCIAS"

PERIODOS: detecta "hoy", "semana", "mes", "año"

CORRECCIÓN DE TIPEO: 
- Corrige errores obvios como "sinta" → "cinta", "tazas" → "Taza Sublimada", "baso" → "vaso blanco"
- Para productos nuevos, usa el nombre que el usuario escribió (ej: "guantes" no se corrige a nada)

FORMATO DE RESPUESTA (SOLO JSON, sin texto adicional):

Para COMPRA:
{{"accion": "COMPRA", "producto": "nombre", "cantidad": numero, "costo": numero, "precio": numero}}

Para VENTA:
{{"accion": "VENTA", "producto": "nombre", "cantidad": numero}}

Para CONSULTA:
{{"accion": "CONSULTA", "producto": "nombre"}}

Para REPORTE_GANANCIAS:
{{"accion": "REPORTE_GANANCIAS", "periodo": "dia/semana/mes/anio"}}

Para TOP_VENTAS:
{{"accion": "TOP_VENTAS", "periodo": "dia/semana/mes/anio"}}

Para TOP_GANANCIAS:
{{"accion": "TOP_GANANCIAS", "periodo": "dia/semana/mes/anio"}}

EJEMPLOS:
- "compre 7 guantes, costo 5, venta 10" → {{"accion": "COMPRA", "producto": "guantes", "cantidad": 7, "costo": 5, "precio": 10}}
- "vendí 3 tazas" → {{"accion": "VENTA", "producto": "Taza Sublimada", "cantidad": 3}}
- "cuanto gane hoy" → {{"accion": "REPORTE_GANANCIAS", "periodo": "dia"}}
- "que se vendio mas esta semana" → {{"accion": "TOP_VENTAS", "periodo": "semana"}}
- "sinta" → {{"accion": "CONSULTA", "producto": "cinta"}}

MENSAJE DEL USUARIO: "{mensaje}"

RESPONDE SOLO EL JSON:
"""
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200
        )
        
        respuesta_texto = response.choices[0].message.content
        print(f"🧠 GROQ raw: {respuesta_texto}")
        
        json_match = re.search(r'\{.*\}', respuesta_texto, re.DOTALL)
        if json_match:
            try:
                resultado = json.loads(json_match.group())
                if "accion" in resultado:
                    return resultado
            except:
                pass
        
        return {"accion": "DESCONOCIDO"}
        
    except Exception as e:
        print(f"Error GROQ: {e}")
        return {"accion": "DESCONOCIDO"}

# ==========================================
# 8. COMANDOS DE TELEGRAM
# ==========================================
async def reporte_ganancias(update: Update, periodo: str = "dia"):
    g = calcular_ganancias(periodo)
    await update.message.reply_text(
        f"📊 *REPORTE DE GANANCIAS - {formatear_periodo(periodo).upper()}* 📊\n\n"
        f"💰 *Total Ventas:* S/{g['total_ventas']:.2f}\n"
        f"📈 *Ganancia:* S/{g['total_ganancia']:.2f}\n\n"
        f"📊 *Transacciones:* {g['num_ventas']} ventas",
        parse_mode="Markdown"
    )

async def top_ventas(update: Update, periodo: str = "dia"):
    top = top_productos_mas_vendidos(periodo, 5)
    if not top:
        await update.message.reply_text(f"📊 No hay ventas {formatear_periodo(periodo)}.")
        return
    msg = f"🏆 *TOP 5 MÁS VENDIDOS - {formatear_periodo(periodo).upper()}* 🏆\n\n"
    for i, item in enumerate(top, 1):
        msg += f"{i}. *{item['producto']}*: {int(item['cantidad'])} uds\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def top_ganancias(update: Update, periodo: str = "dia"):
    top = top_productos_mas_ganancia(periodo, 5)
    if not top:
        await update.message.reply_text(f"📊 No hay ventas {formatear_periodo(periodo)}.")
        return
    msg = f"💰 *TOP 5 GANANCIA - {formatear_periodo(periodo).upper()}* 💰\n\n"
    for i, item in enumerate(top, 1):
        msg += f"{i}. *{item['producto']}*: S/{item['ganancia']:.2f}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌟 *LÚMIN SHOP* 🌟\n\n"
        "📦 *VENDER:* `vendí 3 tazas`\n"
        "🛒 *COMPRAR:* `compre 7 guantes, costo 5, venta 10`\n"
        "🔍 *CONSULTAR:* `taza` o `sinta`\n"
        "📊 *GANANCIAS:* `cuanto gane hoy`\n"
        "🏆 *RANKINGS:* `top ventas esta semana`\n\n"
        "💡 *GROQ interpreta todo en lenguaje natural*",
        parse_mode="Markdown"
    )

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    productos = obtener_productos()
    if not productos:
        await update.message.reply_text("❌ No hay productos")
        return
    msg = "📋 *PRODUCTOS*\n\n"
    for i, (n, i2) in enumerate(sorted(productos.items()), 1):
        msg += f"{i}. {n} - S/{i2['precio']:.2f} - Stock: {int(i2['stock_actual'])}\n"
        if i >= 20: break
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    productos = obtener_productos()
    if not productos:
        await update.message.reply_text("❌ No hay productos")
        return
    msg = "📊 *INVENTARIO*\n\n" + "\n".join([f"• {n}: {int(i['stock_actual'])} uds - S/{i['precio']:.2f}" for n, i in productos.items()])
    await update.message.reply_text(msg[:4000], parse_mode="Markdown")

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = update.message.text
    if mensaje.startswith('/'):
        return
    
    await update.message.chat.send_action(action="typing")
    
    i = interpretar_con_groq(mensaje)
    accion = i.get("accion", "DESCONOCIDO")
    print(f"🎯 Acción final: {accion}")
    
    if accion == "REPORTE_GANANCIAS":
        await reporte_ganancias(update, i.get("periodo", "dia"))
    
    elif accion == "TOP_VENTAS":
        await top_ventas(update, i.get("periodo", "dia"))
    
    elif accion == "TOP_GANANCIAS":
        await top_ganancias(update, i.get("periodo", "dia"))
    
    elif accion == "COMPRA":
        producto = i.get("producto", "")
        cantidad = i.get("cantidad", 0)
        costo = i.get("costo")
        precio = i.get("precio")
        if producto and cantidad > 0:
            ok, resp = actualizar_stock(producto, cantidad, False, costo, precio)
            await update.message.reply_text(resp)
        else:
            await update.message.reply_text("❌ Formato: `compre 7 guantes, costo 5, venta 10`")
    
    elif accion == "VENTA":
        producto = i.get("producto", "")
        cantidad = i.get("cantidad", 0)
        if producto and cantidad > 0:
            ok, resp = actualizar_stock(producto, cantidad, True)
            await update.message.reply_text(resp)
        else:
            await update.message.reply_text("❌ Ejemplo: `vendí 3 tazas`")
    
    elif accion == "CONSULTA":
        producto = i.get("producto", "")
        if not producto:
            await update.message.reply_text("¿Qué producto?")
            return
        # Buscar producto (exacto o corregido)
        productos = obtener_productos()
        prod_real = None
        for nombre in productos:
            if nombre.lower() == producto.lower():
                prod_real = nombre
                break
        if not prod_real:
            for nombre in productos:
                if producto.lower() in nombre.lower() or nombre.lower() in producto.lower():
                    prod_real = nombre
                    break
        if prod_real:
            info = productos[prod_real]
            await update.message.reply_text(f"📦 *{prod_real}*\n💰 Precio: S/{info['precio']:.2f}\n📊 Stock: {int(info['stock_actual'])}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ '{producto}' no existe.\n💡 Para crearlo: `compre 10 {producto}, costo X, venta Y`", parse_mode="Markdown")
    
    else:
        await update.message.reply_text(
            "❌ No entendí. Ejemplos:\n"
            "• `vendí 3 tazas`\n"
            "• `compre 7 guantes, costo 5, venta 10`\n"
            "• `cuanto gane hoy`\n"
            "• `top ventas esta semana`\n"
            "• `taza`",
            parse_mode="Markdown"
        )

# ==========================================
# 9. MAIN
# ==========================================
def main():
    print("=" * 50)
    print("🤖 LÚMIN SHOP Bot - GROQ Cerebro Único")
    print("=" * 50)
    
    productos = obtener_productos()
    print(f"📊 {len(productos)} productos en inventario")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lista", lista_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))
    
    print("\n✅ Bot corriendo...")
    print("💡 Prueba: 'compre 7 guantes, costo 5, venta 10'")
    app.run_polling()

if __name__ == "__main__":
    main()