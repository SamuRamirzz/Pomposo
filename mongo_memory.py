"""
Módulo de memoria persistente usando MongoDB Atlas.
Reemplaza el archivo bot_memory.txt con una base de datos en la nube.
Mantiene compatibilidad total con las funciones existentes.
"""
import os
from pymongo import MongoClient

MONGO_URI = os.getenv('MONGODB_URI')

# --- Conexión ---
_client = None
_db = None
_collection = None

def _get_collection():
    """Obtiene (o crea) la conexión a MongoDB."""
    global _client, _db, _collection
    if _collection is not None:
        return _collection
    
    uri = os.getenv('MONGODB_URI')
    if not uri:
        return None
    
    try:
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _db = _client['pomposo_bot']
        _collection = _db['memory']
        # Test conexión
        _client.admin.command('ping')
        print("✅ Conectado a MongoDB Atlas")
        return _collection
    except Exception as e:
        print(f"⚠️ Error conectando a MongoDB: {e}")
        _collection = None
        return None


# --- Funciones de Memoria (compatibles con las originales) ---

def leer_memoria_completa() -> str:
    """Lee toda la memoria como texto (equivale a leer bot_memory.txt)."""
    col = _get_collection()
    if col is None:
        return _leer_archivo_fallback()
    
    try:
        entries = col.find({}, {'texto': 1}).sort('_id', 1)
        lineas = [doc['texto'] for doc in entries if 'texto' in doc]
        return '\n'.join(lineas)
    except Exception as e:
        print(f"Error leyendo MongoDB: {e}")
        return _leer_archivo_fallback()


def leer_memoria_lineas() -> list:
    """Lee la memoria como lista de líneas."""
    col = _get_collection()
    if col is None:
        return _leer_archivo_lineas_fallback()
    
    try:
        entries = col.find({}, {'texto': 1}).sort('_id', 1)
        return [doc['texto'] for doc in entries if 'texto' in doc]
    except Exception as e:
        print(f"Error leyendo MongoDB: {e}")
        return _leer_archivo_lineas_fallback()


def escribir_en_memoria(texto: str):
    """Agrega una línea a la memoria."""
    col = _get_collection()
    if col is None:
        _escribir_archivo_fallback(texto)
        return
    
    try:
        col.insert_one({'texto': texto.strip()})
    except Exception as e:
        print(f"Error escribiendo en MongoDB: {e}")
        _escribir_archivo_fallback(texto)


def reescribir_memoria_lineas(lineas: list):
    """Reescribe toda la memoria con las líneas dadas."""
    col = _get_collection()
    if col is None:
        _reescribir_archivo_fallback(lineas)
        return
    
    try:
        col.delete_many({})
        if lineas:
            col.insert_many([{'texto': l.strip()} for l in lineas if l.strip()])
    except Exception as e:
        print(f"Error reescribiendo MongoDB: {e}")
        _reescribir_archivo_fallback(lineas)


def olvidar_por_texto(texto_a_buscar: str) -> str:
    """Busca y elimina la línea más parecida al texto dado. Retorna la línea eliminada o None."""
    col = _get_collection()
    if col is None:
        return None
    
    try:
        from fuzzywuzzy import fuzz
        entries = list(col.find({}, {'texto': 1}))
        mejor = None
        mejor_score = 0
        texto_lower = texto_a_buscar.lower()
        
        for doc in entries:
            score = fuzz.partial_ratio(texto_lower, doc['texto'].lower())
            if score > mejor_score:
                mejor_score = score
                mejor = doc
        
        if mejor and mejor_score >= 60:
            col.delete_one({'_id': mejor['_id']})
            return mejor['texto']
        return None
    except Exception as e:
        print(f"Error olvidando en MongoDB: {e}")
        return None


def importar_desde_archivo(filepath: str = 'bot_memory.txt'):
    """Importa el contenido de bot_memory.txt a MongoDB (migración inicial)."""
    col = _get_collection()
    if col is None:
        print("⚠️ No se puede importar: sin conexión a MongoDB")
        return False
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lineas = [l.strip() for l in f.readlines() if l.strip()]
        
        if not lineas:
            print("Archivo vacío, nada que importar.")
            return True
        
        # Verificar si ya hay datos
        count = col.count_documents({})
        if count > 0:
            print(f"MongoDB ya tiene {count} entradas. Saltando importación.")
            return True
        
        col.insert_many([{'texto': l} for l in lineas])
        print(f"✅ Importadas {len(lineas)} líneas de memoria a MongoDB")
        return True
    except FileNotFoundError:
        print("No se encontró bot_memory.txt para importar")
        return False
    except Exception as e:
        print(f"Error importando: {e}")
        return False


# --- Fallbacks a archivo local (cuando no hay MongoDB) ---

MEMORY_FILE = "bot_memory.txt"

def _leer_archivo_fallback() -> str:
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return ""

def _leer_archivo_lineas_fallback() -> list:
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            return [l.strip() for l in f.readlines() if l.strip()]
    except:
        return []

def _escribir_archivo_fallback(texto: str):
    with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{texto}\n")

def _reescribir_archivo_fallback(lineas: list):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        for l in lineas:
            f.write(f"{l}\n")
