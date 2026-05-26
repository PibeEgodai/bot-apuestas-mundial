import os
import re
import calendar
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("Falta BOT_TOKEN en variables de entorno.")

if not DATABASE_URL:
    raise ValueError("Falta DATABASE_URL en variables de entorno.")


MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def normalizar_texto(texto: str) -> str:
    return texto.strip()


def parse_fecha_es(fecha_txt: str):
    """
    Recibe:
    14 de Junio
    14 junio
    """
    fecha_txt = fecha_txt.strip().lower()
    fecha_txt = fecha_txt.replace("  ", " ")

    match = re.search(r"(\d{1,2})\s*(?:de)?\s*([a-záéíóúñ]+)", fecha_txt)
    if not match:
        return None

    dia = int(match.group(1))
    mes_nombre = match.group(2)

    mes_nombre = (
        mes_nombre.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )

    mes = MESES.get(mes_nombre)
    if not mes:
        return None

    return datetime(2026, mes, dia).date()


def formato_fecha_larga(fecha):
    meses = {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }
    return f"{fecha.day} de {meses[fecha.month]}"


def calcular_puntaje(res_local, res_visita, ap_local, ap_visita):
    if ap_local is None or ap_visita is None:
        return None

    puntaje = 0

    # Tendencia correcta: local, empate o visita
    tendencia_real = (res_local > res_visita) - (res_local < res_visita)
    tendencia_apuesta = (ap_local > ap_visita) - (ap_local < ap_visita)

    if tendencia_real == tendencia_apuesta:
        puntaje += 2

    # Diferencia de goles correcta
    if (res_local - res_visita) == (ap_local - ap_visita):
        puntaje += 1

    # Marcador exacto por equipo
    if res_local == ap_local:
        puntaje += 1

    if res_visita == ap_visita:
        puntaje += 1

    return puntaje


def parse_apuesta_linea(linea):
    """
    Diego: Francia 2 - 3 España
    Diego: Francia ❌ - ❌ España
    Diego: Francia X - X España
    """
    linea = linea.strip()

    patron = r"^(.+?):\s+(.+?)\s+([0-9]+|❌|x|X)\s*-\s*([0-9]+|❌|x|X)\s+(.+)$"
    match = re.match(patron, linea)

    if not match:
        return None

    participante = match.group(1).strip()
    pais_1 = match.group(2).strip()
    gol_1 = match.group(3).strip()
    gol_2 = match.group(4).strip()
    pais_2 = match.group(5).strip()

    def convertir_gol(valor):
        if valor in ["❌", "x", "X"]:
            return None
        return int(valor)

    return {
        "participante": participante,
        "pais_1": pais_1,
        "resultado_1": convertir_gol(gol_1),
        "resultado_2": convertir_gol(gol_2),
        "pais_2": pais_2,
    }


def parse_resultado_linea(linea):
    """
    Francia 1 - 0 España
    """
    linea = linea.strip()

    patron = r"^(.+?)\s+([0-9]+)\s*-\s*([0-9]+)\s+(.+)$"
    match = re.match(patron, linea)

    if not match:
        return None

    return {
        "pais_1": match.group(1).strip(),
        "resultado_1": int(match.group(2)),
        "resultado_2": int(match.group(3)),
        "pais_2": match.group(4).strip(),
    }


def validar_limite_apuesta(partido):
    """
    Retorna True si todavía se puede apostar.
    Límite: hasta 5 minutos antes del partido.
    Usa hora Chile según lo guardado en calendario.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fecha, hora
                FROM calendario
                WHERE partido = %s
                """,
                (partido,),
            )
            row = cur.fetchone()

    if not row:
        return False, "No existe el partido indicado."

    fecha = row["fecha"]
    hora = row["hora"]

    inicio_partido = datetime.combine(fecha, hora)
    limite = inicio_partido - timedelta(minutes=5)
    ahora = datetime.now()

    if ahora > limite:
        return False, "Las apuestas para este partido ya cerraron."

    return True, None


async def comandos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = """
Comandos disponibles

GenerarPartidosApuestas
Muestra los partidos de una fecha.
Ejemplo:
GenerarPartidosApuestas
14 de Junio

GenerarMoldesApuestas
Genera el molde oficial para apostar en una fecha.
Ejemplo:
GenerarMoldesApuestas
14 de Junio

IngresarApuestas
Carga las apuestas de un partido.
Ejemplo:
IngresarApuestas
42
Diego: Francia 2 - 1 España
Nico: Francia ❌ - ❌ España

IngresarResultado
Carga el resultado real de un partido y calcula puntajes.
Ejemplo:
IngresarResultado
42
Francia 1 - 0 España

GenerarResultadosApuestas
Muestra los puntajes obtenidos en una fecha.
Ejemplo:
GenerarResultadosApuestas
14 de Junio

GenerarRanking
Muestra el ranking general acumulado.
Ejemplo:
GenerarRanking

MostrarApuestasPartido
Muestra las apuestas cargadas para un partido.
Ejemplo:
MostrarApuestasPartido
42

EliminarApuestasPartido
Elimina todas las apuestas cargadas para un partido.
Ejemplo:
EliminarApuestasPartido
42

Comandos
Muestra este listado de comandos.
Ejemplo:
Comandos
"""
    await update.message.reply_text(texto.strip())


async def generar_partidos_apuestas(update: Update, lineas):
    if len(lineas) < 2:
        await update.message.reply_text("Debes indicar una fecha. Ejemplo:\nGenerarPartidosApuestas\n14 de Junio")
        return

    fecha = parse_fecha_es(lineas[1])
    if not fecha:
        await update.message.reply_text("Fecha inválida. Usa formato: 14 de Junio")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT partido, fecha, hora, etapa, pais_1, pais_2
                FROM calendario
                WHERE fecha = %s
                ORDER BY fecha, hora, partido
                """,
                (fecha,),
            )
            partidos = cur.fetchall()

    if not partidos:
        await update.message.reply_text("No hay partidos para esa fecha.")
        return

    respuesta = f"{formato_fecha_larga(fecha)}\n\n"

    for p in partidos:
        hora = p["hora"].strftime("%H:%M")
        respuesta += f"• {hora} Hrs. - ({p['etapa']}) {p['pais_1']} vs {p['pais_2']}\n"

    await update.message.reply_text(respuesta.rstrip())


async def generar_moldes_apuestas(update: Update, lineas):
    if len(lineas) < 2:
        await update.message.reply_text("Debes indicar una fecha. Ejemplo:\nGenerarMoldesApuestas\n14 de Junio")
        return

    fecha = parse_fecha_es(lineas[1])
    if not fecha:
        await update.message.reply_text("Fecha inválida. Usa formato: 14 de Junio")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT partido, pais_1, pais_2
                FROM calendario
                WHERE fecha = %s
                ORDER BY fecha, hora, partido
                """,
                (fecha,),
            )
            partidos = cur.fetchall()

            cur.execute(
                """
                SELECT participante
                FROM participantes
                ORDER BY id
                """
            )
            participantes = cur.fetchall()

    if not partidos:
        await update.message.reply_text("No hay partidos para esa fecha.")
        return

    if not participantes:
        await update.message.reply_text("No hay participantes cargados.")
        return

    respuesta = ""

    for p in partidos:
        respuesta += f"Partido {p['partido']}\n\n"
        for part in participantes:
            respuesta += f"{part['participante']}: {p['pais_1']} ❌ - ❌ {p['pais_2']}\n"
        respuesta += "\n"

    await update.message.reply_text(respuesta.rstrip())


async def ingresar_apuestas(update: Update, lineas):
    if len(lineas) < 3:
        await update.message.reply_text(
            "Formato inválido.\nEjemplo:\nIngresarApuestas\n42\nDiego: Francia 2 - 1 España"
        )
        return

    try:
        partido = int(lineas[1].strip())
    except ValueError:
        await update.message.reply_text("El partido debe ser numérico.")
        return

    permitido, mensaje = validar_limite_apuesta(partido)
    if not permitido:
        await update.message.reply_text(mensaje)
        return

    apuestas = []
    errores = []

    for linea in lineas[2:]:
        if not linea.strip():
            continue

        apuesta = parse_apuesta_linea(linea)
        if not apuesta:
            errores.append(linea)
        else:
            apuestas.append(apuesta)

    if errores:
        await update.message.reply_text("Hay líneas con formato inválido:\n" + "\n".join(errores))
        return

    if not apuestas:
        await update.message.reply_text("No se detectaron apuestas válidas.")
        return

    insertadas = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for a in apuestas:
                estado = "SIN_APUESTA" if a["resultado_1"] is None or a["resultado_2"] is None else "PENDIENTE"

                cur.execute(
                    """
                    INSERT INTO apuestas (
                        partido,
                        participante,
                        resultado_1,
                        resultado_2,
                        puntaje,
                        estado
                    )
                    VALUES (%s, %s, %s, %s, NULL, %s)
                    ON CONFLICT (partido, participante)
                    DO UPDATE SET
                        resultado_1 = EXCLUDED.resultado_1,
                        resultado_2 = EXCLUDED.resultado_2,
                        puntaje = NULL,
                        estado = EXCLUDED.estado
                    """,
                    (
                        partido,
                        a["participante"],
                        a["resultado_1"],
                        a["resultado_2"],
                        estado,
                    ),
                )
                insertadas += 1

        conn.commit()

    await update.message.reply_text(f"Apuestas actualizadas para el Partido {partido}: {insertadas}")


async def ingresar_resultado(update: Update, lineas):
    if len(lineas) < 3:
        await update.message.reply_text(
            "Formato inválido.\nEjemplo:\nIngresarResultado\n42\nFrancia 1 - 0 España"
        )
        return

    try:
        partido = int(lineas[1].strip())
    except ValueError:
        await update.message.reply_text("El partido debe ser numérico.")
        return

    resultado = parse_resultado_linea(lineas[2])

    if not resultado:
        await update.message.reply_text("Resultado inválido. Ejemplo:\nFrancia 1 - 0 España")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE calendario
                SET resultado_1 = %s,
                    resultado_2 = %s
                WHERE partido = %s
                """,
                (resultado["resultado_1"], resultado["resultado_2"], partido),
            )

            if cur.rowcount == 0:
                await update.message.reply_text("No existe el partido indicado.")
                return

            cur.execute(
                """
                SELECT id, resultado_1, resultado_2, puntaje, estado
                FROM apuestas
                WHERE partido = %s
                """,
                (partido,),
            )
            apuestas = cur.fetchall()

            for a in apuestas:
                if a["resultado_1"] is None or a["resultado_2"] is None:
                    cur.execute(
                        """
                        UPDATE apuestas
                        SET puntaje = 0,
                            estado = 'SIN_APUESTA'
                        WHERE id = %s
                        """,
                        (a["id"],),
                    )
                    continue

                nuevo_puntaje = calcular_puntaje(
                    resultado["resultado_1"],
                    resultado["resultado_2"],
                    a["resultado_1"],
                    a["resultado_2"],
                )

                nuevo_estado = "CALCULADA"

                if a["puntaje"] is not None and a["puntaje"] != nuevo_puntaje:
                    nuevo_estado = "MODIFICADO"

                cur.execute(
                    """
                    UPDATE apuestas
                    SET puntaje = %s,
                        estado = %s
                    WHERE id = %s
                    """,
                    (nuevo_puntaje, nuevo_estado, a["id"]),
                )

        conn.commit()

    await update.message.reply_text(f"Resultado ingresado y puntajes calculados para el Partido {partido}.")


async def generar_resultados_apuestas(update: Update, lineas):
    if len(lineas) < 2:
        await update.message.reply_text("Debes indicar una fecha. Ejemplo:\nGenerarResultadosApuestas\n14 de Junio")
        return

    fecha = parse_fecha_es(lineas[1])
    if not fecha:
        await update.message.reply_text("Fecha inválida. Usa formato: 14 de Junio")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                    p.participante,
                    c.partido,
                    a.puntaje,
                    a.estado
                FROM participantes p
                CROSS JOIN calendario c
                LEFT JOIN apuestas a
                    ON a.participante = p.participante
                    AND a.partido = c.partido
                WHERE c.fecha = %s
                ORDER BY p.id, c.fecha, c.hora, c.partido
                """,
                (fecha,),
            )
            rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("No hay información para esa fecha.")
        return

    respuesta = ""
    participante_actual = None

    for r in rows:
        if participante_actual != r["participante"]:
            if participante_actual is not None:
                respuesta += "\n"
            participante_actual = r["participante"]
            respuesta += f"{participante_actual}\n\n"

        if r["estado"] == "SIN_APUESTA" or r["puntaje"] is None:
            texto_puntaje = "Sin Apuesta"
        else:
            texto_puntaje = f"{r['puntaje']} Pts."

        respuesta += f"• Partido {r['partido']}: {texto_puntaje}\n"

    await update.message.reply_text(respuesta.rstrip())


async def generar_ranking(update: Update):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                    p.participante,
                    COALESCE(SUM(a.puntaje), 0) AS total
                FROM participantes p
                LEFT JOIN apuestas a
                    ON a.participante = p.participante
                GROUP BY p.id, p.participante
                ORDER BY total DESC, p.participante ASC
                """
            )
            rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("No hay participantes para generar ranking.")
        return

    respuesta = "Ranking General\n\n"

    for idx, r in enumerate(rows, start=1):
        nombre = r["participante"]
        total = r["total"]

        if idx == 1:
            pos = "🥇"
        elif idx == 2:
            pos = "🥈"
        elif idx == 3:
            pos = "🥉"
        else:
            pos = f"{idx}º"

        respuesta += f"{pos} - {nombre} ({total} Pts.)\n"

    await update.message.reply_text(respuesta.rstrip())


async def mostrar_apuestas_partido(update: Update, lineas):
    if len(lineas) < 2:
        await update.message.reply_text("Debes indicar partido. Ejemplo:\nMostrarApuestasPartido\n42")
        return

    try:
        partido = int(lineas[1].strip())
    except ValueError:
        await update.message.reply_text("El partido debe ser numérico.")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pais_1, pais_2
                FROM calendario
                WHERE partido = %s
                """,
                (partido,),
            )
            partido_info = cur.fetchone()

            if not partido_info:
                await update.message.reply_text("No existe el partido indicado.")
                return

            cur.execute(
                """
                SELECT participante, resultado_1, resultado_2, puntaje, estado
                FROM apuestas
                WHERE partido = %s
                ORDER BY participante
                """,
                (partido,),
            )
            rows = cur.fetchall()

    if not rows:
        await update.message.reply_text(f"No hay apuestas cargadas para el Partido {partido}.")
        return

    respuesta = f"Partido {partido}\n{partido_info['pais_1']} vs {partido_info['pais_2']}\n\n"

    for r in rows:
        if r["estado"] == "SIN_APUESTA":
            marcador = "Sin Apuesta"
        else:
            marcador = f"{r['resultado_1']} - {r['resultado_2']}"

        puntaje = "NULL" if r["puntaje"] is None else f"{r['puntaje']} Pts."
        respuesta += f"• {r['participante']}: {marcador}"

    await update.message.reply_text(respuesta.rstrip())


async def eliminar_apuestas_partido(update: Update, lineas):
    if len(lineas) < 2:
        await update.message.reply_text("Debes indicar partido. Ejemplo:\nEliminarApuestasPartido\n42")
        return

    try:
        partido = int(lineas[1].strip())
    except ValueError:
        await update.message.reply_text("El partido debe ser numérico.")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM apuestas
                WHERE partido = %s
                """,
                (partido,),
            )
            eliminadas = cur.rowcount

        conn.commit()

    await update.message.reply_text(f"Apuestas eliminadas para el Partido {partido}: {eliminadas}")


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = normalizar_texto(update.message.text)
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]

    if not lineas:
        return

    comando = lineas[0].strip().lower()

    try:
        if comando == "comandos":
            await comandos(update, context)

        elif comando == "generarpartidosapuestas":
            await generar_partidos_apuestas(update, lineas)

        elif comando == "generarmoldesapuestas":
            await generar_moldes_apuestas(update, lineas)

        elif comando == "ingresarapuestas":
            await ingresar_apuestas(update, lineas)

        elif comando in ["ingresarresultado", "ingresarsresultado"]:
            await ingresar_resultado(update, lineas)

        elif comando == "generarresultadosapuestas":
            await generar_resultados_apuestas(update, lineas)

        elif comando == "generarranking":
            await generar_ranking(update)

        elif comando == "mostrarapuestaspartido":
            await mostrar_apuestas_partido(update, lineas)

        elif comando == "eliminarapuestaspartido":
            await eliminar_apuestas_partido(update, lineas)

        else:
            await update.message.reply_text("Comando no reconocido. Usa:\nComandos")

    except Exception as e:
        print("ERROR:", e)
        await update.message.reply_text(f"Error ejecutando comando:\n{e}")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    print("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()